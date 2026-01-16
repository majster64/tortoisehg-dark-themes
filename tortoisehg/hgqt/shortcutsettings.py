# shortcutsettings.py - dialog to configure shortcuts
#
# Copyright 2020 Yuya Nishihara <yuya@tcha.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import typing

from .qtcore import (
    QModelIndex,
    QTimer,
    Qt,
    pyqtSlot,
)

from .qtgui import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import (
    shortcutregistry,
)

from ..util import (
    hglib,
)

from ..util.i18n import _

if typing.TYPE_CHECKING:
    from typing import (
        Optional,
        Text,
    )


class ShortcutSettingsWidget(QWidget):

    def __init__(self,
                 registry: shortcutregistry.ShortcutRegistry,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._registry = registry

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)

        # slightly longer delay than common keyboard auto-repeat rate
        self._filterLater = timer = QTimer(self)
        timer.setInterval(550)
        timer.setSingleShot(True)
        timer.timeout.connect(self._rebuildItems)

        self._filterEdit = edit = QLineEdit(self)
        edit.setPlaceholderText(_('Filter by keywords'))
        edit.textEdited.connect(self._filterLater.start)
        vbox.addWidget(edit)

        self._view = view = QTreeWidget(self)
        view.setHeaderLabels([_('Name'), _('Label'), _('Shortcuts')])
        view.setAllColumnsShowFocus(True)
        view.setRootIsDecorated(False)
        view.setUniformRowHeights(True)
        vbox.addWidget(view)

        hbox = QHBoxLayout()
        label = QLabel(_('&Key'), self)
        hbox.addWidget(label)
        self._keyEdit = edit = QKeySequenceEdit(self)
        label.setBuddy(edit)
        edit.editingFinished.connect(self._applyCurrent)
        hbox.addWidget(edit)
        self._clearButton = button = QPushButton(_('Cl&ear'), self)
        button.clicked.connect(self._clearCurrent)
        hbox.addWidget(button)
        self._resetButton = button = QPushButton(_('&Reset to Default'), self)
        button.clicked.connect(self._resetCurrentToDefault)
        hbox.addWidget(button)
        vbox.addLayout(hbox)

        self._buildItems()
        self._view.resizeColumnToContents(0)
        self._view.resizeColumnToContents(1)
        self._view.setCurrentIndex(QModelIndex())

        selmodel = self._view.selectionModel()
        assert selmodel is not None
        selmodel.currentRowChanged.connect(self._updateKeyEdit)

        self._updateKeyEdit()

    def _buildItems(self) -> None:
        registry = self._registry
        patterns = self._filterEdit.text().lower().split()
        items = []
        for name in registry.allNames():
            label = registry.actionLabel(name).replace('&', '')
            keys = ' | '.join(b.toString() for b in registry.keySequences(name))
            data = [name, label, keys]
            if any(all(p not in s.lower() for s in data) for p in patterns):
                continue
            it = QTreeWidgetItem(data)
            if registry.hasUserKeySequences(name):
                font = self.font()
                font.setBold(True)
                it.setFont(2, font)
            items.append(it)
        self._view.addTopLevelItems(items)

    @pyqtSlot()
    def _rebuildItems(self):
        name = self._currentName()
        self._view.clear()
        self._buildItems()
        self._setCurrentByName(name)

    def registry(self) -> shortcutregistry.ShortcutRegistry:
        return self._registry

    def _currentName(self) -> str:
        it = self._view.currentItem()
        if not it:
            return ''
        return it.text(0)

    def _setCurrentByName(self, name):
        items = self._view.findItems(name, Qt.MatchFlag.MatchExactly)
        if items:
            self._view.setCurrentItem(items[0])
        else:
            self._view.setCurrentIndex(QModelIndex())

    def _updateCurrentItem(self):
        registry = self._registry
        it = self._view.currentItem()
        assert it is not None
        name = it.text(0)
        keys = ' | '.join(b.toString() for b in registry.keySequences(name))
        font = self.font()
        if registry.hasUserKeySequences(name):
            font.setBold(True)
        it.setText(2, keys)
        it.setFont(2, font)

    @pyqtSlot()
    def _updateKeyEdit(self) -> None:
        name = self._currentName()
        self._keyEdit.setEnabled(bool(name))
        self._clearButton.setEnabled(bool(name))
        self._resetButton.setEnabled(bool(name))

        if not name:
            self._keyEdit.clear()
            return

        seqs = self._registry.keySequences(name)
        # TODO: support multiple key sequences
        if seqs:
            self._keyEdit.setKeySequence(seqs[0])
        else:
            self._keyEdit.clear()

    @pyqtSlot()
    def _applyCurrent(self) -> None:
        b = self._keyEdit.keySequence()
        if b.isEmpty():
            seqs = []
        else:
            seqs = [b]
        self._registry.setUserKeySequences(self._currentName(), seqs)
        self._updateCurrentItem()
        self._updateKeyEdit()

    @pyqtSlot()
    def _clearCurrent(self) -> None:
        self._registry.setUserKeySequences(self._currentName(), [])
        self._updateCurrentItem()
        self._updateKeyEdit()

    @pyqtSlot()
    def _resetCurrentToDefault(self) -> None:
        self._registry.unsetUserKeySequences(self._currentName())
        self._updateCurrentItem()
        self._updateKeyEdit()


# TODO: integrate into SettingsDialog
class ShortcutSettingsDialog(QDialog):

    def __init__(self,
                 registry: shortcutregistry.ActionRegistry,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._registry = registry

        self.setWindowTitle(_('Shortcut Settings'))
        vbox = QVBoxLayout(self)

        self._widget = widget = ShortcutSettingsWidget(
            registry.copyShortcuts(), self)
        vbox.addWidget(widget)

        buttons = QDialogButtonBox(self)
        buttons.setStandardButtons(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        vbox.addWidget(buttons)

        self.resize(600, 600)

    @pyqtSlot()
    def accept(self) -> None:
        self._registry.updateShortcuts(self._widget.registry())
        self._registry.saveSettings()
        self._registry.applyChangesToActions()
        super().accept()
