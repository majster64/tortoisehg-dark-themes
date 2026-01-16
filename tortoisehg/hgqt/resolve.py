# resolve.py - TortoiseHg merge conflict resolve
#
# Copyright 2010 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import os
import typing

from .qtcore import (
    QAbstractTableModel,
    QItemSelectionModel,
    QMimeData,
    QModelIndex,
    QPoint,
    QSettings,
    QUrl,
    Qt,
    pyqtSlot,
)
from .qtgui import (
    QAction,
    QActionGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QKeySequence,
    QLabel,
    QMenu,
    QMessageBox,
    QToolButton,
    QTreeView,
    QVBoxLayout,
)

from mercurial import (
    encoding,
    pycompat,
)

from ..util import hglib
from ..util.i18n import _
from . import (
    cmdcore,
    cmdui,
    csinfo,
    qtlib,
    thgrepo,
    visdiff,
)

if typing.TYPE_CHECKING:
    from typing import (
        List,
        Optional,
        Text,
        Tuple,
    )
    from .qtgui import (
        QWidget,
    )
    from ..util.typelib import (
        Qt_ItemFlags,
    )

MARGINS = (8, 0, 0, 0)

class ResolveDialog(QDialog):
    def __init__(self, repoagent, parent=None):
        super().__init__(parent)
        self._repoagent = repoagent
        self._cmdsession = cmdcore.nullCmdSession()
        self.setWindowFlags(self.windowFlags()
                            & ~Qt.WindowType.WindowContextHelpButtonHint
                            | Qt.WindowType.WindowMaximizeButtonHint)
        self.setWindowTitle(_('Resolve Conflicts - %s')
                            % repoagent.displayName())
        self.setWindowIcon(qtlib.geticon('hg-merge'))

        self.setLayout(QVBoxLayout())
        self.layout().setSpacing(5)

        self.mergeState = hglib.readmergestate(self.repo)

        hbox = QHBoxLayout()
        self.layout().addLayout(hbox)

        self.refreshButton = tb = QToolButton(self)
        tb.setIcon(qtlib.geticon('view-refresh'))
        tb.setShortcut(QKeySequence.StandardKey.Refresh)
        tb.clicked.connect(self.refresh)
        self.stlabel = QLabel()
        hbox.addWidget(tb)
        hbox.addWidget(self.stlabel)

        def revisionInfoLayout(repo):
            """
            Return a layout containg the revision information (local and other)
            """
            hbox = QHBoxLayout()
            hbox.setSpacing(0)
            hbox.setContentsMargins(*MARGINS)

            vbox = QVBoxLayout()
            vbox.setContentsMargins(*MARGINS)
            hbox.addLayout(vbox)
            localrevtitle = qtlib.LabeledSeparator(_('Local revision '
                                                     'information'))
            localrevinfo = csinfo.create(repo)
            localrevinfo.updateItems(self.mergeState.localctx)
            vbox.addWidget(localrevtitle)
            vbox.addWidget(localrevinfo)
            vbox.addStretch()

            vbox = QVBoxLayout()
            vbox.setContentsMargins(*MARGINS)
            hbox.addLayout(vbox)
            otherrevtitle = qtlib.LabeledSeparator(_('Other revision '
                                                     'information'))
            otherrevinfo = csinfo.create(repo)
            otherrevinfo.updateItems(self.mergeState.otherctx)

            vbox.addWidget(otherrevtitle)
            vbox.addWidget(otherrevinfo)
            vbox.addStretch()

            return hbox

        if self.mergeState.active():
            self.layout().addLayout(revisionInfoLayout(self.repo))

        unres = qtlib.LabeledSeparator(_('Unresolved conflicts'))
        self.layout().addWidget(unres)

        hbox = QHBoxLayout()
        hbox.setSpacing(0)
        hbox.setContentsMargins(*MARGINS)
        self.layout().addLayout(hbox)

        self.utree = QTreeView(self)
        self.utree.setDragDropMode(QTreeView.DragDropMode.DragOnly)
        self.utree.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        self.utree.setSortingEnabled(True)
        hbox.addWidget(self.utree)

        self.utree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.utreecmenu = QMenu(self)
        mergeactions = QActionGroup(self)
        mergeactions.triggered.connect(self._mergeByAction)
        cmauto = self.utreecmenu.addAction(_('Mercurial Re&solve'))
        cmauto.setToolTip(_('Attempt automatic (trivial) merge'))
        cmauto.setData('internal:merge')
        mergeactions.addAction(cmauto)
        cmmanual = self.utreecmenu.addAction(_('Tool &Resolve'))
        cmmanual.setToolTip(_('Merge using selected merge tool'))
        mergeactions.addAction(cmmanual)
        cmlocal = self.utreecmenu.addAction(_('&Take Local'))
        cmlocal.setToolTip(_('Accept the local file version (yours)'))
        cmlocal.setData('internal:local')
        mergeactions.addAction(cmlocal)
        cmother = self.utreecmenu.addAction(_('Take &Other'))
        cmother.setToolTip(_('Accept the other file version (theirs)'))
        cmother.setData('internal:other')
        mergeactions.addAction(cmother)
        cmres = self.utreecmenu.addAction(_('&Mark as Resolved'))
        cmres.setToolTip(_('Mark this file as resolved'))
        cmres.triggered.connect(self.markresolved)
        self.utreecmenu.addSeparator()
        cmdiffLocToAnc = self.utreecmenu.addAction(_('Diff &Local to Ancestor'))
        cmdiffLocToAnc.triggered.connect(self.diffLocToAnc)
        cmdiffOthToAnc = self.utreecmenu.addAction(_('&Diff Other to Ancestor'))
        cmdiffOthToAnc.triggered.connect(self.diffOthToAnc)
        self.umenuitems = (cmauto, cmmanual, cmlocal, cmother, cmres,
                           cmdiffLocToAnc, cmdiffOthToAnc)
        self.utree.customContextMenuRequested.connect(self.utreeMenuRequested)

        self.utree.doubleClicked.connect(self.utreeDoubleClicked)

        vbox = QVBoxLayout()
        vbox.setContentsMargins(*MARGINS)
        hbox.addLayout(vbox)
        for action in [cmauto, cmmanual, cmlocal, cmother, cmres]:
            vbox.addWidget(qtlib.ActionPushButton(action, self))
        vbox.addStretch(1)

        res = qtlib.LabeledSeparator(_('Resolved conflicts'))
        self.layout().addWidget(res)

        hbox = QHBoxLayout()
        hbox.setContentsMargins(*MARGINS)
        hbox.setSpacing(0)
        self.layout().addLayout(hbox)

        self.rtree = QTreeView(self)
        self.rtree.setDragDropMode(QTreeView.DragDropMode.DragOnly)
        self.rtree.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        self.rtree.setSortingEnabled(True)
        hbox.addWidget(self.rtree)

        self.rtree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.rtreecmenu = QMenu(self)
        cmedit = self.rtreecmenu.addAction(_('&Edit File'))
        cmedit.setToolTip(_('Edit resolved file'))
        cmedit.triggered.connect(self.edit)
        cmv3way = self.rtreecmenu.addAction(_('3-&Way Diff'))
        cmv3way.setToolTip(_('Visual three-way diff'))
        cmv3way.triggered.connect(self.v3way)
        cmvp0 = self.rtreecmenu.addAction(_('Diff to &Local'))
        cmvp0.setToolTip(_('Visual diff between resolved file and first '
                           'parent'))
        cmvp0.triggered.connect(self.vp0)
        cmvp1 = self.rtreecmenu.addAction(_('&Diff to Other'))
        cmvp1.setToolTip(_('Visual diff between resolved file and second '
                           'parent'))
        cmvp1.triggered.connect(self.vp1)
        cmures = self.rtreecmenu.addAction(_('Mark as &Unresolved'))
        cmures.setToolTip(_('Mark this file as unresolved'))
        cmures.triggered.connect(self.markunresolved)
        self.rmenuitems = (cmedit, cmvp0, cmures)
        self.rmmenuitems = (cmvp1, cmv3way)
        self.rtree.customContextMenuRequested.connect(self.rtreeMenuRequested)

        self.rtree.doubleClicked.connect(self.v3way)

        vbox = QVBoxLayout()
        vbox.setContentsMargins(*MARGINS)
        hbox.addLayout(vbox)
        for action in [cmedit, cmv3way, cmvp0, cmvp1, cmures]:
            vbox.addWidget(qtlib.ActionPushButton(action, self))
        vbox.addStretch(1)

        hbox = QHBoxLayout()
        hbox.setContentsMargins(*MARGINS)
        hbox.setSpacing(4)
        self.layout().addLayout(hbox)

        self.tcombo = ToolsCombo(self.repo, self)
        self.tcombo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)

        hbox.addWidget(QLabel(_('Detected merge/diff tools:')))
        hbox.addWidget(self.tcombo)
        hbox.addStretch(1)

        out = qtlib.LabeledSeparator(_('Command output'))
        self.layout().addWidget(out)
        self._cmdlog = cmdui.LogWidget(self)
        self.layout().addWidget(self._cmdlog)

        bbox = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bbox.rejected.connect(self.reject)
        self.layout().addWidget(bbox)
        self.bbox = bbox

        s = QSettings()
        self.restoreGeometry(qtlib.readByteArray(s, 'resolve/geom'))

        self.refresh()
        self.utree.selectAll()
        self.utree.setFocus()
        repoagent.configChanged.connect(self.tcombo.reset)
        repoagent.repositoryChanged.connect(self.refresh)

    @property
    def repo(self):
        return self._repoagent.rawRepo()

    def getSelectedPaths(self, tree: QTreeView) -> List[Tuple[bytes, bytes]]:
        paths = []
        if not tree.selectionModel():
            return paths
        model = tree.model()
        assert isinstance(model, PathsModel)  # help pytype
        for idx in tree.selectionModel().selectedRows():
            root, wfile = model.getPathForIndex(idx)
            paths.append((root, wfile))
        return paths

    def runCommand(self, tree: QTreeView, cmdline: List[str]) -> None:
        cmdlines = []
        selected = self.getSelectedPaths(tree)
        while selected:
            curroot = selected[0][0]
            cmd = cmdline + ['--repository', curroot, '--']
            for root, wfile in selected:
                if root == curroot:
                    cmd.append(os.path.normpath(os.path.join(root, wfile)))
            cmdlines.append(pycompat.maplist(hglib.tounicode, cmd))
            selected = [(r, w) for r, w in selected if r != curroot]
        if cmdlines:
            sess = self._repoagent.runCommandSequence(cmdlines, self)
            self._cmdsession = sess
            sess.commandFinished.connect(self.refresh)
            sess.outputReceived.connect(self._cmdlog.appendLog)
            self._updateActions()

    def merge(self, tool=''):
        if not tool:
            tool = self.tcombo.readValue()
            if tool:
                tool = encoding.strfromlocal(tool)
        cmd = ['resolve']
        if tool:
            cmd += ['--tool=' + tool]
        self.runCommand(self.utree, cmd)

    @pyqtSlot(QAction)
    def _mergeByAction(self, action):
        self.merge(action.data())

    def markresolved(self):
        self.runCommand(self.utree, ['resolve', '--mark'])

    def markunresolved(self):
        self.runCommand(self.rtree, ['resolve', '--unmark'])

    def edit(self):
        paths = self.getSelectedPaths(self.rtree)
        if paths:
            abspaths = [os.path.join(r, w) for r, w in paths]
            qtlib.editfiles(self.repo, abspaths, parent=self)

    def getVdiffFiles(self, tree: QTreeView) -> List[bytes]:
        paths = self.getSelectedPaths(tree)
        if not paths:
            return []
        files, sub = [], False
        for root, wfile in paths:
            if root == self.repo.root:
                files.append(wfile)
            else:
                sub = True
        if sub:
            qtlib.InfoMsgBox(_('Unable to show subrepository files'),
                    _('Visual diffs are not supported for files in '
                      'subrepositories. They will not be shown.'))
        return files

    def v3way(self):
        paths = self.getVdiffFiles(self.rtree)
        if paths:
            p1 = self.mergeState.localctx
            p2 = self.mergeState.otherctx

            dlg = visdiff.visual_diff(self.repo.ui, self.repo, paths, p1, p2,
                                      self.repo[None], self.tcombo.readValue())
            if dlg:
                dlg.exec()

    def vp0(self):
        paths = self.getVdiffFiles(self.rtree)
        if paths:
            p1 = self.mergeState.localctx
            dlg = visdiff.visual_diff(self.repo.ui, self.repo, paths, p1, None,
                                      self.repo[None], self.tcombo.readValue())
            if dlg:
                dlg.exec()

    def vp1(self):
        paths = self.getVdiffFiles(self.rtree)
        if paths:
            p2 = self.mergeState.otherctx
            dlg = visdiff.visual_diff(self.repo.ui, self.repo, paths, p2, None,
                                      self.repo[None], self.tcombo.readValue())
            if dlg:
                dlg.exec()

    def diffLocToAnc(self):
        paths = self.getVdiffFiles(self.utree)
        if paths:
            p1 = self.mergeState.localctx.hex()
            p2 = self.mergeState.otherctx.hex()
            revs = hglib.tounicode(b'ancestor(%s,%s)..%s' % (p1, p2, p1))
            opts = {'rev': [revs],
                    'tool': self.tcombo.readValue()}
            dlg = visdiff.visualdiff(self.repo.ui, self.repo, paths, opts)
            if dlg:
                dlg.exec()

    def diffOthToAnc(self):
        paths = self.getVdiffFiles(self.utree)
        if paths:
            p1 = self.mergeState.localctx.hex()
            p2 = self.mergeState.otherctx.hex()
            revs = hglib.tounicode(b'ancestor(%s,%s)..%s' % (p1, p2, p2))
            opts = {'rev': [revs],
                    'tool': self.tcombo.readValue()}
            dlg = visdiff.visualdiff(self.repo.ui, self.repo, paths, opts)
            if dlg:
                dlg.exec()

    @pyqtSlot()
    def refresh(self):
        u, r = [], []
        for root, path, status in thgrepo.recursiveMergeStatus(self.repo):
            if status == b'u':
                u.append((root, path))
            else:
                r.append((root, path))

        self.mergeState = hglib.readmergestate(self.repo)

        paths = self.getSelectedPaths(self.utree)
        oldmodel = self.utree.model()
        self.utree.setModel(PathsModel(u, self))
        self.utree.resizeColumnToContents(0)
        self.utree.resizeColumnToContents(1)
        if oldmodel:
            oldmodel.setParent(None)  # gc-ed

        model = self.utree.model()
        smodel = self.utree.selectionModel()
        assert model is not None
        sflags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
        for i, path in enumerate(u):
            if path in paths:
                smodel.select(model.index(i, 0), sflags)

        smodel.selectionChanged.connect(self._updateUnresolvedActions)
        self._updateUnresolvedActions()

        paths = self.getSelectedPaths(self.rtree)
        oldmodel = self.rtree.model()
        self.rtree.setModel(PathsModel(r, self))
        self.rtree.resizeColumnToContents(0)
        self.rtree.resizeColumnToContents(1)
        if oldmodel:
            oldmodel.setParent(None)  # gc-ed

        model = self.rtree.model()
        smodel = self.rtree.selectionModel()
        assert model is not None
        for i, path in enumerate(r):
            if path in paths:
                smodel.select(model.index(i, 0), sflags)

        smodel.selectionChanged.connect(self._updateResolvedActions)
        self._updateResolvedActions()

        if u:
            txt = _('There are merge <b>conflicts</b> to be resolved')
        elif r:
            txt = _('All conflicts are resolved.')
        else:
            txt = _('There are no conflicting file merges.')
        self.stlabel.setText('<h2>' + txt + '</h2>')

    def reject(self):
        s = QSettings()
        s.setValue('resolve/geom', self.saveGeometry())
        model = self.utree.model()
        assert model is not None
        if model.rowCount() > 0:
            main = _('Exit without finishing resolve?')
            text = _('Unresolved conflicts remain. Are you sure?')
            labels = ((QMessageBox.StandardButton.Yes, _('E&xit')),
                      (QMessageBox.StandardButton.No, _('Cancel')))
            if not qtlib.QuestionMsgBox(_('Confirm Exit'), main, text,
                                labels=labels, parent=self):
                return
        super().reject()

    def _updateActions(self):
        self._updateUnresolvedActions()
        self._updateResolvedActions()

    @pyqtSlot()
    def _updateUnresolvedActions(self):
        enable = (self.utree.selectionModel().hasSelection()
                  and self._cmdsession.isFinished())
        for c in self.umenuitems:
            c.setEnabled(enable)

    @pyqtSlot()
    def _updateResolvedActions(self):
        enable = (self.rtree.selectionModel().hasSelection()
                  and self._cmdsession.isFinished())
        for c in self.rmenuitems:
            c.setEnabled(enable)

        for c in self.rmmenuitems:
            c.setEnabled(enable and self.mergeState.active())

    @pyqtSlot(QPoint)
    def utreeMenuRequested(self, point: QPoint) -> None:
        self.utreecmenu.popup(self.utree.viewport().mapToGlobal(point))

    @pyqtSlot(QPoint)
    def rtreeMenuRequested(self, point: QPoint) -> None:
        self.rtreecmenu.popup(self.rtree.viewport().mapToGlobal(point))

    def utreeDoubleClicked(self):
        if self._repoagent.configBool('tortoisehg', 'autoresolve', True):
            self.merge()
        else:
            self.merge('internal:merge')


class PathsModel(QAbstractTableModel):

    rows: List[Tuple[bytes, bytes, bytes]]

    def __init__(self,
                 pathlist: List[Tuple[bytes, bytes]],
                 parent: Optional[QWidget]) -> None:
        QAbstractTableModel.__init__(self, parent)
        self.headers = (_('Path'), _('Ext'), _('Repository'))
        self.rows = []
        for root, path in pathlist:
            name, ext = os.path.splitext(path)
            self.rows.append((path, ext, root))

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0 # no child
        return len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0 # no child
        return len(self.headers)

    def data(self,
             index: QModelIndex,
             role: int = Qt.ItemDataRole.DisplayRole) -> Optional[str]:
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            data = self.rows[index.row()][index.column()]
            return hglib.tounicode(data)
        return None

    def flags(self, index: QModelIndex) -> Qt_ItemFlags:
        flags = super().flags(index)
        if not index.isValid():
            return flags
        flags |= Qt.ItemFlag.ItemIsDragEnabled
        return flags

    def headerData(self,
                   col: int,
                   orientation: int,
                   role: int = Qt.ItemDataRole.DisplayRole) -> Optional[str]:
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        else:
            return self.headers[col]

    def getPathForIndex(self, index: QModelIndex) -> Tuple[bytes, bytes]:
        'return root, wfile for the given row'
        row = index.row()
        return self.rows[row][2], self.rows[row][0]

    def mimeTypes(self) -> List[str]:
        return ['text/uri-list']

    def mimeData(self, indexes: List[QModelIndex]) -> QMimeData:
        paths = [hglib.tounicode(os.path.join(*self.getPathForIndex(i)))
                 for i in indexes if i.column() == 0]
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(p) for p in paths])
        return data


class ToolsCombo(QComboBox):
    def __init__(self, repo, parent):
        QComboBox.__init__(self, parent)
        self.setEditable(False)
        self.default = _('<default>')
        self.repo = repo
        self.reset()

    @pyqtSlot()
    def reset(self):
        self.clear()
        self.addItem(self.default)
        for t in self.repo.mergetools:
            self.addItem(hglib.tounicode(t))

    def readValue(self) -> Optional[bytes]:
        text = self.currentText()
        if text != self.default:
            return hglib.fromunicode(text)
