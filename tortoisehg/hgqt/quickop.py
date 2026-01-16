# quickop.py - TortoiseHg's dialog for quick dirstate operations
#
# Copyright 2009 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import os
import sys
from typing import (
    List,
)

from .qtcore import (
    QObject,
    QSettings,
    Qt,
)
from .qtgui import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QKeySequence,
    QLabel,
    QHBoxLayout,
    QPushButton,
    QShortcut,
    QVBoxLayout,
)

from hgext.largefiles import (
    lfutil,
)

from mercurial import (
    error,
    pycompat,
    util,
)

from ..util import (
    hglib,
    shlib,
)
from ..util.i18n import _
from . import (
    cmdcore,
    cmdui,
    lfprompt,
    qtlib,
    status,
)

LABELS = { 'add': (_('Checkmark files to add'), _('Add')),
           'forget': (_('Checkmark files to forget'), _('Forget')),
           'revert': (_('Checkmark files to revert'), _('Revert')),
           'remove': (_('Checkmark files to remove'), _('Remove')),}

ICONS = { 'add': 'hg-add',
           'forget': 'hg-remove',
           'revert': 'hg-revert',
           'remove': 'hg-remove',}

class QuickOpDialog(QDialog):
    """ Dialog for performing quick dirstate operations """
    def __init__(self, repoagent, command, pats, parent):
        QDialog.__init__(self, parent)
        self.setWindowFlags(Qt.WindowType.Window)
        self.pats = pats
        self._repoagent = repoagent
        self._cmdsession = cmdcore.nullCmdSession()
        self._cmddialog = cmdui.CmdSessionDialog(self)

        # Handle rm alias
        if command == 'rm':
            command = 'remove'
        self.command = command

        self.setWindowTitle(_('%s - hg %s')
                            % (repoagent.displayName(), command))
        self.setWindowIcon(qtlib.geticon(ICONS[command]))

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        toplayout = QVBoxLayout()
        toplayout.setContentsMargins(5, 5, 5, 0)
        layout.addLayout(toplayout)

        hbox = QHBoxLayout()
        lbl = QLabel(LABELS[command][0])
        slbl = QLabel()
        hbox.addWidget(lbl)
        hbox.addStretch(1)
        hbox.addWidget(slbl)
        self.status_label = slbl
        toplayout.addLayout(hbox)

        types = { 'add'    : 'I?',
                  'forget' : 'MAR!C',
                  'revert' : 'MAR!',
                  'remove' : 'MAR!CI?',
                }
        filetypes = types[self.command]

        checktypes = { 'add'    : '?',
                       'forget' : '',
                       'revert' : 'MAR!',
                       'remove' : '',
                     }
        defcheck = checktypes[self.command]

        opts = {}
        for s, val in status.statusTypes.items():
            opts[val.name] = s in filetypes

        opts['checkall'] = True # pre-check all matching files
        stwidget = status.StatusWidget(repoagent, pats, opts, self,
                                       defcheck=defcheck)
        toplayout.addWidget(stwidget, 1)

        hbox = QHBoxLayout()
        if self.command == 'revert':
            ## no backup checkbox
            chk = QCheckBox(_('Do not save backup files (*.orig)'))
        elif self.command == 'remove':
            ## force checkbox
            chk = QCheckBox(_('Force removal of modified files (--force)'))
        else:
            chk = None
        if chk:
            self.chk = chk
            hbox.addWidget(chk)

        self.statusbar = cmdui.ThgStatusBar(self)
        stwidget.showMessage.connect(self.statusbar.showMessage)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Close)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.StandardButton.Ok).setDefault(True)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(LABELS[command][1])
        hbox.addStretch()
        hbox.addWidget(bb)
        toplayout.addLayout(hbox)
        self.bb = bb

        if self.command == 'add':
            if b'largefiles' in self.repo.extensions():
                self.addLfilesButton = QPushButton(_('Add &Largefiles'))
            else:
                self.addLfilesButton = None
            if self.addLfilesButton:
                self.addLfilesButton.clicked.connect(self.addLfiles)
                bb.addButton(self.addLfilesButton, QDialogButtonBox.ButtonRole.ActionRole)

        layout.addWidget(self.statusbar)

        s = QSettings()
        stwidget.loadSettings(s, 'quickop')
        self.restoreGeometry(qtlib.readByteArray(s, 'quickop/geom'))
        if hasattr(self, 'chk'):
            if self.command == 'revert':
                self.chk.setChecked(
                    qtlib.readBool(s, 'quickop/nobackup', True))
            elif self.command == 'remove':
                self.chk.setChecked(
                    qtlib.readBool(s, 'quickop/forceremove', False))
        self.stwidget = stwidget
        self.stwidget.refreshWctx()
        QShortcut(QKeySequence('Ctrl+Return'), self, self.accept)
        QShortcut(QKeySequence('Ctrl+Enter'), self, self.accept)
        qtlib.newshortcutsforstdkey(QKeySequence.StandardKey.Refresh, self,
                                    self.stwidget.refreshWctx)
        QShortcut(QKeySequence('Escape'), self, self.reject)

    @property
    def repo(self):
        return self._repoagent.rawRepo()

    def _runCommand(self,
                    files: List[bytes],
                    lfiles: List[bytes],
                    opts) -> None:
        cmdlines = []
        if files:
            cmdlines.append(hglib.buildcmdargs(self.command, *[
                hglib.escapepath(hglib.tounicode(f)) for f in files
            ], **opts))
        if lfiles:
            assert self.command == 'add', self.command
            lopts = opts.copy()
            lopts['large'] = True
            cmdlines.append(hglib.buildcmdargs(self.command, *[
                hglib.escapepath(hglib.tounicode(f)) for f in lfiles
            ], **lopts))
        self.files = files + lfiles

        self._cmdsession = sess = self._repoagent.runCommandSequence(cmdlines,
                                                                     self)
        sess.commandFinished.connect(self.commandFinished)
        sess.progressReceived.connect(self.statusbar.setProgress)
        self._cmddialog.setSession(sess)
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def commandFinished(self, ret):
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        self.statusbar.clearProgress()
        if ret == 0:
            shlib.shell_notify(self.files)
            self.reject()
        else:
            self._cmddialog.show()

    def accept(self):
        cmdopts = {}
        if hasattr(self, 'chk'):
            if self.command == 'revert':
                cmdopts['no_backup'] = self.chk.isChecked()
            elif self.command == 'remove':
                cmdopts['force'] = self.chk.isChecked()
        files = self.stwidget.getChecked()
        if not files:
            qtlib.WarningMsgBox(_('No files selected'),
                                _('No operation to perform'),
                                parent=self)
            return
        if self.command == 'remove':
            with lfutil.lfstatus(self.repo):
                try:
                    repostate = self.repo.status()
                except (OSError, error.Abort) as e:
                    qtlib.WarningMsgBox(_('Unable to read repository status'),
                                        hglib.exception_str(e), parent=self)
                    return

            if not self.chk.isChecked():
                selmodified = []
                for wfile in files:
                    if wfile in repostate.modified:
                        selmodified.append(wfile)
                if selmodified:
                    prompt = qtlib.CustomPrompt(
                        _('Confirm Remove'),
                        _('You have selected one or more files that have been '
                          'modified.  By default, these files will not be '
                          'removed.  What would you like to do?'),
                        self,
                        (_('Remove &Unmodified Files'),
                         _('Remove &All Selected Files'),
                         _('Cancel')),
                        0, 2, selmodified)
                    ret = prompt.run()
                    if ret == 1:
                        cmdopts['force'] = True
                    elif ret == 2:
                        return
            for wfile in files:
                if wfile in repostate.unknown or wfile in repostate.ignored:
                    try:
                        util.unlink(wfile)
                    except OSError:
                        pass
                    files.remove(wfile)
        elif self.command == 'add':
            if b'largefiles' in self.repo.extensions():
                self.addWithPrompt(files)
                return
        if files:
            self._runCommand(files, [], cmdopts)
        else:
            self.reject()

    def reject(self):
        if not self._cmdsession.isFinished():
            self._cmdsession.abort()
        elif not self.stwidget.canExit():
            return
        else:
            s = QSettings()
            self.stwidget.saveSettings(s, 'quickop')
            s.setValue('quickop/geom', self.saveGeometry())
            if hasattr(self, 'chk'):
                if self.command == 'revert':
                    s.setValue('quickop/nobackup', self.chk.isChecked())
                elif self.command == 'remove':
                    s.setValue('quickop/forceremove', self.chk.isChecked())
            QDialog.reject(self)

    def addLfiles(self):
        files = self.stwidget.getChecked()
        if not files:
            qtlib.WarningMsgBox(_('No files selected'),
                                _('No operation to perform'),
                                parent=self)
            return
        self._runCommand([], files, {})

    def addWithPrompt(self, files):
        result = lfprompt.promptForLfiles(self, self.repo.ui, self.repo, files)
        if not result:
            return
        files, lfiles = result
        self._runCommand(files, lfiles, {})

class HeadlessQuickop(QObject):
    def __init__(self, repoagent, cmdline):
        QObject.__init__(self)
        self.files = cmdline[1:]
        self._cmddialog = cmdui.CmdSessionDialog()
        sess = repoagent.runCommand(pycompat.maplist(hglib.tounicode, cmdline))
        sess.commandFinished.connect(self.commandFinished)
        self._cmddialog.setSession(sess)

    def commandFinished(self, ret):
        if ret == 0:
            shlib.shell_notify(self.files)
            sys.exit(0)
        else:
            self._cmddialog.show()

    # dummy methods to act as QWidget (see run.qtrun)
    def show(self):
        pass
    def raise_(self):
        pass

def run(ui, repoagent, *pats, **opts):
    repo = repoagent.rawRepo()
    pats = hglib.canonpaths(pats)
    command = opts['alias']
    imm = repo.ui.config(b'tortoisehg', b'immediate')
    if opts.get('headless') or command in imm.lower():
        cmdline = [command] + pats
        return HeadlessQuickop(repoagent, cmdline)
    else:
        os.chdir(repo.root)  # for scmutil.match() in StatusThread
        return QuickOpDialog(repoagent, hglib.tounicode(command), pats, None)
