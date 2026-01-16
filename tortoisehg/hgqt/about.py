# about.py - About dialog for TortoiseHg
#
# Copyright 2007 TK Soh <teekaysoh@gmail.com>
# Copyright 2007 Steve Borho <steve@borho.org>
# Copyright 2010 Yuki KODAMA <endflow.net@gmail.com>
# Copyright 2010 Johan Samyn <johan.samyn@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.
"""
TortoiseHg About dialog - PyQt4 version
"""

from __future__ import annotations

import sys

from .qtcore import (
    PYQT_VERSION_STR,
    QSettings,
    QSize,
    QTimer,
    Qt,
    pyqtSlot,
    qVersion,
)
from .qtgui import (
    QDialog,
    QDialogButtonBox,
    QFont,
    QLabel,
    QLayout,
    QPixmap,
    QPlainTextEdit,
    QVBoxLayout,
)

from ..util import (
    hglib,
    paths,
    version,
)
from ..util.i18n import _
from . import (
    qtlib,
    updatecheck,
)

class AboutDialog(QDialog):
    """Dialog for showing info about TortoiseHg"""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowIcon(qtlib.geticon('thg'))
        self.setWindowTitle(_('About'))
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self.vbox = QVBoxLayout()
        self.vbox.setSpacing(8)

        self.logo_lbl = QLabel()
        self.logo_lbl.setMinimumSize(QSize(92, 50))
        self.logo_lbl.setScaledContents(False)
        self.logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_lbl.setPixmap(QPixmap(qtlib.iconpath('thg_logo_92x50.png')))
        self.vbox.addWidget(self.logo_lbl)

        self.name_version_libs_lbl = QLabel()
        self.name_version_libs_lbl.setText(' ')
        self.name_version_libs_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_version_libs_lbl.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
        self.vbox.addWidget(self.name_version_libs_lbl)
        self.getVersionInfo()

        self.copyright_lbl = QLabel()
        self.copyright_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.copyright_lbl.setText('\n'
                + _('Copyright 2008-2025 Steve Borho and others'))
        self.vbox.addWidget(self.copyright_lbl)
        self.courtesy_lbl = QLabel()
        self.courtesy_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.courtesy_lbl.setText(
              _('Several icons are courtesy of the TortoiseSVN and Tango projects') + '\n')
        self.vbox.addWidget(self.courtesy_lbl)

        self.download_url_lbl = QLabel()
        self.download_url_lbl.setMouseTracking(True)
        self.download_url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.download_url_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.download_url_lbl.setOpenExternalLinks(True)
        self.download_url_lbl.setText('<a href=%s>%s</a>' %
                                      ('https://tortoisehg.bitbucket.io',
                                       _('You can visit our site here')))
        self.vbox.addWidget(self.download_url_lbl)

        # Extra space between the URL and hosting credits
        self.vbox.addWidget(QLabel())

        self.hosting_lbl = QLabel()
        self.hosting_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hosting_lbl.setText(
            _('Hosting donated by %s and %s') % (
                '<a href=https://clever-cloud.com>Clever Cloud</a>',
                '<a href=https://octobus.net>Octobus</a>'
            )
        )
        self.hosting_lbl.setMouseTracking(True)
        self.hosting_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.hosting_lbl.setOpenExternalLinks(True)
        self.vbox.addWidget(self.hosting_lbl)

        # Let's have some space between the hosting and the buttons.
        self.blancline_lbl = QLabel()
        self.vbox.addWidget(self.blancline_lbl)

        bbox = QDialogButtonBox(self)
        self.license_btn = bbox.addButton(_('&License'),
                                          QDialogButtonBox.ButtonRole.ResetRole)
        self.license_btn.setAutoDefault(False)
        self.license_btn.clicked.connect(self.showLicense)
        self.close_btn = bbox.addButton(QDialogButtonBox.StandardButton.Close)
        self.close_btn.setDefault(True)
        self.close_btn.clicked.connect(self.close)
        self.vbox.addWidget(bbox)

        self.setLayout(self.vbox)
        self.layout().setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        self._readsettings()

        # Spawn it later, so that the dialog gets visible quickly.
        self._updatechecker = updatecheck.Checker()
        self._updatechecker.updateAvailable.connect(self.updateAvailable)
        QTimer.singleShot(1000, self._updatechecker.query)

    def getVersionInfo(self):
        def make_version(tuple):
            vers = ".".join([str(x) for x in tuple])
            return vers
        thgv = (_('version %s') % version.version())
        libv = (_('with Mercurial-%s, Python-%s, PyQt-%s, Qt-%s') %
                (hglib.hgversion, make_version(sys.version_info[0:3]),
              PYQT_VERSION_STR, qVersion()))
        par = ('<p style=\" margin-top:0px; margin-bottom:6px;\">'
                '<span style=\"font-size:%spt; font-weight:600;\">'
                '%s</span></p>')
        name = (par % (14, 'TortoiseHg'))
        thgv = (par % (10, thgv))
        nvl = ''.join([name, thgv, libv])
        self.name_version_libs_lbl.setText(nvl)

    @pyqtSlot(str, str)
    def updateAvailable(self, newverstr, upgradeurl):
        url_lbl = _('A new version of TortoiseHg (%s) '
                    'is ready for download!') % newverstr
        urldata = ('<a href=%s>%s</a>' % (upgradeurl, url_lbl))
        self.download_url_lbl.setText(urldata)

    def showLicense(self):
        ld = LicenseDialog(self)
        ld.exec()

    def closeEvent(self, event):
        self._updatechecker.close()
        self._writesettings()
        super().closeEvent(event)

    def _readsettings(self):
        s = QSettings()
        self.restoreGeometry(qtlib.readByteArray(s, 'about/geom'))

    def _writesettings(self):
        s = QSettings()
        s.setValue('about/geom', self.saveGeometry())


class LicenseDialog(QDialog):
    """Dialog for showing the TortoiseHg license"""
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowIcon(qtlib.geticon('thg'))
        self.setWindowTitle(_('License'))
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.resize(700, 400)

        self.lic_txt = QPlainTextEdit()
        self.lic_txt.setFont(QFont('Monospace'))
        self.lic_txt.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByKeyboard|Qt.TextInteractionFlag.TextSelectableByMouse)
        try:
            with open(paths.get_license_path()) as fp:
                lic = fp.read()
            self.lic_txt.setPlainText(lic)
        except OSError:
            pass

        bbox = QDialogButtonBox(self)
        self.close_btn = bbox.addButton(QDialogButtonBox.StandardButton.Close)
        self.close_btn.clicked.connect(self.close)

        self.vbox = QVBoxLayout()
        self.vbox.setSpacing(6)
        self.vbox.addWidget(self.lic_txt)
        self.vbox.addWidget(bbox)

        self.setLayout(self.vbox)
        self._readsettings()

    def closeEvent(self, event):
        self._writesettings()
        super().closeEvent(event)

    def _readsettings(self):
        s = QSettings()
        self.restoreGeometry(qtlib.readByteArray(s, 'license/geom'))

    def _writesettings(self):
        s = QSettings()
        s.setValue('license/geom', self.saveGeometry())
