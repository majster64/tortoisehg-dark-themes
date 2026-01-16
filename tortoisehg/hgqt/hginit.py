# hginit.py - TortoiseHg dialog to initialize a repo
#
# Copyright 2008 Steve Borho <steve@borho.org>
# Copyright 2010 Johan Samyn <johan.samyn@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import os
import typing

from .qtcore import (
    pyqtSignal,
    pyqtSlot,
)
from .qtgui import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
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
        List,
        Optional,
        Text,
    )
    from mercurial import (
        ui as uimod,
    )
    from .qtgui import (
        QWidget,
    )


class InitWidget(cmdui.AbstractCmdWidget):

    def __init__(self,
                 ui: uimod.ui,
                 cmdagent: cmdcore.CmdAgent,
                 destdir: str = '.',
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._cmdagent = cmdagent

        form = QFormLayout()
        self.setLayout(form)

        # dest widgets
        self._dest_edit = QLineEdit()
        self._dest_edit.setMinimumWidth(300)
        self._dest_btn = QPushButton(_('Browse...'))
        self._dest_btn.setAutoDefault(False)
        destbox = QHBoxLayout()
        destbox.addWidget(self._dest_edit, 1)
        destbox.addWidget(self._dest_btn)
        form.addRow(_('Destination path:'), destbox)

        # options checkboxes
        if ui.config(b'tortoisehg', b'initskel'):
            l = _('Copy working directory files from skeleton')
        else:
            l = _('Create special files (.hgignore, ...)')
        self._add_files_chk = QCheckBox(l)
        self._make_pre_1_7_chk = QCheckBox(
                _('Make repo compatible with Mercurial <1.7'))
        optbox = QVBoxLayout()
        optbox.addWidget(self._add_files_chk)
        optbox.addWidget(self._make_pre_1_7_chk)
        form.addRow('', optbox)

        # some extras
        self._hgcmd_txt = QLineEdit()
        self._hgcmd_txt.setReadOnly(True)
        form.addRow(_('Hg command:'), self._hgcmd_txt)

        # init defaults
        path = os.path.abspath(destdir)
        if os.path.isfile(path):
            path = os.path.dirname(path)
        self._dest_edit.setText(path)
        self._add_files_chk.setChecked(True)
        self._make_pre_1_7_chk.setChecked(False)
        self._composeCommand()

        # connecting slots
        self._dest_edit.textChanged.connect(self._composeCommand)
        self._dest_btn.clicked.connect(self._browseDestination)
        self._add_files_chk.toggled.connect(self._composeCommand)
        self._make_pre_1_7_chk.toggled.connect(self._composeCommand)

    @pyqtSlot()
    def _browseDestination(self) -> None:
        """Select the destination directory"""
        caption = _('Select Destination Folder')
        path = QFileDialog.getExistingDirectory(self, caption)
        if path:
            self._dest_edit.setText(path)

    def destination(self) -> str:
        return self._dest_edit.text().strip()

    def _buildCommand(self) -> List[str]:
        cfgs = []
        if self._add_files_chk.isChecked():
            cfgs.append('hooks.post-init.thgskel='
                        'python:tortoisehg.util.hgcommands.postinitskel')
        if self._make_pre_1_7_chk.isChecked():
            cfgs.append('format.dotencode=False')
        return hglib.buildcmdargs('init', self.destination(), config=cfgs)

    @pyqtSlot()
    def _composeCommand(self) -> None:
        cmdline = self._buildCommand()
        self._hgcmd_txt.setText('hg ' + hglib.prettifycmdline(cmdline))
        self.commandChanged.emit()

    def canRunCommand(self) -> bool:
        return bool(self.destination())

    def runCommand(self) -> cmdcore.CmdSession:
        cmdline = self._buildCommand()
        return self._cmdagent.runCommand(cmdline, self)


class InitDialog(cmdui.CmdControlDialog):

    newRepository = pyqtSignal(str)

    def __init__(self,
                 ui: uimod.ui,
                 destdir: str = '.',
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_('New Repository'))
        self.setWindowIcon(qtlib.geticon('hg-init'))
        self.setObjectName('init')
        self.setRunButtonText(_('&Create'))
        self._cmdagent = cmdagent = cmdcore.CmdAgent(ui, self)
        cmdagent.serviceStopped.connect(self.reject)
        self.setCommandWidget(InitWidget(ui, cmdagent, destdir, self))
        self.commandFinished.connect(self._handleNewRepo)

    def destination(self) -> str:
        return self.commandWidget().destination()

    @pyqtSlot(int)
    def _handleNewRepo(self, ret: int) -> None:
        if ret != 0:
            return
        self.newRepository.emit(self.destination())

    def done(self, r: int) -> None:
        if self._cmdagent.isServiceRunning():
            self._cmdagent.stopService()
            return  # postponed until serviceStopped
        super().done(r)
