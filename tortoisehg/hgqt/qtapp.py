# qtapp.py - utility to start Qt application
#
# Copyright 2008 Steve Borho <steve@borho.org>
# Copyright 2008 TK Soh <teekaysoh@gmail.com>
# Copyright (C) 2026 Peter Demcak <majster64@gmail.com> (dark theme)
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import gc
import os
import signal
import sys
import traceback

from .qtcore import (
    PYQT_VERSION,
    PYQT_VERSION_STR,
    QByteArray,
    QEvent,
    QIODevice,
    QLibraryInfo,
    QObject,
    QSettings,
    QSignalMapper,
    QSocketNotifier,
    QT_API,
    QT_VERSION,
    QTimer,
    QTranslator,
    Qt,
    pyqtSignal,
    pyqtSlot,
    qVersion,
)
from .qtgui import (
    QApplication,
    QDialog,
    QMainWindow,
    QFont,
    QWidget
)
from .qtnetwork import (
    QLocalServer,
    QLocalSocket,
)

from mercurial import (
    encoding,
    error,
)
from mercurial.utils import (
    procutil,
    stringutil,
)

from ..util import (
    hglib,
    i18n,
    version as thgversion,
)
from ..util.i18n import _
from . import (
    bugreport,
    hgconfig,
    qtlib,
    shortcutregistry,
    thgrepo,
    workbench,
)
from .theme import THEME

if os.name == 'nt' and getattr(sys, 'frozen', False):
    # load QtSvg4.dll and QtXml4.dll by .pyd, so that imageformats/qsvg4.dll
    # can find them without relying on unreliable PATH variable. The filenames
    # change for PyQt5 but the basic problem remains the same.
    _mod = __import__(QT_API, globals(), locals(), ['QtSvg', 'QtXml'])
    _mod.QtSvg.__name__, _mod.QtXml.__name__  # no demandimport

# TODO: replace QT_VERSION, because it is the build time version, not
#  necessarily the runtime version.
if PYQT_VERSION < 0x50b00 or QT_VERSION < 0x50b00:
    sys.stderr.write('TortoiseHg requires at least Qt 5.11 and PyQt 5.11\n')
    sys.stderr.write('You have Qt %s and PyQt %s\n' %
                     (qVersion(), PYQT_VERSION_STR))
    sys.exit(-1)

if getattr(sys, 'frozen', False) and os.name == 'nt':
    # load icons and translations
    from . import icons_rc, translations_rc  # pytype: disable=import-error

try:
    from thginithook import thginithook  # pytype: disable=import-error
except ImportError:
    thginithook = None

def _ugetuser():
    return hglib.tounicode(procutil.getuser())

# {exception class: message}
# It doesn't check the hierarchy of exception classes for simplicity.
_recoverableexc = {
    error.RepoLookupError: _('Try refreshing your repository.'),
    error.RevlogError:     _('Try refreshing your repository.'),
    error.ParseError: _('Error string "%(arg0)s" at %(arg1)s<br>Please '
                        '<a href="#edit:%(arg1)s">edit</a> your config'),
    error.ConfigError: _('Configuration Error: "%(arg0)s",<br>Please '
                         '<a href="#fix:%(arg0)s">fix</a> your config'),
    error.Abort: _('Operation aborted:<br><br>%(arg0)s.'),
    error.LockUnavailable: _('Repository is locked'),
    }

def earlyExceptionMsgBox(e):
    """Show message for recoverable error before the QApplication is started"""
    opts = {'cmd': ' '.join(sys.argv[1:]),
            'values': [hglib.exception_str(e)],
            'error': traceback.format_exc(),
            'nofork': True}
    errstring = _recoverableexc[e.__class__]
    if isinstance(e, error.ConfigError) and e.location:
        # If ConfigError contained a location, the config file couldn't be
        # parsed at all. So '#edit:<location>' is the only way to fix.
        errstring = _recoverableexc[error.ParseError]
        opts['values'] += [hglib.tounicode(e.location)]
    elif isinstance(e, error.ParseError) and e.location:
        opts['values'] += [hglib.tounicode(e.location)]
    if not QApplication.instance():
        main = QApplication(sys.argv)
    dlg = bugreport.ExceptionMsgBox(hglib.exception_str(e), errstring, opts)
    dlg.exec()

def earlyBugReport(e):
    """Show generic errors before the QApplication is started"""
    opts = {'cmd': ' '.join(sys.argv[1:]),
            'error': traceback.format_exc()}
    if not QApplication.instance():
        main = QApplication(sys.argv)
    dlg = bugreport.BugReport(opts)
    dlg.exec()

class ExceptionCatcher(QObject):
    """Catch unhandled exception raised inside Qt event loop"""

    _exceptionOccured = pyqtSignal(object, object, object)

    def __init__(self, ui, mainapp, parent=None):
        super().__init__(parent)
        self._ui = ui
        self._mainapp = mainapp
        self.errors = []

        # can be emitted by another thread; postpones it until next
        # eventloop of main (GUI) thread.
        self._exceptionOccured.connect(self.putexception,
                                       Qt.ConnectionType.QueuedConnection)

        self._origexcepthook = None
        if not self._ui.configbool(b'tortoisehg', b'traceback'):
            self._ui.debug(b'setting up excepthook\n')
            self._origexcepthook = sys.excepthook
            sys.excepthook = self.ehook

        self._originthandler = signal.signal(signal.SIGINT, self._inthandler)
        self._initWakeup()

    def release(self):
        if self._origexcepthook:
            self._ui.debug(b'restoring excepthook\n')
            sys.excepthook = self._origexcepthook
            self._origexcepthook = None
        if self._originthandler:
            signal.signal(signal.SIGINT, self._originthandler)
            self._originthandler = None
            self._releaseWakeup()

    def ehook(self, etype, evalue, tracebackobj):
        'Will be called by any thread, on any unhandled exception'
        if self._ui.debugflag:
            elist = traceback.format_exception(etype, evalue, tracebackobj)
            self._ui.debug(encoding.strtolocal(''.join(elist)))
        self._exceptionOccured.emit(etype, evalue, tracebackobj)
        # not thread-safe to touch self.errors here

    @pyqtSlot(object, object, object)
    def putexception(self, etype, evalue, tracebackobj):
        'Enque exception info and display it later; run in main thread'
        if not self.errors:
            QTimer.singleShot(10, self.excepthandler)
        self.errors.append((etype, evalue, tracebackobj))

    @pyqtSlot()
    def excepthandler(self):
        'Display exception info; run in main (GUI) thread'
        try:
            self._showexceptiondialog()
        except:
            # make sure to quit mainloop first, so that it never leave
            # zombie process.
            self._mainapp.exit(1)
            self._printexception()
        finally:
            self.errors = []

    def _showexceptiondialog(self):
        opts = {'cmd': ' '.join(sys.argv[1:]),
                'error': ''.join(''.join(traceback.format_exception(*args))
                                 for args in self.errors)}
        etype, evalue = self.errors[0][:2]
        parent = self._mainapp.activeWindow()
        if (len({e[0] for e in self.errors}) == 1
            and etype in _recoverableexc):
            opts['values'] = [hglib.exception_str(evalue)]
            errstr = _recoverableexc[etype]
            if etype is error.Abort and evalue.hint:
                errstr = ''.join([errstr, '<br><b>', _('hint:'),
                                   '</b> %(arg1)s'])
                opts['values'] = [
                    hglib.exception_str(evalue), hglib.tounicode(evalue.hint)
                ]
            elif etype is error.ConfigError and evalue.location:
                # If ConfigError contained a location, the config file couldn't
                # be parsed at all. So '#edit:<location>' is the only way to
                # fix.
                errstr = _recoverableexc[error.ParseError]
                opts['values'] += [hglib.tounicode(evalue.location)]
            elif etype is error.ParseError and evalue.location:
                opts['values'] += [hglib.tounicode(evalue.location)]

            dlg = bugreport.ExceptionMsgBox(hglib.exception_str(evalue),
                                            errstr, opts, parent=parent)
            dlg.exec()
        else:
            dlg = bugreport.BugReport(opts, parent=parent)
            dlg.exec()

    def _printexception(self):
        for args in self.errors:
            traceback.print_exception(*args)

    def _inthandler(self, signum, frame):
        # QTimer makes sure to not enter new event loop in signal handler,
        # which will be invoked at random location.  Note that some windows
        # may show modal confirmation dialog in closeEvent().
        QTimer.singleShot(0, self._mainapp.closeAllWindows)

    if os.name == 'posix':
        # Wake up Python interpreter via pipe so that SIGINT can be handled
        # immediately.  (https://doc.qt.io/qt-4.8/unix-signals.html)

        def _initWakeup(self):
            import fcntl
            rfd, wfd = os.pipe()
            for fd in (rfd, wfd):
                flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            self._wakeupsn = QSocketNotifier(rfd, QSocketNotifier.Type.Read, self)
            self._wakeupsn.activated.connect(self._handleWakeup)
            self._origwakeupfd = signal.set_wakeup_fd(wfd)

        def _releaseWakeup(self):
            self._wakeupsn.setEnabled(False)
            rfd = int(self._wakeupsn.socket())
            wfd = signal.set_wakeup_fd(self._origwakeupfd)
            self._origwakeupfd = -1
            os.close(rfd)
            os.close(wfd)

        @pyqtSlot()
        def _handleWakeup(self):
            # here Python signal handler will be invoked
            self._wakeupsn.setEnabled(False)
            rfd = int(self._wakeupsn.socket())
            try:
                os.read(rfd, 1)
            except OSError as inst:
                self._ui.debug(b'failed to read wakeup fd: %s\n'
                               % stringutil.forcebytestr(inst))
            self._wakeupsn.setEnabled(True)

    else:
        # On Windows, non-blocking anonymous pipe or socket is not available.
        # So run Python instruction at a regular interval.  Because it wastes
        # CPU time, it is disabled if thg is known to be detached from tty.

        def _initWakeup(self):
            self._wakeuptimer = 0
            if self._ui._isatty(self._ui.fin):
                self._wakeuptimer = self.startTimer(200)

        def _releaseWakeup(self):
            if self._wakeuptimer > 0:
                self.killTimer(self._wakeuptimer)
                self._wakeuptimer = 0

        def timerEvent(self, event):
            # nop for instant SIGINT handling
            pass

def is_windows_11():
    if sys.platform != 'win32':
        return False
    # Windows 11 reports build >= 22000
    return int(platform.version().split('.')[-1]) >= 22000

def qcolor_to_bgr_dword(color: QColor) -> int:
    return (color.blue() << 16) | (color.green() << 8) | color.red()

def enable_dark_title_bar(window):
    if sys.platform != 'win32':
        return

    try:
        hwnd = int(window.winId())
    except Exception:
        return

    try:
        import ctypes
        from ctypes import wintypes
        import platform

        dwmapi = ctypes.WinDLL("dwmapi")
        user32 = ctypes.WinDLL("user32")
        TRUE = wintypes.BOOL(1)

        # Immersive dark mode (Win10/11)
        dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), wintypes.DWORD(20), ctypes.byref(TRUE), ctypes.sizeof(TRUE))
        dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), wintypes.DWORD(19), ctypes.byref(TRUE), ctypes.sizeof(TRUE))

        build = int(platform.version().split(".")[-1])

        if build >= 22000:
            bg = wintypes.DWORD(qcolor_to_bgr_dword(THEME.titlebar_background))
            fg = wintypes.DWORD(qcolor_to_bgr_dword(THEME.titlebar_text))
            dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), wintypes.DWORD(35), ctypes.byref(bg), ctypes.sizeof(bg))
            dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), wintypes.DWORD(36), ctypes.byref(fg), ctypes.sizeof(fg))

        # Force non-client frame refresh
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_NOZORDER = 0x0004
        SWP_NOACTIVATE = 0x0010
        SWP_FRAMECHANGED = 0x0020

        user32.SetWindowPos(
            wintypes.HWND(hwnd), None, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
        )

        RDW_INVALIDATE = 0x0001
        RDW_UPDATENOW = 0x0100
        RDW_FRAME = 0x0400
        user32.RedrawWindow(wintypes.HWND(hwnd), None, None, RDW_INVALIDATE | RDW_UPDATENOW | RDW_FRAME)

        # Win10 workaround: force NC activation refresh
        WM_NCACTIVATE = 0x0086
        user32.SendMessageW(wintypes.HWND(hwnd), WM_NCACTIVATE, wintypes.WPARAM(0), wintypes.LPARAM(0))
        user32.SendMessageW(wintypes.HWND(hwnd), WM_NCACTIVATE, wintypes.WPARAM(1), wintypes.LPARAM(0))

    except Exception:
        pass

class DarkTitleBarFilter(QObject):
    def __init__(self):
        super().__init__()
        self._applied_hwnds = set()

    def _apply_once(self, w):
        try:
            hwnd = int(w.winId())
        except Exception:
            return

        if hwnd in self._applied_hwnds:
            return

        self._applied_hwnds.add(hwnd)

        try:
            enable_dark_title_bar(w)
        except Exception:
            pass

    def eventFilter(self, obj, event):
        if not isinstance(obj, QWidget):
            return False

        if not obj.isWindow():
            return False

        et = event.type()

        if et == QEvent.Type.Show:
            if isinstance(obj, QMainWindow):
                self._apply_once(obj)
            elif isinstance(obj, QDialog):
                QTimer.singleShot(0, lambda w=obj: self._apply_once(w))

        elif et == QEvent.Type.WindowActivate:
            if isinstance(obj, QDialog):
                QTimer.singleShot(0, lambda w=obj: self._apply_once(w))

        return False

class GarbageCollector(QObject):
    '''
    Disable automatic garbage collection and instead collect manually
    every INTERVAL milliseconds.

    This is done to ensure that garbage collection only happens in the GUI
    thread, as otherwise Qt can crash.
    '''

    INTERVAL = 5000

    def __init__(self, ui, parent):
        QObject.__init__(self, parent)
        self._ui = ui

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check)

        self.threshold = gc.get_threshold()
        gc.disable()
        self.timer.start(self.INTERVAL)
        #gc.set_debug(gc.DEBUG_SAVEALL)

    def check(self):
        l0, l1, l2 = gc.get_count()
        if l0 > self.threshold[0]:
            num = gc.collect(0)
            self._ui.debug(b'GarbageCollector.check: %d %d %d\n' % (l0, l1, l2))
            self._ui.debug(b'collected gen 0, found %d unreachable\n' % num)
            if l1 > self.threshold[1]:
                num = gc.collect(1)
                self._ui.debug(b'collected gen 1, found %d unreachable\n' % num)
                if l2 > self.threshold[2]:
                    num = gc.collect(2)
                    self._ui.debug(b'collected gen 2, found %d unreachable\n'
                                   % num)

    def debug_cycles(self):
        gc.collect()
        for obj in gc.garbage:
            self._ui.debug(
                hglib.fromunicode('%s, %r, %s\n' % (obj, obj, type(obj)))
            )


def allowSetForegroundWindow(processid=-1):
    """Allow a given process to set the foreground window"""
    # processid = -1 means ASFW_ANY (i.e. allow any process)
    if os.name == 'nt':
        # on windows we must explicitly allow bringing the main window to
        # the foreground. To do so we must use ctypes
        try:
            from ctypes import windll  # pytype: disable=import-error
            windll.user32.AllowSetForegroundWindow(processid)
        except ImportError:
            pass

def connectToExistingWorkbench(root, revset=None):
    """
    Connect and send data to an existing workbench server

    For the connection to be successful, the server must loopback the data
    that we send to it.

    Normally the data that is sent will be a repository root path, but we can
    also send "echo" to check that the connection works (i.e. that there is a
    server)
    """
    if revset:
        data = b'\0'.join([root, revset])
    else:
        data = root
    servername = QApplication.applicationName() + '-' + _ugetuser()
    socket = QLocalSocket()
    socket.connectToServer(servername, QIODevice.OpenModeFlag.ReadWrite)
    if socket.waitForConnected(10000):
        # Momentarily let any process set the foreground window
        # The server process with revoke this permission as soon as it gets
        # the request
        allowSetForegroundWindow()
        socket.write(QByteArray(data))
        socket.flush()
        socket.waitForReadyRead(10000)
        reply = socket.readAll()
        if data == reply:
            return True
    elif socket.error() == QLocalSocket.LocalSocketError.ConnectionRefusedError:
        # last server process was crashed?
        QLocalServer.removeServer(servername)
    return False


def _fixapplicationfont(ui):
    if (os.name != 'nt'
        or QT_VERSION >= 0x060000
        or not ui.configbool(b'experimental', b'thg.fix-app-font')):
        return
    try:
        import ctypes, win32con  # pytype: disable=import-error
    except ImportError:
        return

    class LOGFONTW(ctypes.Structure):
        _fields_ = [
            ('lfHeight', ctypes.wintypes.LONG),
            ('lfWidth', ctypes.wintypes.LONG),
            ('lfEscapement', ctypes.wintypes.LONG),
            ('lfOrientation', ctypes.wintypes.LONG),
            ('lfWeight', ctypes.wintypes.LONG),
            ('lfItalic', ctypes.wintypes.BYTE),
            ('lfUnderline', ctypes.wintypes.BYTE),
            ('lfStrikeOut', ctypes.wintypes.BYTE),
            ('lfCharSet', ctypes.wintypes.BYTE),
            ('lfOutPrecision', ctypes.wintypes.BYTE),
            ('lfClipPrecision', ctypes.wintypes.BYTE),
            ('lfQuality', ctypes.wintypes.BYTE),
            ('lfPitchAndFamily', ctypes.wintypes.BYTE),
            ('lfFaceName', ctypes.wintypes.WCHAR * 32),
        ]

    class NONCLIENTMETRICSW(ctypes.Structure):
        _fields_ = [
            ('cbSize', ctypes.wintypes.UINT),
            ('iBorderWidth', ctypes.c_int),
            ('iScrollWidth', ctypes.c_int),
            ('iScrollHeight', ctypes.c_int),
            ('iCaptionWidth', ctypes.c_int),
            ('iCaptionHeight', ctypes.c_int),
            ('lfCaptionFont', LOGFONTW),
            ('iSmCaptionWidth', ctypes.c_int),
            ('iSmCaptionHeight', ctypes.c_int),
            ('lfSmCaptionFont', LOGFONTW),
            ('iMenuWidth', ctypes.c_int),
            ('iMenuHeight', ctypes.c_int),
            ('lfMenuFont', LOGFONTW),
            ('lfStatusFont', LOGFONTW),
            ('lfMessageFont', LOGFONTW),
            ('iPaddedBorderWidth', ctypes.c_int),
        ]

    ncm = NONCLIENTMETRICSW()
    size = ctypes.sizeof(ncm)
    ncm.cbSize = size

    user32 = ctypes.windll.user32  # pytype: disable=module-attr

    # use configurable font like GTK, Mozilla XUL or Eclipse SWT
    if not user32.SystemParametersInfoForDpi(
        win32con.SPI_GETNONCLIENTMETRICS, size, ctypes.byref(ncm), 0, 96
    ):
        return

    lf = ncm.lfMessageFont
    f = QFont(hglib.tounicode(lf.lfFaceName))
    f.setItalic(lf.lfItalic)
    if lf.lfWeight != win32con.FW_DONTCARE:
        weights = [(0, QFont.Weight.Light), (400, QFont.Weight.Normal), (600, QFont.Weight.DemiBold),
                   (700, QFont.Weight.Bold), (800, QFont.Weight.Black)]
        n, w = [e for e in weights if e[0] <= lf.lfWeight][-1]
        f.setWeight(w)
    f.setPointSizeF((abs(lf.lfHeight) * 72.0) / 96.0)
    QApplication.setFont(f, 'QWidget')

def _gettranslationpath():
    """Return path to Qt's translation file (.qm)"""
    if getattr(sys, 'frozen', False) and os.name == 'nt':
        return ':/translations'
    # pytype: disable=attribute-error
    elif QT_API == 'PyQt5':
        # QLibraryInfo.LibraryPath.PluginsPath is not available with
        # PyQt 5.13.2/Qt 5.9.9
        return QLibraryInfo.location(QLibraryInfo.TranslationsPath)
    else:
        return QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    # pytype: enable=attribute-error

class QtRunner(QObject):
    """Run Qt app and hold its windows

    NOTE: This object will be instantiated before QApplication, it means
    there's a limitation on Qt's event handling. See
    https://doc.qt.io/qt-4.8/threads-qobject.html#per-thread-event-loop
    """

    def __init__(self):
        super().__init__()
        self._ui = None
        self._config = None
        self._mainapp = None
        self._exccatcher = None
        self._actionregistry = None
        self._server = None
        self._repomanager = None
        self._reporeleaser = None
        self._mainreporoot = None
        self._workbench = None

    def __call__(self, dlgfunc, ui, *args, **opts):
        if self._mainapp:
            self._opendialog(dlgfunc, args, opts)
            return

        QSettings.setDefaultFormat(QSettings.Format.IniFormat)

        self._ui = ui
        self._config = hgconfig.HgConfig(ui)

        if QT_API == "PyQt5":
            # pytype: disable=attribute-error
            QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
            QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
            # pytype: enable=attribute-error

        if sys.platform == 'darwin':
            QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, True)

        self._mainapp = QApplication(sys.argv)

        if THEME.enabled:
            workbench.apply_dark_palette(self._mainapp)
            base = self._mainapp.setStyle("Fusion") # Needed for comboboxes and checkboxes
            self._mainapp.setStyle(workbench.DarkItemViewCheckStyle(base)) # Custom checkbox style for HgFileListView
            self._mainapp.setStyleSheet(workbench.build_dark_stylesheet(THEME))
            if sys.platform == 'win32':
                self._dark_titlebar_filter = DarkTitleBarFilter()
                self._mainapp.installEventFilter(self._dark_titlebar_filter)

        self._exccatcher = ExceptionCatcher(ui, self._mainapp, self)
        self._gc = GarbageCollector(ui, self)

        # default org is used by QSettings
        self._mainapp.setApplicationName('TortoiseHgQt')
        self._mainapp.setOrganizationName('TortoiseHg')
        self._mainapp.setOrganizationDomain('tortoisehg.org')
        self._mainapp.setApplicationVersion(thgversion.version())
        if hasattr(self._mainapp, 'setDesktopFileName'):  # introduced in Qt 5.7
            self._mainapp.setDesktopFileName('thg')
        self._fixlibrarypaths()
        self._installtranslator()
        QFont.insertSubstitutions('monospace', ['monaco', 'courier new'])
        _fixapplicationfont(ui)
        qtlib.configstyles(ui)
        qtlib.initfontcache(ui)
        self._mainapp.setWindowIcon(qtlib.geticon('thg'))

        self._actionregistry = shortcutregistry.ActionRegistry()
        self._actionregistry.readSettings()

        self._repomanager = thgrepo.RepoManager(ui, self)
        self._reporeleaser = releaser = QSignalMapper(self)

        qtlib.getMappedStringSignal(releaser).connect(
            self._repomanager.releaseRepoAgent
        )

        # stop services after control returns to the main event loop
        self._mainapp.setQuitOnLastWindowClosed(False)
        self._mainapp.lastWindowClosed.connect(self._quitGracefully,
                                               Qt.ConnectionType.QueuedConnection)

        dlg, reporoot = self._createdialog(dlgfunc, args, opts)
        self._mainreporoot = reporoot
        try:
            if dlg:
                dlg.show()
                dlg.raise_()
            else:
                if reporoot:
                    self._repomanager.releaseRepoAgent(reporoot)
                    self._mainreporoot = None
                return -1

            if thginithook is not None:
                thginithook()

            return self._mainapp.exec()
        finally:
            self._exccatcher.release()
            self._mainapp = self._ui = self._config = None

    @pyqtSlot()
    def _quitGracefully(self):
        # won't be called if the application is quit by BugReport dialog
        if self._mainreporoot:
            self._repomanager.releaseRepoAgent(self._mainreporoot)
            self._mainreporoot = None
        if self._server:
            self._server.close()
        if self._tryQuit():
            return
        self._ui.debug(b'repositories are closing asynchronously\n')
        self._repomanager.repositoryClosed.connect(self._tryQuit)
        QTimer.singleShot(5000, self._mainapp.quit)  # in case of bug

    @pyqtSlot()
    def _tryQuit(self):
        if self._repomanager.repoRootPaths():
            return False
        self._mainapp.quit()
        return True

    def _fixlibrarypaths(self):
        # make sure to use the bundled Qt plugins to avoid ABI incompatibility
        # https://doc.qt.io/qt-4.8/deployment-windows.html#qt-plugins
        if os.name == 'nt' and getattr(sys, 'frozen', False):
            self._mainapp.setLibraryPaths([self._mainapp.applicationDirPath()])

    def _installtranslator(self):
        if not i18n.language:
            return
        t = QTranslator(self._mainapp)
        t.load('qt_' + i18n.language, _gettranslationpath())
        self._mainapp.installTranslator(t)

    def _createdialog(self, dlgfunc, args, opts):
        assert self._ui and self._repomanager
        reporoot = None
        try:
            args = list(args)
            if 'repository' in opts:
                repoagent = self._repomanager.openRepoAgent(
                    hglib.tounicode(opts['repository']))
                reporoot = repoagent.rootPath()
                args.insert(0, repoagent)
            return dlgfunc(self._ui, *args, **opts), reporoot
        except error.RepoError as inst:
            qtlib.WarningMsgBox(_('Repository Error'),
                                hglib.tounicode(stringutil.forcebytestr(inst)))
        except error.Abort as inst:
            qtlib.WarningMsgBox(_('Abort'),
                                hglib.tounicode(stringutil.forcebytestr(inst)),
                                hglib.tounicode(inst.hint or ''))
        if reporoot:
            self._repomanager.releaseRepoAgent(reporoot)
        return None, None

    def _opendialog(self, dlgfunc, args, opts):
        dlg, reporoot = self._createdialog(dlgfunc, args, opts)
        if not dlg:
            return

        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if reporoot:
            dlg.destroyed.connect(self._reporeleaser.map)
            self._reporeleaser.setMapping(dlg, reporoot)
        if dlg is not self._workbench and not dlg.parent():
            # keep reference to avoid garbage collection.  workbench should
            # exist when run.dispatch() is called for the second time.
            assert self._workbench
            dlg.setParent(self._workbench, dlg.windowFlags())
        dlg.show()

    def actionRegistry(self) -> shortcutregistry.ActionRegistry:
        assert self._actionregistry
        return self._actionregistry

    def createWorkbench(self):
        """Create Workbench window and keep single reference"""
        assert self._ui and self._config and self._mainapp and self._repomanager
        assert self._actionregistry
        assert not self._workbench
        self._workbench = workbench.Workbench(
            self._ui, self._config, self._actionregistry, self._repomanager)
        return self._workbench

    @pyqtSlot(str)
    def openRepoInWorkbench(self, uroot):
        """Show the specified repository in Workbench; reuses the existing
        Workbench process"""
        assert self._config
        singlewb = self._config.configBool('tortoisehg', 'workbench.single')
        # only if the server is another process; otherwise it would deadlock
        if (singlewb and not self._server
            and connectToExistingWorkbench(hglib.fromunicode(uroot))):
            return
        self.showRepoInWorkbench(uroot)

    def showRepoInWorkbench(self, uroot, rev=-1):
        """Show the specified repository in Workbench"""
        assert self._mainapp
        if not self._workbench:
            self.createWorkbench()
            assert self._workbench

        wb = self._workbench
        wb.show()
        wb.activateWindow()
        wb.raise_()
        wb.showRepo(uroot)
        if rev != -1:
            wb.goto(hglib.fromunicode(uroot), rev)

    def createWorkbenchServer(self):
        assert self._mainapp
        assert not self._server
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._handleNewConnection)
        self._server.listen(self._mainapp.applicationName() + '-' + _ugetuser())

    @pyqtSlot()
    def _handleNewConnection(self):
        socket = self._server.nextPendingConnection()
        if socket:
            socket.waitForReadyRead(10000)
            data = bytes(socket.readAll())
            if data and data != b'[echo]':
                args = data.split(b'\0', 1)
                if len(args) > 1:
                    uroot, urevset = map(hglib.tounicode, args)
                else:
                    uroot = hglib.tounicode(args[0])
                    urevset = None
                self.showRepoInWorkbench(uroot)

                wb = self._workbench
                if urevset:
                    wb.setRevsetFilter(uroot, urevset)

                # Bring the workbench window to the front
                # This assumes that the client process has
                # called allowSetForegroundWindow(-1) right before
                # sending the request
                wb.setWindowState(wb.windowState() & ~Qt.WindowState.WindowMinimized
                                  | Qt.WindowState.WindowActive)
                wb.show()
                wb.raise_()
                wb.activateWindow()
                # Revoke the blanket permission to set the foreground window
                allowSetForegroundWindow(os.getpid())

            socket.write(QByteArray(data))
            socket.flush()
