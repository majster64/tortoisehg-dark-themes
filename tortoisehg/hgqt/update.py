# update.py - Update dialog for TortoiseHg
#
# Copyright 2007 TK Soh <teekaysoh@gmail.com>
# Copyright 2007 Steve Borho <steve@borho.org>
# Copyright 2010 Yuki KODAMA <endflow.net@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import typing

from .qtcore import (
    pyqtSlot,
)
from .qtgui import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QVBoxLayout,
)

from mercurial import (
    error,
    pycompat,
)

from ..util import hglib
from ..util.i18n import _
from . import (
    cmdcore,
    cmdui,
    csinfo,
    qtlib,
    resolve,
)

if typing.TYPE_CHECKING:
    from typing import (
        Any,
        Dict,
        Optional,
        Text,
        Union,
    )
    from mercurial import context
    from .qtcore import QSettings
    from .qtgui import QWidget
    from .thgrepo import RepoAgent

    UpdateOpts = Dict[str, Any]


class UpdateWidget(cmdui.AbstractCmdWidget):

    def __init__(self,
                 repoagent: RepoAgent,
                 rev: Optional[Union[str, pycompat.unicode]] = None,
                 parent: Optional[QWidget] = None,
                 opts: Optional[UpdateOpts] = None) -> None:
        # TODO: unify the `rev` type
        super().__init__(parent)
        if opts is None:
            opts = {}
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._repoagent = repoagent
        repo = repoagent.rawRepo()

        ## main layout
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        self.setLayout(form)

        ### target revision combo
        self.rev_combo = combo = QComboBox()
        combo.setEditable(True)
        combo.setMinimumContentsLength(30)  # cut long name
        combo.installEventFilter(qtlib.BadCompletionBlocker(combo))
        form.addRow(_('Update to:'), combo)

        # always include integer revision
        if rev:
            assert isinstance(rev, (pycompat.unicode, str)), repr(rev)
            try:
                ctx = hglib.revsymbol(self.repo, hglib.fromunicode(rev))
                if isinstance(ctx.rev(), int):  # could be patch name
                    combo.addItem(str(ctx.rev()))
            except error.RepoLookupError:
                pass

        combo.addItems(pycompat.maplist(hglib.tounicode,
                                        hglib.namedbranches(repo)))
        tags = list(self.repo.tags()) + list(repo._bookmarks.keys())
        tags.sort(reverse=True)
        combo.addItems(pycompat.maplist(hglib.tounicode, tags))

        if rev is None:
            selecturev = hglib.tounicode(self.repo.dirstate.branch())
        else:
            selecturev = hglib.tounicode(rev)
        selectindex = combo.findText(selecturev)
        if selectindex >= 0:
            combo.setCurrentIndex(selectindex)
        else:
            combo.setEditText(selecturev)

        ### target revision info
        items = ('%(rev)s', ' %(branch)s', ' %(tags)s', '<br />%(summary)s')
        style = csinfo.labelstyle(contents=items, width=350, selectable=True)
        factory = csinfo.factory(self.repo, style=style)
        self.target_info = factory()
        form.addRow(_('Target:'), self.target_info)

        ### parent revision info
        self.ctxs = self.repo[None].parents()
        if len(self.ctxs) == 2:
            self.p1_info = factory()
            form.addRow(_('Parent 1:'), self.p1_info)
            self.p2_info = factory()
            form.addRow(_('Parent 2:'), self.p2_info)
        else:
            self.p1_info = factory()
            form.addRow(_('Parent:'), self.p1_info)

        # show a subrepo "pull path" combo, with the
        # default path as the first (and default) path
        self.path_combo_label = QLabel(_('Pull subrepos from:'))
        self.path_combo = QComboBox(self)
        syncpaths = dict(repoagent.configStringItems('paths'))
        aliases = sorted(syncpaths)
        # make sure that the default path is the first one
        if 'default' in aliases:
            aliases.remove('default')
            aliases.insert(0, 'default')
        for n, alias in enumerate(aliases):
            self.path_combo.addItem(alias)
            self.path_combo.setItemData(n, syncpaths[alias])
        self.path_combo.currentIndexChanged.connect(
            self._updatePathComboTooltip)
        self._updatePathComboTooltip(0)
        form.addRow(self.path_combo_label, self.path_combo)

        ### options
        self.optbox = QVBoxLayout()
        self.optbox.setSpacing(6)
        self.optexpander = expander = qtlib.ExpanderLabel(_('Options:'), False)
        expander.expanded.connect(self.show_options)
        form.addRow(expander, self.optbox)

        self.verbose_chk = QCheckBox(_('List updated files (--verbose)'))
        self.discard_chk = QCheckBox(_('Discard local changes, no backup '
                                       '(-C/--clean)'))
        self.merge_chk = QCheckBox(_('Always merge (when possible)'))
        self.autoresolve_chk = QCheckBox(_('Automatically resolve merge '
                                           'conflicts where possible'))
        self.optbox.addWidget(self.verbose_chk)
        self.optbox.addWidget(self.discard_chk)
        self.optbox.addWidget(self.merge_chk)
        self.optbox.addWidget(self.autoresolve_chk)

        self.discard_chk.setChecked(bool(opts.get('clean')))

        # signal handlers
        self.rev_combo.editTextChanged.connect(self.update_info)
        self.discard_chk.toggled.connect(self.update_info)

        # prepare to show
        self.merge_chk.setHidden(True)
        self.autoresolve_chk.setHidden(True)
        self.update_info()
        if not self.canRunCommand():
            # need to change rev
            self.rev_combo.lineEdit().selectAll()

    def readSettings(self, qs: QSettings) -> None:
        self.merge_chk.setChecked(qtlib.readBool(qs, 'merge'))
        self.autoresolve_chk.setChecked(
            self._repoagent.configBool('tortoisehg', 'autoresolve',
                                       qtlib.readBool(qs, 'autoresolve', True)))
        self.verbose_chk.setChecked(qtlib.readBool(qs, 'verbose'))

        # expand options if a hidden one is checked
        self.optexpander.set_expanded(self.hiddenSettingIsChecked())

    def writeSettings(self, qs: QSettings) -> None:
        qs.setValue('merge', self.merge_chk.isChecked())
        qs.setValue('autoresolve', self.autoresolve_chk.isChecked())
        qs.setValue('verbose', self.verbose_chk.isChecked())

    @property
    def repo(self):
        return self._repoagent.rawRepo()

    def hiddenSettingIsChecked(self) -> bool:
        return (self.merge_chk.isChecked()
                or self.autoresolve_chk.isChecked())

    @pyqtSlot()
    def update_info(self) -> None:
        self.p1_info.updateItems(self.ctxs[0].node())
        merge = len(self.ctxs) == 2
        if merge:
            self.p2_info.updateItems(self.ctxs[1].node())
        new_rev = hglib.fromunicode(self.rev_combo.currentText())
        if new_rev == b'null':
            self.target_info.setText(_('remove working directory'))
            self.commandChanged.emit()
            return
        try:
            new_ctx = hglib.revsymbol(self.repo, new_rev)

            if not merge and new_ctx.rev() == self.ctxs[0].rev() \
                    and not new_ctx.bookmarks():
                self.target_info.setText(_('(same as parent)'))
            else:
                self.target_info.updateItems(new_ctx)
            # only show the path combo when there are multiple paths
            # and the target revision has subrepos
            showpathcombo = self.path_combo.count() > 1 and \
                b'.hgsubstate' in new_ctx
            self.path_combo_label.setVisible(showpathcombo)
            self.path_combo.setVisible(showpathcombo)
        except (error.LookupError, error.RepoError, OSError):
            self.target_info.setText(_('unknown revision!'))
        self.commandChanged.emit()

    def canRunCommand(self) -> bool:
        new_rev = hglib.fromunicode(self.rev_combo.currentText())
        try:
            new_ctx = hglib.revsymbol(self.repo, new_rev)
        except (error.LookupError, error.RepoError, OSError):
            return False

        return (self.discard_chk.isChecked()
                or len(self.ctxs) == 2
                or new_ctx.rev() != self.ctxs[0].rev()
                or bool(new_ctx.bookmarks()))

    def runCommand(self) -> cmdcore.CmdSession:
        cmdline = ['update']
        if self.verbose_chk.isChecked():
            cmdline += ['--verbose']
        cmdline += ['--config', 'ui.merge=internal:' +
                    (self.autoresolve_chk.isChecked() and 'merge' or 'fail')]
        rev: str = self.rev_combo.currentText()

        activatebookmarkmode = self._repoagent.configString(
            'tortoisehg', 'activatebookmarks')
        if activatebookmarkmode != 'never':
            brev: bytes = hglib.fromunicode(rev)
            bookmarks = hglib.revsymbol(self.repo, brev).bookmarks()

            if bookmarks and brev not in bookmarks:
                # The revision that we are updating into has bookmarks,
                # but the user did not refer to the revision by one of them
                # (probably used a revision number or hash)
                # Ask the user if it wants to update to one of these bookmarks
                # instead
                selectedbookmark = None
                if len(bookmarks) == 1:
                    if activatebookmarkmode == 'auto':
                        activatebookmark = True
                    else:
                        activatebookmark = qtlib.QuestionMsgBox(
                            _('Activate bookmark?'),
                            _('The selected revision (%s) has a bookmark on it '
                              'called "<i>%s</i>".<p>Do you want to activate '
                              'it?<br></b>'
                              '<i>You can disable this prompt by configuring '
                              'Settings/Workbench/Activate Bookmarks</i>') \
                            % (rev, hglib.tounicode(bookmarks[0])))
                    if activatebookmark:
                        selectedbookmark = hglib.tounicode(bookmarks[0])
                else:
                    # Even in auto mode, when there is more than one bookmark
                    # we must ask the user which one must be activated
                    selectedbookmark = qtlib.ChoicePrompt(
                        _('Activate bookmark?'),
                        _('The selected revision (<i>%s</i>) has <i>%d</i> '
                          'bookmarks on it.<p>Select the bookmark that you '
                          'want to activate and click <i>OK</i>.'
                          "<p>Click <i>Cancel</i> if you don't want to "
                          'activate any of them.<p><p>'
                          '<i>You can disable this prompt by configuring '
                          'Settings/Workbench/Activate Bookmarks</i><p>') \
                        % (rev, len(bookmarks)),
                        self, [hglib.tounicode(b) for b in bookmarks],
                        hglib.tounicode(hglib.activebookmark(self.repo))).run()
                if selectedbookmark:
                    rev = selectedbookmark
                else:
                    activebookmark = hglib.activebookmark(self.repo)
                    if (activebookmark and hglib.revsymbol(self.repo, brev)
                        == hglib.revsymbol(self.repo, activebookmark)):
                        deactivatebookmark = qtlib.QuestionMsgBox(
                            _('Deactivate current bookmark?'),
                            _('Do you really want to deactivate the <i>%s</i> '
                              'bookmark?')
                            % hglib.tounicode(activebookmark))
                        if deactivatebookmark:
                            cmdline = ['bookmark']
                            if self.verbose_chk.isChecked():
                                cmdline += ['--verbose']
                            cmdline += ['-i',
                                        hglib.tounicode(activebookmark)]
                            return self._repoagent.runCommand(cmdline, self)
                        return cmdcore.nullCmdSession()

        cmdline.append('--rev')
        cmdline.append(rev)

        pullpathname = hglib.fromunicode(
            self.path_combo.currentText())
        if pullpathname and pullpathname != b'default':
            # We must tell mercurial to pull any missing repository
            # revisions from the selected path. The only way to do so is
            # to temporarily set the default path to the selected path URL
            pullpath = self.path_combo.itemData(self.path_combo.currentIndex())
            cmdline.append('--config')
            cmdline.append('paths.default=%s' % pullpath)

        if self.discard_chk.isChecked():
            cmdline.append('--clean')
        else:
            cur: context.changectx = self.repo.hgchangectx(b'.')
            try:
                node = self.repo.hgchangectx(
                    hglib.revsymbol(self.repo, hglib.fromunicode(rev)).rev())
            except (error.LookupError, error.RepoError, OSError):
                return cmdcore.nullCmdSession()
            def isclean() -> bool:
                '''whether WD is changed'''
                try:
                    wc = self.repo[None]
                    if wc.modified() or wc.added() or wc.removed():
                        return False
                    for s in wc.substate:
                        if wc.sub(s).dirty():
                            return False
                except OSError:
                    return False
                return True
            def ismergedchange() -> bool:
                '''whether the local changes are merged (have 2 parents)'''
                wc = self.repo[None]
                return len(wc.parents()) == 2
            def islocalmerge(p1: context.changectx,
                             p2: context.changectx,
                             clean: Optional[bool] = None) -> bool:
                if clean is None:
                    clean = isclean()
                pa = p1.ancestor(p2)
                return not clean \
                    and (p1.rev() == pa.rev() or p2.rev() == pa.rev())
            def confirmupdate() -> Optional[str]:
                msg = _('Detected uncommitted local changes in working tree.\n'
                        'Please select to continue:\n')
                data = {'discard': (_('&Discard'),
                                    _('Discard - discard local changes, no '
                                      'backup')),
                        'shelve': (_('&Shelve'),
                                  _('Shelve - move local changes to a patch')),
                        'merge': (_('&Merge'),
                                  _('Merge - allow to merge with local '
                                    'changes'))}

                opts = ['discard']
                if not ismergedchange():
                    opts.append('shelve')

                opts.append('merge')

                dlg = QMessageBox(QMessageBox.Icon.Question, _('Confirm Update'),
                                  '', QMessageBox.StandardButton.Cancel, self)
                buttonnames = {}
                for name in opts:
                    label, desc = data[name]
                    msg += '\n'
                    msg += desc
                    btn = dlg.addButton(label, QMessageBox.ButtonRole.ActionRole)
                    buttonnames[btn] = name
                dlg.setDefaultButton(QMessageBox.StandardButton.Cancel)
                dlg.setText(msg)
                dlg.exec()
                return buttonnames.get(dlg.clickedButton())

            # If merge-by-default, we want to merge whenever possible,
            # without prompting user (similar to command-line behavior)
            defaultmerge = self.merge_chk.isChecked()
            clean = isclean()
            if clean:
                cmdline.append('--check')
            elif not defaultmerge:
                clicked = confirmupdate()
                if clicked == 'discard':
                    cmdline.append('--clean')
                elif clicked == 'shelve':
                    from tortoisehg.hgqt import shelve
                    dlg = shelve.ShelveDialog(self._repoagent, self)
                    dlg.finished.connect(dlg.deleteLater)
                    dlg.exec()
                    return cmdcore.nullCmdSession()
                elif clicked == 'merge':
                    cmdline.append('--merge')
                else:
                    return cmdcore.nullCmdSession()
            elif not islocalmerge(cur, node, clean):
                cmdline.append('--merge')

        return self._repoagent.runCommand(cmdline, self)

    @pyqtSlot(bool)
    def show_options(self, visible: bool) -> None:
        self.merge_chk.setVisible(visible)
        self.autoresolve_chk.setVisible(visible)

    @pyqtSlot(int)
    def _updatePathComboTooltip(self, idx: int) -> None:
        self.path_combo.setToolTip(self.path_combo.itemData(idx))


class UpdateDialog(cmdui.CmdControlDialog):

    def __init__(self,
                 repoagent: RepoAgent,
                 rev: Optional[Union[str, pycompat.unicode]] = None,
                 parent: Optional[QWidget] = None,
                 opts: Optional[UpdateOpts] = None) -> None:
        super().__init__(parent)
        if opts is None:
            opts = {}
        self._repoagent = repoagent

        self.setWindowTitle(_('Update - %s') % repoagent.displayName())
        self.setWindowIcon(qtlib.geticon('hg-update'))
        self.setObjectName('update')
        self.setRunButtonText(_('&Update'))
        self.setCommandWidget(UpdateWidget(repoagent, rev, self, opts))
        self.commandFinished.connect(self._checkMergeConflicts)

    @pyqtSlot(int)
    def _checkMergeConflicts(self, ret: int) -> None:
        if ret != 1:
            return
        qtlib.InfoMsgBox(_('Merge caused file conflicts'),
                         _('File conflicts need to be resolved'))
        dlg = resolve.ResolveDialog(self._repoagent, self)
        dlg.exec()
        if not self.isLogVisible():
            self.reject()
