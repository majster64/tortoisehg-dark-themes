# Copyright (c) 2009-2010 LOGILAB S.A. (Paris, FRANCE).
# http://www.logilab.fr/ -- mailto:contact@logilab.fr
# Copyright 2010 Steve Borho <steve@borho.org>
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

from math import sin, cos, pi

from .qtcore import (
    QItemSelectionModel,
    QPoint,
    QPointF,
    QRectF,
    QSettings,
    QSize,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from .qtgui import (
    QAbstractItemView,
    QAction,
    QApplication,
    QBrush,
    QClipboard,
    QColor,
    QDialog,
    QDialogButtonBox,
    QFont,
    QFontMetrics,
    QFormLayout,
    QHeaderView,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QProxyStyle,
    QSpinBox,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTableView,
)

from mercurial import (
    error,
    pycompat,
)

from ..util import hglib
from ..util.i18n import _
from . import (
    graph,
    qtlib,
    repomodel,
)
from .theme import THEME

class HgRepoView(QTableView):

    revisionSelected = pyqtSignal(object)
    revisionActivated = pyqtSignal(object)
    menuRequested = pyqtSignal(QPoint, object)
    showMessage = pyqtSignal(str)
    columnsVisibilityChanged = pyqtSignal()

    def __init__(self, repoagent, cfgname, colselect, parent=None):
        QTableView.__init__(self, parent)
        self._repoagent = repoagent
        self.current_rev = -1
        self.cfgname = cfgname
        self.colselect = colselect
        self.setShowGrid(False)
        self.setWordWrap(False)

        vh = self.verticalHeader()
        vh.hide()
        vh.setMinimumSectionSize(0)  # we'll set fixed size later
        vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)

        header = self.horizontalHeader()
        header.setSectionsClickable(False)
        header.setSectionsMovable(True)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft)
        header.setHighlightSections(False)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self.headerMenuRequest)
        header.sectionMoved.connect(self.columnsVisibilityChanged)
        header.sectionMoved.connect(self._saveColumnSettings)

        self.createActions()
        
        if THEME.enabled:
            # Set delegate for all columns to apply selection background workaround
            self.setItemDelegate(SelectionWorkaroundDelegate(self))
            
        self.setItemDelegateForColumn(repomodel.GraphColumn,
                                      GraphDelegate(self))
        self.setItemDelegateForColumn(repomodel.DescColumn,
                                      LabeledDelegate(self))
        self.setItemDelegateForColumn(repomodel.ChangesColumn,
                                      LabeledDelegate(self, margin=0))

        self.setAcceptDrops(True)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)

        # do not pass self.style() to HgRepoViewStyle() because it would steal
        # the ownership from QApplication and cause SEGV after the deletion of
        # this widget.
        self.setStyle(HgRepoViewStyle())
        self._paletteswitcher = qtlib.PaletteSwitcher(self)

        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

        self.doubleClicked.connect(self.revActivated)
        if repoagent.configBool('tortoisehg', 'copy_hash_selection'):
            self.clicked.connect(self._copyHashToSelection)

    @property
    def repo(self):
        return self._repoagent.rawRepo()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        index = self.indexAt(event.pos())
        if not index.isValid():
            return
        if event.button() == Qt.MouseButton.MiddleButton:
            self.gotoAncestor(index)
            return
        QTableView.mousePressEvent(self, event)

    def contextMenuEvent(self, event):
        self.menuRequested.emit(event.globalPos(), self.selectedRevisions())

    def createActions(self):
        menu = QMenu(self)
        act = QAction(_('C&hoose Log Columns...'), self)
        act.triggered.connect(self.setHistoryColumns)
        menu.addAction(act)
        act = QAction(_('&Resize Columns'), self)
        act.triggered.connect(self._resizeIgnoreSettings)
        menu.addAction(act)
        self.headermenu = menu

    @pyqtSlot(QPoint)
    def headerMenuRequest(self, point):
        self.headermenu.exec(self.horizontalHeader().mapToGlobal(point))

    def setHistoryColumns(self):
        dlg = ColumnSelectDialog(self.colselect[1],
                                 self.model(), self.visibleColumns())
        dlg.setRowHeight(self.defaultRowHeight())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.setVisibleColumns(dlg.selectedColumns())
            self.resizeColumns()
            self.setDefaultRowHeight(dlg.rowHeight())
            self._saveColumnSettings()  # for new repository tab

    def _loadColumnSettings(self):
        model = self.model()
        assert model is not None
        s = QSettings()
        s.beginGroup(self.colselect[0])
        cols = qtlib.readStringList(s, 'columns')
        cols = [str(col) for col in cols]
        # Fixup older names for columns
        if 'Log' in cols:
            cols[cols.index('Log')] = 'Description'
            s.setValue('columns', cols)
        if 'ID' in cols:
            cols[cols.index('ID')] = 'Rev'
            s.setValue('columns', cols)
        s.endGroup()
        allcolumns = model.allColumns()
        validcols = [col for col in cols if col in allcolumns]
        if not validcols:
            validcols = model._defaultcolumns
        self.setVisibleColumns(validcols)

    @pyqtSlot()
    def _saveColumnSettings(self):
        s = QSettings()
        s.beginGroup(self.colselect[0])
        s.setValue('columns', self.visibleColumns())
        s.endGroup()

    def visibleColumns(self):
        model = self.model()
        hh = self.horizontalHeader()
        assert model is not None
        return [model.allColumns()[hh.logicalIndex(visualindex)]
                for visualindex in pycompat.xrange(hh.count()
                                                   - hh.hiddenSectionCount())]

    def setVisibleColumns(self, visiblecols):
        model = self.model()
        if not model or visiblecols == self.visibleColumns():
            return
        hh = self.horizontalHeader()
        hh.sectionMoved.disconnect(self.columnsVisibilityChanged)
        allcolumns = model.allColumns()
        for logicalindex, colname in enumerate(allcolumns):
            hh.setSectionHidden(logicalindex, colname not in visiblecols)
        for newvisualindex, colname in enumerate(visiblecols):
            logicalindex = allcolumns.index(colname)
            hh.moveSection(hh.visualIndex(logicalindex), newvisualindex)
        hh.sectionMoved.connect(self.columnsVisibilityChanged)
        self.columnsVisibilityChanged.emit()

    def setModel(self, model):
        oldmodel = self.model()
        QTableView.setModel(self, model)
        if type(oldmodel) is not type(model):
            # logical columns are vary by model class
            self._loadColumnSettings()
        #Check if the font contains the glyph needed by the model
        if not QFontMetrics(self.font()).inFont('\u2605'):
            model.unicodestar = False
        if not QFontMetrics(self.font()).inFont('\u2327'):
            model.unicodexinabox = False
        self.selectionModel().currentRowChanged.connect(self.onRowChange)
        self._rev_history = []
        self._rev_pos = -1
        self._in_history = False

    def _adjustRowHeight(self):
        """Resize rows to fit to LabeledDelegate item"""
        s = QSettings()
        s.beginGroup(self.cfgname)
        h = qtlib.readInt(s, 'row_height', default=-1)
        s.endGroup()

        if h < 0:
            h = self._fallbackRowHeight()
        self.setDefaultRowHeight(h)

    def _fallbackRowHeight(self):
        fm = QFontMetrics(self.font())
        return fm.height() + 2 + 2  # see _LabelsLayout and LabeledDelegate

    def defaultRowHeight(self):
        return self.verticalHeader().defaultSectionSize()

    def setDefaultRowHeight(self, height):
        self.verticalHeader().setDefaultSectionSize(height)

    @pyqtSlot()
    def _resizeIgnoreSettings(self):
        self.resizeColumns(False)

    @pyqtSlot()
    def resizeColumns(self, usesettings=True):
        if not self.model():
            return

        col_widths = []
        if usesettings:
            qs = QSettings()
            key = '%s/column_widths/%s' % (self.cfgname,
                                           hglib.shortrepoid(self.repo))
            try:
                col_widths = [int(w) for w in qtlib.readStringList(qs, key)]
            except ValueError:
                pass

        hh = self.horizontalHeader()
        hh.setStretchLastSection(False)
        self._resizeColumns(col_widths)
        hh.setStretchLastSection(True)

    def _resizeColumns(self, col_widths):
        # _resizeColumns misbehaves if called with last section streched
        hh = self.horizontalHeader()
        model = self.model()
        fontm = QFontMetrics(self.font())
        assert model is not None

        for c in range(model.columnCount()):
            if hh.isSectionHidden(c):
                continue
            if c < len(col_widths) and col_widths[c] > 0:
                w = col_widths[c]
            else:
                w = model.maxWidthValueForColumn(c)

            if isinstance(w, int):
                pass
            elif w is not None:
                w = fontm.horizontalAdvance(str(w) + 'w')
            else:
                w = super().sizeHintForColumn(c)
            self.setColumnWidth(c, w)

    def revFromindex(self, index):
        if not index.isValid():
            return
        model = self.model()
        if model and model.graph:
            row = index.row()
            gnode = model.graph[row]
            return gnode.rev

    def context(self, rev):
        return self.repo[rev]

    def _copyHashToSelection(self, index):
        rev = self.revFromindex(index)
        if rev is not None:
            clip = QApplication.clipboard()
            if clip.supportsSelection():
                clip.setText(str(self.repo[rev]), QClipboard.Mode.Selection)

    def revActivated(self, index):
        rev = self.revFromindex(index)
        if rev is not None:
            self.revisionActivated.emit(rev)

    def onRowChange(self, index, index_from):
        rev = self.revFromindex(index)
        if self.current_rev != rev and not self._in_history:
            del self._rev_history[self._rev_pos+1:]
            self._rev_history.append(rev)
            self._rev_pos = len(self._rev_history)-1
        self._in_history = False
        self.current_rev = rev
        self.revisionSelected.emit(rev)

    def selectedRevisions(self):
        """Return the list of selected revisions"""
        selmodel = self.selectionModel()
        return [self.revFromindex(i) for i in selmodel.selectedRows()]

    def gotoAncestor(self, index):
        rev = self.revFromindex(index)
        if rev is None or self.current_rev is None:
            return
        ctx = self.context(self.current_rev)
        ctx2 = self.context(rev)
        if ctx.thgmqunappliedpatch() or ctx2.thgmqunappliedpatch():
            return
        ancestor = ctx.ancestor(ctx2)
        self.showMessage.emit(_("Goto ancestor of %s and %s") % (
                                ctx.rev(), ctx2.rev()))
        self.goto(ancestor.rev())

    def canGoBack(self):
        return bool(self._rev_history and self._rev_pos > 0)

    def canGoForward(self):
        return bool(self._rev_history
                    and self._rev_pos < len(self._rev_history) - 1)

    def back(self):
        if self.canGoBack():
            model = self.model()
            assert model is not None
            self._rev_pos -= 1
            idx = model.indexFromRev(self._rev_history[self._rev_pos])
            if idx.isValid():
                self._in_history = True
                self.setCurrentIndex(idx)

    def forward(self):
        if self.canGoForward():
            model = self.model()
            assert model is not None
            self._rev_pos += 1
            idx = model.indexFromRev(self._rev_history[self._rev_pos])
            if idx.isValid():
                self._in_history = True
                self.setCurrentIndex(idx)

    def _symbolic_rev_to_numeric_rev(self, symbolic_rev):
        """Converts symbolic_rev into a numeric revision number for the local
        repository.

        symbolic_rev may be either:
        - a numeric revision
        - a mercurial commit hash
        - a git commit hash. This is looked up for only if no matches are found
          for the preceding cases, and the hg-git extension is loaded

        If no revision is found for symbolic_rev, the function raises an
        error.RepoError or an error.LookupError, which have to be handled
        by the caller.
        """
        if isinstance(symbolic_rev, int):
            return symbolic_rev

        if isinstance(symbolic_rev, pycompat.unicode):
            symbolic_rev = hglib.fromunicode(symbolic_rev)

        try:
            # look for a mercurial commit hash
            return hglib.revsymbol(self.repo, symbolic_rev).rev()
        except error.RepoError:
            if b'hggit' not in self.repo.extensions():
                # hg-git is not installed, do not try doing a gitnode() lookup
                raise
            # look for a git commit hash
            baseset = self.repo.revs(b'gitnode(%s)', symbolic_rev)

            if len(baseset) == 0:
                raise error.RepoLookupError(b"No revision found with gitnode"
                                            b"('%s')" % symbolic_rev)

            # There would not be any strict requirement to check for
            # len(baseset) > 1, since hg-git already raises a LookupError if it
            # gets passed an ambiguous hash.
            # However, leaving a case unhandled can always be a source of bugs
            # later, so let's be exhaustive instead.
            if len(baseset) > 1:
                raise error.AmbiguousPrefixLookupError(
                    symbolic_rev, self.repo.githandler.map_file,
                    b'Ambiguous commit hash')

            return baseset.first()

    def goto(self, rev):
        """
        Select revision 'rev' (can be anything understood by repo[])
        """
        try:
            rev = self._symbolic_rev_to_numeric_rev(rev)
        except error.FilteredRepoLookupError:
            self.showMessage.emit(_("Revision '%s' is hidden") % str(rev))
            return
        except error.RepoError:
            self.showMessage.emit(_("Can't find revision '%s'") % str(rev))
            return
        # When Mercurial is not able to disambiguate commit hash prefixes,
        # it raises an error.AmbiguousPrefixLookupError, which ultimately
        # comes from its revlog._partialmatch(). However, in the same
        # situation hg-git (currently 2135ddef6d6e) raises a less specific
        # error.LookupError from its revset_gitnode().
        #
        # We cannot narrow this clause until hg-git is updated.
        except error.LookupError as e:
            self.showMessage.emit(hglib.exception_str(e))
            return

        model = self.model()
        assert model is not None
        idx = model.indexFromRev(rev)
        if idx.isValid():
            flags = (QItemSelectionModel.SelectionFlag.ClearAndSelect
                     | QItemSelectionModel.SelectionFlag.Rows)
            self.selectionModel().setCurrentIndex(idx, flags)
            self.scrollTo(idx)

    def saveSettings(self, s = None):
        if not s:
            s = QSettings()

        s.beginGroup(self.cfgname)
        if self.defaultRowHeight() == self._fallbackRowHeight():
            s.remove('row_height')  # recalculate next time
        else:
            s.setValue('row_height', self.defaultRowHeight())
        s.endGroup()

        model = self.model()
        assert model is not None
        col_widths = []
        for c in range(model.columnCount()):
            col_widths.append(self.columnWidth(c))

        try:
            key = '%s/column_widths/%s' % (self.cfgname,
                                           hglib.shortrepoid(self.repo))
            s.setValue(key, col_widths)
        except OSError:
            pass

        self._saveColumnSettings()

    def showEvent(self, event):
        self._adjustRowHeight()
        super().showEvent(event)

    def enablefilterpalette(self, enable):
        self._paletteswitcher.enablefilterpalette(enable)


class HgRepoViewStyle(QProxyStyle):
    "Override a style's drawPrimitive method to customize the drop indicator"

    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PrimitiveElement.PE_IndicatorItemViewItemDrop:
            # Drop indicators should be painted using the full viewport width
            if option.rect.height() != 0:
                vp = widget.viewport().rect()
                painter.drawRect(vp.x(), option.rect.y(),
                                 vp.width() - 1, 0)
        else:
            super().drawPrimitive(element, option, painter,
                                                       widget)


def get_style(line_type, active):
    if line_type == graph.LINE_TYPE_GRAFT:
        return Qt.PenStyle.DashLine
    if line_type == graph.LINE_TYPE_OBSOLETE:
        return Qt.PenStyle.DotLine
    return Qt.PenStyle.SolidLine

def get_width(line_type, active):
    if line_type >= graph.LINE_TYPE_FAMILY or not active:
        return 1
    return 2

def _edge_color(edge, active):
    if not active or edge.linktype == graph.LINE_TYPE_FAMILY:
        return "gray"
    else:
        colors = graph.COLORS
        return colors[edge.color % len(colors)]


class SelectionWorkaroundDelegate(QStyledItemDelegate):
    """Delegate that applies Windows 11 PyQt6 selection background workaround"""

    def paint(self, painter, option, index):
        if THEME.enabled:
            option_state = option.state.value if hasattr(option.state, 'value') else option.state
            if option_state & qtlib.QtStateFlag.State_Selected:
                painter.fillRect(option.rect, THEME.selection_background)
        super().paint(painter, option, index)


class GraphDelegate(SelectionWorkaroundDelegate):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rowheight = 16  # updated to the actual height on paint()

    def _col2x(self, col):
        maxradius = max(self._rowheight // 2, 1)
        return maxradius * (col + 1)

    def _colcount(self, width):
        maxradius = max(self._rowheight // 2, 1)
        return (width + maxradius - 1) // maxradius

    def _dotradius(self):
        return 0.4 * self._rowheight

    def paint(self, painter, option, index):
        if THEME.enabled:
            super().paint(painter, option, index)
        else:
            QStyledItemDelegate.paint(self, painter, option, index)

        # update to the actual height that should be the same for all rows
        self._rowheight = option.rect.height()
        visibleend = self._colcount(option.rect.width())
        gnode = index.data(repomodel.GraphNodeRole)
        painter.save()
        try:
            painter.setClipRect(option.rect)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.translate(option.rect.topLeft())
            self._drawEdges(painter, index, gnode, visibleend)
            if gnode.x < visibleend:
                self._drawNode(painter, index, gnode)
        finally:
            painter.restore()

    def _drawEdges(self, painter, index, gnode, visibleend):
        h = self._rowheight
        dot_y = h // 2

        def isactive(e):
            m = index.model()
            return m.isActiveRev(e.startrev) and m.isActiveRev(e.endrev)
        def lineimportance(pe):
            return isactive(pe[1]), pe[1].importance

        for y1, y4, lines in ((dot_y, dot_y + h, gnode.bottomlines),
                              (dot_y - h, dot_y, gnode.toplines)):
            y2 = y1 + 1 * (y4 - y1) // 4
            ymid = (y1 + y4) // 2
            y3 = y1 + 3 * (y4 - y1) // 4

            # omit invisible lines
            lines = [((start, end), e) for (start, end), e in lines
                     if start < visibleend or end < visibleend]
            # remove hidden lines that can be partly visible due to antialiasing
            lines = list(dict(sorted(lines, key=lineimportance)).items())
            # still necessary to sort by importance because lines can partially
            # overlap near contact point
            lines.sort(key=lineimportance)

            for (start, end), e in lines:
                active = isactive(e)
                lpen = QPen(QColor(_edge_color(e, active)))
                lpen.setStyle(get_style(e.linktype, active))
                lpen.setWidth(get_width(e.linktype, active))
                painter.setPen(lpen)
                x1 = self._col2x(start)
                x2 = self._col2x(end)
                if x1 == x2:
                    painter.drawLine(x1, y1, x2, y4)
                else:
                    path = QPainterPath()
                    path.moveTo(x1, y1)
                    path.cubicTo(x1, y2,
                                 x1, y2,
                                 (x1 + x2) // 2, ymid)
                    path.cubicTo(x2, y3,
                                 x2, y3,
                                 x2, y4)
                    painter.drawPath(path)

    def _drawNode(self, painter, index, gnode):
        m = index.model()
        if not m.isActiveRev(gnode.rev):
            dot_color = QColor("gray")
            radius = self._dotradius() * 0.8
        else:
            fg = index.data(Qt.ItemDataRole.ForegroundRole)
            if not fg:
                # work around integrity error in HgRepoListModel, which may
                # provide a valid index for stripped revision and data()
                # returns None due to RepoLookupError. (#4451)
                return
            dot_color = QBrush(fg).color()
            radius = self._dotradius()
        dotcolor = dot_color.lighter()
        pencolor = dot_color.darker()
        truewhite = QColor("white")
        white = QColor("white")
        fillcolor = gnode.rev is None and white or dotcolor

        pen = QPen(pencolor)
        pen.setWidthF(1.5)
        painter.setPen(pen)

        centre_x = self._col2x(gnode.x)
        centre_y = self._rowheight // 2

        def circle(r):
            rect = QRectF(centre_x - r,
                          centre_y - r,
                          2 * r, 2 * r)
            painter.drawEllipse(rect)

        def closesymbol(s):
            rect_ = QRectF(centre_x - 1.5 * s, centre_y - 0.5 * s, 3 * s, s)
            painter.drawRect(rect_)

        def polygon(r, sides, shift=0):
            """draw a regular poligon, pointing upward"""
            phaseincr = (2 * pi / sides)
            phaseshift = phaseincr * shift
            phases = [phaseshift + phaseincr * n for n in range(sides)]
            points = [QPointF(centre_x + r * -sin(p), centre_y + r * -cos(p))
                      for p in phases]
            points.append(points[-1])
            poly = QPolygonF(points)
            painter.drawPolygon(poly)

        def diamond(r):
            poly = QPolygonF([QPointF(centre_x - r, centre_y),
                              QPointF(centre_x, centre_y - r),
                              QPointF(centre_x + r, centre_y),
                              QPointF(centre_x, centre_y + r),
                              QPointF(centre_x - r, centre_y),])
            painter.drawPolygon(poly)

        def square(r):
            rect = QRectF(centre_x - r,
                          centre_y - r,
                          2 * r, 2 * r)
            painter.drawRect(rect)

        if gnode.shape == graph.NODE_SHAPE_APPLIEDPATCH:
            symbolsize = radius / 1.5
            symbol = diamond
        elif gnode.shape == graph.NODE_SHAPE_UNAPPLIEDPATCH:
            symbolsize = radius / 1.5
            fillcolor = QColor('#dddddd')
            painter.setPen(fillcolor)
            symbol = diamond
        elif gnode.shape == graph.NODE_SHAPE_CLOSEDBRANCH:
            symbolsize = 0.5 * radius
            symbol = closesymbol
        elif gnode.shape == graph.NODE_SHAPE_REVISION_SECRET:
            symbolsize = 0.45 * radius
            symbol = square
        elif gnode.shape == graph.NODE_SHAPE_REVISION_DRAFT:
            symbolsize = 0.57 * radius
            symbol = lambda size: polygon(size, 5)
        else:
            symbolsize = 0.5 * radius
            symbol = circle

        if gnode.faded:
            painter.setBrush(truewhite)
            painter.setPen(truewhite)
            white.setAlpha(64)
            fillcolor.setAlpha(64)
            symbol(symbolsize)
            pencolor.setAlpha(64)
            pen.setColor(pencolor)
            painter.setPen(pen)
        if gnode.wdparent and gnode.shape != graph.NODE_SHAPE_CLOSEDBRANCH:
            painter.setBrush(white)
            symbol(2 * 0.9 * symbolsize)
        painter.setBrush(fillcolor)
        symbol(symbolsize)

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        gnode = index.data(repomodel.GraphNodeRole)
        if gnode:
            # return width for current height assuming that row height
            # is calculated first (mimic width-for-height policy)
            return QSize(self._col2x(gnode.cols), max(size.height(), 16))
        else:
            return size


class _LabelsLayout:
    """Lay out and render text labels"""

    def __init__(self, labels, font, margin=2):
        self._labels = labels
        self._margin = margin
        if font.bold():
            # cancel bold of working-directory row
            font = QFont(font)
            font.setBold(False)
        self._font = font
        fm = QFontMetrics(font)
        self._twidths = [fm.horizontalAdvance(t) for t, _s in labels]
        self._th = fm.height()
        self._padw = 2
        self._padh = 1  # may overwrite horizontal frame to fit row

    def width(self):
        space = 2 * self._padw + self._margin
        return sum(self._twidths) + len(self._labels) * space - self._margin

    def height(self):
        return self._th + 2 * self._padh

    def draw(self, painter, pos):
        painter.save()
        try:
            painter.translate(pos)
            self._drawLabels(painter)
        finally:
            painter.restore()

    def _drawLabels(self, painter):
        th = self._th
        padw = self._padw
        padh = self._padh

        painter.setFont(self._font)
        x = 0
        for (text, style), tw in zip(self._labels, self._twidths):
            lw = tw + 2 * padw
            lh = th + 2 * padh
            # draw bevel, background and text in order
            bg = qtlib.getbgcoloreffect(style)
            painter.fillRect(x, 0, lw, lh, bg.darker(110))
            painter.fillRect(x + 1, 1, lw - 2, lh - 2, bg.lighter(110))
            painter.fillRect(x + 2, 2, lw - 4, lh - 4, bg)
            painter.setPen(qtlib.gettextcoloreffect(style))
            painter.drawText(x + padw, padh, tw, th, 0, text)
            x += lw + self._margin


class LabeledDelegate(SelectionWorkaroundDelegate):
    """Render text labels in place of icon/pixmap decoration"""

    def __init__(self, parent=None, margin=2):
        super().__init__(parent)
        self._margin = margin

    def _makeLabelsLayout(self, labels, option):
        return _LabelsLayout(labels, option.font, self._margin)

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        labels = index.data(repomodel.LabelsRole)
        if not labels:
            return
        lay = self._makeLabelsLayout(labels, option)
        option.decorationSize = QSize(lay.width(), lay.height())
        if isinstance(option, QStyleOptionViewItem):
            option.features |= QStyleOptionViewItem.ViewItemFeature.HasDecoration

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        labels = index.data(repomodel.LabelsRole)
        if not labels:
            return

        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        if option.widget:
            style = option.widget.style()
        else:
            style = QApplication.style()
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemDecoration, option,
                                    option.widget)

        # for maximum readability, use vivid color regardless of option.state
        lay = self._makeLabelsLayout(labels, option)
        painter.save()
        try:
            painter.setClipRect(option.rect)
            lay.draw(painter, rect.topLeft())
        finally:
            painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        # give additional margins for each row (even if it has no labels
        # because all rows must have the same height)
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        lay = self._makeLabelsLayout([], option)
        return QSize(size.width(), max(size.height(), lay.height() + 2))


class ColumnSelectDialog(QDialog):
    def __init__(self, name, model, curcolumns, parent=None):
        QDialog.__init__(self, parent)
        assert model is not None
        all = model.allColumns()
        colnames = dict(model.allColumnHeaders())

        self.setWindowTitle(name)
        self.setWindowFlags(self.windowFlags() & \
                            ~Qt.WindowType.WindowContextHelpButtonHint)
        self.setMinimumSize(250, 265)

        disabled = [c for c in all if c not in curcolumns]

        layout = QFormLayout()
        layout.setContentsMargins(5, 5, 5, 5)
        self.setLayout(layout)

        list = QListWidget()
        # enabled cols are listed in sorted order, disabled are listed last
        for c in curcolumns + disabled:
            item = QListWidgetItem(colnames[c])
            item.columnid = c
            item.setFlags(Qt.ItemFlag.ItemIsSelectable |
                          Qt.ItemFlag.ItemIsEnabled |
                          Qt.ItemFlag.ItemIsDragEnabled |
                          Qt.ItemFlag.ItemIsUserCheckable)
            if c in curcolumns:
                item.setCheckState(Qt.CheckState.Checked)
            else:
                item.setCheckState(Qt.CheckState.Unchecked)
            list.addItem(item)
        list.setDragDropMode(QListView.DragDropMode.InternalMove)
        layout.addRow(list)
        self.list = list

        layout.addRow(QLabel(_('Drag to change order')))

        self._rowHeightEdit = edit = QSpinBox(self)
        edit.setRange(10, 50)  # just limit to reasonable range
        edit.setSuffix(_(' px'))
        layout.addRow(_('Row height'), edit)

        # dialog buttons
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def selectedColumns(self):
        cols = []
        for i in pycompat.xrange(self.list.count()):
            item = self.list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                # TODO: better to use data(role) instead
                cols.append(item.columnid)  # pytype: disable=attribute-error
        return cols

    def rowHeight(self):
        return self._rowHeightEdit.value()

    def setRowHeight(self, height):
        self._rowHeightEdit.setValue(height)
