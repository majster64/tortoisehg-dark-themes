# filectxactions.py - context menu actions for repository files
#
# Copyright 2010 Adrian Buehlmann <adrian@cadifra.com>
# Copyright 2010 Steve Borho <steve@borho.org>
# Copyright 2012 Yuya Nishihara <yuya@tcha.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import os
import re
import typing

from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Text,
    Tuple,
    Type,
    TypeVar,
    cast,
)

from .qtcore import (
    QDir,
    QMimeData,
    QObject,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from .qtgui import (
    QAction,
    QApplication,
    QFileDialog,
    QMenu,
    QWidget,
)

from mercurial import (
    context,
    pycompat,
)

from ..util import (
    hglib,
    shlib,
)
from ..util.i18n import _
from . import (
    cmdcore,
    cmdui,
    customtools,
    lfprompt,
    qtlib,
    rejects,
    revert,
    visdiff,
)


if typing.TYPE_CHECKING:
    from mercurial import (
        localrepo,
        ui as uimod,
    )
    from .cmdcore import CmdSession
    from .filedata import _AbstractFileData
    from .thgrepo import RepoAgent
    from ..util.typelib import HgContext
    FileData = _AbstractFileData

_ActionTableEntry = Tuple[str, Optional[str], Optional[str], str,
                          Tuple["_FileDataFilter", ...]]
_ActionTable = Dict[str, _ActionTableEntry]

# The filter function type to reduce the list of input FileData entries.
_FileDataFilter = Callable[[List["FileData"]], List["FileData"]]

_MenuActionEntry = Tuple[QAction, Tuple[_FileDataFilter, ...]]

_W = TypeVar('_W', bound=QWidget)


def _lcanonpaths(fds: List[FileData]) -> List[bytes]:
    return [hglib.fromunicode(e.canonicalFilePath()) for e in fds]

# predicates to filter files
def _anydeleted(fds: List[FileData]) -> List[FileData]:
    if any(
        e.rev() is None and cast(context.workingctx, e.rawContext()).deleted()
        for e in fds
    ):
        return fds
    return []
def _committed(fds: List[FileData]) -> List[FileData]:
    # pytype: disable=unsupported-operands
    return [e for e in fds if e.rev() is not None and e.rev() >= 0]
    # pytype: enable=unsupported-operands

def _filepath(pat: str) -> _FileDataFilter:
    patre = re.compile(pat)
    return lambda fds: [e for e in fds if patre.search(e.filePath())]
def _filestatus(s: str) -> _FileDataFilter:
    s = frozenset(s)
    # include directory since its status is unknown
    return lambda fds: [e for e in fds if e.isDir() or e.fileStatus() in s]
def _indirectbaserev(fds: List[FileData]) -> List[FileData]:
    return [e for e in fds if e.baseRev() not in e.parentRevs()]
def _isdir(fds: List[FileData]) -> List[FileData]:
    return [e for e in fds if e.isDir()]
def _isfile(fds: List[FileData]) -> List[FileData]:
    return [e for e in fds if not e.isDir()]
def _merged(fds: List[FileData]) -> List[FileData]:
    return [e for e in fds if len(e.rawContext().parents()) > 1]
def _mergestatus(s: str) -> _FileDataFilter:
    s = frozenset(s)
    # include directory since its status is unknown
    return lambda fds: [e for e in fds if e.isDir() or e.mergeStatus() in s]
def _notpatch(fds: List[FileData]) -> List[FileData]:
    # pytype: disable=unsupported-operands
    return [e for e in fds if e.rev() is None or e.rev() >= 0]
    # pytype: enable=unsupported-operands

def _notsubrepo(fds: List[FileData]) -> List[FileData]:
    return [e for e in fds if not e.repoRootPath() and not e.subrepoType()]
def _notsubroot(fds: List[FileData]) -> List[FileData]:
    return [e for e in fds if not e.subrepoType()]
def _single(fds: List[FileData]) -> List[FileData]:
    if len(fds) != 1:
        return []
    return fds
def _subrepotype(t: str) -> _FileDataFilter:
    return lambda fds: [e for e in fds if e.subrepoType() == t]

def _filterby(
    fdfilters: Tuple[_FileDataFilter, ...],
    fds: List[FileData]
) -> List[FileData]:
    for f in fdfilters:
        if not fds:
            return []
        fds = f(fds)
    return fds

def _tablebuilder(table):
    """Make decorator to define actions that receive filtered files

    If the slot, wrapped(), is invoked, the specified function is called
    with filtered files, func(fds), only if "fds" is not empty.
    """
    def slot(text, icon, shortcut, statustip, fdfilters=()):
        if not isinstance(fdfilters, tuple):
            fdfilters = (fdfilters,)
        def decorate(func):
            name = func.__name__
            table[name] = (text, icon, shortcut, statustip, fdfilters)
            def wrapped(self):
                fds = self.fileDataListForAction(name)
                if not fds:
                    return
                func(self, fds)
            return pyqtSlot(name=name)(wrapped)
        return decorate
    return slot


class FilectxActions(QObject):
    """Container for repository file actions"""

    linkActivated = pyqtSignal(str)
    filterRequested = pyqtSignal(str)
    """Ask the repowidget to change its revset filter"""
    runCustomCommandRequested = pyqtSignal(str, list)

    _actiontable: _ActionTable = {}
    actionSlot = _tablebuilder(_actiontable)

    def __init__(
        self,
        repoagent: RepoAgent,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        if not isinstance(parent, QWidget):
            raise ValueError('parent must be a QWidget')

        self._repoagent = repoagent
        self._cmdsession: CmdSession = cmdcore.nullCmdSession()
        self._selfds: List[FileData] = []

        self._nav_dialogs = qtlib.DialogKeeper(FilectxActions._createnavdialog,
                                               FilectxActions._gennavdialogkey,
                                               self)

        self._actions: Dict[str, _MenuActionEntry] = {}
        self._customactions: Dict[str, _MenuActionEntry] = {}
        for name, d in self._actiontable.items():
            desc, icon, key, tip, fdfilters = d
            # QAction must be owned by QWidget; otherwise statusTip for context
            # menu cannot be displayed (QTBUG-16114)
            act = QAction(desc, self.parent())
            if icon:
                act.setIcon(qtlib.geticon(icon))
            if key:
                qtlib.setContextMenuShortcut(act, key)
                act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            if tip:
                act.setStatusTip(tip)
            act.triggered.connect(getattr(self, name))
            self._addAction(name, act, fdfilters)

        self._initAdditionalActions()
        self._updateActions()

    def _initAdditionalActions(self) -> None:
        # override to add actions that cannot be declared as actionSlot
        pass

    def _parentWidget(self) -> QWidget:
        p = self.parent()
        assert isinstance(p, QWidget)
        return p

    @property
    def _ui(self) -> uimod.ui:
        repo = self._repoagent.rawRepo()
        return repo.ui

    def _repoAgentFor(self, fd: FileData) -> RepoAgent:
        rpath = fd.repoRootPath()
        if not rpath:
            return self._repoagent
        return self._repoagent.subRepoAgent(rpath)

    def _updateActions(self) -> None:
        idle = self._cmdsession.isFinished()
        selfds = self._selfds
        allactions = (list(self._actions.values())
                    + list(self._customactions.values()))
        for act, fdfilters in allactions:
            act.setEnabled(idle and bool(_filterby(fdfilters, selfds)))

    def fileDataListForAction(self, name: str) -> List[FileData]:
        fdfilters = self._actions[name][1]
        return _filterby(fdfilters, self._selfds)

    def setFileDataList(self, selfds: List[FileData]) -> None:
        self._selfds = list(selfds)
        self._updateActions()

    def actions(self) -> List[QAction]:
        """List of the actions; The owner widget should register them"""
        return [a for a, _f in self._actions.values()]

    def action(self, name: str) -> QAction:
        return self._actions[name][0]

    def _addAction(
        self, name: str,
        action: QAction,
        fdfilters: Tuple[_FileDataFilter, ...]
    ) -> None:
        assert name not in self._actions, name
        self._actions[name] = action, fdfilters

    def _runCommand(self, cmdline: List[str]) -> CmdSession:
        if not self._cmdsession.isFinished():
            return cmdcore.nullCmdSession()
        sess = self._repoagent.runCommand(cmdline, self._parentWidget())
        self._handleNewCommand(sess)
        return sess

    def _runCommandSequence(self, cmdlines: List[List[str]]) -> CmdSession:
        if not self._cmdsession.isFinished():
            return cmdcore.nullCmdSession()
        sess = self._repoagent.runCommandSequence(cmdlines, self._parentWidget())
        self._handleNewCommand(sess)
        return sess

    def _handleNewCommand(self, sess: CmdSession) -> None:
        assert self._cmdsession.isFinished()
        self._cmdsession = sess
        sess.commandFinished.connect(self._onCommandFinished)
        self._updateActions()

    @pyqtSlot(int)
    def _onCommandFinished(self, ret: int) -> None:
        if ret == 255:
            cmdui.errorMessageBox(self._cmdsession, self._parentWidget())
        self._updateActions()

    @actionSlot(_('File &History / Annotate'), 'hg-log', 'Shift+Return',
                _('Show the history of the selected file'),
                (_isfile, _notpatch, _filestatus('MARC!')))
    def navigateFileLog(self, fds: List[FileData]) -> None:
        from tortoisehg.hgqt import filedialogs, fileview
        for fd in fds:
            dlg = self._navigate(filedialogs.FileLogDialog, fd)
            if not dlg:
                continue
            dlg.setFileViewMode(fileview.AnnMode)

    @actionSlot(_('Co&mpare File Revisions'), 'compare-files', None,
                _('Compare revisions of the selected file'),
                (_isfile, _notpatch))
    def navigateFileDiff(self, fds: List[FileData]) -> None:
        from tortoisehg.hgqt import filedialogs
        for fd in fds:
            self._navigate(filedialogs.FileDiffDialog, fd)

    def _navigate(
        self,
        dlgclass: Type[_W],
        fd: FileData
    ) -> Optional[_W]:
        repoagent = self._repoAgentFor(fd)
        repo = repoagent.rawRepo()
        filename = hglib.fromunicode(fd.canonicalFilePath())
        if repo.file(filename):
            dlg = self._nav_dialogs.open(dlgclass, repoagent, filename)
            dlg.goto(fd.rev())
            return dlg

    def _createnavdialog(
        self,
        dlgclass: Type[_W],
        repoagent: RepoAgent,
        filename: bytes,
    ) -> _W:
        return dlgclass(repoagent, filename)

    def _gennavdialogkey(
        self,
        dlgclass: Type[_W],
        repoagent: RepoAgent,
        filename: bytes,
    ) -> Tuple[Type[_W], bytes]:
        repo = repoagent.rawRepo()
        return dlgclass, repo.wjoin(filename)

    @actionSlot(_('Filter Histor&y'), 'hg-log', None,
                _('Query about changesets affecting the selected files'),
                _notsubrepo)
    def filterFile(self, fds: List[FileData]) -> None:
        pats = ["file('path:%s')" % e.filePath() for e in fds]
        self.filterRequested.emit(' or '.join(pats))

    @actionSlot(_('Diff &Changeset to Parent'), 'visualdiff', None, '',
                _notpatch)
    def visualDiff(self, fds: List[FileData]) -> None:
        self._visualDiffToBase(fds[0], [])

    @actionSlot(_('Diff Changeset to Loc&al'), 'ldiff', None, '',
                _committed)
    def visualDiffToLocal(self, fds: List[FileData]) -> None:
        self._visualDiff(fds[0], [], rev=['rev(%d)' % fds[0].rev()])

    @actionSlot(_('&Diff to Parent'), 'visualdiff', 'Ctrl+D',
                _('View file changes in external diff tool'),
                (_notpatch, _notsubroot, _filestatus('MAR!')))
    def visualDiffFile(self, fds: List[FileData]) -> None:
        self._visualDiffToBase(fds[0], fds)

    @actionSlot(_('Diff to &Local'), 'ldiff', 'Shift+Ctrl+D',
                _('View changes to current in external diff tool'),
                _committed)
    def visualDiffFileToLocal(self, fds: List[FileData]) -> None:
        self._visualDiff(fds[0], fds, rev=['rev(%d)' % fds[0].rev()])

    def _visualDiffToBase(
        self,
        an_fd: FileData,
        fds: List[FileData],
    ) -> None:
        if an_fd.baseRev() == an_fd.parentRevs()[0]:
            self._visualDiff(an_fd, fds, change=an_fd.rev())  # can 3-way
        else:
            revs = [an_fd.baseRev()]
            if an_fd.rev() is not None:
                revs.append(an_fd.rev())
            self._visualDiff(an_fd, fds, rev=['rev(%d)' % r for r in revs])

    def _visualDiff(
        self,
        an_fd: FileData,
        fds: List[FileData],
        **opts,
    ) -> None:
        filenames = _lcanonpaths(fds)
        repo = self._repoAgentFor(an_fd).rawRepo()
        dlg = visdiff.visualdiff(repo.ui, repo, filenames, opts)
        if dlg:
            dlg.exec()

    @actionSlot(_('&View at Revision'), 'view-at-revision', 'Shift+Ctrl+E',
                _('View file as it appeared at this revision using the '
                  'visual editor'),
                _committed)
    def editFile(self, fds: List[FileData]) -> None:
        self._editFileAt(fds, fds[0].rawContext())

    def _editFileAt(self, fds: List[FileData], ctx: HgContext) -> None:
        repo = self._repoAgentFor(fds[0]).rawRepo()
        filenames = _lcanonpaths(fds)
        base, _ = visdiff.snapshot(repo, filenames, ctx)
        files = [os.path.join(base, filename)
                 for filename in filenames]
        qtlib.editfiles(repo, files, parent=self._parentWidget())

    @actionSlot(_('&Open at Revision'), 'open-at-revision', None,
                _("Open file as it appeared at this revision using the "
                  "system's default application for this file type"),
                _committed)
    def openFile(self, fds: List[FileData]) -> None:
        self._openFileAt(fds, fds[0].rawContext())

    def _openFileAt(self, fds: List[FileData], ctx: HgContext) -> None:
        repo = self._repoAgentFor(fds[0]).rawRepo()
        filenames = _lcanonpaths(fds)
        base, _ = visdiff.snapshot(repo, filenames, ctx)
        files = [os.path.join(base, filename)
                 for filename in filenames]
        for f in files:
            qtlib.openlocalurl(f)

    @actionSlot(_('&Save at Revision...'), None, 'Shift+Ctrl+S',
                _('Save file as it appeared at this revision'),
                _committed)
    def saveFile(self, fds: List[FileData]) -> None:
        cmdlines = []
        for fd in fds:
            wfile, ext = os.path.splitext(fd.absoluteFilePath())
            extfilter = [_("All files (*)")]
            filename = "%s@%d%s" % (wfile, fd.rev(), ext)
            if ext:
                extfilter.insert(0, "*%s" % ext)

            result, _filter = QFileDialog.getSaveFileName(
                self._parentWidget(), _("Save file to"), filename,
                ";;".join(extfilter))
            if not result:
                continue
            # checkout in working-copy line endings, etc. by --decode
            cmdlines.append(hglib.buildcmdargs(
                'cat', hglib.escapepath(fd.canonicalFilePath()), rev=fd.rev(),
                output=result, decode=True))

        if cmdlines:
            self._runCommandSequence(cmdlines)

    @actionSlot(_('&Edit Local'), 'edit-file', None,
                _('Edit current file(s) in working copy with the visual '
                  'editor'),
                (_isfile, _filestatus('MACI?')))
    def editLocalFile(self, fds: List[FileData]) -> None:
        repo = self._repoAgentFor(fds[0]).rawRepo()
        filenames = _lcanonpaths(fds)
        qtlib.editfiles(repo, filenames, parent=self._parentWidget())

    @actionSlot(_('&Open Local'), None, 'Shift+Ctrl+L',
                _("Open current file(s) in working copy with the system's "
                  "default application for this file type"),
                (_isfile, _filestatus('MACI?')))
    def openLocalFile(self, fds: List[FileData]) -> None:
        repo = self._repoAgentFor(fds[0]).rawRepo()
        for fd in fds:
            qtlib.openlocalurl(fd.absoluteFilePath())

    @actionSlot(_('E&xplore Local'), 'system-file-manager', None,
                _('Open parent folder of current file in the system file '
                  'manager'),
                (_isfile, _filestatus('MACI?')))
    def exploreLocalFile(self, fds: List[FileData]) -> None:
        for fd in fds:
            qtlib.openlocalurl(os.path.dirname(fd.absoluteFilePath()))

    @actionSlot(_('&Copy Patch'), 'copy-patch', None, '',
                (_notpatch, _notsubroot, _filestatus('MAR!')))
    def copyPatch(self, fds: List[FileData]) -> None:
        paths = [hglib.escapepath(fd.filePath()) for fd in fds]
        revs = pycompat.maplist(hglib.escaperev,
                                [fds[0].baseRev(), fds[0].rev()])
        cmdline = hglib.buildcmdargs('diff', *paths, r=revs)
        sess = self._runCommand(cmdline)
        sess.setCaptureOutput(True)
        sess.commandFinished.connect(self._copyPatchOutputToClipboard)

    @pyqtSlot(int)
    def _copyPatchOutputToClipboard(self, ret: int) -> None:
        if ret != 0:
            return
        output = self._cmdsession.readAll()
        mdata = QMimeData()
        mdata.setData('text/x-diff', output)  # for lossless import
        mdata.setText(hglib.tounicode(bytes(output)))
        QApplication.clipboard().setMimeData(mdata)

    @actionSlot(_('Copy Absolute &Path'), None, 'Shift+Ctrl+C',
                _('Copy full path of file(s) to the clipboard'))
    def copyPath(self, fds: List[FileData]) -> None:
        paths = [e.absoluteFilePath() for e in fds]
        QApplication.clipboard().setText(os.linesep.join(paths))

    @actionSlot(_('Copy Relative Path'), None, None,
                _('Copy repository relative path of file(s) to the clipboard'))
    def copyRelativePath(self, fds: List[FileData]) -> None:
        paths = [QDir.toNativeSeparators(e.filePath()) for e in fds]
        QApplication.clipboard().setText(os.linesep.join(paths))

    @actionSlot(_('&Revert to Revision...'), 'hg-revert', 'Shift+Ctrl+R',
                _('Revert file(s) to contents at this revision'),
                _notpatch)
    def revertFile(self, fds: List[FileData]) -> None:
        repoagent = self._repoAgentFor(fds[0])
        fileSelection = [e.canonicalFilePath() for e in fds]
        rev = fds[0].rev()
        if rev is None:
            repo = repoagent.rawRepo()
            rev = repo[rev].p1().rev()
        dlg = revert.RevertDialog(repoagent, fileSelection, rev,
                                  parent=self._parentWidget())
        dlg.exec()

    @actionSlot(_('Open S&ubrepository'), 'thg-repository-open', None,
                _('Open the selected subrepository'),
                _subrepotype('hg'))
    def openSubrepo(self, fds: List[FileData]) -> None:
        for fd in fds:
            if fd.rev() is None:
                link = 'repo:%s' % fd.absoluteFilePath()
            else:
                ctx = fd.rawContext()
                spath = hglib.fromunicode(fd.canonicalFilePath())
                revid = hglib.tounicode(ctx.substate[spath][1])
                link = 'repo:%s?%s' % (fd.absoluteFilePath(), revid)
            self.linkActivated.emit(link)

    @actionSlot(_('E&xplore Folder'), 'system-file-manager', None,
                _('Open the selected folder in the system file manager'),
                _isdir)
    def explore(self, fds: List[FileData]) -> None:
        for fd in fds:
            qtlib.openlocalurl(fd.absoluteFilePath())

    @actionSlot(_('Open &Terminal'), 'utilities-terminal', None,
                _('Open a shell terminal in the selected folder'),
                _isdir)
    def terminal(self, fds: List[FileData]) -> None:
        for fd in fds:
            root = hglib.fromunicode(fd.absoluteFilePath())
            currentfile = hglib.fromunicode(fd.filePath())
            qtlib.openshell(root, currentfile, self._ui)

    def setupCustomToolsMenu(self, location: str) -> None:
        tools, toollist = hglib.tortoisehgtools(self._ui, location)
        submenu = QMenu(_('Custom Tools'), self._parentWidget())
        submenu.triggered.connect(self._runCustomCommandByMenu)
        for name in toollist:
            if name == '|':
                submenu.addSeparator()
                continue
            info = tools.get(name, None)
            if info is None:
                continue
            command = info.get('command', None)
            if not command:
                continue
            label = info.get('label', name)
            icon = info.get('icon', customtools.DEFAULTICONNAME)
            status = info.get('status')
            assert isinstance(label, str)  # help pytype
            a = submenu.addAction(label)
            a.setData(name)
            if icon:
                a.setIcon(qtlib.geticon(icon))
            if status:
                fdfilters = (_filestatus(status),)
            else:
                fdfilters = ()
            self._customactions[name] = (a, fdfilters)
        submenu.menuAction().setVisible(bool(self._customactions))
        self._addAction('customToolsMenu', submenu.menuAction(), ())
        self._updateActions()

    @pyqtSlot(QAction)
    def _runCustomCommandByMenu(self, action: QAction) -> None:
        name = str(action.data())
        fdfilters = self._customactions[name][1]
        fds = _filterby(fdfilters, self._selfds)
        files = [fd.filePath() for fd in fds]
        self.runCustomCommandRequested.emit(name, files)


class WctxActions(FilectxActions):
    'container class for working context actions'

    refreshNeeded = pyqtSignal()

    _actiontable: _ActionTable = FilectxActions._actiontable.copy()
    actionSlot = _tablebuilder(_actiontable)

    def _initAdditionalActions(self) -> None:
        repo = self._repoagent.rawRepo()
        # the same shortcut as editFile that is disabled for working rev
        a = self.action('editLocalFile')
        qtlib.setContextMenuShortcut(a, 'Ctrl+Shift+E')
        a.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        a = self.action('addLargefile')
        a.setVisible(b'largefiles' in repo.extensions())
        self._addAction('renameFileMenu', *self._createRenameFileMenu())
        self._addAction('remergeFileMenu', *self._createRemergeFileMenu())

    @property
    def repo(self) -> localrepo.localrepository:
        return self._repoagent.rawRepo()

    def _runWorkingFileCommand(
        self,
        cmdname: str,
        fds: List[FileData],
        opts: Optional[Dict[str, Any]]=None,
    ) -> CmdSession:
        if not opts:
            opts = {}
        paths = [hglib.escapepath(fd.filePath()) for fd in fds]
        cmdline = hglib.buildcmdargs(cmdname, *paths, **opts)
        sess = self._runCommand(cmdline)
        sess.commandFinished.connect(self._notifyChangesOnCommandFinished)
        return sess

    @pyqtSlot(int)
    def _notifyChangesOnCommandFinished(self, ret: int) -> None:
        if ret == 0:
            self._notifyChanges()

    def _notifyChanges(self) -> None:
        # include all selected files for maximum possibility
        wfiles = [hglib.fromunicode(fd.absoluteFilePath())
                  for fd in self._selfds]
        shlib.shell_notify(wfiles)
        self.refreshNeeded.emit()

    # this action will no longer be necessary if status widget can toggle
    # base revision in amend/qrefresh mode
    @actionSlot(_('Diff &Local'), 'ldiff', 'Ctrl+Shift+D', '',
                (_indirectbaserev, _notsubroot, _filestatus('MARC!')))
    def visualDiffLocalFile(self, fds: List[FileData]) -> None:
        self._visualDiff(fds[0], fds)

    @actionSlot(_('&View Missing'), None, None, '',
                (_isfile, _filestatus('R!')))
    def editMissingFile(self, fds: List[FileData]) -> None:
        wctx = fds[0].rawContext()
        self._editFileAt(fds, wctx.p1())

    @actionSlot(_('View O&ther'), None, None, '',
                (_isfile, _merged, _filestatus('MA')))
    def editOtherFile(self, fds: List[FileData]) -> None:
        wctx = fds[0].rawContext()
        self._editFileAt(fds, wctx.p2())

    @actionSlot(_('&Add'), 'hg-add', None, '',
                (_notsubroot, _filestatus('RI?')))
    def addFile(self, fds: List[FileData]) -> None:
        repo = self._repoAgentFor(fds[0]).rawRepo()
        if b'largefiles' in repo.extensions():
            self._addFileWithPrompt(fds)
        else:
            self._runWorkingFileCommand('add', fds)

    def _addFileWithPrompt(self, fds: List[FileData]) -> None:
        repo = self._repoAgentFor(fds[0]).rawRepo()
        result = lfprompt.promptForLfiles(self._parentWidget(), repo.ui, repo,
                                          _lcanonpaths(fds))
        if not result:
            return
        cmdlines = []
        for opt, paths in zip(('normal', 'large'), result):
            if not paths:
                continue
            paths = [hglib.escapepath(hglib.tounicode(e)) for e in paths]
            cmdlines.append(hglib.buildcmdargs('add', *paths, **{opt: True}))
        sess = self._runCommandSequence(cmdlines)
        sess.commandFinished.connect(self._notifyChangesOnCommandFinished)

    @actionSlot(_('Add &Largefiles...'), None, None, '',
                (_notsubroot, _filestatus('I?')))
    def addLargefile(self, fds: List[FileData]) -> None:
        self._runWorkingFileCommand('add', fds, {'large': True})

    @actionSlot(_('&Forget'), 'hg-remove', None, '',
                (_notsubroot, _filestatus('MAC!')))
    def forgetFile(self, fds: List[FileData]) -> None:
        self._runWorkingFileCommand('forget', fds)

    @actionSlot(_('&Delete Unversioned...'), 'hg-purge', 'Delete', '',
                (_notsubroot, _filestatus('?I')))
    def purgeFile(self, fds: List[FileData]) -> None:
        parent = self._parentWidget()
        files = [hglib.fromunicode(fd.filePath()) for fd in fds]
        res = qtlib.CustomPrompt(
            _('Confirm Delete Unversioned'),
            _('Delete the following unversioned files?'),
            parent, (_('&Delete'), _('Cancel')), 1, 1, files).run()
        if res == 1:
            return
        opts = {'config': 'extensions.purge=', 'all': True}
        self._runWorkingFileCommand('purge', fds, opts)

    @actionSlot(_('Re&move Versioned'), 'hg-remove', None, '',
                (_notsubroot, _filestatus('C')))
    def removeFile(self, fds: List[FileData]) -> None:
        self._runWorkingFileCommand('remove', fds)

    @actionSlot(_('&Revert...'), 'hg-revert', None, '',
                _filestatus('MAR!'))
    def revertWorkingFile(self, fds: List[FileData]) -> None:
        parent = self._parentWidget()
        files = _lcanonpaths(fds)
        wctx = cast(context.workingctx, fds[0].rawContext())
        revertopts = {'date': None, 'rev': '.', 'all': False}
        if len(wctx.parents()) > 1:
            res = qtlib.CustomPrompt(
                _('Uncommited merge - please select a parent revision'),
                _('Revert files to local or other parent?'), parent,
                (_('&Local'), _('&Other'), _('Cancel')), 0, 2, files).run()
            if res == 0:
                revertopts['rev'] = wctx.p1().rev()
            elif res == 1:
                revertopts['rev'] = wctx.p2().rev()
            else:
                return
        elif [file for file in files if file in wctx.modified()]:
            res = qtlib.CustomPrompt(
                _('Confirm Revert'),
                _('Revert local file changes?'), parent,
                (_('&Revert with backup'), _('&Discard changes'),
                 _('Cancel')), 2, 2, files).run()
            if res == 2:
                return
            if res == 1:
                revertopts['no_backup'] = True
        else:
            res = qtlib.CustomPrompt(
                _('Confirm Revert'),
                _('Revert the following files?'),
                parent, (_('&Revert'), _('Cancel')), 1, 1, files).run()
            if res == 1:
                return
        self._runWorkingFileCommand('revert', fds, revertopts)

    @actionSlot(_('&Copy...'), 'edit-copy', None, '',
                (_single, _isfile, _filestatus('MAC')))
    def copyFile(self, fds: List[FileData]) -> None:
        self._openRenameDialog(fds, iscopy=True)

    @actionSlot(_('Re&name...'), 'hg-rename', None, '',
                (_single, _isfile, _filestatus('MAC')))
    def renameFile(self, fds: List[FileData]) -> None:
        self._openRenameDialog(fds, iscopy=False)

    def _openRenameDialog(self, fds: List[FileData], iscopy: bool) -> None:
        from tortoisehg.hgqt.rename import RenameDialog
        srcfd, = fds
        repoagent = self._repoAgentFor(srcfd)
        dlg = RenameDialog(repoagent, self._parentWidget(),
                           srcfd.canonicalFilePath(), iscopy=iscopy)
        if dlg.exec() == 0:
            self._notifyChanges()

    @actionSlot(_('&Ignore...'), 'thg-ignore', None, '',
                (_notsubroot, _filestatus('?')))
    def editHgignore(self, fds: List[FileData]) -> None:
        from tortoisehg.hgqt.hgignore import HgignoreDialog
        repoagent = self._repoAgentFor(fds[0])
        parent = self._parentWidget()
        files = _lcanonpaths(fds)
        dlg = HgignoreDialog(repoagent, parent, *files)
        dlg.finished.connect(dlg.deleteLater)
        dlg.exec()
        self._notifyChanges()

    @actionSlot(_('Edit Re&jects'), None, None,
                _('Manually resolve rejected patch chunks'),
                (_single, _isfile, _filestatus('?I'), _filepath(r'\.rej$')))
    def editRejects(self, fds: List[FileData]) -> None:
        lpath = hglib.fromunicode(fds[0].absoluteFilePath()[:-4])  # drop .rej
        dlg = rejects.RejectsDialog(self._ui, lpath, self._parentWidget())
        if dlg.exec():
            self._notifyChanges()

    @actionSlot(_('De&tect Renames...'), 'thg-guess', None, '',
                (_isfile, _filestatus('A?!')))
    def guessRename(self, fds: List[FileData]) -> None:
        from tortoisehg.hgqt.guess import DetectRenameDialog
        repoagent = self._repoAgentFor(fds[0])
        parent = self._parentWidget()
        files = _lcanonpaths(fds)
        dlg = DetectRenameDialog(repoagent, parent, *files)
        def matched():
            ret[0] = True
        ret = [False]
        dlg.matchAccepted.connect(matched)
        dlg.finished.connect(dlg.deleteLater)
        dlg.exec()
        if ret[0]:
            self._notifyChanges()

    @actionSlot(_('&Mark Resolved'), None, None, '',
                (_notsubroot, _mergestatus('U')))
    def markFileAsResolved(self, fds: List[FileData]) -> None:
        self._runWorkingFileCommand('resolve', fds, {'mark': True})

    @actionSlot(_('&Mark Unresolved'), None, None, '',
                (_notsubroot, _mergestatus('R')))
    def markFileAsUnresolved(self, fds: List[FileData]) -> None:
        self._runWorkingFileCommand('resolve', fds, {'unmark': True})

    @actionSlot(_('Restart Mer&ge'), None, None, '',
                (_notsubroot, _mergestatus('U')))
    def remergeFile(self, fds: List[FileData]) -> None:
        self._runWorkingFileCommand('resolve', fds)

    def _createRenameFileMenu(self) -> _MenuActionEntry:
        menu = QMenu(_('Was renamed from'), self._parentWidget())
        menu.aboutToShow.connect(self._updateRenameFileMenu)
        menu.triggered.connect(self._renameFrom)
        fdfilters = (_single, _isfile, _filestatus('?'), _anydeleted)
        return menu.menuAction(), fdfilters

    @pyqtSlot()
    def _updateRenameFileMenu(self) -> None:
        menu = self.sender()
        assert isinstance(menu, QMenu), repr(menu)
        menu.clear()
        fds = self.fileDataListForAction('renameFileMenu')
        if not fds:
            return
        wctx = fds[0].rawContext()
        for d in wctx.deleted()[:15]:
            menu.addAction(hglib.tounicode(d))

    @pyqtSlot(QAction)
    def _renameFrom(self, action: QAction) -> None:
        fds = self.fileDataListForAction('renameFileMenu')
        if not fds:
            # selection might be changed after menu is shown
            return
        deleted = hglib.escapepath(action.text())
        unknown = hglib.escapepath(fds[0].filePath())
        cmdlines = [hglib.buildcmdargs('copy', deleted, unknown, after=True),
                    hglib.buildcmdargs('forget', deleted)]  # !->R
        sess = self._runCommandSequence(cmdlines)
        sess.commandFinished.connect(self._notifyChangesOnCommandFinished)

    def _createRemergeFileMenu(self) -> _MenuActionEntry:
        menu = QMenu(_('Restart Merge &with'), self._parentWidget())
        menu.aboutToShow.connect(self._populateRemergeFileMenu)  # may be slow
        menu.triggered.connect(self._remergeFileWith)
        return menu.menuAction(), (_notsubroot, _mergestatus('U'))

    @pyqtSlot()
    def _populateRemergeFileMenu(self) -> None:
        menu = self.sender()
        assert isinstance(menu, QMenu), repr(menu)
        menu.aboutToShow.disconnect(self._populateRemergeFileMenu)
        for tool in hglib.mergetools(self._ui):
            menu.addAction(hglib.tounicode(tool))

    @pyqtSlot(QAction)
    def _remergeFileWith(self, action: QAction) -> None:
        fds = self.fileDataListForAction('remergeFileMenu')
        self._runWorkingFileCommand('resolve', fds, {'tool': action.text()})
