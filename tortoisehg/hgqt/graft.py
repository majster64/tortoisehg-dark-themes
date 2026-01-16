# graft.py - Graft dialog for TortoiseHg
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
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QMessageBox,
    QVBoxLayout,
)

from ..util import hglib
from ..util.i18n import _
from . import (
    cmdcore,
    cmdui,
    csinfo,
    cslist,
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

class GraftDialog(QDialog):

    def __init__(self,
                 repoagent: RepoAgent,
                 parent: Optional[QWidget],
                 **opts) -> None:
        super().__init__(parent)
        self.setWindowIcon(qtlib.geticon('hg-transplant'))
        self.setWindowFlags(self.windowFlags()
                            & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._repoagent = repoagent
        self._cmdsession = cmdcore.nullCmdSession()
        self.valid = True

        self.sourcelist = [
            hglib.revsymbol(self.repo, hglib.fromunicode(rev)).rev()
            for rev in opts.get('source', ['.'])]
        currgraftrevs = self.graftstate()
        if currgraftrevs:
            currgraftrevs = [hglib.revsymbol(self.repo, rev).rev()
                             for rev in currgraftrevs]
            if self.sourcelist != currgraftrevs:
                res = qtlib.CustomPrompt(_('Interrupted graft operation found'),
                    _('An interrupted graft operation has been found.\n\n'
                      'You cannot perform a different graft operation unless '
                      'you abort the interrupted graft operation first.'),
                    self,
                    (_('Continue or abort interrupted graft operation?'),
                     _('Cancel')), 1, 2).run()
                if res != 0:
                    # Cancel
                    self.valid = False
                    return
                # Continue creating the dialog, but use the graft source
                # of the existing, interrupted graft as the source, rather than
                # the one that was passed as an option to the dialog constructor
                self.sourcelist = currgraftrevs

        box = QVBoxLayout()
        box.setSpacing(8)
        box.setContentsMargins(*(6,)*4)
        self.setLayout(box)

        self.srcb = srcb = QGroupBox()
        srcb.setLayout(QVBoxLayout())
        srcb.layout().setContentsMargins(*(2,)*4)

        self.cslist = cslist.ChangesetList(self.repo)
        self._updateSource(0)
        srcb.layout().addWidget(self.cslist)
        box.addWidget(srcb)

        destrev = self.repo[b'.'].rev()
        style = csinfo.panelstyle(selectable=True)
        destb = QGroupBox(_('To graft destination'))
        destb.setLayout(QVBoxLayout())
        destb.layout().setContentsMargins(*(2,)*4)
        dest = csinfo.create(self.repo, destrev, style, withupdate=True)
        destb.layout().addWidget(dest)
        self.destcsinfo = dest
        box.addWidget(destb)

        sep = qtlib.LabeledSeparator(_('Options'))
        box.addWidget(sep)

        self._optchks = {}
        for name, text in [
                ('currentuser', _('Use my user name instead of graft '
                                  'committer user name')),
                ('currentdate', _('Use current date')),
                ('log', _('Append graft info to log message')),
                ('autoresolve', _('Automatically resolve merge conflicts '
                                  'where possible'))]:
            self._optchks[name] = w = QCheckBox(text)
            box.addWidget(w)

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
        self.graftbtn = bbox.addButton(_('Graft'), QDialogButtonBox.ButtonRole.ActionRole)
        self.graftbtn.clicked.connect(self.graft)
        self.abortbtn = bbox.addButton(_('Abort'), QDialogButtonBox.ButtonRole.ActionRole)
        self.abortbtn.clicked.connect(self.abort)
        box.addWidget(bbox)
        self.bbox = bbox

        self._wctxcleaner = wctxcleaner.WctxCleaner(repoagent, self)
        self._wctxcleaner.checkFinished.connect(self._onCheckFinished)
        if self.checkResolve():
            self.abortbtn.setEnabled(True)
        else:
            self._stbar.showMessage(_('Checking...'))
            self.abortbtn.setEnabled(False)
            self.graftbtn.setEnabled(False)
            QTimer.singleShot(0, self._wctxcleaner.check)

        self.setMinimumWidth(480)
        self.setMaximumHeight(800)
        self.resize(0, 340)
        self.setWindowTitle(_('Graft - %s') % repoagent.displayName())
        self._readSettings()

    @property
    def repo(self):
        return self._repoagent.rawRepo()

    def _readSettings(self) -> None:
        qs = QSettings()
        qs.beginGroup('graft')
        for n, w in self._optchks.items():
            if n == 'autoresolve':
                w.setChecked(
                    self._repoagent.configBool('tortoisehg', n,
                                               qtlib.readBool(qs, n, True)))
            else:
                w.setChecked(qtlib.readBool(qs, n))
        qs.endGroup()

    def _writeSettings(self) -> None:
        qs = QSettings()
        qs.beginGroup('graft')
        for n, w in self._optchks.items():
            qs.setValue(n, w.isChecked())
        qs.endGroup()

    def _updateSourceTitle(self, idx: int) -> None:
        numrevs = len(self.sourcelist)
        if numrevs <= 1:
            title = _('Graft changeset')
        else:
            title = _('Graft changeset #%d of %d') % (idx + 1, numrevs)
        self.srcb.setTitle(title)

    def _updateSource(self, idx: int) -> None:
        self._updateSourceTitle(idx)
        self.cslist.updateItems(self.sourcelist[idx:])

    @pyqtSlot(bool)
    def _onCheckFinished(self, clean: bool) -> None:
        if not clean:
            self.graftbtn.setEnabled(False)
            txt = _('Before graft, you must '
                    '<a href="commit"><b>commit</b></a>, '
                    '<a href="shelve"><b>shelve</b></a> to patch, '
                    'or <a href="discard"><b>discard</b></a> changes.')
        else:
            self.graftbtn.setEnabled(True)
            txt = _('You may continue or start the graft')
        self._stbar.showMessage(txt)

    def graft(self) -> None:
        self.graftbtn.setEnabled(False)
        self.cancelbtn.setVisible(False)
        opts = {n: w.isChecked() for n, w in self._optchks.items()}
        itool = 'merge' if opts.pop('autoresolve') else 'fail'
        opts['config'] = 'ui.merge=internal:%s' % itool
        if hglib.graft_in_progress(self.repo):
            opts['continue'] = True
            args = []
        else:
            args = [str(s) for s in self.sourcelist]
        cmdline = hglib.buildcmdargs('graft', *args, **opts)
        sess = self._runCommand(cmdline)
        sess.commandFinished.connect(self._graftFinished)

    def abort(self) -> None:
        self.abortbtn.setDisabled(True)
        cmdline = hglib.buildcmdargs('graft', abort=True)
        sess = self._runCommand(cmdline)
        sess.commandFinished.connect(self._abortFinished)

    def graftstate(self) -> Optional[List[bytes]]:
        return hglib.readgraftstate(self.repo)

    def _runCommand(self, cmdline: List[str]) -> cmdcore.CmdSession:
        assert self._cmdsession.isFinished()
        self._cmdsession = sess = self._repoagent.runCommand(cmdline, self)
        sess.commandFinished.connect(self._stbar.clearProgress)
        sess.outputReceived.connect(self._cmdlog.appendLog)
        sess.progressReceived.connect(self._stbar.setProgress)
        cmdui.updateStatusMessage(self._stbar, sess)
        return sess

    @pyqtSlot(int)
    def _graftFinished(self, ret: int) -> None:
        if self.checkResolve() is False:
            msg = _('Graft is complete')
            if ret == 255:
                msg = _('Graft failed')
                self._cmdlog.show()  # contains hint
            else:
                self._updateSource(len(self.sourcelist) - 1)
            self._stbar.showMessage(msg)
            self._makeCloseButton()

    @pyqtSlot()
    def _abortFinished(self) -> None:
        if self.checkResolve() is False:
            self._stbar.showMessage(_('Graft aborted'))
            self._makeCloseButton()

    def _makeCloseButton(self) -> None:
        self.graftbtn.setEnabled(True)
        self.graftbtn.setText(_('Close'))
        self.graftbtn.clicked.disconnect(self.graft)
        self.graftbtn.clicked.connect(self.accept)

    def checkResolve(self) -> bool:
        for root, path, status in thgrepo.recursiveMergeStatus(self.repo):
            if status == b'u':
                txt = _('Graft generated merge <b>conflicts</b> that must '
                        'be <a href="resolve"><b>resolved</b></a>')
                self.graftbtn.setEnabled(False)
                break
        else:
            self.graftbtn.setEnabled(True)
            txt = _('You may continue the graft')
        self._stbar.showMessage(txt)

        currgraftrevs = self.graftstate()
        if currgraftrevs:
            def findrev(rev, revlist):
                rev = self.repo[rev].rev()
                for n, r in enumerate(revlist):
                    r = self.repo[r].rev()
                    if rev == r:
                        return n
                return None
            idx = findrev(currgraftrevs[0], self.sourcelist)
            if idx is not None:
                self._updateSource(idx)
            self.abortbtn.setEnabled(True)
            self.graftbtn.setText('Continue')
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
        if self._wctxcleaner.isChecking():
            return
        if hglib.graft_in_progress(self.repo):
            main = _('Exiting with an unfinished graft is not recommended.')
            text = _('Consider aborting the graft first.')
            labels = ((QMessageBox.StandardButton.Yes, _('&Exit')),
                      (QMessageBox.StandardButton.No, _('Cancel')))
            if not qtlib.QuestionMsgBox(_('Confirm Exit'), main, text,
                                        labels=labels, parent=self):
                return
        super().reject()

    def done(self, r: int) -> None:
        self._writeSettings()
        super().done(r)
