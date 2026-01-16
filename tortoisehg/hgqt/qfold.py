# qfold.py - QFold dialog for TortoiseHg
#
# Copyright 2010 Steve Borho <steve@borho.org>
# Copyright 2010 Johan Samyn <johan.samyn@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

from typing import (
    List,
)

from .qtcore import (
    QSettings,
    Qt,
    pyqtSlot,
)
from .qtgui import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QShortcut,
    QTextEdit,
    QVBoxLayout,
)

from hgext import mq
from mercurial import (
    pycompat,
)

from ..util import hglib
from ..util.i18n import _
from . import (
    messageentry,
    qscilib,
    qtlib,
)

class QFoldDialog(QDialog):

    def __init__(self, repoagent, patches, parent):
        super().__init__(parent)
        self._repoagent = repoagent
        self.setWindowTitle(_('Patch fold - %s') % repoagent.displayName())
        self.setWindowIcon(qtlib.geticon('hg-qfold'))

        f = self.windowFlags()
        self.setWindowFlags(f & ~Qt.WindowType.WindowContextHelpButtonHint
                            | Qt.WindowType.WindowMaximizeButtonHint)

        self.setLayout(QVBoxLayout())

        mlbl = QLabel(_('New patch message:'))
        self.layout().addWidget(mlbl)
        self.msgte = messageentry.MessageEntry(self)
        self.msgte.installEventFilter(qscilib.KeyPressInterceptor(self))
        self.layout().addWidget(self.msgte)

        self.keepchk = QCheckBox(_('Keep patch files'))
        self.keepchk.setChecked(True)
        self.layout().addWidget(self.keepchk)

        q = self.repo.mq
        q.parseseries()
        patches = [p for p in q.series if p in patches]

        class PatchListWidget(QListWidget):
            def __init__(self, parent):
                QListWidget.__init__(self, parent)
                self.setCurrentRow(0)
            def focusInEvent(self, event):
                i = self.item(self.currentRow())
                if i:
                    self.parent().parent().showSummary(i)
                QListWidget.focusInEvent(self, event)
            def dropEvent(self, event):
                QListWidget.dropEvent(self, event)
                spp = self.parent().parent()
                spp.msgte.setText(spp.composeMsg(self.getPatchList()))
            def getPatchList(self):
                return [hglib.fromunicode(self.item(i).text()) \
                        for i in pycompat.xrange(0, self.count())]

        ugb = QGroupBox(_('Patches to fold'))
        ugb.setLayout(QVBoxLayout())
        ugb.layout().setContentsMargins(*(0,)*4)
        self.ulw = PatchListWidget(self)
        self.ulw.setDragDropMode(QListView.DragDropMode.InternalMove)
        ugb.layout().addWidget(self.ulw)
        self.ulw.currentItemChanged.connect(lambda:
                self.showSummary(self.ulw.item(self.ulw.currentRow())))
        self.layout().addWidget(ugb)

        for p in patches:
            item = QListWidgetItem(hglib.tounicode(p))
            item.setFlags(Qt.ItemFlag.ItemIsSelectable |
                          Qt.ItemFlag.ItemIsEnabled |
                          Qt.ItemFlag.ItemIsDragEnabled)
            self.ulw.addItem(item)

        slbl = QLabel(_('Summary:'))
        self.layout().addWidget(slbl)
        self.summ = QTextEdit()
        self.summ.setFont(qtlib.getfont('fontcomment').font())
        self.summ.setMaximumHeight(80)
        self.summ.setReadOnly(True)
        self.summ.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.layout().addWidget(self.summ)

        bbox = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        self.layout().addWidget(bbox)
        self.bbox = bbox

        QShortcut('Ctrl+Return', self, self.accept)
        QShortcut('Ctrl+Enter', self, self.accept)

        self._repoagent.configChanged.connect(self.configChanged)

        self._readsettings()

        self.msgte.setText(self.composeMsg(patches))
        self.msgte.refresh(self.repo)

    @property
    def repo(self):
        return self._repoagent.rawRepo()

    def showSummary(self, item):
        patchname = hglib.fromunicode(item.text())
        txt = '\n'.join(mq.patchheader(self.repo.mq.join(patchname)).message)
        self.summ.setText(hglib.tounicode(txt))

    def composeMsg(self, patches):
        descs = [hglib.revsymbol(self.repo, b'qtip').description()]
        # lookup of unapplied patches is handled by thgrepo hack
        descs.extend(self.repo[p].description() for p in patches)
        return '\n* * *\n'.join(map(hglib.tounicode, descs))

    @pyqtSlot()
    def configChanged(self):
        '''Repository is reporting its config files have changed'''
        self.msgte.refresh(self.repo)

    def options(self):
        return {'keep': self.keepchk.isChecked(),
                'message': self.msgte.text()}

    def patches(self) -> List[str]:
        return hglib.to_unicode_list(self.ulw.getPatchList())

    def accept(self):
        self._writesettings()
        QDialog.accept(self)

    def closeEvent(self, event):
        self._writesettings()
        super().closeEvent(event)

    def _readsettings(self):
        s = QSettings()
        self.restoreGeometry(qtlib.readByteArray(s, 'qfold/geom'))

    def _writesettings(self):
        s = QSettings()
        s.setValue('qfold/geom', self.saveGeometry())
