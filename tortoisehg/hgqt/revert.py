# revert.py - File revert dialog for TortoiseHg
#
# Copyright 2010 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

from .qtcore import (
    Qt,
    pyqtSlot,
)
from .qtgui import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)

from mercurial.node import nullid

from ..util import hglib
from ..util.i18n import _
from . import (
    cmdcore,
    cmdui,
    qtlib,
)

class RevertDialog(QDialog):
    def __init__(self, repoagent, wfiles, rev, parent):
        super().__init__(parent)

        self._repoagent = repoagent
        self._cmdsession = cmdcore.nullCmdSession()
        self.setWindowTitle(_('Revert - %s') % repoagent.displayName())

        f = self.windowFlags()
        self.setWindowFlags(f & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.wfiles = wfiles

        self.setLayout(QVBoxLayout())

        if len(wfiles) == 1:
            lblText = _('<b>Revert %s to its contents'
                        ' at the following revision?</b>') % (
                      wfiles[0])
        else:
            lblText = _('<b>Revert %d files to their contents'
                        ' at the following revision?</b>') % (
                      len(wfiles))
        lbl = QLabel(lblText)
        self.layout().addWidget(lbl)

        self._addRevertTargetCombo(rev)

        self.allchk = QCheckBox(_('Revert all files to this revision'))
        self.layout().addWidget(self.allchk)

        bbox = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        self.layout().addWidget(bbox)
        self.bbox = bbox

    def _addRevertTargetCombo(self, rev):
        if rev is None:
            raise ValueError('Cannot revert to working directory')
        self.revcombo = QComboBox()
        revnames = ['revision %d' % rev]
        repo = self._repoagent.rawRepo()
        ctx = repo[rev]
        parents = ctx.parents()[:2]
        if len(parents) == 1:
            parentdesctemplate = ("revision %d's parent (i.e. revision %d)",)
        else:
            parentdesctemplate = (
                _("revision %d's first parent (i.e. revision %d)"),
                _("revision %d's second parent (i.e. revision %d)"),
            )
        for n, pctx in enumerate(parents):
            if pctx.node() == nullid:
                revdesc = _('null revision (i.e. remove file(s))')
            else:
                revdesc = parentdesctemplate[n] % (rev, pctx.rev())
            revnames.append(revdesc)
        self.revcombo.addItems(revnames)
        reverttargets = [ctx] + parents
        for n, ctx in enumerate(reverttargets):
            self.revcombo.setItemData(n, hglib.tounicode(ctx.hex()))
        self.layout().addWidget(self.revcombo)

    def accept(self):
        rev = self.revcombo.itemData(self.revcombo.currentIndex())
        if self.allchk.isChecked():
            if not qtlib.QuestionMsgBox(_('Confirm Revert'),
                     _('Reverting all files will discard changes and '
                       'leave affected files in a modified state.<br>'
                       '<br>Are you sure you want to use revert?<br><br>'
                       '(use update to checkout another revision)'),
                       parent=self):
                return
            cmdline = hglib.buildcmdargs('revert', all=True, rev=rev)
        else:
            cmdline = hglib.buildcmdargs('revert', rev=rev, *self.wfiles)
        self.bbox.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self._cmdsession = sess = self._repoagent.runCommand(cmdline, self)
        sess.commandFinished.connect(self._onCommandFinished)

    @pyqtSlot(int)
    def _onCommandFinished(self, ret):
        if ret == 0:
            self.reject()
        else:
            cmdui.errorMessageBox(self._cmdsession, self)
