# Copyright (c) 2009-2010 LOGILAB S.A. (Paris, FRANCE).
# http://www.logilab.fr/ -- mailto:contact@logilab.fr
# Copyright (C) 2026 Peter Demcak <majster64@gmail.com> (dark theme)
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

from __future__ import annotations

import typing

from .qtcore import (
    QModelIndex,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from .qtgui import (
    QAbstractItemView,
    QBrush,
    QMessageBox,
    QSizePolicy,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..util import hglib
from . import (
    manifestmodel,
    qtlib,
)

if typing.TYPE_CHECKING:
    from typing import (
        List,
        Optional,
    )
    from .qtcore import (
        QAbstractItemModel,
    )

from .theme import THEME

class PreserveStatusColorDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        if qtlib.stateValue(opt.state) & qtlib.QtStateFlag.State_Selected:
            # preserve per-item foreground color
            fg = index.data(qtlib.QtItemDataRole.ForegroundRole)
            if isinstance(fg, QBrush):
                brush = fg
            elif fg is not None:
                brush = QBrush(fg)
            else:
                brush = opt.palette.brush(qtlib.QtPaletteRole.Text)

            # critical for keyboard selection
            opt.palette.setBrush(qtlib.QtPaletteRole.HighlightedText, brush)
            opt.palette.setBrush(qtlib.QtPaletteRole.Text, brush)

        super().paint(painter, opt, index)

class HgFileListView(QTreeView):
    """Display files and statuses between two revisions or patch"""

    fileSelected = pyqtSignal(str, str)
    clearDisplay = pyqtSignal()

    def __init__(self, parent: Optional[QWidget]) -> None:
        QTreeView.__init__(self, parent)
        self.setHeaderHidden(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setRootIsDecorated(False)
        self.setTextElideMode(Qt.TextElideMode.ElideLeft)

        # give consistent height and enable optimization
        self.setIconSize(qtlib.smallIconSize())
        self.setUniformRowHeights(True)

        if THEME.enabled:
            self.setItemDelegate(PreserveStatusColorDelegate(self))
            qtlib.applyCustomScrollBars(self)

    def _model(self) -> manifestmodel.ManifestModel:
        model = self.model()
        assert isinstance(model, manifestmodel.ManifestModel)
        return model

    def setModel(self, model: QAbstractItemModel) -> None:
        assert isinstance(model, manifestmodel.ManifestModel)
        QTreeView.setModel(self, model)
        model.layoutChanged.connect(self._onLayoutChanged)
        model.revLoaded.connect(self._onRevLoaded)
        self.selectionModel().currentRowChanged.connect(self._emitFileChanged)

    def currentFile(self) -> bytes:
        index = self.currentIndex()
        model = self._model()
        return hglib.fromunicode(model.filePath(index))

    def setCurrentFile(self, path: bytes) -> None:
        model = self._model()
        model.fetchMore(QModelIndex())  # make sure path is populated
        self.setCurrentIndex(model.indexFromPath(hglib.tounicode(path)))

    def getSelectedFiles(self) -> List[bytes]:
        model = self._model()
        return [hglib.fromunicode(model.filePath(index))
                for index in self.selectedRows()]

    def _initCurrentIndex(self) -> None:
        m = self._model()
        if m.rowCount() > 0:
            self.setCurrentIndex(m.index(0, 0))
        else:
            self.clearDisplay.emit()

    @pyqtSlot()
    def _onLayoutChanged(self) -> None:
        index = self.currentIndex()
        if index.isValid():
            self.scrollTo(index)
            return
        self._initCurrentIndex()

    @pyqtSlot()
    def _onRevLoaded(self) -> None:
        index = self.currentIndex()
        if index.isValid():
            # redisplay previous row
            self._emitFileChanged()
        else:
            self._initCurrentIndex()

    @pyqtSlot()
    def _emitFileChanged(self) -> None:
        index = self.currentIndex()
        m = self._model()
        if index.isValid():
            # TODO: delete status from fileSelected because it isn't primitive
            # pseudo directory node has no status
            st = m.fileStatus(index) or ''
            self.fileSelected.emit(m.filePath(index), st)
        else:
            self.clearDisplay.emit()

    def selectedRows(self) -> List[QModelIndex]:
        return self.selectionModel().selectedRows()
