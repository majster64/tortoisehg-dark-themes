# rebase.py - Rebase dialog for TortoiseHg
#
# Copyright 2010 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import typing

from .qtcore import (
    QSettings,
    QTimer,
    Qt,
    pyqtSlot,
)
from .qtgui import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QMessageBox,
    QRadioButton,
    QVBoxLayout,
)

from ..util import hglib
from ..util.i18n import _
from . import (
    cmdcore,
    cmdui,
    csinfo,
    qtlib,
    resolve,
    thgrepo,
    wctxcleaner,
)

if typing.TYPE_CHECKING:
    from typing import (
        List,
        Optional,
        Text,
    )
    from .qtgui import (
        QWidget,
    )
    from .thgrepo import (
        RepoAgent,
    )


BB = QDialogButtonBox

_SOURCE_SPECS = [
    # name, title, option label
    ('source', _('Rebase source and descendants'), '-s/--source'),
    ('base', _('Rebase entire source branch'), '-b/--base'),
    ('rev', _('Rebase exact revision'), '-r/--rev'),
]

class RebaseDialog(QDialog):

    def __init__(self,
                 repoagent: RepoAgent,
                 parent: Optional[QWidget],
                 **opts) -> None:
        super().__init__(parent)
        self.setWindowIcon(qtlib.geticon('hg-rebase'))
        self.setWindowFlags(self.windowFlags()
                            & ~Qt.WindowType.WindowContextHelpButtonHint)
        self._repoagent = repoagent
        self._cmdsession = cmdcore.nullCmdSession()
        repo = repoagent.rawRepo()
        self.opts = opts

        box = QVBoxLayout()
        box.setSpacing(8)
        box.setContentsMargins(*(6,)*4)
        self.setLayout(box)

        style = csinfo.panelstyle(selectable=True)

        self._sourcebox = srcb = QGroupBox(self)
        srcb.setLayout(QVBoxLayout())
        srcb.layout().setContentsMargins(*(2,)*4)
        s = opts.get('source', '.')
        source = csinfo.create(self.repo, s, style, withupdate=True)
        srcb.layout().addWidget(source)
        self.sourcecsinfo = source
        box.addWidget(srcb)

        destb = QGroupBox(_('To rebase destination'))
        destb.setLayout(QVBoxLayout())
        destb.layout().setContentsMargins(*(2,)*4)
        d = opts.get('dest', '.')
        dest = csinfo.create(self.repo, d, style, withupdate=True)
        destb.layout().addWidget(dest)
        self.destcsinfo = dest
        box.addWidget(destb)

        self.swaplabel = QLabel('<a href="X">%s</a>'  # don't care href
                                % _('Swap source and destination'))
        self.swaplabel.linkActivated.connect(self.swap)
        box.addWidget(self.swaplabel)

        sep = qtlib.LabeledSeparator(_('Options'))
        box.addWidget(sep)

        self._sourcegroup = QButtonGroup(self)
        self._sourcegroup.buttonClicked.connect(self._updateSourceSelector)
        for i, (name, title, oplabel) in enumerate(_SOURCE_SPECS):
            w = QRadioButton('%s (%s)' % (title, oplabel), self)
            w.setChecked(name == 'source')
            self._sourcegroup.addButton(w, i)
            box.addWidget(w)

        self.keepchk = QCheckBox(_('Keep original changesets (--keep)'))
        self.keepchk.setChecked(opts.get('keep', False))
        box.addWidget(self.keepchk)

        self.keepbrancheschk = QCheckBox(_('Keep original branch names '
                                           '(--keepbranches)'))
        self.keepbrancheschk.setChecked(opts.get('keepbranches', False))
        box.addWidget(self.keepbrancheschk)

        self.collapsechk = QCheckBox(_('Collapse the rebased changesets '
                                       '(--collapse)'))
        self.collapsechk.setChecked(opts.get('collapse', False))
        box.addWidget(self.collapsechk)

        self.autoresolvechk = QCheckBox(_('Automatically resolve merge '
                                          'conflicts where possible'))
        box.addWidget(self.autoresolvechk)

        self.svnchk = QCheckBox(_('Rebase unpublished onto Subversion head '
                                  '(override source, destination)'))
        self.svnchk.setVisible(b'hgsubversion' in repo.extensions())
        box.addWidget(self.svnchk)

        self._cmdlog = cmdui.LogWidget(self)
        self._cmdlog.hide()
        box.addWidget(self._cmdlog, 2)
        self._stbar = cmdui.ThgStatusBar(self)
        self._stbar.setSizeGripEnabled(False)
        self._stbar.linkActivated.connect(self.linkActivated)
        box.addWidget(self._stbar)

        bbox = QDialogButtonBox()
        self.cancelbtn = bbox.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.cancelbtn.clicked.connect(self.reject)
        self.rebasebtn = bbox.addButton(_('Rebase'),
                                        QDialogButtonBox.ButtonRole.ActionRole)
        self.rebasebtn.clicked.connect(self.rebase)
        self.abortbtn = bbox.addButton(_('Abort'),
                                       QDialogButtonBox.ButtonRole.ActionRole)
        self.abortbtn.clicked.connect(self.abort)
        box.addWidget(bbox)
        self.bbox = bbox

        self._wctxcleaner = wctxcleaner.WctxCleaner(repoagent, self)
        self._wctxcleaner.checkFinished.connect(self._onCheckFinished)
        if self.checkResolve() or not (s or d):
            for w in (srcb, destb, sep, self.keepchk,
                      self.collapsechk, self.keepbrancheschk):
                w.setHidden(True)
            self._cmdlog.show()
        else:
            self._stbar.showMessage(_('Checking...'))
            self.abortbtn.setEnabled(False)
            self.rebasebtn.setEnabled(False)
            QTimer.singleShot(0, self._wctxcleaner.check)

        self.setMinimumWidth(480)
        self.setMaximumHeight(800)
        self.resize(0, 340)
        self.setWindowTitle(_('Rebase - %s') % repoagent.displayName())
        self._updateSourceSelector()
        self._readSettings()

    @property
    def repo(self):
        return self._repoagent.rawRepo()

    def _readSettings(self) -> None:
        qs = QSettings()
        qs.beginGroup('rebase')
        self.autoresolvechk.setChecked(
            self._repoagent.configBool('tortoisehg', 'autoresolve',
                                       qtlib.readBool(qs, 'autoresolve', True)))
        qs.endGroup()

    def _writeSettings(self) -> None:
        qs = QSettings()
        qs.beginGroup('rebase')
        qs.setValue('autoresolve', self.autoresolvechk.isChecked())
        qs.endGroup()

    @pyqtSlot(bool)
    def _onCheckFinished(self, clean: bool) -> None:
        if not clean:
            self.rebasebtn.setEnabled(False)
            txt = _('Before rebase, you must '
                    '<a href="commit"><b>commit</b></a>, '
                    '<a href="shelve"><b>shelve</b></a> to patch, '
                    'or <a href="discard"><b>discard</b></a> changes.')
        else:
            self.rebasebtn.setEnabled(True)
            txt = _('You may continue the rebase')
        self._stbar.showMessage(txt)

    @pyqtSlot()
    def _updateSourceSelector(self) -> None:
        _name, title, _oplabel = _SOURCE_SPECS[self._sourcegroup.checkedId()]
        self._sourcebox.setTitle(title)

    def rebase(self) -> None:
        self.rebasebtn.setEnabled(False)
        self.cancelbtn.setVisible(False)
        self.keepchk.setEnabled(False)
        self.keepbrancheschk.setEnabled(False)
        for w in self._sourcegroup.buttons():
            w.setEnabled(False)
        self.collapsechk.setEnabled(False)
        self.swaplabel.setVisible(False)

        itool = 'merge' if self.autoresolvechk.isChecked() else 'fail'
        opts = {'config': 'ui.merge=internal:%s' % itool}
        if hglib.rebase_in_progress(self.repo):
            opts['continue'] = True
        else:
            opts.update({
                'keep': self.keepchk.isChecked(),
                'keepbranches': self.keepbrancheschk.isChecked(),
                'collapse': self.collapsechk.isChecked(),
                })
            if self.svnchk.isChecked():
                opts['svn'] = True
            else:
                sourcearg = _SOURCE_SPECS[self._sourcegroup.checkedId()][0]
                opts[sourcearg] = str(self.opts.get('source'))
                opts['dest'] = str(self.opts.get('dest'))
        cmdline = hglib.buildcmdargs('rebase', **opts)
        sess = self._runCommand(cmdline)
        sess.commandFinished.connect(self._rebaseFinished)

    def swap(self) -> None:
        oldsource = self.opts.get('source', '.')
        olddest = self.opts.get('dest', '.')

        self.sourcecsinfo.updateItems(target=olddest)
        self.destcsinfo.updateItems(target=oldsource)

        self.opts['source'] = olddest
        self.opts['dest'] = oldsource

    def abort(self) -> None:
        cmdline = hglib.buildcmdargs('rebase', abort=True)
        sess = self._runCommand(cmdline)
        sess.commandFinished.connect(self._abortFinished)

    def _runCommand(self, cmdline: List[str]) -> cmdcore.CmdSession:
        assert self._cmdsession.isFinished()
        self._cmdsession = sess = self._repoagent.runCommand(cmdline, self)
        sess.commandFinished.connect(self._stbar.clearProgress)
        sess.outputReceived.connect(self._cmdlog.appendLog)
        sess.progressReceived.connect(self._stbar.setProgress)
        cmdui.updateStatusMessage(self._stbar, sess)
        return sess

    @pyqtSlot(int)
    def _rebaseFinished(self, ret: int) -> None:
        # TODO since hg 2.6, rebase will end with ret=1 in case of "unresolved
        # conflicts", so we can fine-tune checkResolve() later.
        if self.checkResolve() is False:
            msg = _('Rebase is complete')
            if ret == 255:
                msg = _('Rebase failed')
                self._cmdlog.show()  # contains hint
            self._stbar.showMessage(msg)
            self._makeCloseButton()

    @pyqtSlot()
    def _abortFinished(self) -> None:
        if self.checkResolve() is False:
            self._stbar.showMessage(_('Rebase aborted'))
            self._makeCloseButton()

    def _makeCloseButton(self) -> None:
        self.rebasebtn.setEnabled(True)
        self.rebasebtn.setText(_('Close'))
        self.rebasebtn.clicked.disconnect(self.rebase)
        self.rebasebtn.clicked.connect(self.accept)

    def checkResolve(self) -> bool:
        for root, path, status in thgrepo.recursiveMergeStatus(self.repo):
            if status == b'u':
                txt = _('Rebase generated merge <b>conflicts</b> that must '
                        'be <a href="resolve"><b>resolved</b></a>')
                self.rebasebtn.setEnabled(False)
                break
        else:
            self.rebasebtn.setEnabled(True)
            txt = _('You may continue the rebase')
        self._stbar.showMessage(txt)

        if hglib.rebase_in_progress(self.repo):
            self.swaplabel.setVisible(False)
            self.abortbtn.setEnabled(True)
            self.rebasebtn.setText('Continue')
            return True
        else:
            self.abortbtn.setEnabled(False)
            return False

    def linkActivated(self, cmd: str) -> None:
        if cmd == 'resolve':
            dlg = resolve.ResolveDialog(self._repoagent, self)
            dlg.exec()
            self.checkResolve()
        else:
            self._wctxcleaner.runCleaner(cmd)

    def reject(self) -> None:
        if hglib.rebase_in_progress(self.repo):
            main = _('Exiting with an unfinished rebase is not recommended.')
            text = _('Consider aborting the rebase first.')
            labels = ((QMessageBox.StandardButton.Yes, _('&Exit')),
                      (QMessageBox.StandardButton.No, _('Cancel')))
            if not qtlib.QuestionMsgBox(_('Confirm Exit'), main, text,
                                        labels=labels, parent=self):
                return
        super().reject()

    def done(self, r: int) -> None:
        self._writeSettings()
        super().done(r)
