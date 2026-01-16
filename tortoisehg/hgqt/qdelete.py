# qdelete.py - QDelete dialog for TortoiseHg
#
# Copyright 2010 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

from .qtcore import (
    QSettings,
    Qt,
)
from .qtgui import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)

from ..util.i18n import _
from . import qtlib

class QDeleteDialog(QDialog):

    def __init__(self, patches, parent):
        super().__init__(parent)
        self.setWindowTitle(_('Delete Patches'))
        self.setWindowIcon(qtlib.geticon('hg-qdelete'))
        self.setWindowFlags(self.windowFlags()
                            & ~Qt.WindowType.WindowContextHelpButtonHint)

        self.setLayout(QVBoxLayout())

        msg = _('Remove patches from queue?')
        patchesu = '<li>'.join(patches)
        lbl = QLabel('<b>%s<ul><li>%s</ul></b>' % (msg, patchesu))
        self.layout().addWidget(lbl)

        self._keepchk = QCheckBox(_('Keep patch files'))
        self.layout().addWidget(self._keepchk)

        bbox = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        self.layout().addWidget(bbox)
        self._readSettings()

    def _readSettings(self):
        qs = QSettings()
        qs.beginGroup('qdelete')
        self._keepchk.setChecked(qtlib.readBool(qs, 'keep', True))
        qs.endGroup()

    def _writeSettings(self):
        qs = QSettings()
        qs.beginGroup('qdelete')
        qs.setValue('keep', self._keepchk.isChecked())
        qs.endGroup()

    def accept(self):
        self._writeSettings()
        super().accept()

    def options(self):
        return {'keep': self._keepchk.isChecked()}
