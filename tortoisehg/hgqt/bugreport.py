# bugreport.py - Report Python tracebacks to the user
#
# Copyright 2010 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import os
import sys

from .qtcore import (
    PYQT_VERSION_STR,
    QSettings,
    QTimer,
    Qt,
    pyqtSlot,
    qVersion,
)
from .qtgui import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMessageBox,
    QTextBrowser,
    QTextOption,
    QVBoxLayout,
)

from mercurial import (
    encoding,
    extensions,
    vfs as vfsmod,
)
from ..util import (
    hglib,
    hgversion,
    version,
)
from ..util.i18n import _
from . import (
    qtlib,
    updatecheck,
)

try:
    from .qsci import QSCINTILLA_VERSION_STR
except (ImportError, AttributeError, RuntimeError):
    # show BugReport dialog even if QScintilla is missing
    # or incompatible (RuntimeError: the sip module implements API v...)
    QSCINTILLA_VERSION_STR = '(unknown)'

def _safegetcwd():
    try:
        return os.getcwd()
    except OSError:
        return '.'

class BugReport(QDialog):

    def __init__(self, opts, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout()
        self.setLayout(layout)

        compat_msg = hgversion.checkhgversion(hgversion.hgversion)
        bts_url = 'https://foss.heptapod.net/mercurial/tortoisehg/thg/-/issues'

        lbl = QLabel(self)
        if compat_msg:
            lbl.setText(' '.join([
                hglib.tounicode(compat_msg),
                _('<a href="%s">bug tracker</a>') % bts_url,
            ]))
        else:
            lbl.setText(
                _('Please report this bug to our <a href="%s">bug tracker</a>')
                % bts_url)
        lbl.setOpenExternalLinks(True)
        self.layout().addWidget(lbl)

        tb = QTextBrowser()
        self.text = self.gettext(opts)
        tb.setHtml('<pre>' + qtlib.htmlescape(self.text, False) + '</pre>')
        tb.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        layout.addWidget(tb)

        self.download_url_lbl = QLabel(_('Checking for updates...'))
        self.download_url_lbl.setMouseTracking(True)
        self.download_url_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.download_url_lbl.setOpenExternalLinks(True)
        layout.addWidget(self.download_url_lbl)

        # dialog buttons
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Save)
        bb.button(QDialogButtonBox.StandardButton.Ok).clicked.connect(self.accept)
        bb.button(QDialogButtonBox.StandardButton.Save).clicked.connect(self.save)
        bb.button(QDialogButtonBox.StandardButton.Ok).setDefault(True)
        bb.addButton(_('Copy'), QDialogButtonBox.ButtonRole.HelpRole).clicked.connect(self.copyText)
        bb.addButton(_('Quit'), QDialogButtonBox.ButtonRole.DestructiveRole).clicked.connect(QApplication.instance().quit)
        layout.addWidget(bb)

        self.setWindowTitle(_('TortoiseHg Bug Report'))
        self.setWindowFlags(self.windowFlags() & \
                            ~Qt.WindowType.WindowContextHelpButtonHint)
        self.resize(650, 400)
        self._readsettings()

        self._updatechecker = updatecheck.Checker()
        self._updatechecker.updateAvailable.connect(self.updateAvailable)
        self._updatechecker.updateUnavailable.connect(self.updateUnavailable)
        QTimer.singleShot(0, self._updatechecker.query)

    @pyqtSlot(str, str)
    def updateAvailable(self, newverstr, upgradeurl):
        url_lbl = _('Upgrading to a more recent TortoiseHg is recommended.')
        urldata = ('<a href=%s>%s</a>' % (upgradeurl, url_lbl))
        self.download_url_lbl.setText(urldata)

    @pyqtSlot()
    def updateUnavailable(self):
        self.download_url_lbl.setText(_('Your TortoiseHg is up to date.'))

    def gettext(self, opts):
        def ext_with_ver(x):
            ext = x[0]
            ver = extensions.moduleversion(x[1])
            if ver:
                ext += b' ' + ver
            return hglib.tounicode(ext)

        # TODO: make this more uniformly unicode safe
        text = '```python\n'
        text += '** Mercurial version (%s).  TortoiseHg version (%s)\n' % (
                hglib.hgversion, version.version())
        text += '** Command: %s\n' % (hglib.tounicode(opts.get('cmd', 'N/A')))
        text += '** CWD: %s\n' % hglib.tounicode(_safegetcwd())
        text += '** Encoding: %s\n' % hglib._encoding
        extlist = [ext_with_ver(x) for x in sorted(extensions.extensions())]
        text += '** Extensions loaded: %s\n' % ', '.join(extlist)
        text += '** Python version: %s\n' % sys.version.replace('\n', '')
        if os.name == 'nt':
            text += self.getarch()
        elif os.name == 'posix':
            text += '** System: %s\n' % hglib.tounicode(' '.join(os.uname()))
        text += ('** Qt-%s PyQt-%s QScintilla-%s\n'
                 % (qVersion(), PYQT_VERSION_STR, QSCINTILLA_VERSION_STR))
        text += hglib.tounicode(opts.get('error', 'N/A'))
        text += '\n```'
        return text

    def copyText(self):
        QApplication.clipboard().setText(self.text)

    def getarch(self):
        text = '** Windows version: %s\n' % str(
            sys.getwindowsversion())  # pytype: disable=module-attr
        arch = 'unknown (failed to import win32api)'
        try:
            import win32api  # pytype: disable=import-error
            arch = 'unknown'
            archval = win32api.GetNativeSystemInfo()[0]
            if archval == 9:
                arch = 'x64'
            elif archval == 0:
                arch = 'x86'
        except (ImportError, AttributeError):
            pass
        text += '** Processor architecture: %s\n' % arch
        return text

    def save(self):
        try:
            fname, _filter = QFileDialog.getSaveFileName(self,
                        _('Save error report to'),
                        os.path.join(_safegetcwd(), 'bugreport.txt'),
                        _('Text files (*.txt)'))
            if fname:
                with open(fname, 'wb') as fp:
                    fp.write(hglib.fromunicode(self.text))
        except OSError as e:
            QMessageBox.critical(
                self, _('Error writing file'), hglib.exception_str(e)
            )

    def closeEvent(self, event):
        self._updatechecker.close()
        super().closeEvent(event)

    def accept(self):
        self._writesettings()
        super().accept()

    def reject(self):
        self._writesettings()
        super().reject()

    def _readsettings(self):
        s = QSettings()
        self.restoreGeometry(qtlib.readByteArray(s, 'bugreport/geom'))

    def _writesettings(self):
        s = QSettings()
        s.setValue('bugreport/geom', self.saveGeometry())

class ExceptionMsgBox(QDialog):
    """Message box for recoverable exception"""
    def __init__(self, main, text, opts, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.setWindowTitle(_('TortoiseHg Error'))

        self._opts = opts

        labelflags = Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse

        self.setLayout(QVBoxLayout())

        if '%(arg' in text:
            values = opts.get('values', [])
            msgopts = {}
            for i, val in enumerate(values):
                msgopts['arg' + str(i)] = qtlib.htmlescape(hglib.tounicode(val),
                                                           False)
            try:
                text = text % msgopts
            except Exception as e:
                print(e, msgopts)
        else:
            self._mainlabel = QLabel('<b>%s</b>'
                                     % qtlib.htmlescape(main, False),
                                     textInteractionFlags=labelflags)
            self.layout().addWidget(self._mainlabel)

        text = text + "<br><br>" + _('If you still have trouble, '
              '<a href="#bugreport">please file a bug report</a>.')
        self._textlabel = QLabel(text, wordWrap=True,
                                 textInteractionFlags=labelflags)
        self._textlabel.linkActivated.connect(self._openlink)
        self._textlabel.setWordWrap(False)
        self.layout().addWidget(self._textlabel)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, centerButtons=True)
        bb.rejected.connect(self.reject)
        self.layout().addWidget(bb)
        desktopgeom = QApplication.primaryScreen().availableGeometry()
        self.resize(desktopgeom.size() * 0.20)

    @pyqtSlot(str)
    def _openlink(self, ref: str) -> None:
        if ref == '#bugreport':
            BugReport(self._opts, self).exec()
            return
        if ref.startswith('#edit:'):
            fname, lineno = ref[6:].rsplit(':', 1)
            try:
                # A chicken-egg problem here, we need a ui to get your
                # editor in order to repair your ui config file.
                class FakeRepo:
                    def __init__(self):
                        self.root = encoding.getcwd()
                        self.ui = hglib.loadui()
                        self.vfs = vfsmod.vfs(
                            os.path.join(self.root, b'.hg'),
                            expandpath=True,
                            realpath=True
                        )

                    def wjoin(self, f: bytes, *insidef: bytes) -> bytes:
                        return self.vfs.reljoin(self.root, f, *insidef)

                fake = FakeRepo()
                # TODO: a protocol could fix the bogus repo arg type
                # pytype: disable=wrong-arg-types
                qtlib.editfiles(fake, [hglib.fromunicode(fname)], int(lineno),
                                parent=self)
                # pytype: enable=wrong-arg-types
            except Exception as e:
                qtlib.openlocalurl(fname)
        if ref.startswith('#fix:'):
            from tortoisehg.hgqt import settings
            errtext = ref[5:].split(' ')[0]
            sd = settings.SettingsDialog(configrepo=False, focus=errtext,
                                parent=self, root=b'')
            sd.exec()

def run(ui, *pats, **opts):
    return BugReport(opts)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    form = BugReport({'cmd':'cmd', 'error':'error'})
    form.show()
    app.exec()
