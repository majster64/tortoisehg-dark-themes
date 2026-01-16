# locktool.py - TortoiseHg's file locking widget
#
# Copyright 2016 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import os
import typing

from .qtcore import (
    QModelIndex,
    QSettings,
    QTimer,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from .qtgui import (
    QAction,
    QDialog,
    QFileDialog,
    QKeySequence,
    QLabel,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from mercurial import (
    extensions,
    pycompat,
    util,
)

from ..util import hglib
from ..util.i18n import _
from . import (
    cmdcore,
    cmdui,
    qtlib,
)

if typing.TYPE_CHECKING:
    from typing import (
        Dict,
        List,
        Optional,
        Text,
    )
    from mercurial import localrepo
    from .qtgui import QWidget
    from .thgrepo import RepoAgent


_FILE_FILTER = ';;'.join([
    _('Word docs (*.doc *.docx)'),
    _('PDF docs (*.pdf)'),
    _('Excel files (*.xls *.xlsx)'),
    _('All files (*)')])

class LockDialog(QDialog):
    showMessage = pyqtSignal(str)

    @property
    def repo(self) -> localrepo.localrepository:
        return self._repoagent.rawRepo()

    def __init__(self,
                 repoagent: RepoAgent,
                 parent: Optional[QWidget] = None) -> None:
        QDialog.__init__(self, parent)

        self.setWindowTitle(_('TortoiseHg Lock Tool - %s') % \
                            repoagent.shortName())
        self.setWindowIcon(qtlib.geticon('thg-password'))

        layout = QVBoxLayout()
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)
        self.setLayout(layout)

        self._repoagent = repoagent
        self._cmdsession = cmdcore.nullCmdSession()

        self._repoagent.configChanged.connect(self.reload)

        tb = QToolBar(self)
        tb.setIconSize(qtlib.toolBarIconSize())
        tb.setStyleSheet(qtlib.tbstylesheet)
        self.layout().addWidget(tb)

        self.refreshAction = a = QAction(self)
        a.setToolTip(_('Refresh lock information'))
        a.setIcon(qtlib.geticon('view-refresh'))
        a.triggered.connect(self.reload)
        tb.addAction(a)

        self.addAction = a = QAction(self)
        a.setToolTip(_('Lock a file not described in .hglocks'))
        a.setIcon(qtlib.geticon('new-group')) # TODO: not the best icon
        a.triggered.connect(self.lockany)
        tb.addAction(a)

        self.stopAction = a = QAction(self)
        a.setToolTip(_('Stop current operation'))
        a.setIcon(qtlib.geticon('process-stop'))
        a.triggered.connect(self.stopclicked)
        tb.addAction(a)

        lbl = QLabel(_('Locked And Lockable Files:'))
        self.locktw = tw = QTreeWidget(self)
        tw.setColumnCount(3)
        tw.setHeaderLabels([_('Path'), _('Locking User'), _('Purpose')])
        tw.setEnabled(False)
        tw.doubleClicked.connect(self.rowDoubleClicked)
        layout.addWidget(lbl)
        layout.addWidget(tw)

        self._stbar = cmdui.ThgStatusBar()
        layout.addWidget(self._stbar)
        self.showMessage.connect(self._stbar.showMessage)

        s = QSettings()
        self.restoreGeometry(qtlib.readByteArray(s, 'lock/geom'))
        self.locktw.header().restoreState(
            qtlib.readByteArray(s, 'lock/treestate'))

        QTimer.singleShot(0, self.finishSetup)

    @pyqtSlot()
    def finishSetup(self) -> None:
        'complete the setup, some of these steps might fail'
        # this code is specific to simplelock
        try:
            self.sl = extensions.find(b'simplelock')
        except KeyError:
            qtlib.WarningMsgBox(_('Simplelock extension not enabled'),
                                _('Please enable and configure simplelock'),
                                parent=self)
            self.reject()
            return
        # this code is specific to simplelock

        self.refillModel()
        self.reload()

    def refillModel(self) -> Dict[str, List[str]]:
        # this code is specific to simplelock
        locks: Dict[bytes, List[bytes]] = self.sl.parseLocks(self.repo)
        lockables: List[bytes] = self.sl.readlockables(self.repo)
        for wfile in lockables:
            if wfile not in locks:
                locks[wfile] = [b'', b'']
        # this code is specific to simplelock

        self.locktw.clear()
        self.rawrows = sorted([(w, u, p) for w, (u, p) in locks.items()])
        rows = []
        for wfile, user, purpose in self.rawrows:
            uwfile = hglib.tounicode(wfile)
            uuser = hglib.tounicode(user)
            upurpose = hglib.tounicode(purpose)
            rows.append(QTreeWidgetItem([uwfile, uuser, upurpose]))
        self.locktw.addTopLevelItems(rows)
        return pycompat.rapply(hglib.tounicode, locks)

    def reject(self):
        s = QSettings()
        s.setValue('lock/geom', self.saveGeometry())
        s.setValue('lock/treestate', self.locktw.header().saveState())
        QDialog.reject(self)

    @pyqtSlot()
    def _updateUi(self):
        sess = self._cmdsession
        self.refreshAction.setEnabled(sess.isFinished())
        self.addAction.setEnabled(sess.isFinished())
        self.locktw.setEnabled(sess.isFinished())
        self.stopAction.setEnabled(not sess.isFinished())

    @pyqtSlot()
    def lockany(self) -> None:
        wfile, _filter = QFileDialog.getOpenFileName(
            self, _('Open a (nonmergable) file you wish to be locked'),
            hglib.tounicode(self.repo.root), _FILE_FILTER)

        wfile = hglib.normpath(wfile)
        pathprefix = hglib.tounicode(util.normpath(self.repo.root)) + '/'
        if not os.path.normcase(wfile).startswith(os.path.normcase(pathprefix)):
            self.showMessage.emit(_('File was not within current repository'))
        wfile = wfile[len(pathprefix):]

        self.showMessage.emit(_('Locking %s') % wfile)
        self.lockrun(['lock', wfile])

    def unlock(self, wfile: str) -> None:
        self.showMessage.emit(_('Unlocking %s') % wfile)
        self.lockrun(['unlock', wfile])

    def lock(self, wfile: str, user: str) -> None:
        self.showMessage.emit(_('Locking %s') % wfile)
        self.lockrun(['lock', wfile])

    @pyqtSlot()
    def reload(self) -> None:
        'update list of locks, then update UI'
        self.showMessage.emit(_('Refreshing locks...'))
        self.lockrun(['locks']) # has side-effect of refreshing locks

    def lockrun(self, ucmdline: List[str]) -> None:
        self.operation = ucmdline + [None]
        self._cmdsession = sess = self._repoagent.runCommand(ucmdline, self)
        sess.commandFinished.connect(self.operationComplete)
        self._updateUi()

    def operationComplete(self) -> None:
        locks = self.refillModel()
        self._updateUi()

        op, wfile = self.operation[:2]
        if op == 'lock':
            if wfile in locks and locks[wfile][1]:
                self.showMessage.emit(_('Lock of %s successful') % wfile)
                qtlib.openlocalurl(wfile)
            else:
                self.showMessage.emit(_('Lock of %s failed, retry') % wfile)
        elif op == 'unlock':
            if wfile in locks and locks[wfile][1]:
                self.showMessage.emit(_('Unlock of %s failed, retry') % wfile)
            else:
                self.showMessage.emit(_('Unlock of %s successful') % wfile)
        elif locks:
            self.showMessage.emit(_('Ready, double click to lock or unlock'))
        else:
            self.showMessage.emit(_('Ready'))
        self.operation = ['N/A', None]

    @pyqtSlot()
    def stopclicked(self):
        self._cmdsession.abort()

    @pyqtSlot(QModelIndex)
    def rowDoubleClicked(self, index: QModelIndex) -> None:
        wfile, user, purpose = self.rawrows[index.row()]
        curuser = hglib.fromunicode(qtlib.getCurrentUsername(self, self.repo))
        if user or purpose:
            if user != curuser:
                self.showMessage.emit(_('You can only release your own locks'))
            else:
                self.unlock(hglib.tounicode(wfile))
        else:
            self.lock(hglib.tounicode(wfile), hglib.tounicode(curuser))

    def canExit(self) -> bool:
        return self._cmdsession.isFinished()

    def keyPressEvent(self, event):
        sess = self._cmdsession
        if event.matches(QKeySequence.StandardKey.Refresh):
            self.reload()
        elif event.key() == Qt.Key.Key_Escape and not sess.isFinished():
            sess.abort()
        else:
            return super().keyPressEvent(event)
