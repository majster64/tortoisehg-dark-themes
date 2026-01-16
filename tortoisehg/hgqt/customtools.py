# customtools.py - Settings panel and configuration dialog for TortoiseHg custom tools
#
# This module implements 3 main classes:
#
# 1. A ToolsFrame which is meant to be shown on the settings dialog
# 2. A ToolList widget, part of the ToolsFrame, showing a list of
#    configured custom tools
# 3. A CustomToolConfigDialog, that can be used to add a new or
#    edit an existing custom tool
#
# The ToolsFrame and specially the ToolList must implement some methods
# which are common to all settings widgets.
#
# Copyright 2012 Angel Ezquerra <angel.ezquerra@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import re
import typing

from typing import (
    Dict,
    Iterable,
    List,
    Optional,
    Pattern,
    Tuple,
    TypeVar,
    Union,
)

from .qtcore import (
    QModelIndex,
    QSettings,
    Qt,
)
from .qtgui import (
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QIcon,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..util import hglib
from ..util.i18n import _
from . import qtlib

if typing.TYPE_CHECKING:
    from typing import (
        Any,
    )

    from ..util.typelib import (
        IniConfig,
    )

_W = TypeVar('_W', bound=QWidget)

DEFAULTICONNAME: str = 'tools-spanner-hammer'


class ToolsFrame(QFrame):
    def __init__(
        self, ini: IniConfig, parent: Optional[QWidget]=None, **opts
    ) -> None:
        QFrame.__init__(self, parent, **opts)
        self.widgets = []
        self.ini = ini
        self.tortoisehgtools: Dict[str, Dict[str, Union[str, bool]]]
        self.tortoisehgtools, guidef = hglib.tortoisehgtools(self.ini)
        self.setValue(self.tortoisehgtools)

        # The frame has a header and 3 columns:
        # - The header shows a combo with the list of locations
        # - The columns show:
        #     - The current location tool list and its associated buttons
        #     - The add to list button
        #     - The "available tools" list and its associated buttons
        topvbox = QVBoxLayout()
        self.setLayout(topvbox)

        topvbox.addWidget(QLabel(_('Select a GUI location to edit:')))

        self.locationcombo = QComboBox(self,
            toolTip=_('Select the toolbar or menu to change'))

        def selectlocation(index: int) -> None:
            location = self.locationcombo.itemData(index)
            for widget in self.widgets:
                if widget.location == location:
                    widget.removeInvalid(self.value())
                    widget.show()
                else:
                    widget.hide()
        self.locationcombo.currentIndexChanged.connect(selectlocation)
        topvbox.addWidget(self.locationcombo)

        hbox = QHBoxLayout()
        topvbox.addLayout(hbox)
        vbox = QVBoxLayout()

        self.globaltoollist = ToolListBox(self.ini, minimumwidth=100,
                                          parent=self)
        self.globaltoollist.doubleClicked.connect(self.editToolItem)

        vbox.addWidget(QLabel(_('Tools shown on selected location')))
        for location, locationdesc in hglib.tortoisehgtoollocations:
            self.locationcombo.addItem(locationdesc.decode('utf-8'), location)
            toollist = ToolListBox(self.ini, location=location,
                minimumwidth=100, parent=self)
            toollist.doubleClicked.connect(self.editToolFromName)
            vbox.addWidget(toollist)
            toollist.hide()
            self.widgets.append(toollist)

        deletefromlistbutton = QPushButton(_('Delete from list'), self)
        deletefromlistbutton.clicked.connect(
            lambda: self.forwardToCurrentToolList('deleteTool', remove=False))
        vbox.addWidget(deletefromlistbutton)
        hbox.addLayout(vbox)

        vbox = QVBoxLayout()
        vbox.addWidget(QLabel('')) # to align all lists
        addtolistbutton = QPushButton('<< ' + _('Add to list') + ' <<', self)
        addtolistbutton.clicked.connect(self.addToList)
        addseparatorbutton = QPushButton('<< ' + _('Add separator'), self)
        addseparatorbutton.clicked.connect(
            lambda: self.forwardToCurrentToolList('addSeparator'))

        vbox.addWidget(addtolistbutton)
        vbox.addWidget(addseparatorbutton)
        vbox.addStretch()
        hbox.addLayout(vbox)

        vbox = QVBoxLayout()
        vbox.addWidget(QLabel(_('List of all tools')))
        vbox.addWidget(self.globaltoollist)
        newbutton = QPushButton(_('New Tool ...'), self)
        newbutton.clicked.connect(self.newTool)
        editbutton = QPushButton(_('Edit Tool ...'), self)
        editbutton.clicked.connect(lambda: self.editTool(row=None))
        deletebutton = QPushButton(_('Delete Tool'), self)
        deletebutton.clicked.connect(self.deleteCurrentTool)

        vbox.addWidget(newbutton)
        vbox.addWidget(editbutton)
        vbox.addWidget(deletebutton)
        hbox.addLayout(vbox)

        # Ensure that the first location list is shown
        selectlocation(0)

    def getCurrentToolList(self) -> Optional[ToolListBox]:
        index = self.locationcombo.currentIndex()
        location = self.locationcombo.itemData(index)
        for widget in self.widgets:
            if widget.location == location:
                return widget
        return None

    def addToList(self) -> None:
        gtl = self.globaltoollist
        row = gtl.currentIndex().row()
        if row < 0:
            row = 0
        item = gtl.item(row)
        if item is None:
            return
        toolname = item.text()
        self.forwardToCurrentToolList('addOrInsertItem', toolname, icon=item.icon())

    def forwardToCurrentToolList(self, funcname: str, *args, **opts) -> None:
        w = self.getCurrentToolList()
        if w is not None:
            getattr(w, funcname)(*args, **opts)
        return None

    def newTool(self) -> None:
        td = CustomToolConfigDialog(self)
        res = td.exec()
        if res:
            toolname, toolconfig = td.value()
            self.globaltoollist.addOrInsertItem(
                toolname, icon=toolconfig.get('icon', None))
            self.tortoisehgtools[toolname] = toolconfig

    def editTool(self, row: Optional[int] = None) -> None:
        gtl = self.globaltoollist
        if row is None:
            row = gtl.currentIndex().row()
        if row < 0:
            return self.newTool()
        else:
            item = gtl.item(row)
            toolname = item.text()
            td = CustomToolConfigDialog(
                self, toolname=toolname,
                toolconfig=self.tortoisehgtools[toolname])
            res = td.exec()
            if res:
                toolname, toolconfig = td.value()
                icon = toolconfig.get('icon', '')
                if not icon:
                    icon = DEFAULTICONNAME
                item = QListWidgetItem(qtlib.geticon(icon), toolname)
                gtl.takeItem(row)
                gtl.insertItem(row, item)
                gtl.setCurrentRow(row)
                self.tortoisehgtools[toolname] = toolconfig

    def editToolItem(self, item: QModelIndex) -> None:
        self.editTool(item.row())

    def editToolFromName(self, idx: QModelIndex) -> None:
        # [TODO] connect to toollist doubleClick (not global)
        name = idx.data(Qt.ItemDataRole.DisplayRole)
        gtl = self.globaltoollist
        if name == gtl.SEPARATOR:
            return
        guidef = gtl.values()
        for row, toolname in enumerate(guidef):
            if toolname == name:
                self.editTool(row)
                return

    def deleteCurrentTool(self) -> None:
        row = self.globaltoollist.currentIndex().row()
        if row >= 0:
            item = self.globaltoollist.item(row)
            itemtext = item.text()
            self.globaltoollist.deleteTool(row=row)

            self.deleteTool(itemtext)
            self.forwardToCurrentToolList('removeInvalid', self.value())

    def deleteTool(self, name: str) -> None:
        try:
            del self.tortoisehgtools[name]
        except KeyError:
            pass

    def applyChanges(self, ini: IniConfig) -> bool:
        # widget.value() returns the _NEW_ values
        # widget.curvalue returns the _ORIGINAL_ values (yes, this is a bit
        # misleading! "cur" means "current" as in currently valid)
        def updateIniValue(
            section: str, key: str, newvalue: Optional[bytes]
        ) -> None:
            section = hglib.fromunicode(section)
            key = hglib.fromunicode(key)
            try:
                del ini[section][key]
            except KeyError:
                pass
            if newvalue is not None:
                ini.set(section, key, newvalue)

        emitChanged = False
        if not self.isDirty():
            return emitChanged

        emitChanged = True
        # 1. Save the new tool configurations
        #
        # In order to keep the tool order we must delete all existing
        # custom tool configurations, and then set all the configuration
        # settings anew:
        section = 'tortoisehg-tools'
        fieldnames = ('command', 'workingdir', 'label', 'tooltip',
                      'icon', 'location', 'enable', 'showoutput',)
        for name in self.curvalue:
            for field in fieldnames:
                updateIniValue(section, '%s.%s' % (name, field), None)

        tools = self.value()
        for name in tools:
            if name[0] in '|-':
                continue
            for field in sorted(tools[name]):
                keyname = '%s.%s' % (name, field)
                value = tools[name][field]
                # value may be bool if originating from hglib.tortoisehgtools()
                if value != '':
                    updateIniValue(section, keyname,
                                   hglib.fromunicode(str(value)))

        # 2. Save the new guidefs
        for n, toollistwidget in enumerate(self.widgets):
            toollocation = self.locationcombo.itemData(n)
            if not toollistwidget.isDirty():
                continue
            emitChanged = True
            toollist = toollistwidget.value()

            updateIniValue('tortoisehg', toollocation,
                           hglib.fromunicode(' '.join(toollist)))

        return emitChanged

    ## common APIs for all edit widgets
    def setValue(self, curvalue: Dict[str, Dict[str, Union[str, bool]]]) -> None:
        self.curvalue = dict(curvalue)

    def value(self) -> Dict[str, Dict[str, Union[str, bool]]]:
        return self.tortoisehgtools

    def isDirty(self) -> bool:
        for toollistwidget in self.widgets:
            if toollistwidget.isDirty():
                return True
        if self.globaltoollist.isDirty():
            return True
        return self.tortoisehgtools != self.curvalue

    def refresh(self) -> None:
        self.tortoisehgtools, guidef = hglib.tortoisehgtools(self.ini)
        self.setValue(self.tortoisehgtools)
        self.globaltoollist.refresh()
        for w in self.widgets:
            w.refresh()


class HooksFrame(QFrame):
    def __init__(
        self, ini: IniConfig, parent: Optional[QWidget]=None, **opts
    ) -> None:
        super().__init__(parent, **opts)
        self.ini = ini
        # The frame is created empty, and will be populated on 'refresh',
        # which usually happens when the frames is activated
        self.setValue({})

        topbox = QHBoxLayout()
        self.setLayout(topbox)
        self.hooktable = QTableWidget(0, 3, parent)
        self.hooktable.setHorizontalHeaderLabels((_('Type'), _('Name'), _('Command')))
        self.hooktable.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.hooktable.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.hooktable.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.hooktable.cellDoubleClicked.connect(self.editHook)
        topbox.addWidget(self.hooktable)
        buttonbox = QVBoxLayout()
        self.btnnew = QPushButton(_('New hook'))
        buttonbox.addWidget(self.btnnew)
        self.btnnew.clicked.connect(self.newHook)
        self.btnedit = QPushButton(_('Edit hook'))
        buttonbox.addWidget(self.btnedit)
        self.btnedit.clicked.connect(self.editCurrentHook)
        self.btndelete = QPushButton(_('Delete hook'))
        self.btndelete.clicked.connect(self.deleteCurrentHook)
        buttonbox.addWidget(self.btndelete)
        buttonbox.addStretch()
        topbox.addLayout(buttonbox)

    def newHook(self) -> None:
        td = HookConfigDialog(self)
        res = td.exec()
        if res:
            uhooktype, ucommand, uhookname = td.value()
            hooktype = hglib.fromunicode(uhooktype)
            command = hglib.fromunicode(ucommand)
            hookname = hglib.fromunicode(uhookname)

            # Does the new hook already exist?
            hooks = self.value()
            if hooktype in hooks:
                existingcommand = hooks[hooktype].get(hookname, None)
                if existingcommand is not None:
                    if existingcommand == command:
                        # The command already exists "as is"!
                        return
                    if not qtlib.QuestionMsgBox(
                            _('Replace existing hook?'),
                            _('There is an existing %s.%s hook.\n\n'
                            'Do you want to replace it?')
                            % (uhooktype, uhookname),
                            parent=self):
                        return
                    # Delete existing matching hooks in reverse order
                    # (otherwise the row numbers will be wrong after the first
                    # deletion)
                    for r in reversed(self.findHooks(
                            hooktype=hooktype, hookname=hookname)):
                        self.deleteHook(r)
            self.hooktable.setSortingEnabled(False)
            row = self.hooktable.rowCount()
            self.hooktable.insertRow(row)
            for c, text in enumerate((uhooktype, uhookname, ucommand)):
                self.hooktable.setItem(row, c, QTableWidgetItem(text))
            # Make the hook column not editable (a dialog is used to edit it)
            itemhook = self.hooktable.item(row, 0)
            itemhook.setFlags(itemhook.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.hooktable.setSortingEnabled(True)
            self.hooktable.resizeColumnsToContents()
            self.updatebuttons()

    def editHook(self, r: int, c: int = 0) -> bool:
        if r < 0:
            r = 0
        numrows = self.hooktable.rowCount()
        if not numrows or r >= numrows:
            return False
        if c > 0:
            # Only show the edit dialog when clicking
            # on the "Hook Type" (i.e. the 1st) column
            return False
        hooktype = self.hooktable.item(r, 0).text()
        hookname = self.hooktable.item(r, 1).text()
        command = self.hooktable.item(r, 2).text()
        td = HookConfigDialog(self, hooktype=hooktype,
                              command=command, hookname=hookname)
        res = td.exec()
        if res:
            hooktype, command, hookname = td.value()
            # Update the table
            # Note that we must disable the ordering while the table
            # is updated to avoid updating the wrong cell!
            self.hooktable.setSortingEnabled(False)
            self.hooktable.item(r, 0).setText(hooktype)
            self.hooktable.item(r, 1).setText(hookname)
            self.hooktable.item(r, 2).setText(command)
            self.hooktable.setSortingEnabled(True)
            self.hooktable.clearSelection()
            self.hooktable.setState(QTableWidget.State.NoState)
            self.hooktable.resizeColumnsToContents()
        return bool(res)

    def editCurrentHook(self) -> None:
        self.editHook(self.hooktable.currentRow())

    def deleteHook(self, row: Optional[int] = None) -> None:
        if row is None:
            row = self.hooktable.currentRow()
            if row < 0:
                row = self.hooktable.rowCount() - 1
        self.hooktable.removeRow(row)
        self.hooktable.resizeColumnsToContents()
        self.updatebuttons()

    def deleteCurrentHook(self) -> None:
        self.deleteHook()

    def findHooks(
        self,
        hooktype: Optional[bytes] = None,
        hookname: Optional[bytes] = None,
        command: Optional[bytes] = None,
    ) -> List[int]:
        matchingrows = []
        for r in range(self.hooktable.rowCount()):
            currhooktype = hglib.fromunicode(self.hooktable.item(r, 0).text())
            currhookname = hglib.fromunicode(self.hooktable.item(r, 1).text())
            currcommand = hglib.fromunicode(self.hooktable.item(r, 2).text())
            matchinghooktype = hooktype is None or hooktype == currhooktype
            matchinghookname = hookname is None or hookname == currhookname
            matchingcommand = command is None or command == currcommand
            if matchinghooktype and matchinghookname and matchingcommand:
                matchingrows.append(r)
        return matchingrows

    def updatebuttons(self) -> None:
        tablehasitems = self.hooktable.rowCount() > 0
        self.btnedit.setEnabled(tablehasitems)
        self.btndelete.setEnabled(tablehasitems)

    def applyChanges(self, ini: IniConfig) -> bool:
        # widget.value() returns the _NEW_ values
        # widget.curvalue returns the _ORIGINAL_ values (yes, this is a bit
        # misleading! "cur" means "current" as in currently valid)
        emitChanged = False
        if not self.isDirty():
            return emitChanged
        emitChanged = True

        # 1. Delete the previous hook configurations
        section = b'hooks'
        hooks = self.curvalue
        for hooktype in hooks:
            for keyname in hooks[hooktype]:
                if keyname:
                    keyname = b'%s.%s' % (hooktype, keyname)
                else:
                    keyname = hooktype
                try:
                    del ini[section][keyname]
                except KeyError:
                    pass
        # 2. Save the new configurations
        hooks = self.value()
        for hooktype in hooks:
            for field in sorted(hooks[hooktype]):
                if field:
                    keyname = b'%s.%s' % (hooktype, field)
                else:
                    keyname = hooktype
                value = hooks[hooktype][field]
                if value:
                    ini.set(section, keyname, value)
        return emitChanged

    ## common APIs for all edit widgets
    def setValue(self, curvalue: Dict[bytes, Dict[bytes, bytes]]) -> None:
        self.curvalue = dict(curvalue)

    def value(self) -> Dict[bytes, Dict[bytes, bytes]]:
        hooks = {}
        for r in range(self.hooktable.rowCount()):
            hooktype = hglib.fromunicode(self.hooktable.item(r, 0).text())
            hookname = hglib.fromunicode(self.hooktable.item(r, 1).text())
            command = hglib.fromunicode(self.hooktable.item(r, 2).text())
            if hooktype not in hooks:
                hooks[hooktype] = {}
            hooks[hooktype][hookname] = command
        return hooks

    def isDirty(self) -> bool:
        return self.value() != self.curvalue

    def gethooks(self) -> Dict[bytes, Dict[bytes, bytes]]:
        hooks = {}
        for key, value in self.ini.items(b'hooks'):
            keyparts: List[bytes] = key.split(b'.', 1)
            hooktype = keyparts[0]
            if len(keyparts) == 1:
                name = b''
            else:
                name = keyparts[1]
            if hooktype not in hooks:
                hooks[hooktype] = {}
            hooks[hooktype][name] = value
        return hooks

    def refresh(self) -> None:
        hooks = self.gethooks()
        self.setValue(hooks)
        self.hooktable.setSortingEnabled(False)
        self.hooktable.setRowCount(0)
        for hooktype in sorted(hooks):
            for name in sorted(hooks[hooktype]):
                itemhook = QTableWidgetItem(hglib.tounicode(hooktype))
                # Make the hook column not editable
                # (a dialog is used to edit it)
                itemhook.setFlags(itemhook.flags() & ~Qt.ItemFlag.ItemIsEditable)
                itemname = QTableWidgetItem(hglib.tounicode(name))
                itemtool = QTableWidgetItem(
                    hglib.tounicode(hooks[hooktype][name]))
                self.hooktable.insertRow(self.hooktable.rowCount())
                self.hooktable.setItem(self.hooktable.rowCount() - 1, 0, itemhook)
                self.hooktable.setItem(self.hooktable.rowCount() - 1, 1, itemname)
                self.hooktable.setItem(self.hooktable.rowCount() - 1, 2, itemtool)
        self.hooktable.setSortingEnabled(True)
        self.hooktable.resizeColumnsToContents()
        self.updatebuttons()


class ToolListBox(QListWidget):
    SEPARATOR: str = '------'
    def __init__(
        self,
        ini: IniConfig,
        parent: Optional[QWidget] = None,
        location: Optional[str] = None,
        minimumwidth: Optional[int] = None,
        **opts
    ) -> None:
        QListWidget.__init__(self, parent, **opts)
        self.opts = opts
        self.curvalue = None
        self.ini = ini
        self.location: Optional[str] = location

        if minimumwidth:
            self.setMinimumWidth(minimumwidth)

        self.refresh()

        # Enable drag and drop to reorder the tools
        self.setDragEnabled(True)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def _guidef2toollist(self, guidef: Iterable[str]) -> List[str]:
        toollist = []
        for name in guidef:
            if name == '|':
                name = self.SEPARATOR
                # avoid putting multiple separators together
                if [name] == toollist[-1:]:
                    continue
            toollist.append(name)
        return toollist

    def _toollist2guidef(self, toollist: List[str]) -> List[str]:
        guidef = []
        for uname in toollist:
            if uname == self.SEPARATOR:
                name = '|'
                # avoid putting multiple separators together
                if [name] == toollist[-1:]:
                    continue
            else:
                name = uname
            guidef.append(name)
        return guidef

    def addOrInsertItem(
        self, text: str, icon: Optional[Union[str, QIcon]] = None
    ) -> None:
        if text == self.SEPARATOR:
            item = text
        else:
            if not icon:
                icon = DEFAULTICONNAME
            if hglib.isbasestring(icon):
                icon = qtlib.geticon(icon)
            item = QListWidgetItem(icon, text)
        row = self.currentIndex().row()
        if row < 0:
            self.addItem(item)
            self.setCurrentRow(self.count()-1)
        else:
            self.insertItem(row+1, item)
            self.setCurrentRow(row+1)

    def deleteTool(
        self, row: Optional[int] = None, remove: bool = False
    ) -> None:
        if row is None:
            row = self.currentIndex().row()
        if row >= 0:
            self.takeItem(row)

    def addSeparator(self) -> None:
        self.addOrInsertItem(self.SEPARATOR, icon=None)

    def values(self) -> List[str]:
        out = []
        for row in range(self.count()):
            out.append(self.item(row).text())
        return out

    ## common APIs for all edit widgets
    def setValue(self, curvalue: List[str]) -> None:
        self.curvalue = curvalue

    def value(self) -> List[str]:
        return self._toollist2guidef(list(self.values()))

    def isDirty(self) -> bool:
        return self.value() != self.curvalue

    def refresh(self) -> None:
        toolsdefs, guidef = hglib.tortoisehgtools(self.ini,
            selectedlocation=self.location)
        self.toollist: List[str] = self._guidef2toollist(guidef)
        self.setValue(guidef)
        self.clear()
        for toolname in self.toollist:
            icon = toolsdefs.get(toolname, {}).get('icon', None)
            self.addOrInsertItem(toolname, icon=icon)

    def removeInvalid(
        self, validtools: Dict[str, Dict[str, Union[str, bool]]]
    ) -> None:
        validguidef = []
        for toolname in self.value():
            if toolname[0] not in '|-':
                if toolname not in validtools:
                    continue
            validguidef.append(toolname)
        self.clear()
        self.toollist = self._guidef2toollist(validguidef)
        for toolname in self.toollist:
            icon = validtools.get(toolname, {}).get('icon', None)
            self.addOrInsertItem(toolname, icon=icon)


class CustomConfigDialog(QDialog):
    '''Custom Config Dialog base class'''

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        dialogname: str = '',
        **kwargs
    ) -> None:
        QDialog.__init__(self, parent, **kwargs)
        self.dialogname = dialogname
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self.hbox = QHBoxLayout()
        self.formvbox = QFormLayout()

        self.hbox.addLayout(self.formvbox)
        vbox = QVBoxLayout()
        self.okbutton = QPushButton(_('OK'))
        self.okbutton.clicked.connect(self.okClicked)
        vbox.addWidget(self.okbutton)
        self.cancelbutton = QPushButton(_('Cancel'))
        self.cancelbutton.clicked.connect(self.reject)
        vbox.addWidget(self.cancelbutton)
        vbox.addStretch()
        self.hbox.addLayout(vbox)
        self.setLayout(self.hbox)
        self.setMaximumHeight(self.sizeHint().height())
        self._readsettings()

    def value(self) -> None:
        return None

    def _genCombo(
        self,
        items: Iterable[str],
        selecteditem: Optional[str] = None,
        tooltips: Optional[Iterable[str]] = None,
    ) -> QComboBox:
        index = 0
        if selecteditem:
            try:
                index = list(items).index(selecteditem)
            except ValueError:
                pass
        combo = QComboBox()
        combo.addItems(items)
        if index:
            combo.setCurrentIndex(index)
        if tooltips:
            for idx, tooltip in enumerate(tooltips):
                combo.setItemData(idx, tooltip, Qt.ItemDataRole.ToolTipRole)
        return combo

    def _addConfigItem(
        self,
        parent: QFormLayout,
        label: str,
        configwidget: _W,
        tooltip: Optional[str]=None,
    ) -> _W:
        if tooltip:
            configwidget.setToolTip(tooltip)
        parent.addRow(label, configwidget)
        return configwidget

    def okClicked(self) -> None:
        errormsg = self.validateForm()
        if errormsg:
            qtlib.WarningMsgBox(_('Missing information'), errormsg)
            return
        return self.accept()

    def validateForm(self) -> str:
        return '' # No error

    def _readsettings(self) -> QSettings:
        s = QSettings()
        if self.dialogname:
            self.restoreGeometry(
                qtlib.readByteArray(s, self.dialogname + '/geom'))
        return s

    def _writesettings(self) -> None:
        s = QSettings()
        if self.dialogname:
            s.setValue(self.dialogname + '/geom', self.saveGeometry())

    def done(self, r: int) -> None:
        self._writesettings()
        super().done(r)


class CustomToolConfigDialog(CustomConfigDialog):
    '''Dialog for editing custom tool configurations'''

    _enablemappings: List[Tuple[str, str]] = [(_('All items'), 'istrue'),
                       (_('Working directory'), 'iswd'),
                       (_('All revisions'), 'isrev'),
                       (_('All contexts'), 'isctx'),
                       (_('Fixed revisions'), 'fixed'),
                       (_('Applied patches'), 'applied'),
                       (_('Applied patches or qparent'), 'qgoto'),
                       ]
    _defaulticonstring: str = _('<default icon>')

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        toolname: Optional[str] = None,
        toolconfig: Optional[Dict[str, Union[str, bool]]] = None,
    ) -> None:
        super().__init__(parent,
            dialogname='customtools',
            windowTitle=_('Configure Custom Tool'),
            windowIcon=qtlib.geticon(DEFAULTICONNAME))

        if toolconfig is None:
            toolconfig = {}
        vbox = self.formvbox

        command = toolconfig.get('command', '')
        workingdir = toolconfig.get('workingdir', '')
        label = toolconfig.get('label', '')
        tooltip = toolconfig.get('tooltip', '')
        ico = toolconfig.get('icon', '')
        enable = toolconfig.get('enable', 'all')
        showoutput = str(toolconfig.get('showoutput', False))

        self.name: QLineEdit = self._addConfigItem(vbox, _('Tool name'),
            QLineEdit(toolname), _('The tool name. It cannot contain spaces.'))
            # Execute a mercurial command. These _MUST_ start with "hg"
        self.command: QLineEdit = self._addConfigItem(vbox, _('Command'),
            QLineEdit(command), _('The command that will be executed.\n'
            'To execute a Mercurial command use "hg" (rather than "hg.exe") '
            'as the executable command.\n'
            'You can use several {VARIABLES} to compose your command.\n'
            'Common variables:\n'
            '- {ROOT}: The path to the current repository root.\n'
            '- {REV} / {REVID}: Selected revisions numbers / hexadecimal'
            ' revision id hashes respectively formatted as a revset'
            ' expression.\n'
            '- {SELECTEDFILES}: The list of files selected by the user on the '
            'revision details file list.\n'
            '- {FILES}: The list of files touched by the selected revisions.\n'
            '- {ALLFILES}: All the files tracked by Mercurial on the selected'
            ' revisions.\n'
            'Pair selection variables:\n'
            '- {REV_A} / {REVID_A}: the first selected revision number / '
            'hexadecimal revision id hash respectively.\n'
            '- {REV_B} / {REVID_B}: the second selected revision number / '
            'hexadecimal revision id hash respectively.\n'))
        self.workingdir: QLineEdit = self._addConfigItem(vbox, _('Working Directory'),
            QLineEdit(workingdir),
            _('The directory where the command will be executed.\n'
            'If this is not set, the root of the current repository '
            'will be used instead.\n'
            'You can use the same {VARIABLES} as on the "Command" setting.\n'))
        self.label: QLineEdit = self._addConfigItem(vbox, _('Tool label'),
            QLineEdit(label),
            _('The tool label, which is what will be shown '
            'on the repowidget context menu.\n'
            'If no label is set, the tool name will be used as the tool label.\n'
            'If no tooltip is set, the label will be used as the tooltip as well.'))
        self.tooltip: QLineEdit = self._addConfigItem(vbox, _('Tooltip'),
            QLineEdit(tooltip),
            _('The tooltip that will be shown on the tool button.\n'
            'This is only shown when the tool button is shown on\n'
            'the workbench toolbar.'))

        iconnames = qtlib.getallicons()
        combo = QComboBox()
        if not ico:
            ico = self._defaulticonstring
        elif ico not in iconnames:
            combo.addItem(qtlib.geticon(ico), ico)
        combo.addItem(qtlib.geticon(DEFAULTICONNAME),
                      self._defaulticonstring)
        for name in iconnames:
            combo.addItem(qtlib.geticon(name), name)
        combo.setEditable(True)
        idx = combo.findText(ico)
        # note that idx will always be >= 0 because if ico not in iconnames
        # it will have been added as the first element on the combobox!
        combo.setCurrentIndex(idx)

        self.icon: QComboBox = self._addConfigItem(vbox, _('Icon'),
            combo,
            _('The tool icon.\n'
            'You can use any built-in TortoiseHg icon\n'
            'by setting this value to a valid TortoiseHg icon name\n'
            '(e.g. clone, add, remove, sync, thg-logo, hg-update, etc).\n'
            'You can also set this value to the absolute path to\n'
            'any icon on your file system.'))

        combo = self._genCombo([l for l, _v in self._enablemappings],
                               self._enable2label(enable))
        self.enable: QComboBox = self._addConfigItem(vbox, _('On repowidget, show for'),
            combo,  _('For which kinds of revisions the tool will be enabled\n'
            'It is only taken into account when the tool is shown on the\n'
            'selected revision context menu.'))

        combo = self._genCombo(('True', 'False'), showoutput)
        self.showoutput: QComboBox = self._addConfigItem(vbox, _('Show Output Log'),
            combo, _('When enabled, automatically show the Output Log when the '
            'command is run.\nDefault: False.'))

    def value(self) -> Tuple[str, Dict[str, str]]:  # pytype: disable=signature-mismatch
        toolname = self.name.text().strip()
        toolconfig = {
            'label': self.label.text(),
            'command': self.command.text(),
            'workingdir': self.workingdir.text(),
            'tooltip': self.tooltip.text(),
            'icon': self.icon.currentText(),
            'enable': self._enablemappings[self.enable.currentIndex()][1],
            'showoutput': self.showoutput.currentText(),
        }
        if toolconfig['icon'] == self._defaulticonstring:
            toolconfig['icon'] = ''
        return toolname, toolconfig

    def _enable2label(self, value: str) -> Optional[str]:
        return {v: l for l, v in self._enablemappings}.get(value)

    def validateForm(self) -> str:
        name, config = self.value()
        if not name:
            return _('You must set a tool name.')
        if name.find(' ') >= 0:
            return _('The tool name cannot have any spaces in it.')
        if not config['command']:
            return _('You must set a command to run.')
        return '' # No error


class HookConfigDialog(CustomConfigDialog):
    '''Dialog for editing the a hook configuration'''

    _hooktypes = (
        'changegroup',
        'commit',
        'incoming',
        'outgoing',
        'prechangegroup',
        'precommit',
        'prelistkeys',
        'preoutgoing',
        'prepushkey',
        'pretag',
        'pretxnchangegroup',
        'pretxncommit',
        'preupdate',
        'listkeys',
        'pushkey',
        'tag',
        'update',
    )

    _hooktooltips = (
        _('Run after a changegroup has been added via push, pull or unbundle. '
            'ID of the first new changeset is in <tt>$HG_NODE</tt> and last in '
            '<tt>$HG_NODE_LAST</tt>. URL from which changes came is in '
            '<tt>$HG_URL</tt>.'),
        _('Run after a changeset has been created in the local repository. ID '
            'of the newly created changeset is in <tt>$HG_NODE</tt>. Parent '
            'changeset IDs are in <tt>$HG_PARENT1</tt> and '
            '<tt>$HG_PARENT2</tt>.'),
        _('Run after a changeset has been pulled, pushed, or unbundled into '
            'the local repository. The ID of the newly arrived changeset is in '
            '<tt>$HG_NODE</tt>. URL that was source of changes came is in '
            '<tt>$HG_URL</tt>.'),
        _('Run after sending changes from local repository to another. ID of '
            'first changeset sent is in <tt>$HG_NODE</tt>. Source of operation '
            'is in <tt>$HG_SOURCE</tt>.'),
        _('Run before a changegroup is added via push, pull or unbundle. Exit '
            'status 0 allows the changegroup to proceed. Non-zero status will '
            'cause the push, pull or unbundle to fail. URL from which changes '
            'will come is in <tt>$HG_URL</tt>.'),
        _('Run before starting a local commit. Exit status 0 allows the commit '
            'to proceed. Non-zero status will cause the commit to fail. Parent '
            'changeset IDs are in <tt>$HG_PARENT1</tt> and '
            '<tt>$HG_PARENT2</tt>.'),
        _('Run before listing pushkeys (like bookmarks) in the repository. '
            'Non-zero status will cause failure. The key namespace is in '
            '<tt>$HG_NAMESPACE</tt>.'),
        _('Run before collecting changes to send from the local repository to '
            'another. Non-zero status will cause failure. This lets you '
            'prevent pull over HTTP or SSH. Also prevents against local pull, '
            'push (outbound) or bundle commands, but not effective, since you '
            'can just copy files instead then. Source of operation is in '
            '<tt>$HG_SOURCE</tt>. If "serve", operation is happening on behalf '
            'of remote SSH or HTTP repository. If "push", "pull" or "bundle", '
            'operation is happening on behalf of repository on same system.'),
        _('Run before a pushkey (like a bookmark) is added to the repository. '
            'Non-zero status will cause the key to be rejected. The key '
            'namespace is in <tt>$HG_NAMESPACE</tt>, the key is in '
            '<tt>$HG_KEY</tt>, the old value (if any) is in <tt>$HG_OLD</tt>, '
            'and the new value is in <tt>$HG_NEW</tt>.'),
        _('Run before creating a tag. Exit status 0 allows the tag to be '
            'created. Non-zero status will cause the tag to fail. ID of '
            'changeset to tag is in <tt>$HG_NODE</tt>. Name of tag is in '
            '<tt>$HG_TAG</tt>. Tag is local if <tt>$HG_LOCAL=1</tt>, in '
            'repository if <tt>$HG_LOCAL=0</tt>.'),
        _('Run after a changegroup has been added via push, pull or unbundle, '
            'but before the transaction has been committed. Changegroup is '
            'visible to hook program. This lets you validate incoming changes '
            'before accepting them. Passed the ID of the first new changeset '
            'in <tt>$HG_NODE</tt> and last in <tt>$HG_NODE_LAST</tt>. Exit '
            'status 0 allows the transaction to commit. Non-zero status will '
            'cause the transaction to be rolled back and the push, pull or '
            'unbundle will fail. URL that was source of changes is in '
            '<tt>$HG_URL</tt>.'),
        _('Run after a changeset has been created but the transaction not yet '
            'committed. Changeset is visible to hook program. This lets you '
            'validate commit message and changes. Exit status 0 allows the '
            'commit to proceed. Non-zero status will cause the transaction to '
            'be rolled back. ID of changeset is in <tt>$HG_NODE</tt>. Parent '
            'changeset IDs are in <tt>$HG_PARENT1</tt> and '
            '<tt>$HG_PARENT2</tt>.'),
        _('Run before updating the working directory. Exit status 0 allows the '
            'update to proceed. Non-zero status will prevent the update. '
            'Changeset ID of first new parent is in <tt>$HG_PARENT1</tt>. '
            'If merge, ID of second new parent is in <tt>$HG_PARENT2</tt>.'),
        _('Run after listing pushkeys (like bookmarks) in the repository. The '
            'key namespace is in <tt>$HG_NAMESPACE</tt>. <tt>$HG_VALUES</tt> '
            'is a dictionary containing the keys and values.'),
        _('Run after a pushkey (like a bookmark) is added to the repository. '
            'The key namespace is in <tt>$HG_NAMESPACE</tt>, the key is in '
            '<tt>$HG_KEY</tt>, the old value (if any) is in <tt>$HG_OLD</tt>, '
            'and the new value is in <tt>$HG_NEW</tt>.'),
        _('Run after a tag is created. ID of tagged changeset is in '
            '<tt>$HG_NODE</tt>. Name of tag is in <tt>$HG_TAG</tt>. Tag is '
            'local if <tt>$HG_LOCAL=1</tt>, in repository if '
            '<tt>$HG_LOCAL=0</tt>.'),
        _('Run after updating the working directory. Changeset ID of first new '
            'parent is in <tt>$HG_PARENT1</tt>. If merge, ID of second new '
            'parent is in <tt>$HG_PARENT2</tt>. If the update succeeded, '
            '<tt>$HG_ERROR=0</tt>. If the update failed (e.g. because '
            'conflicts not resolved), <tt>$HG_ERROR=1</tt>.'),
    )

    _rehookname: Pattern[str] = re.compile(r'^[^=\s]*$')

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        hooktype: Optional[str] = None,
        command: str = '',
        hookname: str = '',
    ) -> None:
        super().__init__(parent,
            dialogname='hookconfigdialog',
            windowTitle=_('Configure Hook'),
            windowIcon=qtlib.geticon('tools-hooks'))

        vbox = self.formvbox
        combo = self._genCombo(self._hooktypes, hooktype, self._hooktooltips)
        self.hooktype: QComboBox = self._addConfigItem(vbox, _('Hook type'),
            combo, _('Select when your command will be run'))
        self.name: QLineEdit = self._addConfigItem(vbox, _('Tool name'),
            QLineEdit(hookname), _('The hook name. It cannot contain spaces.'))
        self.command: QLineEdit = self._addConfigItem(vbox, _('Command'),
            QLineEdit(command), _('The command that will be executed.\n'
                 'To execute a python function prepend the command with '
                 '"python:".\n'))

    def value(self) -> Tuple[str, str, str]:  # pytype: disable=signature-mismatch
        hooktype = self.hooktype.currentText()
        hookname = self.name.text().strip()
        command = self.command.text().strip()
        return hooktype, command, hookname

    def validateForm(self) -> str:
        hooktype, command, hookname = self.value()
        if hooktype not in self._hooktypes:
            return _('You must set a valid hook type.')
        if self._rehookname.match(hookname) is None:
            return _('The hook name cannot contain any spaces, '
                     'tabs or \'=\' characters.')
        if not command:
            return _('You must set a command to run.')
        return '' # No error
