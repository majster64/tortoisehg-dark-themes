# clone.py - Clone dialog for TortoiseHg
#
# Copyright 2007 TK Soh <teekaysoh@gmail.com>
# Copyright 2007 Steve Borho <steve@borho.org>
# Copyright 2010 Yuki KODAMA <endflow.net@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import os
import typing

from .qtcore import (
    QDir,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)
from .qtgui import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mercurial import (
    cmdutil,
    commands,
    hg,
    pycompat,
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
        Tuple,
    )
    from mercurial import (
        ui as uimod,
    )
    from .qtcore import (
        QSettings,
    )
    from .hgconfig import (
        HgConfig,
    )


def _startrev_available() -> bool:
    entry = cmdutil.findcmd(b'clone', commands.table)[1]
    longopts = {e[1] for e in entry[1]}
    return b'startrev' in longopts

def _suggesteddest(src: str, basedest: str) -> str:
    if '://' in basedest:
        return basedest
    try:
        if not os.listdir(basedest):
            # premade empty directory, just use it
            return basedest
    except OSError:
        # guess existing base assuming "{basedest}/{name}"
        basedest = os.path.dirname(basedest)
    name = hglib.tounicode(hg.defaultdest(hglib.fromunicode(src, 'replace')))
    if not name or name == '.':
        return basedest
    newdest = os.path.join(basedest, name)
    if os.path.exists(newdest):
        newdest += '-clone'
    return newdest


class CloneWidget(cmdui.AbstractCmdWidget):

    def __init__(self,
                 config: HgConfig,
                 cmdagent: cmdcore.CmdAgent,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._config = config
        self._cmdagent = cmdagent

        ## main layout
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.setLayout(form)

        ### source combo and button
        self.src_combo = QComboBox()
        self.src_combo.setEditable(True)
        self.src_combo.setMinimumContentsLength(30)  # cut long path
        self.src_btn = QPushButton(_('Browse...'))
        self.src_btn.setAutoDefault(False)
        self.src_btn.clicked.connect(self._browseSource)
        srcbox = QHBoxLayout()
        srcbox.addWidget(self.src_combo, 1)
        srcbox.addWidget(self.src_btn)
        form.addRow(_('Source:'), srcbox)

        ### destination combo and button
        self.dest_combo = QComboBox()
        self.dest_combo.setEditable(True)
        self.dest_combo.setMinimumContentsLength(30)  # cut long path
        self.dest_btn = QPushButton(_('Browse...'))
        self.dest_btn.setAutoDefault(False)
        self.dest_btn.clicked.connect(self._browseDestination)
        destbox = QHBoxLayout()
        destbox.addWidget(self.dest_combo, 1)
        destbox.addWidget(self.dest_btn)
        form.addRow(_('Destination:'), destbox)

        for combo in (self.src_combo, self.dest_combo):
            qtlib.allowCaseChangingInput(combo)
            combo.installEventFilter(qtlib.BadCompletionBlocker(combo))

        self.setSource(config.configString('tortoisehg', 'defaultclonedest')
                       or hglib.getcwdu())
        self.setDestination(self.source())

        ### options
        expander = qtlib.ExpanderLabel(_('Options'), False)
        optwidget = QWidget(self)
        expander.expanded.connect(optwidget.setVisible)
        optbox = QVBoxLayout()
        optbox.setContentsMargins(0, 0, 0, 0)
        optbox.setSpacing(6)
        optwidget.setLayout(optbox)
        form.addRow(expander, optwidget)

        def chktext(chklabel, btnlabel=None, btnslot=None, stretch=None):
            hbox = QHBoxLayout()
            hbox.setSpacing(0)
            optbox.addLayout(hbox)
            chk = QCheckBox(chklabel)
            text = QLineEdit(enabled=False)
            chk.toggled.connect(text.setEnabled)
            chk.toggled.connect(text.setFocus)
            hbox.addWidget(chk)
            hbox.addWidget(text)
            if stretch is not None:
                hbox.addStretch(stretch)
            if btnlabel:
                btn = QPushButton(btnlabel)
                btn.setEnabled(False)
                btn.setAutoDefault(False)
                btn.clicked.connect(btnslot)
                chk.toggled.connect(btn.setEnabled)
                hbox.addSpacing(6)
                hbox.addWidget(btn)
                return chk, text, btn
            else:
                return chk, text, None

        def chktext2(chklabel: str,
                     stretch: Optional[int] = None) -> Tuple[QCheckBox, QLineEdit]:
            # pytype gets confused if the returned tuple is sliced and returned
            # without unpacking.
            chk, text, _unused = chktext(chklabel, stretch=stretch)
            return chk, text

        def chktext3(chklabel: str,
                     btnlabel: str,
                     btnslot,
                     stretch: Optional[int] = None) -> Tuple[QCheckBox, QLineEdit, QPushButton]:
            assert btnlabel
            ret = chktext(chklabel, btnlabel, btnslot, stretch)
            assert isinstance(ret[2], QPushButton)
            return ret

        self.rev_chk, self.rev_text = chktext2(_('Clone to revision:'),
                                               stretch=40)
        self.rev_text.setToolTip(_('A revision identifier, bookmark, tag or '
                                   'branch name'))

        self.noupdate_chk = QCheckBox(_('Do not update the new working directory'))
        self.pproto_chk = QCheckBox(_('Use pull protocol to copy metadata'))
        self.stream_chk = QCheckBox(_('Clone with minimal processing'))
        optbox.addWidget(self.noupdate_chk)
        optbox.addWidget(self.pproto_chk)
        optbox.addWidget(self.stream_chk)
        self._opt_checks = {
            'noupdate': self.noupdate_chk,
            'pull': self.pproto_chk,
            'stream': self.stream_chk,
        }

        self.qclone_chk, self.qclone_txt, self.qclone_btn = \
                chktext3(_('Include patch queue'), btnlabel=_('Browse...'),
                         btnslot=self._browsePatchQueue)

        self.proxy_chk = QCheckBox(_('Use proxy server'))
        optbox.addWidget(self.proxy_chk)
        useproxy = bool(config.configString('http_proxy', 'host'))
        self.proxy_chk.setEnabled(useproxy)
        self.proxy_chk.setChecked(useproxy)

        self.insecure_chk = QCheckBox(_('Do not verify host certificate'))
        optbox.addWidget(self.insecure_chk)
        self.insecure_chk.setEnabled(False)

        self.remote_chk, self.remote_text = chktext2(_('Remote command:'))

        self.largefiles_chk = QCheckBox(_('Use largefiles'))
        optbox.addWidget(self.largefiles_chk)

        # allow to specify start revision for p4 & svn repos.
        self.startrev_chk, self.startrev_text = chktext2(_('Start revision:'),
                                                         stretch=40)

        self.hgcmd_txt = QLineEdit()
        self.hgcmd_txt.setReadOnly(True)
        form.addRow(_('Hg command:'), self.hgcmd_txt)

        # connect extra signals
        self.src_combo.editTextChanged.connect(self._onSourceChanged)
        self.src_combo.currentIndexChanged.connect(self._suggestDestination)
        t = QTimer(self, interval=200, singleShot=True)
        t.timeout.connect(self._suggestDestination)
        le = self.src_combo.lineEdit()
        le.editingFinished.connect(t.stop)  # only while it has focus
        le.textEdited.connect(t.start)
        self.dest_combo.editTextChanged.connect(self._composeCommand)
        self.rev_chk.toggled.connect(self._composeCommand)
        self.rev_text.textChanged.connect(self._composeCommand)
        self.noupdate_chk.toggled.connect(self._composeCommand)
        self.pproto_chk.toggled.connect(self._composeCommand)
        self.stream_chk.toggled.connect(self._composeCommand)
        self.qclone_chk.toggled.connect(self._composeCommand)
        self.qclone_txt.textChanged.connect(self._composeCommand)
        self.proxy_chk.toggled.connect(self._composeCommand)
        self.insecure_chk.toggled.connect(self._composeCommand)
        self.remote_chk.toggled.connect(self._composeCommand)
        self.remote_text.textChanged.connect(self._composeCommand)
        self.largefiles_chk.toggled.connect(self._composeCommand)
        self.startrev_chk.toggled.connect(self._composeCommand)

        # prepare to show
        optwidget.hide()

        self.startrev_chk.setVisible(_startrev_available())
        self.startrev_text.setVisible(_startrev_available())

        self._composeCommand()

    def readSettings(self, qs: QSettings) -> None:
        for key, combo in [('source', self.src_combo),
                           ('dest', self.dest_combo)]:
            # addItems() can overwrite temporary edit text
            edittext = combo.currentText()
            combo.blockSignals(True)
            combo.addItems(qtlib.readStringList(qs, key))
            combo.setCurrentIndex(combo.findText(edittext))
            combo.setEditText(edittext)
            combo.blockSignals(False)

        self.src_combo.lineEdit().selectAll()

    def writeSettings(self, qs: QSettings) -> None:
        for key, combo in [('source', self.src_combo),
                           ('dest', self.dest_combo)]:
            l = [combo.currentText()]
            l.extend(combo.itemText(i) for i in pycompat.xrange(combo.count())
                     if combo.itemText(i) != combo.currentText())
            qs.setValue(key, l[:10])

    def source(self) -> str:
        return self.src_combo.currentText().strip()

    def setSource(self, url: str) -> None:
        self.src_combo.setCurrentIndex(self.src_combo.findText(url))
        self.src_combo.setEditText(url)

    def destination(self) -> str:
        return self.dest_combo.currentText().strip()

    def setDestination(self, url: str) -> None:
        self.dest_combo.setCurrentIndex(self.dest_combo.findText(url))
        self.dest_combo.setEditText(url)

    @pyqtSlot()
    def _suggestDestination(self) -> None:
        self.setDestination(_suggesteddest(self.source(), self.destination()))

    def revSymbol(self) -> str:
        if not self.rev_chk.isChecked():
            return ''
        return self.rev_text.text().strip()

    def setRevSymbol(self, rev: str) -> None:
        self.rev_chk.setChecked(bool(rev))
        self.rev_text.setText(rev)

    def testOption(self, key: str) -> bool:
        return self._opt_checks[key].isChecked()

    def setOption(self, key: str, on: bool) -> None:
        self._opt_checks[key].setChecked(on)

    @pyqtSlot()
    def _composeCommand(self) -> List[str]:
        opts = {
            'verbose': True,
            'config': [],
            }
        for k in self._opt_checks:
            opts[k] = self.testOption(k)
        if (self._config.configString('http_proxy', 'host')
            and not self.proxy_chk.isChecked()):
            assert isinstance(opts['config'], list)  # help pytype
            opts['config'].append('http_proxy.host=')
        if self.remote_chk.isChecked():
            opts['remotecmd'] = self.remote_text.text().strip() or None
        opts['rev'] = self.revSymbol() or None
        if self.startrev_chk.isChecked():
            opts['startrev'] = self.startrev_text.text().strip() or None
        if self.largefiles_chk.isChecked():
            assert isinstance(opts['config'], list)  # help pytype
            opts['config'].append('extensions.largefiles=')

        src = self.source()
        dest = self.destination()
        if src.startswith('https://'):
            opts['insecure'] = self.insecure_chk.isChecked()

        if self.qclone_chk.isChecked():
            name = 'qclone'
            opts['patches'] = self.qclone_txt.text().strip() or None
        else:
            name = 'clone'

        cmdline = hglib.buildcmdargs(name, src, dest or None, **opts)
        self.hgcmd_txt.setText('hg ' + hglib.prettifycmdline(cmdline))
        self.commandChanged.emit()
        return cmdline

    def canRunCommand(self) -> bool:
        src, dest = self.source(), self.destination()
        return bool(src and dest and src != dest)

    def runCommand(self) -> cmdcore.CmdSession:
        cmdline = self._composeCommand()
        return self._cmdagent.runCommand(cmdline, self)

    @pyqtSlot()
    def _browseSource(self) -> None:
        FD = QFileDialog
        caption = _("Select source repository")
        path = FD.getExistingDirectory(self, caption,
            self.src_combo.currentText(), QFileDialog.Option.ShowDirsOnly)
        if path:
            self.src_combo.setEditText(QDir.toNativeSeparators(path))
            self._suggestDestination()
            self.dest_combo.setFocus()

    @pyqtSlot()
    def _browseDestination(self) -> None:
        FD = QFileDialog
        caption = _("Select destination repository")
        path = FD.getExistingDirectory(self, caption,
            self.dest_combo.currentText(), QFileDialog.Option.ShowDirsOnly)
        if path:
            self.dest_combo.setEditText(QDir.toNativeSeparators(path))
            self._suggestDestination()  # in case existing dir is selected
            self.dest_combo.setFocus()

    @pyqtSlot()
    def _browsePatchQueue(self) -> None:
        FD = QFileDialog
        caption = _("Select patch folder")
        upatchroot = os.path.join(self.src_combo.currentText(), '.hg')
        upath = FD.getExistingDirectory(self, caption, upatchroot,
                                        QFileDialog.Option.ShowDirsOnly)
        if upath:
            self.qclone_txt.setText(QDir.toNativeSeparators(upath))
            self.qclone_txt.setFocus()

    @pyqtSlot()
    def _onSourceChanged(self) -> None:
        self.insecure_chk.setEnabled(self.source().startswith('https://'))
        self._composeCommand()


class CloneDialog(cmdui.CmdControlDialog):

    clonedRepository = pyqtSignal(str, str)

    def __init__(self,
                 ui: uimod.ui,
                 config: HgConfig,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self.setWindowTitle(_('Clone - %s') % hglib.getcwdu())
        self.setWindowIcon(qtlib.geticon('hg-clone'))
        self.setObjectName('clone')
        self.setRunButtonText(_('&Clone'))
        self._cmdagent = cmdagent = cmdcore.CmdAgent(ui, self)
        cmdagent.serviceStopped.connect(self.reject)
        self.setCommandWidget(CloneWidget(config, cmdagent, self))
        self.commandFinished.connect(self._emitCloned)

    def source(self) -> str:
        return self.commandWidget().source()

    def setSource(self, url: str) -> None:
        assert self.isCommandFinished()
        self.commandWidget().setSource(url)

    def destination(self) -> str:
        return self.commandWidget().destination()

    def setDestination(self, url: str) -> None:
        assert self.isCommandFinished()
        self.commandWidget().setDestination(url)

    def revSymbol(self) -> str:
        return self.commandWidget().revSymbol()

    def setRevSymbol(self, rev: str) -> None:
        assert self.isCommandFinished()
        self.commandWidget().setRevSymbol(rev)

    def testOption(self, key: str) -> bool:
        return self.commandWidget().testOption(key)

    def setOption(self, key: str, on: bool) -> None:
        assert self.isCommandFinished()
        self.commandWidget().setOption(key, on)

    @pyqtSlot(int)
    def _emitCloned(self, ret: int) -> None:
        if ret == 0:
            self.clonedRepository.emit(self.destination(), self.source())

    def done(self, r: int) -> None:
        if self._cmdagent.isServiceRunning():
            self._cmdagent.stopService()
            return  # postponed until serviceStopped
        super().done(r)
