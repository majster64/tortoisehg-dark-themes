# compress.py - History compression dialog for TortoiseHg
#
# Copyright 2010 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

from .qtcore import (
    QSettings,
    QTimer,
    Qt,
    pyqtSlot,
)
from .qtgui import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLayout,
    QSizePolicy,
    QVBoxLayout,
)

from ..util.i18n import _
from . import (
    cmdui,
    commit,
    csinfo,
    qtlib,
    wctxcleaner,
)

class CompressDialog(QDialog):

    def __init__(self, repoagent, revs, parent):
        super().__init__(parent)
        f = self.windowFlags()
        self.setWindowFlags(f & ~Qt.WindowType.WindowContextHelpButtonHint)
        self._repoagent = repoagent
        self.revs = revs

        box = QVBoxLayout()
        box.setSpacing(8)
        box.setContentsMargins(*(6,)*4)
        box.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self.setLayout(box)

        style = csinfo.panelstyle(selectable=True)

        srcb = QGroupBox(_('Compress changesets up to and including'))
        srcb.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        srcb.setLayout(QVBoxLayout())
        srcb.layout().setContentsMargins(*(2,)*4)
        source = csinfo.create(self.repo, revs[0], style, withupdate=True)
        srcb.layout().addWidget(source)
        self.layout().addWidget(srcb)

        destb = QGroupBox(_('Onto destination'))
        destb.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        destb.setLayout(QVBoxLayout())
        destb.layout().setContentsMargins(*(2,)*4)
        dest = csinfo.create(self.repo, revs[1], style, withupdate=True)
        destb.layout().addWidget(dest)
        self.destcsinfo = dest
        self.layout().addWidget(destb)

        self._cmdcontrol = cmd = cmdui.CmdSessionControlWidget(self)
        cmd.finished.connect(self.done)
        cmd.setLogVisible(True)
        self.compressbtn = cmd.addButton(_('Compress'),
                                         QDialogButtonBox.ButtonRole.AcceptRole)
        self.compressbtn.setEnabled(False)
        self.compressbtn.clicked.connect(self.compress)
        self.layout().addWidget(cmd)

        cmd.showStatusMessage(_('Checking...'))
        self._wctxcleaner = wctxcleaner.WctxCleaner(repoagent, self)
        self._wctxcleaner.checkFinished.connect(self._checkCompleted)
        cmd.linkActivated.connect(self._wctxcleaner.runCleaner)
        QTimer.singleShot(0, self._wctxcleaner.check)

        self.resize(480, 340)
        self.setWindowTitle(_('Compress - %s') % repoagent.displayName())

        self.restoreSettings()

    @property
    def repo(self):
        return self._repoagent.rawRepo()

    @pyqtSlot(bool)
    def _checkCompleted(self, clean):
        if not clean:
            self.compressbtn.setEnabled(False)
            txt = _('Before compress, you must '
                    '<a href="commit"><b>commit</b></a>, '
                    '<a href="shelve"><b>shelve</b></a> to patch, '
                    'or <a href="discard"><b>discard</b></a> changes.')
        else:
            self.compressbtn.setEnabled(True)
            txt = _('You may continue the compress')
        self._cmdcontrol.showStatusMessage(txt)

    def compress(self):
        uc = ['update', '--clean', '--rev', str(self.revs[1])]
        rc = ['revert', '--all', '--rev', str(self.revs[0])]
        sess = self._repoagent.runCommandSequence([uc, rc], self)
        self._cmdcontrol.setSession(sess)
        sess.commandFinished.connect(self.commandFinished)
        self.compressbtn.setEnabled(sess.isFinished())

    @pyqtSlot()
    def commandFinished(self):
        self._cmdcontrol.showStatusMessage(_('Changes have been moved, you '
                                             'must now commit'))
        self.compressbtn.setText(_('Commit', 'action button'))
        self.compressbtn.clicked.disconnect(self.compress)
        self.compressbtn.clicked.connect(self.commit)
        self.compressbtn.setEnabled(self._cmdcontrol.session().isFinished())

    def commit(self):
        tip, base = self.revs
        revs = [c for c in self.repo.revs(b'%d::%d' % (base, tip)) if c != base]
        descs = [self.repo[c].description() for c in revs]
        self.repo.vfs(b'cur-message.txt', b'w').write(b'\n* * *\n'.join(descs))

        dlg = commit.CommitDialog(self._repoagent, [], {}, self)
        dlg.finished.connect(dlg.deleteLater)
        dlg.exec()
        self._cmdcontrol.showStatusMessage(_('Compress is complete, old '
                                             'history untouched'))
        self.compressbtn.hide()
        self.storeSettings()

    def storeSettings(self):
        s = QSettings()
        s.setValue('compress/geometry', self.saveGeometry())

    def restoreSettings(self):
        s = QSettings()
        self.restoreGeometry(qtlib.readByteArray(s, 'compress/geometry'))

    def reject(self):
        self._cmdcontrol.reject()
