# repotreemodel.py - model for the reporegistry
#
# Copyright 2010 Adrian Buehlmann <adrian@cadifra.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import os

from typing import (
    List,
    Optional,
)

from .qtcore import (
    QAbstractItemModel,
    QByteArray,
    QFile,
    QIODevice,
    QMimeData,
    QModelIndex,
    QUrl,
    QXmlStreamReader,
    QXmlStreamWriter,
    Qt,
    pyqtSlot,
)
from .qtgui import (
    QFont,
)

from mercurial import (
    pycompat,
)

from ..util import hglib
from ..util.i18n import _
from . import repotreeitem

extractXmlElementName = 'reporegextract'
reporegistryXmlElementName = 'reporegistry'

repoRegMimeType = 'application/thg-reporegistry'
repoExternalMimeType = 'text/uri-list'


def writeXml(target, item, rootElementName):
    xw = QXmlStreamWriter(target)
    xw.setAutoFormatting(True)
    xw.setAutoFormattingIndent(2)
    xw.writeStartDocument()
    xw.writeStartElement(rootElementName)
    item.dumpObject(xw)
    xw.writeEndElement()
    xw.writeEndDocument()

def readXml(source, rootElementName):
    itemread = None
    xr = QXmlStreamReader(source)
    if xr.readNextStartElement():
        ele = xr.name()
        if ele != rootElementName:
            print("unexpected xml element '%s' "
                  "(was looking for %s)" % (ele, rootElementName))
            return
    if xr.hasError():
        print(xr.errorString())
    if xr.readNextStartElement():
        itemread = repotreeitem.undumpObject(xr)
        xr.skipCurrentElement()
    if xr.hasError():
        print(xr.errorString())
    return itemread

def iterRepoItemFromXml(source):
    'Used by thgrepo.relatedRepositories to scan the XML file'
    xr = QXmlStreamReader(source)
    while not xr.atEnd():
        t = xr.readNext()
        if (t == QXmlStreamReader.TokenType.StartElement
            and xr.name() in ('repo', 'subrepo')):
            rti = repotreeitem.undumpObject(xr)
            assert isinstance(rti, repotreeitem.RepoItem)
            yield rti

def getRepoItemList(root, standalone=False):
    if standalone:
        stopfunc = lambda e: isinstance(e, repotreeitem.RepoItem)
    else:
        stopfunc = None
    return [e for e in repotreeitem.flatten(root, stopfunc=stopfunc)
            if isinstance(e, repotreeitem.RepoItem)]


class RepoTreeModel(QAbstractItemModel):
    def __init__(self, f, repomanager, parent=None,
                 showShortPaths=False):
        QAbstractItemModel.__init__(self, parent)

        self._repomanager = repomanager
        self._repomanager.configChanged.connect(self._updateShortName)
        self._repomanager.repositoryChanged.connect(self._updateBaseNode)
        self._repomanager.repositoryOpened.connect(self._updateItem)

        self.showShortPaths = showShortPaths
        self._activeRepoItem = None

        root = None
        if f:
            if f.open(QIODevice.OpenModeFlag.ReadOnly):
                root = readXml(f, reporegistryXmlElementName)
                f.close()

        if not root:
            root = repotreeitem.RepoTreeItem()
        # due to issue #1075, 'all' may be missing even if 'root' exists
        try:
            all = repotreeitem.find(
                root, lambda e: isinstance(e, repotreeitem.AllRepoGroupItem))
        except ValueError:
            all = repotreeitem.AllRepoGroupItem()
            root.appendChild(all)

        self.rootItem = root
        self.allrepos = all
        self.updateCommonPaths()

    # see https://doc.qt.io/qt-4.8/model-view-programming.html#model-subclassing-reference

    # overrides from QAbstractItemModel

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            parentItem = self.rootItem
        else:
            parentItem = parent.internalPointer()
        childItem = parentItem.child(row)
        if childItem:
            return self.createIndex(row, column, childItem)
        else:
            return QModelIndex()

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        childItem = index.internalPointer()
        parentItem = childItem.parent()
        if parentItem is self.rootItem:
            return QModelIndex()
        return self.createIndex(parentItem.row(), 0, parentItem)

    def rowCount(self, parent=QModelIndex()):
        if parent.column() > 0:
            return 0
        if not parent.isValid():
            parentItem = self.rootItem
        else:
            parentItem = parent.internalPointer()
        return parentItem.childCount()

    def columnCount(self, parent=QModelIndex()):
        if parent.isValid():
            return parent.internalPointer().columnCount()
        else:
            return self.rootItem.columnCount()

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole, Qt.ItemDataRole.DecorationRole,
                Qt.ItemDataRole.FontRole):
            return None
        item = index.internalPointer()
        if role == Qt.ItemDataRole.FontRole and item is self._activeRepoItem:
            font = QFont()
            font.setBold(True)
            return font
        else:
            return item.data(index.column(), role)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal:
                if section == 1:
                    return _('Path')
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        item = index.internalPointer()
        return item.flags()

    def supportedDropActions(self):
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction | Qt.DropAction.LinkAction

    def removeRows(self, row, count, parent=QModelIndex()):
        item = parent.internalPointer()
        if item is None:
            item = self.rootItem
        if count <= 0 or row < 0 or row + count > item.childCount():
            return False
        self.beginRemoveRows(parent, row, row+count-1)
        if self._activeRepoItem in item.childs[row:row + count]:
            self._activeRepoItem = None
        res = item.removeRows(row, count)
        self.endRemoveRows()
        return res

    def mimeTypes(self):
        return [repoRegMimeType, repoExternalMimeType]

    def mimeData(self, indexes):
        i = indexes[0]
        item = i.internalPointer()
        buf = QByteArray()
        writeXml(buf, item, extractXmlElementName)
        d = QMimeData()
        d.setData(repoRegMimeType, buf)
        if isinstance(item, repotreeitem.RepoItem):
            d.setUrls([QUrl.fromLocalFile(item.rootpath())])
        else:
            d.setText(item.name)
        return d

    def dropMimeData(self, data, action, row, column, parent):
        group = parent.internalPointer()
        d = bytes(data.data(repoRegMimeType))
        if not data.hasUrls():
            # The source is a group
            if row < 0:
                # The group has been dropped on a group
                # In that case, place the group at the same level as the target
                # group
                row = parent.row()
                parent = parent.parent()
                group = parent.internalPointer()
                if row < 0 or not isinstance(group, repotreeitem.RepoGroupItem):
                    # The group was dropped at the top level
                    group = self.rootItem
                    parent = QModelIndex()
        itemread = readXml(d, extractXmlElementName)
        if itemread is None:
            return False
        if group is None:
            return False
        # Avoid copying subrepos multiple times
        if Qt.DropAction.CopyAction == action and self.getRepoItem(itemread.rootpath()):
            return False
        if row < 0:
            row = 0
        self.beginInsertRows(parent, row, row)
        group.insertChild(row, itemread)
        self.endInsertRows()
        if isinstance(itemread, repotreeitem.AllRepoGroupItem):
            self.allrepos = itemread
        return True

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False
        if not value:
            return False
        item = index.internalPointer()
        if item.setData(index.column(), value):
            self.dataChanged.emit(index, index)
            return True
        return False

    # functions not defined in QAbstractItemModel

    def addRepo(self, uroot, row=-1, parent=QModelIndex()):
        if not parent.isValid():
            parent = self._indexFromItem(self.allrepos)
        rgi = parent.internalPointer()
        if row < 0:
            row = rgi.childCount()

        # make sure all paths are properly normalized
        uroot = os.path.normpath(uroot)

        # Check whether the repo that we are adding is a subrepo
        knownitem = self.getRepoItem(uroot, lookForSubrepos=True)
        itemIsSubrepo = isinstance(knownitem,
                                   (repotreeitem.StandaloneSubrepoItem,
                                    repotreeitem.SubrepoItem))

        self.beginInsertRows(parent, row, row)
        if itemIsSubrepo:
            ri = repotreeitem.StandaloneSubrepoItem(uroot)
        else:
            ri = repotreeitem.RepoItem(uroot)
        rgi.insertChild(row, ri)
        self.endInsertRows()

        return self._indexFromItem(ri)

    # TODO: merge getRepoItem() to indexFromRepoRoot()
    def getRepoItem(self, reporoot, lookForSubrepos=False):
        reporoot = os.path.normcase(reporoot)
        items = getRepoItemList(self.rootItem, standalone=not lookForSubrepos)
        for e in items:
            if os.path.normcase(e.rootpath()) == reporoot:
                return e

    def indexFromRepoRoot(self, uroot, column=0, standalone=False):
        item = self.getRepoItem(uroot, lookForSubrepos=not standalone)
        return self._indexFromItem(item, column)

    def isKnownRepoRoot(self, uroot, standalone=False):
        return self.indexFromRepoRoot(uroot, standalone=standalone).isValid()

    def indexesOfRepoItems(self, column=0, standalone=False):
        return [self._indexFromItem(e, column)
                for e in getRepoItemList(self.rootItem, standalone)]

    def _indexFromItem(self, item, column=0):
        if item and item is not self.rootItem:
            return self.createIndex(item.row(), column, item)
        else:
            return QModelIndex()

    def repoRoot(self, index: QModelIndex) -> Optional[str]:
        item = index.internalPointer()
        if not isinstance(item, repotreeitem.RepoItem):
            return
        return item.rootpath()

    def addGroup(self, name):
        ri = self.rootItem
        cc = ri.childCount()
        self.beginInsertRows(QModelIndex(), cc, cc + 1)
        ri.appendChild(repotreeitem.RepoGroupItem(name, ri))
        self.endInsertRows()

    def itemPath(self, index):
        """Virtual path of the item at the given index"""
        if index.isValid():
            item = index.internalPointer()
        else:
            item = self.rootItem
        return repotreeitem.itempath(item)

    def indexFromItemPath(self, path: str, column=0):
        """Model index for the item specified by the given virtual path"""
        try:
            item = repotreeitem.findbyitempath(self.rootItem,
                                               path)
        except ValueError:
            return QModelIndex()
        return self._indexFromItem(item, column)

    def write(self, fn):
        f = QFile(fn)
        f.open(QIODevice.OpenModeFlag.WriteOnly)
        writeXml(f, self.rootItem, reporegistryXmlElementName)
        f.close()

    def _emitItemDataChanged(self, item):
        self.dataChanged.emit(self._indexFromItem(item, 0),
                              self._indexFromItem(item, self.columnCount() - 1))

    def setActiveRepo(self, index):
        """Highlight the specified item as active"""
        newitem = index.internalPointer()
        if newitem is self._activeRepoItem:
            return
        previtem = self._activeRepoItem
        self._activeRepoItem = newitem
        for it in [previtem, newitem]:
            if it:
                self._emitItemDataChanged(it)

    def activeRepoIndex(self, column=0):
        return self._indexFromItem(self._activeRepoItem, column)

    # TODO: rename loadSubrepos() and appendSubrepos() to scanRepo() ?
    def loadSubrepos(self, index: QModelIndex) -> List[str]:
        """Scan subrepos of the repo; returns list of invalid paths"""
        item = index.internalPointer()
        if (not isinstance(item, repotreeitem.RepoItem)
            or isinstance(item, repotreeitem.AlienSubrepoItem)):
            return []
        self.removeRows(0, item.childCount(), index)

        # XXX dirty hack to know childCount _before_ insertion; should be
        # fixed later when you refactor appendSubrepos().
        tmpitem = item.__class__(item.rootpath())
        invalidpaths = tmpitem.appendSubrepos()
        if tmpitem.childCount() > 0:
            self.beginInsertRows(index, 0, tmpitem.childCount() - 1)
            for e in tmpitem.childs:
                item.appendChild(e)
            self.endInsertRows()
        if (item._sharedpath != tmpitem._sharedpath
            or item._valid != tmpitem._valid):
            item._sharedpath = tmpitem._sharedpath
            item._valid = tmpitem._valid
            self._emitItemDataChanged(item)
        return hglib.to_unicode_list(invalidpaths)

    def updateCommonPaths(self, showShortPaths=None):
        if showShortPaths is not None:
            self.showShortPaths = showShortPaths
        for grp in self.rootItem.childs:
            if isinstance(grp, repotreeitem.RepoGroupItem):
                if self.showShortPaths:
                    grp.updateCommonPath()
                else:
                    grp.updateCommonPath('')

    @pyqtSlot(str)
    def _updateShortName(self, uroot: str):
        repoagent = self._repomanager.repoAgent(uroot)
        it = self.getRepoItem(uroot)
        if it:
            it.setShortName(repoagent.shortName())
            self._emitItemDataChanged(it)

    @pyqtSlot(str)
    def _updateBaseNode(self, uroot: str):
        repo = self._repomanager.repoAgent(uroot).rawRepo()
        it = self.getRepoItem(uroot)
        if it:
            it.setBaseNode(hglib.repoidnode(repo))

    @pyqtSlot(str)
    def _updateItem(self, uroot):
        self._updateShortName(uroot)
        self._updateBaseNode(uroot)

    def sortchilds(self, childs, keyfunc):
        self.layoutAboutToBeChanged.emit()
        childs.sort(key=keyfunc)
        self.layoutChanged.emit()
