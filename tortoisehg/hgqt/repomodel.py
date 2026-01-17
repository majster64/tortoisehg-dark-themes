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

import binascii
import os
import re
import typing

from .qtcore import (
    QAbstractTableModel,
    QByteArray,
    QMimeData,
    QModelIndex,
    QT_VERSION,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from .qtgui import (
    QApplication,
    QColor,
    QFont,
)

from mercurial import (
    error,
    pycompat,
)
from mercurial.utils import (
    dateutil,
)

from ..util import hglib
from ..util.i18n import _
from . import (
    cmdcore,
    filedata,
    graph,
    graphopt,
)

from .theme import THEME

if typing.TYPE_CHECKING:
    from typing import (
        Any,
        FrozenSet,
        List,
        Optional,
        Text,
        Tuple,
        Union,
    )
    from mercurial import context
    from .qtcore import QObject
    from . import (
        thgrepo,
        filedialogs,
    )
    from ..util.typelib import (
        Qt_ItemFlags,
    )

    # _UnfilteredRepoAgentProxy is a short term hack, so don't use this type
    # for RepoAgent everywhere.
    RepoAgent = Union[thgrepo.RepoAgent, filedialogs._UnfilteredRepoAgentProxy]

mqpatchmimetype = 'application/thg-mqunappliedpatch'

# pick names from "hg help templating" if any
GraphColumn = 0
RevColumn = 1
BranchColumn = 2
DescColumn = 3
AuthorColumn = 4
TagsColumn = 5
LatestTagColumn = 6
NodeColumn = 7
GitNodeColumn = 8
AgeColumn = 9
LocalDateColumn = 10
UtcDateColumn = 11
ChangesColumn = 12
ConvertedColumn = 13
PhaseColumn = 14
TopicColumn = 15
FileColumn = 16

COLUMNHEADERS = (
    ('Graph', _('Graph', 'column header')),
    ('Rev', _('Rev', 'column header')),
    ('Branch', _('Branch', 'column header')),
    ('Description', _('Description', 'column header')),
    ('Author', _('Author', 'column header')),
    ('Tags', _('Tags', 'column header')),
    ('Latest tags', _('Latest tags', 'column header')),
    ('Node', _('Node', 'column header')),
    ('GitNode', _('Git Commit', 'column header')),
    ('Age', _('Age', 'column header')),
    ('LocalTime', _('Local Time', 'column header')),
    ('UTCTime', _('UTC Time', 'column header')),
    ('Changes', _('Changes', 'column header')),
    ('Converted', _('Converted From', 'column header')),
    ('Phase', _('Phase', 'column header')),
    ('Topic', _('Topic', 'column header')),
    ('Filename', _('Filename', 'column header')),
    )
ALLCOLUMNS = tuple(name for name, _text in COLUMNHEADERS)

UNAPPLIED_PATCH_COLOR = QColor('#999999')
HIDDENREV_COLOR = QColor('#666666')
TROUBLED_COLOR = QColor(172, 34, 34)
PUBLISHED_TOPIC_COLOR = QColor('#666666')

GraphNodeRole = Qt.ItemDataRole.UserRole + 0
LabelsRole = Qt.ItemDataRole.UserRole + 1  # [(text, style), ...]

def _parsebranchcolors(value: str) -> List[Tuple[str, str]]:
    r"""Parse tortoisehg.branchcolors setting

    >>> _parsebranchcolors('foo:#123456  bar:#789abc ')
    [('foo', '#123456'), ('bar', '#789abc')]
    >>> _parsebranchcolors(r'foo\ bar:black foo\:bar:white')
    [('foo bar', 'black'), ('foo:bar', 'white')]

    >>> _parsebranchcolors(r'\u00c0:black')
    [(u'\xc0', 'black')]
    >>> _parsebranchcolors('\xc0:black')
    [('\xc0', 'black')]

    >>> _parsebranchcolors(None)
    []
    >>> _parsebranchcolors('ill:formed:value no-value')
    []
    >>> _parsebranchcolors(r'\ubad:unicode-repr')
    []
    """
    if not value:
        return []

    colors = []
    for e in re.split(r'(?:(?<=\\\\)|(?<!\\)) ', value):
        pair = re.split(r'(?:(?<=\\\\)|(?<!\\)):', e)
        if len(pair) != 2:
            continue # ignore ill-formed
        key, val = pair
        key = key.replace('\\:', ':').replace('\\ ', ' ')
        if r'\u' in key:
            # apply unicode_escape only if \u found, so that raw non-ascii
            # value isn't always mangled. leave non-ascii string unmodified
            # since we don't know how to feed it to unicode_escape decoder.
            try:
                key = key.encode('ascii').decode('unicode_escape')
            except (UnicodeDecodeError, UnicodeEncodeError):
                continue
        colors.append((key, val))
    return colors


class HgRepoListModel(QAbstractTableModel):
    """
    Model used for displaying the revisions of a Hg *local* repository
    """
    showMessage = pyqtSignal(str)

    # emitted when listed revisions are updated because of repository or
    # filter option change; a view might have to change current index
    revsUpdated = pyqtSignal()

    _defaultcolumns = ('Graph', 'Rev', 'Branch', 'Description', 'Author',
                       'Age', 'Tags', 'Phase')

    _mqtags = (b'qbase', b'qtip', b'qparent')

    _selectedrevs: FrozenSet[int]

    def __init__(self,
                 repoagent: RepoAgent,
                 parent: Optional[QObject] = None) -> None:
        QAbstractTableModel.__init__(self, parent)
        self._cache = []
        self._timerhandle = None
        self._rowcount = 0
        self._repoagent = repoagent
        self._selectedrevs = frozenset([])
        self._revspec = ''
        self._filterbyrevset = True
        self.unicodestar = True
        self.unicodexinabox = True
        self._branchheads = {}  # branch: {node, ...}
        self._latesttags = {-1: (0, 0, 'null')}  # date, dist, tag
        self._fullauthorname = False
        self._filterbranch = ''  # unicode
        self._allparents = False
        self._showgraftsource = True

        # To be deleted
        self._user_colors = {}
        self._branch_colors = {}

        self._querysess = cmdcore.nullCmdSession()
        self._pendingrebuild = False

        repoagent.configChanged.connect(self._invalidate)
        repoagent.repositoryChanged.connect(self._reloadGraph)

        self._initBranchColors()
        self._reloadConfig()
        self.graph = self._createGraph()

    @property
    def repo(self):
        return self._repoagent.rawRepo()

    def _initBranchColors(self) -> None:
        # Always assign the first color to the default branch
        self._branch_colors['default'] = graph.COLORS[0]

        # Set the colors specified in the tortoisehg.brachcolors config key
        self._branch_colors.update(_parsebranchcolors(
            self._repoagent.configString('tortoisehg', 'branchcolors')))

    def setBranch(self, branch: str, allparents: bool = False) -> None:
        branchchanged = (branch != self._filterbranch)
        parentchanged = (allparents != self._allparents)
        self._filterbranch = branch
        self._allparents = allparents
        if branchchanged or (branch and parentchanged):
            self._rebuildGraph()

    def setShowGraftSource(self, visible: bool) -> None:
        if self._showgraftsource == visible:
            return
        self._showgraftsource = visible
        self._rebuildGraph()

    def _createGraph(self):
        opts = {
            'branch': hglib.fromunicode(self._filterbranch),
            'showgraftsource': self._showgraftsource,
            }
        if self._revspec and not self._selectedrevs and self._filterbyrevset:
            return graph.Graph(self.repo, [])  # no matches found
        if self._selectedrevs and self._filterbyrevset:
            opts['revset'] = self._selectedrevs
            opts['showfamilyline'] = self._repoagent.configBool(
                'tortoisehg', 'showfamilyline')
            grapher = graph.revision_grapher(self.repo, opts)
            if self._repoagent.configBool('tortoisehg', 'graphopt'):
                return graphopt.Graph(self.repo, opts)
            return graph.Graph(self.repo, grapher)
        else:
            opts['allparents'] = self._allparents
            grapher = graph.revision_grapher(self.repo, opts)
            if self._repoagent.configBool('tortoisehg', 'graphopt'):
                g = graphopt.Graph(self.repo, opts)
            else:
                g = graph.Graph(self.repo, grapher)
            if self.repo.thgmqunappliedpatches:
                g = graph.GraphWithMq(g, self.repo.thgmqunappliedpatches)
            return g

    @pyqtSlot()
    def _reloadGraph(self) -> None:
        self._branchheads.clear()
        self._latesttags = {-1: self._latesttags[-1]}  # clear
        if self._revspec:
            self._runQuery()
        self._rebuildGraph()

    def _rebuildGraph(self) -> None:
        if not self._querysess.isFinished():
            self._pendingrebuild = True
            return
        # skip costly operation while initializing options
        if self._rowcount <= 0 and not self.graph.isfilled():
            assert not self._cache
            self.graph = self._createGraph()
            return

        # QTBUG-66444: Do not emit layoutChanged if we're sure the model
        # was/is about to be empty. Otherwise all columns would get visible
        # due to a bug existing in Qt 5.9.5-5.10.x.
        newgraph = self._createGraph()
        if not newgraph:
            newgraph.build_nodes(self._fill_step)
        if self._rowcount <= 0 or not newgraph:
            self._cache = []
            self.graph = newgraph
            self._expandRowCount()
            self._shrinkRowCount()
            self._pendingrebuild = False
            self.revsUpdated.emit()
            return

        self.layoutAboutToBeChanged.emit()
        try:
            oldindexmap = {}  # rev: [index, ...]
            for i in self.persistentIndexList():
                rev = self.graph[i.row()].rev
                if rev not in oldindexmap:
                    oldindexmap[rev] = []
                oldindexmap[rev].append(i)
            try:
                brev = min(rev for rev in oldindexmap if isinstance(rev, int))
            except ValueError:
                brev = None

            self._cache = []
            try:
                self.graph = newgraph
                self._ensureBuilt(brev)
            except (error.RevlogError, error.RepoError):
                self._shrinkRowCount()  # avoid further exceptions at data()
                raise
            self._expandRowCount()  # old rows may be mapped to inserted rows
            for rev, ois in oldindexmap.items():
                try:
                    row = self.graph.index(rev)
                except ValueError:
                    # QTBUG-66444: Work around QHeaderView bug which makes all
                    # columns visible if the first row gets invalidated. This
                    # is technically wrong because invalid rows should be
                    # removed from the persistent indexes, but less bad than
                    # losing user configuration.
                    if QT_VERSION < 0x50b00 and ois[0].row() == 0:
                        continue
                    # rev stripped or patch (un-)applied, map to invalid index
                    row = -1
                nis = [self.index(row, i.column(), i.parent()) for i in ois]
                self.changePersistentIndexList(ois, nis)
            self._shrinkRowCount()  # old rows should be mapped before removal
            self._pendingrebuild = False
        finally:
            self.layoutChanged.emit()
        self._emitAllDataChanged()
        self.revsUpdated.emit()

    def revset(self) -> str:
        return self._revspec

    def setRevset(self, revspec: str) -> None:
        if revspec == self._revspec:
            return
        self._revspec = revspec
        if not revspec:
            self._querysess.abort()
            self._applyRevsetResult([])
            return
        self._runQuery()

    def _runQuery(self) -> None:
        self.showMessage.emit(_('Searching...'))

        self._querysess.abort()
        cmdline = ['log', '-T', '{rev}\n', '-r', self._revspec]
        self._querysess = sess = self._repoagent.runCommand(cmdline)
        sess.setCaptureOutput(True)
        sess.commandFinished.connect(self._onQueryFinished)

    @pyqtSlot(int)
    def _onQueryFinished(self, ret: int) -> None:
        sess = self._querysess
        if not sess.isFinished():
            # new query is already running
            return
        if ret == 0:
            revs = pycompat.maplist(int, bytes(sess.readAll()).splitlines())
            if revs:
                self.showMessage.emit(_('%d matches found') % len(revs))
            else:
                self.showMessage.emit(_('No matches found'))
            self._applyRevsetResult(revs)
        elif not sess.isAborted():
            self.showMessage.emit(sess.errorString() or sess.warningString())
        if self._pendingrebuild:
            self._rebuildGraph()  # invalid revspec

    def _applyRevsetResult(self, revset: List[int]) -> None:
        self._selectedrevs = frozenset(revset)
        if self._filterbyrevset or self._pendingrebuild:
            self._rebuildGraph()
        else:
            self._invalidate()
            self.revsUpdated.emit()  # some revisions may be disabled

    def setFilterByRevset(self, filtered: bool) -> None:
        if self._filterbyrevset == filtered:
            return
        self._filterbyrevset = filtered
        if self._revspec:
            self._rebuildGraph()

    def _reloadConfig(self) -> None:
        self._fill_step = self._repoagent.configInt('tortoisehg', 'graphlimit')
        self._authorcolor = self._repoagent.configBool(
            'tortoisehg', 'authorcolor')
        self._fullauthorname = self._repoagent.configBool(
            'tortoisehg', 'fullauthorname')
        self._show_branch_head_label = self._repoagent.configBool(
            'tortoisehg', 'show-branch-head-label')

    @pyqtSlot()
    def _invalidate(self) -> None:
        self._reloadConfig()
        self._cache = []
        self._emitAllDataChanged()

    def _emitAllDataChanged(self) -> None:
        if self._rowcount <= 0:
            return
        # optimize range if necessary
        bottomright = self.index(self._rowcount - 1, self.columnCount() - 1)
        self.dataChanged.emit(self.index(0, 0), bottomright)

    def branch(self) -> str:
        return self._filterbranch

    def canFetchMore(self, parent: QModelIndex) -> bool:
        if parent.isValid():
            return False
        return not self.graph.isfilled()

    def fetchMore(self, parent: QModelIndex) -> None:
        if parent.isValid() or self.graph.isfilled():
            return
        self.graph.build_nodes(self._fill_step)
        self._expandRowCount()

    def _ensureBuilt(self, rev: Optional[int]) -> None:
        """
        Make sure rev data is available (graph element created).

        """
        if not isinstance(rev, int):
            rev = len(self.repo)  # working dir or unapplied patch
        self.graph.build_nodes(rev=rev)
        # caller should do _expandRowCount() or _shrinkRowCount() by itself

    def loadall(self) -> None:
        self._timerhandle = self.startTimer(1)

    def timerEvent(self, event):
        if event.timerId() == self._timerhandle:
            self.showMessage.emit(_('filling (%d)')%(len(self.graph)))
            self.graph.build_nodes()
            # we only fill the graph data structures without telling
            # views until the model is loaded, to keep maximal GUI
            # reactivity
            if self.graph.isfilled():
                self.killTimer(self._timerhandle)
                self._timerhandle = None
                self._expandRowCount()
                self.showMessage.emit('')

    def _expandRowCount(self) -> None:
        newlen = len(self.graph)
        if newlen > self._rowcount:
            self.beginInsertRows(QModelIndex(), self._rowcount, newlen - 1)
            self._rowcount = newlen
            self.endInsertRows()

    def _shrinkRowCount(self) -> None:
        newlen = len(self.graph)
        if newlen < self._rowcount:
            self.beginRemoveRows(QModelIndex(), newlen, self._rowcount - 1)
            self._rowcount = newlen
            self.endRemoveRows()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return self._rowcount

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.allColumns())

    def maxWidthValueForColumn(self, column: int) -> Optional[str]:
        if column == RevColumn:
            return '8' * len(str(len(self.repo))) + '+'
        if column in (NodeColumn, GitNodeColumn):
            return '8' * 12 + '+'
        if column in (LocalDateColumn, UtcDateColumn):
            return hglib.tounicode(hglib.displaytime(dateutil.makedate()))
        if column in (TagsColumn, LatestTagColumn):
            try:
                return sorted(self.repo.tags().keys(), key=lambda x: len(x))[-1][:10]
            except IndexError:
                pass
        if column == BranchColumn:
            try:
                return sorted(self.repo.branchmap(), key=lambda x: len(x))[-1]
            except IndexError:
                pass
        if self._hasFileColumn() and column == FileColumn:
            return self._filename
        if column == ChangesColumn:
            return 'Changes'
        # Fall through for DescColumn
        return None

    def rev(self, index: QModelIndex) -> int:
        """Revision number of the specified row; None for working-dir"""
        if not index.isValid():
            return -1
        gnode = self.graph[index.row()]
        if gnode.rev is not None and not isinstance(gnode.rev, int):
            # avoid mixing integer and localstr
            return -1
        return gnode.rev

    def _user_color(self, user: str) -> str:
        'deprecated, please replace with hgtk color scheme'
        if user not in self._user_colors:
            idx = graph.hashcolor(user)
            self._user_colors[user] = graph.COLORS[idx]
        return self._user_colors[user]

    def _namedbranch_color(self, branch: str) -> str:
        'deprecated, please replace with hgtk color scheme'
        if branch not in self._branch_colors:
            idx = graph.hashcolor(branch)
            self._branch_colors[branch] = graph.COLORS[idx]
        return self._branch_colors[branch]

    def data(self,
             index: QModelIndex,
             role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        gnode = self.graph[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            if self._hasFileColumn() and index.column() == FileColumn:
                return hglib.tounicode(gnode.extra[0])
        if role == Qt.ItemDataRole.FontRole:
            if index.column() in (NodeColumn, GitNodeColumn, ConvertedColumn):
                return QFont("Monospace")
            if index.column() == DescColumn and gnode.wdparent:
                font = QApplication.font('QAbstractItemView')
                font.setBold(True)
                return font
        if role == Qt.ItemDataRole.ForegroundRole:
            if (gnode.shape == graph.NODE_SHAPE_UNAPPLIEDPATCH
                and index.column() != DescColumn):
                return UNAPPLIED_PATCH_COLOR
            
            if THEME.enabled:
                # Unapplied patch (keep behavior)
                if (gnode.shape == graph.NODE_SHAPE_UNAPPLIEDPATCH
                    and index.column() != DescColumn):
                    return UNAPPLIED_PATCH_COLOR

                col = index.column()

                # Description column
                if col == DescColumn:
                    return THEME.text

                # Revision number / node
                if col in (NodeColumn, GitNodeColumn):
                    return THEME.text

                # Author
                if col == AuthorColumn:
                    return THEME.text_author

                # Age / Date
                if col == AgeColumn:
                    return THEME.text_author

                # Tags / Branch / Bookmark column
                if col == TagsColumn:
                    return THEME.text_author

                # Phase
                if col == PhaseColumn:
                    return THEME.text_author

        if role == GraphNodeRole:
            return gnode
        # repo may be changed while reading in case of postpull=rebase for
        # example, and result in RevlogError. (issue #429)
        try:
            return self._safedata(index, role)
        except error.RevlogError as e:
            if 'THGDEBUG' in os.environ:
                raise
            if role == Qt.ItemDataRole.DisplayRole:
                return hglib.exception_str(e)
            else:
                return None

    def _safedata(self, index: QModelIndex, role: int) -> Any:
        row = index.row()
        graphlen = len(self.graph)
        cachelen = len(self._cache)
        if graphlen > cachelen:
            self._cache.extend({} for _i in
                               pycompat.xrange(graphlen - cachelen))
        data = self._cache[row]
        idx = (role, index.column())
        if idx not in data:
            try:
                result = self._rawdata(index, role)
            except error.RepoLookupError:
                # happens if repository pruned/stripped or bundle unapplied
                # but model is not reloaded yet because repository is busy
                return None
            except error.Abort:
                return None
            data[idx] = result
        return data[idx]

    def _rawdata(self, index: QModelIndex, role: int) -> Any:
        row = index.row()
        column = index.column()
        gnode = self.graph[row]
        ctx = self.repo[gnode.rev]

        if role == Qt.ItemDataRole.DisplayRole:
            textfunc = self._columnmap.get(column)
            if textfunc is None:
                return None
            return textfunc(self, ctx)
        elif role == Qt.ItemDataRole.ForegroundRole:
            color = None
            if gnode.instabilities:
                color = TROUBLED_COLOR
            elif column == AuthorColumn and self._authorcolor:
                color = QColor(self._user_color(ctx.user()))
            elif column in (GraphColumn, BranchColumn):
                branch = hglib.tounicode(ctx.branch())
                color = QColor(self._namedbranch_color(branch))
            if index.column() != GraphColumn:
                if gnode.faded:
                    if color is None:
                        color = HIDDENREV_COLOR
                    else:
                        color = color.lighter()
            if column == TopicColumn and not ctx.mutable():
                if color is None:
                    color = PUBLISHED_TOPIC_COLOR
                else:
                    color = color.lighter()
            return color
        elif role == LabelsRole and column == DescColumn:
            return self._getrevlabels(ctx)
        elif role == LabelsRole and column == ChangesColumn:
            return self._getchanges(ctx)
        return None

    def flags(self, index: QModelIndex) -> Qt_ItemFlags:
        flags = super().flags(index)
        if not index.isValid():
            return flags
        row = index.row()
        if row >= len(self.graph) and not self.repo.ui.debugflag:
            # TODO: should not happen; internal data went wrong (issue #754)
            return Qt.ItemFlag.NoItemFlags
        rev, isunapplied = self.graph.getrevstate(row)
        if not self.isActiveRev(rev):
            return Qt.ItemFlag.NoItemFlags
        if isunapplied:
            flags |= Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled
        if rev is None:
            flags |= Qt.ItemFlag.ItemIsDropEnabled
        return flags

    def isActiveRev(self, rev: int) -> bool:
        """True if the specified rev is not excluded by revset"""
        return (not self._revspec
                or rev in self._selectedrevs
                # consider everything is active while first query is running
                or (self._revspec and not self._selectedrevs
                    and not self._querysess.isFinished()))

    def mimeTypes(self) -> List[str]:
        return [mqpatchmimetype]

    def supportedDropActions(self):
        return Qt.DropAction.MoveAction

    def mimeData(self, indexes: List[QModelIndex]) -> QMimeData:
        data = set()
        for index in indexes:
            row = b'%d' % index.row()
            if row not in data:
                data.add(row)
        qmd = QMimeData()
        bytearray = QByteArray(b','.join(sorted(data, reverse=True)))
        qmd.setData(mqpatchmimetype, bytearray)
        return qmd

    def dropMimeData(self,
                     data: QMimeData,
                     action: int,
                     row: int,
                     column: int,
                     parent: QModelIndex) -> bool:
        if mqpatchmimetype not in data.formats():
            return False
        dragrows = [int(r)
                    for r in bytes(data.data(mqpatchmimetype)).split(b',')]
        destrow = parent.row()
        if destrow < 0:
            return False
        unapplied = pycompat.maplist(hglib.tounicode,
                                     self.repo.thgmqunappliedpatches[::-1])
        applied = [hglib.tounicode(p.name) for p in self.repo.mq.applied[::-1]]
        if max(dragrows) >= len(unapplied):
            return False
        dragpatches = [unapplied[d] for d in dragrows]
        allpatches = unapplied + applied
        if destrow < len(allpatches):
            destpatch = allpatches[destrow]
        else:
            destpatch = None  # next to working rev

        cmdline = hglib.buildcmdargs('qreorder', after=destpatch, *dragpatches)
        self._repoagent.runCommand(cmdline)
        return True

    def headerData(self,
                   section: int,
                   orientation: int,
                   role: int = Qt.ItemDataRole.DisplayRole) -> Optional[str]:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.allColumnHeaders()[section][1]

    def defaultIndex(self) -> QModelIndex:
        """Index that should be selected when the model is initially loaded
        or the row previously selected is gone"""
        repo = self.repo
        initialsel = self._repoagent.configString(
            'tortoisehg', 'initialrevision')
        changeid = {'current': b'.',
                    'tip': b'tip',
                    'workingdir': None,
                    }.get(initialsel, b'.')
        rev: int = repo[changeid].rev()
        if self._selectedrevs and rev not in self._selectedrevs:
            rev = max(self._selectedrevs)
        index = self.indexFromRev(rev)
        if index.flags() & Qt.ItemFlag.ItemIsEnabled:
            return index

        if self._filterbranch:
            # look for the first active revision as last ditch; should be
            # removed if filterbranch is merged with revset
            for row in pycompat.xrange(len(self.graph)):
                gnode = self.graph[row]
                if not isinstance(gnode.rev, int):
                    continue
                index = self.index(row, 0)
                if index.flags() & Qt.ItemFlag.ItemIsEnabled:
                    return index
        return QModelIndex()

    def indexFromRev(self, rev: int) -> QModelIndex:
        self._ensureBuilt(rev)
        self._expandRowCount()
        try:
            row = self.graph.index(rev)
        except ValueError:
            return QModelIndex()
        return self.index(row, 0)

    def _getbranch(self, ctx: context.changectx) -> str:
        b = hglib.tounicode(ctx.branch())
        assert b is not None
        if ctx.extra().get(b'close'):
            if self.unicodexinabox:
                b += ' \u2327'
            else:
                b += '--'
        return b

    def _getlatesttags(self, ctx: context.changectx) -> str:
        rev = ctx.rev()
        todo = [rev]
        repo = self.repo
        while todo:
            rev = todo.pop()
            if rev in self._latesttags:
                continue
            ctx = repo[rev]
            tags = [hglib.tounicode(t) for t in ctx.tags()
                    if repo.tagtype(t) and repo.tagtype(t) != b'local']
            if tags:
                self._latesttags[rev] = ctx.date()[0], 0, ':'.join(sorted(tags))
                continue
            try:
                # The tuples are laid out so the right one can be found by
                # comparison.
                if ctx.parents():
                    pdate, pdist, ptag = max(
                        self._latesttags[p.rev()] for p in ctx.parents())
                else:
                    pdate, pdist, ptag = 0, -1, ""
            except KeyError:
                # Cache miss - recurse
                todo.append(rev)
                todo.extend(p.rev() for p in ctx.parents())
                continue
            self._latesttags[rev] = pdate, pdist + 1, ptag
        return self._latesttags[rev][2]

    def _gettags(self, ctx: context.changectx) -> str:
        if ctx.rev() is None:
            return ''
        tags = [hglib.tounicode(t) for t in ctx.tags() if t not in self._mqtags]
        return ','.join(tags)

    def _getrev(self, ctx: context.changectx) -> str:
        rev = ctx.rev()
        if isinstance(rev, int):
            return str(rev)
        elif rev is None:
            return '%d+' % ctx.p1().rev()
        else:
            return ''

    def _getauthor(self, ctx: context.changectx) -> str:
        try:
            user = ctx.user()
            if not self._fullauthorname:
                user = hglib.username(user)
            return hglib.tounicode(user)
        except error.Abort:
            return _('Mercurial User')

    def _getlog(self, ctx: context.changectx) -> str:
        if ctx.rev() is None:
            if self.unicodestar:
                # The Unicode symbol is a black star:
                return '\u2605 ' + _('Working Directory') + ' \u2605'
            else:
                return '*** ' + _('Working Directory') + ' ***'
        if self._repoagent.configBool('tortoisehg', 'longsummary'):
            limit = 0x7fffffff  # unlimited (elide it by view)
        else:
            limit = None  # first line
        return hglib.longsummary(ctx.description(), limit)

    def _getrevlabels(self, ctx: context.changectx) -> List[Tuple[str, str]]:
        labels = []

        # as of hg 4.4.2, repo.branchheads() can be slow because of
        # branchmap.updatecache() -> scmutil.filteredhash() calls
        branch = ctx.branch()
        try:
            branchheads = self._branchheads[branch]
        except KeyError:
            branchheads = set(self.repo.branchheads(branch))
            self._branchheads[branch] = branchheads

        if ctx.rev() is None:
            if hglib.createsnewhead(ctx, branchheads):
                labels.append((_('Creates new head!'), 'log.warning'))
            topic = self._gettopic(ctx)
            if topic:
                labels.append((topic, 'topic.active'))
            return labels

        if self._show_branch_head_label:
            if ctx.node() in branchheads:
                labels.append((hglib.tounicode(branch), 'log.branch'))

        # ctx is really thgrepo.thgchangectx, but not accessible here
        if ctx.thgmqunappliedpatch():  # pytype: disable=attribute-error
            style = 'log.unapplied_patch'
            labels.append((hglib.tounicode(ctx._patchname), style))  # pytype: disable=attribute-error

        for mark in ctx.bookmarks():
            style = 'log.bookmark'
            if mark == hglib.activebookmark(self.repo):
                bn = self.repo._bookmarks[hglib.activebookmark(self.repo)]
                if bn in self.repo.dirstate.parents():
                    style = 'log.curbookmark'
            labels.append((hglib.tounicode(mark), style))

        for tag in ctx.thgtags():  # pytype: disable=attribute-error
            if self.repo.thgmqtag(tag):
                style = 'log.patch'
            else:
                style = 'log.tag'
            labels.append((hglib.tounicode(tag), style))

        names = set(self._repoagent.configStringList(
            'experimental', 'thg.displaynames'))
        for name, ns in self.repo.names.items():
            if pycompat.sysstr(name) not in names:
                continue
            # we will use the templatename as the color name since those
            # two should be the same
            for name in ns.names(self.repo, ctx.node()):
                labels.append((hglib.tounicode(name),
                               'log.%s' % hglib.tounicode(ns.colorname)))

        return labels

    def _getchanges(self, ctx: context.changectx) -> List[Tuple[str, str]]:
        """Return the MAR status for the given ctx."""
        labels = []

        # ctx is really thgrepo.thgchangectx, but not accessible here
        M, A, R = ctx.changesToParent(0)  # pytype: disable=attribute-error
        if A:
            labels.append((str(len(A)), 'log.added'))
        if M:
            labels.append((str(len(M)), 'log.modified'))
        if R:
            labels.append((str(len(R)), 'log.removed'))
        return labels

    def _getconv(self, ctx: context.changectx) -> str:
        if ctx.rev() is not None:
            extra = ctx.extra()
            cvt = extra.get(b'convert_revision', b'')
            if cvt:
                if cvt.startswith(b'svn:'):
                    return str(cvt.split(b'@')[-1], 'utf-8', 'replace')
                if len(cvt) == 40:
                    try:
                        binascii.unhexlify(cvt)
                        return hglib.tounicode(cvt[:12])
                    except TypeError:
                        pass
            cvt = extra.get(b'p4', b'')
            if cvt:
                return hglib.tounicode(cvt)
        return ''

    def _getphase(self, ctx: context.changectx) -> str:
        if ctx.rev() is None:
            return ''
        try:
            return hglib.tounicode(ctx.phasestr())
        except:
            return 'draft'

    def _gettopic(self, ctx: context.changectx) -> Optional[str]:
        return hglib.tounicode(ctx.extra().get(b'topic'))

    def _hasFileColumn(self) -> bool:
        return False # no FileColumn

    def allColumns(self) -> Tuple[str, ...]:
        if self._hasFileColumn():
            return ALLCOLUMNS
        else:
            return ALLCOLUMNS[:-1]

    def allColumnHeaders(self):
        if self._hasFileColumn():
            return COLUMNHEADERS
        else:
            return COLUMNHEADERS[:-1]

    _columnmap = {
        RevColumn: _getrev,
        BranchColumn: _getbranch,
        DescColumn: _getlog,
        AuthorColumn: _getauthor,
        TagsColumn: _gettags,
        LatestTagColumn: _getlatesttags,
        NodeColumn: lambda self, ctx: str(ctx),
        GitNodeColumn: lambda self, ctx: hglib.gitcommit_short(ctx),
        AgeColumn: lambda self, ctx: hglib.age(ctx.date()).decode('utf-8'),
        LocalDateColumn: lambda self, ctx: hglib.displaytime(ctx.date()).decode('utf-8'),
        UtcDateColumn: lambda self, ctx: hglib.utctime(ctx.date()),
        ConvertedColumn: _getconv,
        PhaseColumn: _getphase,
        TopicColumn: _gettopic,
        }


class FileRevModel(HgRepoListModel):
    """
    Model used to manage the list of revisions of a file, in file
    viewer of in diff-file viewer dialogs.
    """

    _defaultcolumns = ('Graph', 'Rev', 'Branch', 'Description', 'Author',
                       'Age', 'Filename')

    def __init__(self,
                 repoagent: RepoAgent,
                 filename: bytes,
                 parent: Optional[QObject] = None) -> None:
        self._filename = filename
        HgRepoListModel.__init__(self, repoagent, parent)

    def _hasFileColumn(self):
        return True

    def _createGraph(self):
        grapher = graph.filelog_grapher(self.repo, self._filename)
        return graph.Graph(self.repo, grapher)

    def indexLinkedFromRev(self, rev: Optional[int]) -> QModelIndex:
        """Index for the last changed revision before the specified revision

        This does not follow renames.
        """
        # as of Mercurial 2.6, workingfilectx.linkrev() does not work, and
        # this model has no virtual working-dir revision.
        if rev is None:
            rev = b'.'
        try:
            fctx = self.repo[rev][self._filename]
        except error.LookupError:
            return QModelIndex()
        return self.indexFromRev(fctx.linkrev())

    def fileData(self,
                 index: QModelIndex,
                 baseindex: QModelIndex = QModelIndex()) -> Union[filedata.FileData, filedata.PatchFileData]:
        """Displayable file data at the given index; baseindex specifies the
        revision where status is calculated from"""
        row = index.row()
        if not index.isValid() or row < 0 or row >= len(self.graph):
            return filedata.createNullData(self.repo)
        rev = self.graph[row].rev
        ctx = self.repo[rev]
        if baseindex.isValid():
            prev = self.graph[baseindex.row()].rev
            pctx = self.repo[prev]
        else:
            pctx = ctx.p1()
        filename = self.graph.filename(rev)
        if filename in pctx:
            status = 'M'
        else:
            status = 'A'
        return filedata.createFileData(ctx, pctx, filename, status)
