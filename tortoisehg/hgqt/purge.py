# purge.py - working copy purge dialog, based on Mercurial purge extension
#
# Copyright 2010 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import os
import shutil
import stat

from .qtcore import (
    QSettings,
    QThread,
    QTimer,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from .qtgui import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QVBoxLayout,
)

from hgext.largefiles import (
    lfutil,
)

from mercurial import (
    hg,
    scmutil,
)

from ..util import hglib
from ..util.i18n import _, ngettext
from . import (
    cmdui,
    qtlib,
)

class PurgeDialog(QDialog):

    progress = pyqtSignal(str, object, str, str, object)
    showMessage = pyqtSignal(str)

    def __init__(self, repoagent, parent=None):
        QDialog.__init__(self, parent)
        f = self.windowFlags()
        self.setWindowFlags(f & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._repoagent = repoagent

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setLayout(layout)

        toplayout = QVBoxLayout()
        toplayout.setContentsMargins(10, 10, 10, 10)
        toplayout.setSpacing(5)
        layout.addLayout(toplayout)

        cb = QCheckBox(_('No unknown files found'))
        cb.setChecked(False)
        cb.setEnabled(False)
        toplayout.addWidget(cb)
        self.ucb = cb

        cb = QCheckBox(_('No ignored files found'))
        cb.setChecked(False)
        cb.setEnabled(False)
        toplayout.addWidget(cb)
        self.icb = cb

        cb = QCheckBox(_('No trash files found'))
        cb.setChecked(False)
        cb.setEnabled(False)
        toplayout.addWidget(cb)
        self.tcb = cb

        self.foldercb = QCheckBox(_('Delete empty folders'))
        self.foldercb.setChecked(True)
        toplayout.addWidget(self.foldercb)
        self.hgfilecb = QCheckBox(_('Preserve files beginning with .hg'))
        self.hgfilecb.setChecked(True)
        toplayout.addWidget(self.hgfilecb)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        self.bb = bb
        toplayout.addStretch()
        toplayout.addWidget(bb)

        self.stbar = cmdui.ThgStatusBar(self)
        self.progress.connect(self.stbar.progress)
        self.showMessage.connect(self.stbar.showMessage)
        layout.addWidget(self.stbar)

        self.setWindowTitle(_('%s - purge') % repoagent.displayName())
        self.setWindowIcon(qtlib.geticon('hg-purge'))

        self.bb.setEnabled(False)
        self.progress.emit(*cmdui.startProgress(_('Checking'), '...'))
        s = QSettings()
        desktopgeom = QApplication.primaryScreen().availableGeometry()
        self.resize(desktopgeom.size() * 0.25)
        self.restoreGeometry(qtlib.readByteArray(s, 'purge/geom'))

        self.th = None
        QTimer.singleShot(0, self.checkStatus)

    @property
    def repo(self):
        return self._repoagent.rawRepo()

    def checkStatus(self):
        repo = self.repo
        class CheckThread(QThread):
            def __init__(self, parent):
                QThread.__init__(self, parent)
                self.files = (None, None)
                self.error = None

            def run(self):
                try:
                    with lfutil.lfstatus(repo):
                        stat = repo.status(ignored=True, unknown=True)
                    trashcan = repo.vfs.join(b'Trashcan')
                    if os.path.isdir(trashcan):
                        trash = os.listdir(trashcan)
                    else:
                        trash = []
                    self.files = stat.unknown, stat.ignored, trash
                except Exception as e:
                    self.error = hglib.exception_str(e)

        self.th = CheckThread(self)
        self.th.finished.connect(self._checkCompleted)
        self.th.start()

    @pyqtSlot()
    def _checkCompleted(self):
        self.th.wait()
        self.files = self.th.files
        self.bb.setEnabled(True)
        self.progress.emit(*cmdui.stopProgress(_('Checking')))
        if self.th.error:
            self.showMessage.emit(self.th.error)
        else:
            self.showMessage.emit(_('Ready to purge.'))
            U, I, T = self.files
            if U:
                self.ucb.setText(ngettext(
                    'Delete %d unknown file',
                    'Delete %d unknown files', len(U)) % len(U))
                self.ucb.setChecked(True)
                self.ucb.setEnabled(True)
            if I:
                self.icb.setText(ngettext(
                   'Delete %d ignored file',
                   'Delete %d ignored files', len(I)) % len(I))
                self.icb.setChecked(True)
                self.icb.setEnabled(True)
            if T:
                self.tcb.setText(ngettext(
                    'Delete %d file in .hg/Trashcan',
                    'Delete %d files in .hg/Trashcan', len(T)) % len(T))
                self.tcb.setChecked(True)
                self.tcb.setEnabled(True)

    def reject(self):
        if self.th and self.th.isRunning():
            return
        s = QSettings()
        s.setValue('purge/geom', self.saveGeometry())
        super().reject()

    def accept(self):
        unknown = self.ucb.isChecked()
        ignored = self.icb.isChecked()
        trash = self.tcb.isChecked()
        delfolders = self.foldercb.isChecked()
        keephg = self.hgfilecb.isChecked()

        if not (unknown or ignored or trash or delfolders):
            QDialog.accept(self)
            return
        if not qtlib.QuestionMsgBox(_('Confirm file deletions'),
            _('Are you sure you want to delete these files and/or folders?'),
            parent=self):
            return

        opts = dict(unknown=unknown, ignored=ignored, trash=trash,
                    delfolders=delfolders, keephg=keephg)

        self.th = PurgeThread(self.repo, opts, self)
        self.th.progress.connect(self.progress)
        self.th.showMessage.connect(self.showMessage)
        self.th.finished.connect(self._purgeCompleted)
        self.th.start()

    @pyqtSlot()
    def _purgeCompleted(self):
        self.th.wait()
        F = self.th.failures
        if F:
            qtlib.InfoMsgBox(_('Deletion failures'), ngettext(
                'Unable to delete %d file or folder',
                'Unable to delete %d files or folders', len(F)) % len(F),
                parent=self)
        if F is not None:
            self.reject()

class PurgeThread(QThread):
    progress = pyqtSignal(str, object, str, str, object)
    showMessage = pyqtSignal(str)

    def __init__(self, repo, opts, parent):
        super().__init__(parent)
        self.failures = 0
        self.root = repo.root
        self.opts = opts

    def run(self):
        try:
            self.failures = self.purge(self.root, self.opts)
        except Exception as e:
            self.failures = None
            self.showMessage.emit(hglib.exception_str(e))

    def purge(self, root, opts):
        repo = hg.repository(hglib.loadui(), self.root)
        keephg = opts['keephg']
        directories = []
        failures = []

        if opts['trash']:
            self.showMessage.emit(_('Deleting trash folder...'))
            trashcan = repo.vfs.join(b'Trashcan')
            try:
                shutil.rmtree(trashcan)
            except OSError:
                failures.append(trashcan)

        self.showMessage.emit('')
        match = scmutil.matchall(repo)
        match.explicitdir = match.traversedir = directories.append
        with lfutil.lfstatus(repo):
            status = repo.status(match=match, ignored=opts['ignored'],
                                 unknown=opts['unknown'], clean=False)
        files = []
        if opts['unknown']:
            files.extend(status.unknown)
        if opts['ignored']:
            files.extend(status.ignored)

        def remove(remove_func, name):
            try:
                if keephg and name.startswith(b'.hg'):
                    return
                remove_func(repo.wjoin(name))
            except OSError:
                failures.append(name)

        def removefile(path):
            try:
                os.remove(path)
            except OSError:
                # read-only files cannot be unlinked under Windows
                s = os.stat(path)
                if (s.st_mode & stat.S_IWRITE) != 0:
                    raise
                os.chmod(path, stat.S_IMODE(s.st_mode) | stat.S_IWRITE)
                os.remove(path)

        for i, f in enumerate(sorted(files)):
            data = ('deleting', i, hglib.tounicode(f), '', len(files))
            self.progress.emit(*data)
            remove(removefile, f)
        data = ('deleting', None, '', '', len(files))
        self.progress.emit(*data)
        self.showMessage.emit(_('Deleted %d files') % len(files))

        if opts['delfolders'] and directories:
            for i, f in enumerate(sorted(directories, reverse=True)):
                if match(f) and not os.listdir(repo.wjoin(f)):
                    data = ('rmdir', i, hglib.tounicode(f), '', len(directories))
                    self.progress.emit(*data)
                    remove(os.rmdir, f)
            data = ('rmdir', None, '', '', len(directories))
            self.progress.emit(*data)
            self.showMessage.emit(_('Deleted %d files and %d folders') % (
                                  len(files), len(directories)))
        return failures
