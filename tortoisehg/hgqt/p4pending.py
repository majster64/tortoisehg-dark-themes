# p4pending.py - Display pending p4 changelists, created by perfarce extension
#
# Copyright 2010 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

from .qtcore import (
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from .qtgui import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QVBoxLayout,
)

from mercurial import error

from ..util import hglib
from ..util.i18n import _
from . import (
    cmdcore,
    cmdui,
    cslist,
)

class PerforcePending(QDialog):
    'Dialog for selecting a revision'

    showMessage = pyqtSignal(str)

    def __init__(self, repoagent, pending, url, parent):
        QDialog.__init__(self, parent)
        self._repoagent = repoagent
        repo = repoagent.rawRepo()
        self._cmdsession = cmdcore.nullCmdSession()
        self.url = url
        self.pending = pending # dict of changelist -> hash tuple

        layout = QVBoxLayout()
        self.setLayout(layout)

        self._clcombo = QComboBox()
        layout.addWidget(self._clcombo)

        self.cslist = cslist.ChangesetList(repo)
        layout.addWidget(self.cslist)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.Discard)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.StandardButton.Discard).setText('Revert')
        bb.button(QDialogButtonBox.StandardButton.Discard).setAutoDefault(False)
        bb.button(QDialogButtonBox.StandardButton.Discard).clicked.connect(self.revert)
        bb.button(QDialogButtonBox.StandardButton.Discard).setEnabled(False)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText('Submit')
        bb.button(QDialogButtonBox.StandardButton.Ok).setAutoDefault(True)
        bb.button(QDialogButtonBox.StandardButton.Ok).clicked.connect(self.submit)
        bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        layout.addWidget(bb)
        self.bb = bb

        self._clcombo.activated[int].connect(self.p4clActivated)
        for changelist in self.pending:
            self._clcombo.addItem(hglib.tounicode(changelist))
        self.p4clActivated(self._clcombo.currentIndex())

        self.setWindowTitle(_('Pending Perforce Changelists - %s') %
                            repoagent.displayName())
        self.setWindowFlags(self.windowFlags() &
                            ~Qt.WindowType.WindowContextHelpButtonHint)

    @pyqtSlot(int)
    def p4clActivated(self, curcl_idx):
        'User has selected a changelist, fill cslist'
        curcl = self._clcombo.itemText(curcl_idx)
        repo = self._repoagent.rawRepo()
        curcl = hglib.fromunicode(curcl)
        try:
            hashes = self.pending[curcl]
            revs = [repo[hash] for hash in hashes]
        except (error.Abort, error.RepoLookupError) as e:
            revs = []
        self.cslist.clear()
        self.cslist.updateItems(revs)
        sensitive = not curcl.endswith(b'(submitted)')
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(sensitive)
        self.bb.button(QDialogButtonBox.StandardButton.Discard).setEnabled(sensitive)
        self.curcl = curcl

    def submit(self):
        assert(self.curcl.endswith(b'(pending)'))
        cmdline = ['p4submit', '--verbose',
                   '--config', 'extensions.perfarce=',
                   '--repository', hglib.tounicode(self.url),
                   hglib.tounicode(self.curcl[:-10])]
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.bb.button(QDialogButtonBox.StandardButton.Discard).setEnabled(False)
        self.showMessage.emit(_('Submitting p4 changelist...'))
        self._cmdsession = sess = self._repoagent.runCommand(cmdline, self)
        sess.commandFinished.connect(self.commandFinished)

    def revert(self):
        assert(self.curcl.endswith(b'(pending)'))
        cmdline = ['p4revert', '--verbose',
                   '--config', 'extensions.perfarce=',
                   '--repository', hglib.tounicode(self.url),
                   hglib.tounicode(self.curcl[:-10])]
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.bb.button(QDialogButtonBox.StandardButton.Discard).setEnabled(False)
        self.showMessage.emit(_('Reverting p4 changelist...'))
        self._cmdsession = sess = self._repoagent.runCommand(cmdline, self)
        sess.commandFinished.connect(self.commandFinished)

    @pyqtSlot(int)
    def commandFinished(self, ret):
        self.showMessage.emit('')
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        self.bb.button(QDialogButtonBox.StandardButton.Discard).setEnabled(True)
        if ret == 0:
            self.reject()
        else:
            cmdui.errorMessageBox(self._cmdsession, self)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if not self._cmdsession.isFinished():
                self._cmdsession.abort()
            else:
                self.reject()
        else:
            return super().keyPressEvent(event)
