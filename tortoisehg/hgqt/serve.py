# serve.py - TortoiseHg dialog to start web server
#
# Copyright 2010 Yuya Nishihara <yuya@tcha.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import os
import tempfile
import typing

from .qtcore import (
    Qt,
    pyqtSlot,
)
from .qtgui import (
    QDialog,
    QSystemTrayIcon,
)

from mercurial import (
    error,
    pycompat,
    util,
)

from ..util import (
    hglib,
    paths,
    wconfig,
)
from ..util.i18n import _
from . import (
    cmdcore,
    cmdui,
    qtlib,
)
from .serve_ui import Ui_ServeDialog
from .webconf import WebconfForm

if typing.TYPE_CHECKING:
    from typing import (
        List,
        Text,
        Tuple,
        Optional,
    )
    from .qtgui import (
        QWidget,
    )
    from mercurial import (
        ui as uimod,
    )
    from ..util.typelib import (
        IniConfig,
    )


class ServeDialog(QDialog):
    """Dialog for serving repositories via web"""
    def __init__(self,
                 ui: uimod.ui,
                 webconf: Optional[IniConfig],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowFlags((self.windowFlags() | Qt.WindowType.WindowMinimizeButtonHint)
                            & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.setWindowIcon(qtlib.geticon('hg-serve'))

        self._qui = Ui_ServeDialog()
        self._qui.setupUi(self)

        self._initwebconf(webconf)
        self._initcmd(ui)
        self._initactions()
        self._updateform()

    def _initcmd(self, ui: uimod.ui) -> None:
        # TODO: forget old logs?
        self._log_edit = cmdui.LogWidget(self)
        self._qui.details_tabs.addTab(self._log_edit, _('Log'))
        # as of hg 3.0, hgweb does not cooperate with command-server channel
        self._agent = cmdcore.CmdAgent(ui, self, worker='proc')
        self._agent.outputReceived.connect(self._log_edit.appendLog)
        self._agent.busyChanged.connect(self._updateform)

    def _initwebconf(self, webconf: Optional[IniConfig]) -> None:
        self._webconf_form = WebconfForm(webconf=webconf, parent=self)
        self._qui.details_tabs.addTab(self._webconf_form, _('Repositories'))

    def _initactions(self) -> None:
        self._qui.start_button.clicked.connect(self.start)
        self._qui.stop_button.clicked.connect(self.stop)

    @pyqtSlot()
    def _updateform(self) -> None:
        """update form availability and status text"""
        self._updatestatus()
        self._qui.start_button.setEnabled(not self.isstarted())
        self._qui.stop_button.setEnabled(self.isstarted())
        self._qui.settings_button.setEnabled(not self.isstarted())
        self._qui.port_edit.setEnabled(not self.isstarted())
        self._webconf_form.setEnabled(not self.isstarted())

    def _updatestatus(self) -> None:
        if self.isstarted():
            # TODO: escape special chars
            link = '<a href="%s">%s</a>' % (self.rooturl, self.rooturl)
            msg = _('Running at %s') % link
        else:
            msg = _('Stopped')

        self._qui.status_edit.setText(msg)

    @pyqtSlot()
    def start(self) -> None:
        """Start web server"""
        if self.isstarted():
            return

        self._agent.runCommand(self._cmdargs())

    def _cmdargs(self) -> List[str]:
        """Build command args to run server"""
        a = ['serve', '--port', str(self.port), '-v']
        if self._singlerepo:
            a += ['-R', self._singlerepo]
        else:
            a += ['--web-conf', self._tempwebconf()]
        return a

    def _tempwebconf(self) -> str:
        """Save current webconf to temporary file; return its path"""
        webconf = self._webconf
        if not hasattr(webconf, 'write'):
            return hglib.tounicode(webconf.path)  # pytype: disable=attribute-error

        assert isinstance(webconf, wconfig._wconfig)  # help pytype

        fd, fname = tempfile.mkstemp(prefix=b'webconf_',
                                     dir=qtlib.gettempdir())
        f = os.fdopen(fd, 'w')
        try:
            webconf.write(f)
            return hglib.tounicode(fname)
        finally:
            f.close()

    @property
    def _webconf(self) -> IniConfig:
        """Selected webconf object"""
        return self._webconf_form.webconf

    @property
    def _singlerepo(self) -> Optional[str]:
        """Return repository path if serving single repository"""
        # TODO: The caller crashes if this returns None with:
        #    `'ServeDialog' object has no attribute '_singlerepo'`
        # NOTE: we cannot use web-conf to serve single repository at '/' path
        if len(self._webconf[b'paths']) != 1:
            return
        path = self._webconf.get(b'paths', b'/')
        if path and b'*' not in path:  # exactly a single repo (no wildcard)
            return hglib.tounicode(path)

    @pyqtSlot()
    def stop(self) -> None:
        """Stop web server"""
        self._agent.abortCommands()

    def reject(self) -> None:
        self.stop()
        super().reject()

    def isstarted(self) -> bool:
        """Is the web server running?"""
        return self._agent.isBusy()

    @property
    def rooturl(self) -> str:
        """Returns the root URL of the web server"""
        # TODO: scheme, hostname ?
        return 'http://localhost:%d' % self.port

    @property
    def port(self) -> int:
        """Port number of the web server"""
        return int(self._qui.port_edit.value())

    def setport(self, port: int) -> None:
        self._qui.port_edit.setValue(port)

    def keyPressEvent(self, event):
        if self.isstarted() and event.key() == Qt.Key.Key_Escape:
            self.stop()
            return

        return super().keyPressEvent(event)

    def closeEvent(self, event):
        if self.isstarted():
            self._minimizetotray()
            event.ignore()
            return

        return super().closeEvent(event)

    @util.propertycache
    def _trayicon(self):
        icon = QSystemTrayIcon(self.windowIcon(), parent=self)
        icon.activated.connect(self._restorefromtray)
        icon.setToolTip(self.windowTitle())
        # TODO: context menu
        return icon

    # TODO: minimize to tray by minimize button

    @pyqtSlot()
    def _minimizetotray(self):
        self._trayicon.show()
        self._trayicon.showMessage(_('TortoiseHg Web Server'),
                                   _('Running at %s') % self.rooturl)
        self.hide()

    @pyqtSlot()
    def _restorefromtray(self):
        self._trayicon.hide()
        self.show()

    @pyqtSlot()
    def on_settings_button_clicked(self):
        from tortoisehg.hgqt import settings
        settings.SettingsDialog(parent=self, focus='web.name').exec()


def _asconfigliststr(value: bytes) -> bytes:
    r"""
    >>> _asconfigliststr(b'foo')
    b'foo'
    >>> _asconfigliststr(b'foo bar')
    b'"foo bar"'
    >>> _asconfigliststr(b'foo,bar')
    b'"foo,bar"'
    >>> _asconfigliststr(b'foo "bar"')
    b'"foo \\"bar\\""'
    """
    # ui.configlist() uses isspace(), which is locale-dependent
    if any(c.isspace() or c == b',' for c in pycompat.iterbytestr(value)):
        return b'"' + value.replace(b'"', b'\\"') + b'"'
    else:
        return value

def _readconfig(ui: uimod.ui,
                repopath: Optional[bytes],
                webconfpath: Optional[bytes]) -> Tuple[uimod.ui, Optional[IniConfig]]:
    """Create new ui and webconf object and read appropriate files"""
    lui = ui.copy()
    if webconfpath:
        lui.readconfig(webconfpath)
        # TODO: handle file not found
        c = wconfig.readfile(webconfpath)
        c.path = os.path.abspath(webconfpath)
        return lui, c
    elif repopath:  # imitate webconf for single repo
        lui.readconfig(os.path.join(repopath, b'.hg', b'hgrc'), repopath)
        c = wconfig.config()
        try:
            if not os.path.exists(os.path.join(repopath, b'.hgsub')):
                # no _asconfigliststr(repopath) for now, because ServeDialog
                # cannot parse it as a list in single-repo mode.
                c.set(b'paths', b'/', repopath)
            else:
                # since hg 8cbb59124e67, path entry is parsed as a list
                base = hglib.shortreponame(lui) or os.path.basename(repopath)
                c.set(b'paths', base,
                      _asconfigliststr(os.path.join(repopath, b'**')))
        except (OSError, error.Abort, error.RepoError):
            c.set(b'paths', b'/', repopath)
        return lui, c
    else:
        return lui, None

def run(ui: uimod.ui, *pats, **opts) -> ServeDialog:
    # TODO: No known caller provides **opts so bytes vs str is unknown
    repopath = opts.get('root') or paths.find_root_bytes()
    webconfpath = opts.get('web_conf') or opts.get('webdir_conf')

    lui, webconf = _readconfig(ui, repopath, webconfpath)
    dlg = ServeDialog(lui, webconf=webconf)
    try:
        dlg.setport(int(lui.config(b'web', b'port')))
    except ValueError:
        pass

    if repopath or webconfpath:
        dlg.start()
    return dlg
