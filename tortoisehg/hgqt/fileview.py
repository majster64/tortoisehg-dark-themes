# fileview.py - File diff, content, and annotation display widget
#
# Copyright 2010 Steve Borho <steve@borho.org>
# Copyright (C) 2026 Peter Demcak <majster64@gmail.com> (dark theme)
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import difflib
import os
import re
import typing
import sys

from typing import (
    cast,
)

from . import qsci as Qsci
from .qtcore import (
    QElapsedTimer,
    QEvent,
    QObject,
    QPoint,
    QSettings,
    QTimer,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from .qtgui import (
    QAction,
    QActionGroup,
    QApplication,
    QColor,
    QFontMetrics,
    QFrame,
    QInputDialog,
    QKeySequence,
    QLabel,
    QMouseEvent,
    QPainter,
    QPalette,
    QPixmap,
    QPen,
    QShortcut,
    QStyle,
    QToolBar,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from mercurial import (
    pycompat,
    util,
)

from mercurial.utils import (
    dateutil,
)

from ..util import (
    colormap,
    hglib,
)
from ..util.i18n import _
from . import (
    blockmatcher,
    cmdcore,
    filedata,
    fileencoding,
    lexers,
    qscilib,
    qtlib,
    visdiff,
)

if typing.TYPE_CHECKING:
    from typing import (
        Optional,
    )

from .theme import THEME

qsci = qscilib.Scintilla

# _NullMode is the fallback mode to display error message or repository history
_NullMode = 0
DiffMode = 1
FileMode = 2
AnnMode = 3

_LineNumberMargin = 1
_AnnotateMargin = 2
_ChunkSelectionMargin = 4

_ChunkStartMarker = 0
_IncludedChunkStartMarker = 1
_ExcludedChunkStartMarker = 2
_InsertedLineMarker = 3
_ReplacedLineMarker = 4
_ExcludedLineMarker = 5
_FirstAnnotateLineMarker = 6  # to 31

_ChunkSelectionMarkerMask = (
    (1 << _IncludedChunkStartMarker) | (1 << _ExcludedChunkStartMarker))

class HgFileView(QFrame):
    "file diff, content, and annotation viewer"

    linkActivated = pyqtSignal(str)
    fileDisplayed = pyqtSignal(str, str)
    showMessage = pyqtSignal(str)
    revisionSelected = pyqtSignal(int)
    shelveToolExited = pyqtSignal()
    chunkSelectionChanged = pyqtSignal()

    grepRequested = pyqtSignal(str, dict)
    """Emitted (pattern, opts) when user request to search changelog"""

    def __init__(self, repoagent, parent):
        QFrame.__init__(self, parent)
        framelayout = QVBoxLayout(self)
        framelayout.setContentsMargins(0,0,0,0)

        l = QHBoxLayout()
        l.setContentsMargins(0,0,0,0)
        l.setSpacing(0)

        self._repoagent = repoagent
        repo = repoagent.rawRepo()

        self.topLayout = QVBoxLayout()

        self.labelhbox = hbox = QHBoxLayout()
        hbox.setContentsMargins(0,0,0,0)
        hbox.setSpacing(2)
        self.topLayout.addLayout(hbox)

        self.diffToolbar = QToolBar(_('Diff Toolbar'))
        self.diffToolbar.setIconSize(qtlib.smallIconSize())
        self.diffToolbar.setStyleSheet(qtlib.tbstylesheet)
        hbox.addWidget(self.diffToolbar)

        self.filenamelabel = w = QLabel()
        w.setWordWrap(True)
        f = w.textInteractionFlags()
        w.setTextInteractionFlags(f | Qt.TextInteractionFlag.TextSelectableByMouse)
        w.linkActivated.connect(self.linkActivated)
        hbox.addWidget(w, 1)

        self.extralabel = w = QLabel()
        w.setWordWrap(True)
        w.linkActivated.connect(self.linkActivated)
        self.topLayout.addWidget(w)
        w.hide()

        framelayout.addLayout(self.topLayout)
        framelayout.addLayout(l, 1)

        hbox = QHBoxLayout()
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(0)
        l.addLayout(hbox)

        self.blk = blockmatcher.BlockList(self)
        self.blksearch = blockmatcher.BlockList(self)
        self.sci = qscilib.Scintilla(self)

        if THEME.enabled:
            self.sci.setPaper(THEME.background)
            self.sci.setColor(THEME.text)

            self.sci.setSelectionBackgroundColor(THEME.text_selection)
            self.sci.setSelectionForegroundColor(THEME.text)

            self.sci.setCaretForegroundColor(THEME.caret_foreground)

            self.sci.setMarginsBackgroundColor(THEME.backgroundLighter)
            self.sci.setMarginsForegroundColor(THEME.text_margin)

            self.sci.setWhitespaceBackgroundColor(THEME.background)
            self.sci.setWhitespaceForegroundColor(THEME.text_margin)

        hbox.addWidget(self.blk)
        hbox.addWidget(self.sci, 1)
        hbox.addWidget(self.blksearch)

        self.sci.cursorPositionChanged.connect(self._updateDiffActions)
        self.sci.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sci.customContextMenuRequested.connect(self._onMenuRequested)
        self.sci.SCN_ZOOM.connect(self._updateScrollBar)

        self.blk.linkScrollBar(self.sci.verticalScrollBar())
        self.blk.setVisible(False)
        self.blksearch.linkScrollBar(self.sci.verticalScrollBar())
        self.blksearch.setVisible(False)

        self.sci.setReadOnly(True)
        self.sci.setUtf8(True)
        self.sci.installEventFilter(qscilib.KeyPressInterceptor(self))
        self.sci.setCaretLineVisible(False)

        self.sci.markerDefine(qsci.MarkerSymbol.Invisible, _ChunkStartMarker)

        # hide margin 0 (markers)
        self.sci.setMarginType(0, qsci.MarginType.SymbolMargin)
        self.sci.setMarginWidth(0, 0)

        self.searchbar = qscilib.SearchToolBar()
        self.searchbar.hide()
        self.searchbar.searchRequested.connect(self.find)
        self.searchbar.conditionChanged.connect(self.highlightText)
        self.addActions(self.searchbar.editorActions())
        self.layout().addWidget(self.searchbar)

        self._fd = self._nullfd = filedata.createNullData(repo)
        self._lostMode = _NullMode
        self._lastSearch = '', False

        self._modeToggleGroup = QActionGroup(self)
        self._modeToggleGroup.triggered.connect(self._setModeByAction)
        self._modeActionMap = {}
        for mode, icon, tooltip in [
                (DiffMode, 'view-diff', _('View change as unified diff '
                                          'output')),
                (FileMode, 'view-file', _('View change in context of file')),
                (AnnMode, 'view-annotate', _('Annotate with revision numbers')),
                (_NullMode, '', '')]:
            if icon:
                a = self._modeToggleGroup.addAction(qtlib.geticon(icon), '')
            else:
                a = self._modeToggleGroup.addAction('')
            self._modeActionMap[mode] = a
            a.setCheckable(True)
            a.setData(mode)
            a.setToolTip(tooltip)

        diffc = _DiffViewControl(self.sci, self)
        diffc.chunkMarkersBuilt.connect(self._updateDiffActions)
        filec = _FileViewControl(repo.ui, self.sci, self.blk, self)
        filec.chunkMarkersBuilt.connect(self._updateDiffActions)
        messagec = _MessageViewControl(self.sci, self)
        messagec.forceDisplayRequested.connect(self._forceDisplayFile)
        annotatec = _AnnotateViewControl(repoagent, self.sci, self._fd, self)
        annotatec.showMessage.connect(self.showMessage)
        annotatec.editSelectedRequested.connect(self._editSelected)
        annotatec.grepRequested.connect(self.grepRequested)
        annotatec.searchSelectedTextRequested.connect(self._searchSelectedText)
        annotatec.setSourceRequested.connect(self._setSource)
        annotatec.visualDiffRevisionRequested.connect(self._visualDiffRevision)
        annotatec.visualDiffToLocalRequested.connect(self._visualDiffToLocal)
        chunkselc = _ChunkSelectionViewControl(self.sci, self._fd, self)
        chunkselc.chunkSelectionChanged.connect(self.chunkSelectionChanged)

        self._activeViewControls = []
        self._modeViewControlsMap = {
            DiffMode: [diffc],
            FileMode: [filec],
            AnnMode: [filec, annotatec],
            _NullMode: [messagec],
            }
        self._chunkSelectionViewControl = chunkselc  # enabled as necessary

        # Next/Prev diff (in full file mode)
        self.actionNextDiff = a = QAction(qtlib.geticon('go-down'),
                                          _('Next Diff'), self)
        a.setShortcut('Alt+Down')
        a.setToolTip('%s (%s)' % (a.text(), a.shortcut().toString()))
        a.triggered.connect(self._nextDiff)
        self.actionPrevDiff = a = QAction(qtlib.geticon('go-up'),
                                          _('Previous Diff'), self)
        a.setShortcut('Alt+Up')
        a.setToolTip('%s (%s)' % (a.text(), a.shortcut().toString()))
        a.triggered.connect(self._prevDiff)

        self._parentToggleGroup = QActionGroup(self)
        self._parentToggleGroup.triggered.connect(self._setParentRevision)
        for text in '12':
            a = self._parentToggleGroup.addAction(text)
            a.setCheckable(True)
            a.setShortcut('Ctrl+Shift+%s' % text)

        self.actionFind = self.searchbar.toggleViewAction()
        self.actionFind.setIcon(qtlib.geticon('edit-find'))
        self.actionFind.setToolTip(_('Toggle display of text search bar'))
        self.actionFind.triggered.connect(self._onSearchbarTriggered)
        qtlib.newshortcutsforstdkey(QKeySequence.StandardKey.Find, self,
                                    self._showSearchbar)

        self.actionShelf = QAction('Shelve', self)
        self.actionShelf.setIcon(qtlib.geticon('hg-shelve'))
        self.actionShelf.setToolTip(_('Open shelve tool'))
        self.actionShelf.setVisible(False)
        self.actionShelf.triggered.connect(self._launchShelve)

        self._actionAutoTextEncoding = a = QAction(_('&Auto Detect'), self)
        a.setCheckable(True)
        self._textEncodingGroup = fileencoding.createActionGroup(self)
        self._textEncodingGroup.triggered.connect(self._applyTextEncoding)

        tb = self.diffToolbar
        tb.addActions(self._parentToggleGroup.actions())
        tb.addSeparator()
        tb.addActions(self._modeToggleGroup.actions()[:-1])
        tb.addSeparator()
        tb.addAction(self.actionNextDiff)
        tb.addAction(self.actionPrevDiff)
        tb.addAction(filec.gotoLineAction())
        tb.addSeparator()
        tb.addAction(self.actionFind)
        tb.addAction(self.actionShelf)

        self._clearMarkup()
        self._changeEffectiveMode(_NullMode)

        repoagent.configChanged.connect(self._applyRepoConfig)
        self._applyRepoConfig()

    def _resetMargins(self):
        for m in (_LineNumberMargin, _AnnotateMargin, _ChunkSelectionMargin):
            self.sci.setMarginWidth(m, 0)

    @property
    def repo(self):
        return self._repoagent.rawRepo()

    @pyqtSlot()
    def _launchShelve(self):
        from tortoisehg.hgqt import shelve
        # TODO: pass self._fd.canonicalFilePath()
        dlg = shelve.ShelveDialog(self._repoagent, self)
        dlg.finished.connect(dlg.deleteLater)
        dlg.exec()
        self.shelveToolExited.emit()

    def setShelveButtonVisible(self, visible):
        self.actionShelf.setVisible(visible)

    def loadSettings(self, qs, prefix):
        self.sci.loadSettings(qs, prefix)
        self._actionAutoTextEncoding.setChecked(
            qtlib.readBool(qs, prefix + '/autotextencoding', True))
        enc = qtlib.readString(qs, prefix + '/textencoding')
        if enc:
            try:
                # prefer repository-specific encoding if specified
                enc = fileencoding.contentencoding(self.repo.ui, enc)
            except LookupError:
                enc = ''
        if enc:
            self._changeTextEncoding(enc)

    def saveSettings(self, qs, prefix):
        self.sci.saveSettings(qs, prefix)
        qs.setValue(prefix + '/autotextencoding', self._autoTextEncoding())
        qs.setValue(prefix + '/textencoding', self._textEncoding())

    @pyqtSlot()
    def _applyRepoConfig(self):
        self.sci.setIndentationWidth(self.repo.tabwidth)
        self.sci.setTabWidth(self.repo.tabwidth)
        enc = fileencoding.contentencoding(self.repo.ui, self._textEncoding())
        self._changeTextEncoding(enc)

    def isChangeSelectionEnabled(self):
        chunkselc = self._chunkSelectionViewControl
        controls = self._modeViewControlsMap[DiffMode]
        return chunkselc in controls

    def enableChangeSelection(self, enable):
        'Enable the use of a selection margin when a diff view is active'
        # Should only be called with True from the commit tool when it is in
        # a 'commit' mode and False for other uses
        if self.isChangeSelectionEnabled() == bool(enable):
            return
        chunkselc = self._chunkSelectionViewControl
        controls = self._modeViewControlsMap[DiffMode]
        if enable:
            controls.append(chunkselc)
        else:
            controls.remove(chunkselc)
        if self._effectiveMode() == DiffMode:
            self._changeEffectiveMode(DiffMode)

    @pyqtSlot(QAction)
    def _setModeByAction(self, action):
        'One of the mode toolbar buttons has been toggled'
        mode = action.data()
        self._lostMode = _NullMode
        self._changeEffectiveMode(mode)
        self._displayLoaded(self._fd)

    def _effectiveMode(self):
        a = self._modeToggleGroup.checkedAction()
        return a.data()

    def _changeEffectiveMode(self, mode):
        self._modeActionMap[mode].setChecked(True)

        newcontrols = list(self._modeViewControlsMap[mode])
        for c in reversed(self._activeViewControls):
            if c not in newcontrols:
                c.close()

        if THEME.enabled:
            self._resetMargins()

        for c in newcontrols:
            if c not in self._activeViewControls:
                c.open()
        self._activeViewControls = newcontrols

    def _restrictModes(self, available):
        'Disable modes based on content constraints'
        available.add(_NullMode)
        for m, a in self._modeActionMap.items():
            a.setEnabled(m in available)
        self._fallBackToAvailableMode()

    def _fallBackToAvailableMode(self):
        if self._lostMode and self._modeActionMap[self._lostMode].isEnabled():
            self._changeEffectiveMode(self._lostMode)
            self._lostMode = _NullMode
            return
        curmode = self._effectiveMode()
        if curmode and self._modeActionMap[curmode].isEnabled():
            return
        fallbackmode = next(iter(a.data()
                            for a in self._modeToggleGroup.actions()
                            if a.isEnabled()))
        if not self._lostMode:
            self._lostMode = curmode
        self._changeEffectiveMode(fallbackmode)

    def _modeAction(self, mode):
        if not mode:
            raise ValueError('null mode cannot be set explicitly')
        try:
            return self._modeActionMap[mode]
        except KeyError:
            raise ValueError('invalid mode: %r' % mode)

    def setMode(self, mode):
        """Switch view to DiffMode/FileMode/AnnMode if available for the current
        content; otherwise it will be switched later"""
        action = self._modeAction(mode)
        if action.isEnabled():
            if not action.isChecked():
                action.trigger()  # implies _setModeByAction()
        else:
            self._lostMode = mode

    @pyqtSlot(QAction)
    def _setParentRevision(self, action: QAction) -> None:
        fd = self._fd
        ctx = fd.rawContext()
        pctx = {'1': ctx.p1, '2': ctx.p2}[action.text()]()
        self.display(fd.createRebased(pctx))

    def _updateFileDataActions(self):
        fd = self._fd
        ctx = fd.rawContext()
        parents = ctx.parents()
        ismerge = len(parents) == 2
        self._parentToggleGroup.setVisible(ismerge)
        tooltips = [_('Show changes from first parent'),
                    _('Show changes from second parent')]
        for a, pctx, tooltip in zip(self._parentToggleGroup.actions(),
                                    parents, tooltips):
            firstline = hglib.longsummary(pctx.description())
            a.setToolTip('%s:\n%s [%d:%s] %s'
                         % (tooltip, hglib.tounicode(pctx.branch()),
                            pctx.rev(), pctx, firstline))
            a.setChecked(fd.baseRev() == pctx.rev())

    def _autoTextEncoding(self):
        return self._actionAutoTextEncoding.isChecked()

    def _textEncoding(self):
        return fileencoding.checkedActionName(self._textEncodingGroup)

    @pyqtSlot()
    def _applyTextEncoding(self):
        self._fd.setTextEncoding(self._textEncoding())
        self._displayLoaded(self._fd)

    def _changeTextEncoding(self, enc):
        fileencoding.checkActionByName(self._textEncodingGroup, enc)
        if not self._fd.isNull():
            self._applyTextEncoding()

    @pyqtSlot(str, int, int)
    def _setSource(self, path, rev, line):
        # BUG: not work for subrepo
        self.revisionSelected.emit(rev)
        ctx = self.repo[rev]
        fd = filedata.createFileData(ctx, ctx.p1(), hglib.fromunicode(path))
        self.display(fd)
        self.showLine(line)

    def showLine(self, line):
        if line < self.sci.lines():
            self.sci.setCursorPosition(line, 0)

    def _moveAndScrollToLine(self, line):
        self.sci.setCursorPosition(line, 0)
        self.sci.verticalScrollBar().setValue(line)

    def filePath(self):
        return self._fd.filePath()

    @pyqtSlot()
    def clearDisplay(self):
        self._displayLoaded(self._nullfd)

    def _clearMarkup(self):
        self.sci.clear()
        self.sci.clearMarginText()
        self.sci.markerDeleteAll()
        self.blk.clear()
        self.blksearch.clear()
        # Setting the label to ' ' rather than clear() keeps the label
        # from disappearing during refresh, and tool layouts bouncing
        self.filenamelabel.setText(' ')
        self.extralabel.hide()
        self._updateDiffActions()
        self._updateScrollBar()

    @pyqtSlot()
    def _forceDisplayFile(self):
        self._fd.load(self.isChangeSelectionEnabled(), force=True)
        self._displayLoaded(self._fd)

    def display(self, fd):
        if not fd.isLoaded():
            fd.load(self.isChangeSelectionEnabled())
        fd.setTextEncoding(self._textEncoding())
        if self._autoTextEncoding():
            fd.detectTextEncoding()
            fileencoding.checkActionByName(self._textEncodingGroup,
                                           fd.textEncoding())
        self._displayLoaded(fd)

    def _displayLoaded(self, fd):
        if self._fd.filePath() == fd.filePath():
            # Get the last visible line to restore it after reloading the editor
            lastCursorPosition = self.sci.getCursorPosition()
            lastScrollPosition = self.sci.firstVisibleLine()
        else:
            lastCursorPosition = (0, 0)
            lastScrollPosition = 0

        self._updateDisplay(fd)

        # Recover the last cursor/scroll position
        self.sci.setCursorPosition(*lastCursorPosition)
        # Make sure that lastScrollPosition never exceeds the amount of
        # lines on the editor
        lastScrollPosition = min(lastScrollPosition,  self.sci.lines() - 1)
        self.sci.verticalScrollBar().setValue(lastScrollPosition)

    def _updateDisplay(self, fd):
        self._fd = fd

        self._clearMarkup()
        self._updateFileDataActions()

        if fd.elabel:
            self.extralabel.setText(fd.elabel)
            self.extralabel.show()
        else:
            self.extralabel.hide()
        self.filenamelabel.setText(fd.flabel)

        availablemodes = set()
        if fd.isValid():
            if fd.diff:
                availablemodes.add(DiffMode)
            if fd.contents:
                availablemodes.add(FileMode)
            if (fd.contents and (fd.rev() is None or fd.rev() >= 0)
                and fd.fileStatus() != 'R'):
                availablemodes.add(AnnMode)
        self._restrictModes(availablemodes)

        for c in self._activeViewControls:
            c.display(fd)

        self.highlightText(*self._lastSearch)
        self.fileDisplayed.emit(fd.filePath(), fd.fileText())

        self.blksearch.syncPageStep()
        self._updateScrollBar()

    @pyqtSlot(str, bool, bool, bool)
    def find(self, exp, icase=True, wrap=False, forward=True):
        self.sci.find(exp, icase, wrap, forward)

    @pyqtSlot(str, bool)
    def highlightText(self, match, icase=False):
        self._lastSearch = match, icase
        self.sci.highlightText(match, icase)
        blk = self.blksearch
        blk.clear()
        blk.setUpdatesEnabled(False)
        blk.clear()
        for l in self.sci.highlightLines:
            blk.addBlock('s', l, l + 1)
        blk.setVisible(bool(match))
        blk.setUpdatesEnabled(True)

    def _loadSelectionIntoSearchbar(self):
        text = self.sci.selectedText()
        if text:
            self.searchbar.setPattern(text)

    @pyqtSlot(bool)
    def _onSearchbarTriggered(self, checked):
        if checked:
            self._loadSelectionIntoSearchbar()

    @pyqtSlot()
    def _showSearchbar(self):
        self._loadSelectionIntoSearchbar()
        self.searchbar.show()

    @pyqtSlot()
    def _searchSelectedText(self):
        self.searchbar.search(self.sci.selectedText())
        self.searchbar.show()

    def verticalScrollBar(self):
        return self.sci.verticalScrollBar()

    def _findNextChunk(self):
        mask = 1 << _ChunkStartMarker
        line = self.sci.getCursorPosition()[0]
        return self.sci.markerFindNext(line + 1, mask)

    def _findPrevChunk(self):
        mask = 1 << _ChunkStartMarker
        line = self.sci.getCursorPosition()[0] - 1
        if line < 0:
            return -1
        return self.sci.markerFindPrevious(line, mask)

    @pyqtSlot()
    def _nextDiff(self):
        line = self._findNextChunk()
        if line >= 0:
            self._moveAndScrollToLine(line)

    @pyqtSlot()
    def _prevDiff(self):
        line = self._findPrevChunk()
        if line >= 0:
            self._moveAndScrollToLine(line)

    @pyqtSlot()
    def _updateDiffActions(self):
        self.actionNextDiff.setEnabled(self._findNextChunk() >= 0)
        self.actionPrevDiff.setEnabled(self._findPrevChunk() >= 0)

    @pyqtSlot(str, int, int)
    def _editSelected(self, path, rev, line):
        """Open editor to show the specified file"""
        path = hglib.fromunicode(path)
        base = visdiff.snapshot(self.repo, [path], self.repo[rev])[0]
        files = [os.path.join(base, path)]
        pattern = hglib.fromunicode(self.sci.selectedText())
        qtlib.editfiles(self.repo, files, line, pattern, self)

    def _visualDiff(self, path, **opts):
        path = hglib.fromunicode(path)
        dlg = visdiff.visualdiff(self.repo.ui, self.repo, [path], opts)
        if dlg:
            dlg.exec()

    @pyqtSlot(str, int)
    def _visualDiffRevision(self, path, rev):
        self._visualDiff(path, change=rev)

    @pyqtSlot(str, int)
    def _visualDiffToLocal(self, path, rev):
        self._visualDiff(path, rev=[str(rev)])

    @pyqtSlot(QPoint)
    def _onMenuRequested(self, point):
        menu = self._createContextMenu(point)
        menu.exec(self.sci.viewport().mapToGlobal(point))
        menu.setParent(None)

    def _createContextMenu(self, point):
        menu = self.sci.createEditorContextMenu()
        m = menu.addMenu(_('E&ncoding'))
        m.addAction(self._actionAutoTextEncoding)
        m.addSeparator()
        fileencoding.addActionsToMenu(m, self._textEncodingGroup)

        line = self.sci.lineNearPoint(point)

        selection = self.sci.selectedText()
        def sreq(**opts):
            return lambda: self.grepRequested.emit(selection, opts)

        if self._effectiveMode() != AnnMode:
            if selection:
                menu.addSeparator()
                menu.addAction(_('&Search in Current File'),
                               self._searchSelectedText)
                menu.addAction(_('Search in All &History'), sreq(all=True))

        for c in self._activeViewControls:
            c.setupContextMenu(menu, line)
        return menu

    @pyqtSlot()
    def _updateScrollBar(self):
        lexer = self.sci.lexer()
        if lexer:
            font = self.sci.lexer().font(0)
        else:
            font = self.sci.font()
        fm = QFontMetrics(font)

        lines = self.sci.text().splitlines()
        if lines:
            # assume that the longest line has the largest width;
            # fm.width() is too slow to apply to each line.
            longestline = max(lines, key=len)
            maxWidth = fm.horizontalAdvance(longestline)
        else:
            maxWidth = 0
        # setScrollWidth() expects the value to be > 0
        self.sci.setScrollWidth(max(maxWidth, 1))


class _AbstractViewControl(QObject):
    """Provide the mode-specific view in HgFileView"""

    def open(self):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError

    def display(self, fd):
        raise NotImplementedError

    def setupContextMenu(self, menu, line):
        pass

    def _parentWidget(self) -> Optional[QWidget]:
        p = self.parent()
        assert p is None or isinstance(p, QWidget)
        return p

    def _forceDarkViewport(self):
        sci = self._sci

        # Kill edge column
        sci.setEdgeMode(qsci.EdgeNone)
        sci.setEdgeColumn(0)

        # Force dark viewport background
        vp = sci.viewport()
        vp.setAutoFillBackground(True)

        pal = vp.palette()
        pal.setColor(QPalette.Window, THEME.background)
        vp.setPalette(pal)

    def _forceDarkViewportLater(self):
        if not THEME.enabled:
            return
    
        self._sci.setPaper(THEME.background)
        self._sci.setMarginsBackgroundColor(THEME.backgroundLighter)
        self._sci.setMarginsForegroundColor(THEME.text_margin)

        QTimer.singleShot(0, self._applyDarkFixes)


    def _applyDarkFixes(self):
        if not THEME.enabled:
            return
        
        sci = self._sci

        # viewport background
        vp = sci.viewport()
        vp.setAutoFillBackground(True)
        pal = vp.palette()
        pal.setColor(QPalette.Window, THEME.background)
        vp.setPalette(pal)

        # selection (compatible with old Scintilla)
        sci.SendScintilla(qsci.SCI_SETSELBACK, 1, THEME.selection_background)
        sci.SendScintilla(qsci.SCI_SETSELFORE, 1, THEME.selection_text)

        sci.setCaretForegroundColor(THEME.caret_foreground)

        # set colors for lexer
        lexer = sci.lexer()
        if lexer:
            fg = THEME.text
            lexer.setDefaultColor(fg)

            if hasattr(lexer, 'Operator'):
                lexer.setColor(fg, lexer.Operator)

            for style in (1, 2, 10, 11):
                lexer.setColor(fg, style)

_diffHeaderRegExp = re.compile(r"^@@ -[0-9]+,[0-9]+ \+[0-9]+,[0-9]+ @@")

class _DiffViewControl(_AbstractViewControl):
    """Display the unified diff in HgFileView"""

    chunkMarkersBuilt = pyqtSignal()

    def __init__(self, sci, parent=None):
        super().__init__(parent)
        self._sci = sci
        self._buildtimer = QTimer(self)
        self._buildtimer.timeout.connect(self._buildMarker)
        self._linestoprocess = []
        self._firstlinetoprocess = 0

    def open(self):
        self._sci.markerDefine(qsci.MarkerSymbol.Background, _ChunkStartMarker)

        if THEME.enabled:
            self._sci.setMarkerBackgroundColor(THEME.backgroundLighter, _ChunkStartMarker)
        elif qtlib.isDarkTheme(self._sci.palette()):
            self._sci.setMarkerBackgroundColor(QColor('#204820'),
                                               _ChunkStartMarker)
        else:
            self._sci.setMarkerBackgroundColor(QColor('#B0FFA0'),
                                               _ChunkStartMarker)
        self._sci.setLexer(lexers.difflexer(self))

        if THEME.enabled:
            self._sci.setMarginType(_ChunkSelectionMargin, qsci.MarginType.SymbolMargin)
            self._sci.setMarginWidth(_ChunkSelectionMargin, 14)
            self._sci.setMarginMarkerMask(_ChunkSelectionMargin, _ChunkSelectionMarkerMask)
            self._sci.setMarginSensitivity(_ChunkSelectionMargin, True)

            # LEXER - controls text colors
            lexer = lexers.difflexer(self)
            self._sci.setLexer(lexer)

            self._sci.SendScintilla(qsci.SCI_STYLESETBACK, qsci.STYLE_DEFAULT, THEME.background)
            self._sci.SendScintilla(qsci.SCI_STYLESETFORE, _ChunkStartMarker, THEME.diff_text) # color of unmodified text
            self._sci.SendScintilla(qsci.SCI_STYLESETFORE, _ReplacedLineMarker, THEME.diff_start) # @@
            self._sci.SendScintilla(qsci.SCI_STYLESETFORE, _ExcludedLineMarker, THEME.diff_removed)
            self._sci.SendScintilla(qsci.SCI_STYLESETFORE, _FirstAnnotateLineMarker, THEME.diff_added) # color of added text

            # CRITICAL - must be called after setLexer to fix white stripe
            self._forceDarkViewportLater()

    def close(self):
        self._sci.markerDefine(qsci.MarkerSymbol.Invisible, _ChunkStartMarker)
        self._sci.setLexer(None)
        self._buildtimer.stop()

    def display(self, fd):
        self._sci.setText(fd.diffText())
        self._startBuildMarker()

    def _startBuildMarker(self):
        self._linestoprocess = self._sci.text().splitlines()
        self._firstlinetoprocess = 0
        self._buildtimer.start()

    @pyqtSlot()
    def _buildMarker(self):
        self._sci.setUpdatesEnabled(False)

        # Process linesPerBlock lines at a time
        linesPerBlock = 100
        # Look for lines matching the "diff header"
        for n, line in enumerate(self._linestoprocess[:linesPerBlock]):
            if _diffHeaderRegExp.match(line):
                diffLine = self._firstlinetoprocess + n
                self._sci.markerAdd(diffLine, _ChunkStartMarker)
        self._linestoprocess = self._linestoprocess[linesPerBlock:]
        self._firstlinetoprocess += linesPerBlock

        self._sci.setUpdatesEnabled(True)

        if not self._linestoprocess:
            self._buildtimer.stop()
            self.chunkMarkersBuilt.emit()


class _FileViewControl(_AbstractViewControl):
    """Display the file content with chunk markers in HgFileView"""

    chunkMarkersBuilt = pyqtSignal()

    def __init__(self, ui, sci, blk, parent=None):
        super().__init__(parent)
        self._ui = ui
        self._sci = sci
        self._blk = blk
        self._sci.setMarginLineNumbers(_LineNumberMargin, True)
        self._sci.setMarginWidth(_LineNumberMargin, 0)

        # define markers for colorize zones of diff
        self._sci.markerDefine(qsci.MarkerSymbol.Background, _InsertedLineMarker)
        self._sci.markerDefine(qsci.MarkerSymbol.Background, _ReplacedLineMarker)
        if THEME.enabled:
            self._sci.setMarkerBackgroundColor(THEME.diff_added_bg, _InsertedLineMarker)
            self._sci.setMarkerBackgroundColor(THEME.diff_added2_bg, _ReplacedLineMarker)
        elif qtlib.isDarkTheme(self._sci.palette()):
            self._sci.setMarkerBackgroundColor(QColor('#204820'),
                                               _InsertedLineMarker)
            self._sci.setMarkerBackgroundColor(QColor('#202050'),
                                               _ReplacedLineMarker)
        else:
            self._sci.setMarkerBackgroundColor(QColor('#B0FFA0'),
                                               _InsertedLineMarker)
            self._sci.setMarkerBackgroundColor(QColor('#A0A0FF'),
                                               _ReplacedLineMarker)

        self._actionGotoLine = a = QAction(qtlib.geticon('go-jump'),
                                           _('Go to Line'), self)
        a.setEnabled(False)
        a.setShortcut('Ctrl+J')
        a.setToolTip('%s (%s)' % (a.text(), a.shortcut().toString()))
        a.triggered.connect(self._gotoLineDialog)

        self._buildtimer = QTimer(self)
        self._buildtimer.timeout.connect(self._buildMarker)
        self._opcodes = []

    def open(self):
        self._blk.setVisible(True)
        self._actionGotoLine.setEnabled(True)

        if THEME.enabled:
            # to skip white strip
            self._forceDarkViewportLater()

            # This controls the color of margin text
            self._sci.SendScintilla(qsci.SCI_STYLESETFORE, qsci.STYLE_LINENUMBER, THEME.text_margin)
            self._sci.SendScintilla(qsci.SCI_STYLESETBACK, qsci.STYLE_LINENUMBER, THEME.backgroundLighter)

            # dark theme - global, not for margin
            self._sci.setMarginsBackgroundColor(THEME.backgroundLighter)
            self._sci.setMarginsForegroundColor(THEME.text_margin)

            # width by number of lines
            width = len(str(self._sci.lines())) + 2
            self._sci.setMarginLineNumbers(_LineNumberMargin, True)
            self._sci.setMarginWidth(_LineNumberMargin, 'M' * width)

    def close(self):
        self._blk.setVisible(False)
        self._sci.setMarginWidth(_LineNumberMargin, 0)
        self._sci.setLexer(None)
        self._actionGotoLine.setEnabled(False)
        self._buildtimer.stop()

    def display(self, fd):
        if fd.contents:
            filename = fd.filePath()
            lexer = lexers.getlexer(self._ui, filename, fd.contents, self)
            newFont = qtlib.getfont('fontlog').font()

            if THEME.enabled:
                if lexer:
                    lexer.setDefaultPaper(THEME.background)
                    lexer.setDefaultColor(THEME.text)

            self._sci.setFont(newFont)
            self._sci.setLexer(lexer)
            self._sci.setFont(newFont)

            self._sci.setText(fd.fileText())

        self._sci.setMarginsFont(self._sci.font())
        width = len(str(self._sci.lines())) + 2  # 2 for margin
        self._sci.setMarginWidth(_LineNumberMargin, 'M' * width)
        self._blk.syncPageStep()

        if fd.contents and fd.olddata:
            self._startBuildMarker(fd)
        else:
            self._buildtimer.stop()  # in case previous request not finished

        if THEME.enabled:
            self._forceDarkViewportLater() # fixes ocasional white strip for FileView

    @util.propertycache
    def _margin_style(self):
        s = Qsci.QsciStyle(qscilib.STYLE_FILEVIEW_MARGIN)
        s.setPaper(THEME.backgroundLighter)
        s.setColor(THEME.text_margin)
        s.setFont(self._sci.font())
        return s

    def _startBuildMarker(self, fd):
        # use the difflib.SequenceMatcher, which returns a set of opcodes
        # that must be parsed
        olddata = fd.olddata.splitlines()
        newdata = fd.contents.splitlines()
        diff = difflib.SequenceMatcher(None, olddata, newdata)
        self._opcodes = diff.get_opcodes()
        self._buildtimer.start()

    @pyqtSlot()
    def _buildMarker(self):
        self._sci.setUpdatesEnabled(False)
        self._blk.setUpdatesEnabled(False)

        for tag, alo, ahi, blo, bhi in self._opcodes[:30]:
            if tag in ('replace', 'insert'):
                self._sci.markerAdd(blo, _ChunkStartMarker)
            if tag == 'replace':
                self._blk.addBlock('x', blo, bhi)
                for i in range(blo, bhi):
                    self._sci.markerAdd(i, _ReplacedLineMarker)
            elif tag == 'insert':
                self._blk.addBlock('+', blo, bhi)
                for i in range(blo, bhi):
                    self._sci.markerAdd(i, _InsertedLineMarker)
            elif tag in ('equal', 'delete'):
                pass
            else:
                raise ValueError('unknown tag %r' % (tag,))
        self._opcodes = self._opcodes[30:]

        self._sci.setUpdatesEnabled(True)
        self._blk.setUpdatesEnabled(True)

        if not self._opcodes:
            self._buildtimer.stop()
            self.chunkMarkersBuilt.emit()

    def gotoLineAction(self):
        return self._actionGotoLine

    @pyqtSlot()
    def _gotoLineDialog(self):
        last = self._sci.lines()
        if last == 0:
            return
        cur = self._sci.getCursorPosition()[0] + 1
        line, ok = QInputDialog.getInt(self._parentWidget(), _('Go to Line'),
                                       _('Enter line number (1 - %d)') % last,
                                       cur, 1, last)
        if ok:
            self._sci.setCursorPosition(line - 1, 0)
            self._sci.ensureLineVisible(line - 1)
            self._sci.setFocus()


class _MessageViewControl(_AbstractViewControl):
    """Display error message or repository history in HgFileView"""

    forceDisplayRequested = pyqtSignal()

    def __init__(self, sci, parent=None):
        super().__init__(parent)
        self._sci = sci
        self._forceviewindicator = None

    def open(self):
        self._sci.setFont(qtlib.getfont('fontlog').font())
        self._sci.setLexer(None)

    def close(self):
        pass

    def display(self, fd):
        if not fd.isValid():
            errormsg = fd.error or ''
            self._sci.setText(errormsg)
            forcedisplaymsg = filedata.forcedisplaymsg
            linkstart = errormsg.find(forcedisplaymsg)
            if linkstart >= 0:
                # add the link to force to view the data anyway
                self._setupForceViewIndicator()
                self._sci.fillIndicatorRange(
                    0, linkstart, 0, linkstart + len(forcedisplaymsg),
                    self._forceviewindicator)
        elif fd.ucontents:
            # subrepo summary and perhaps other data
            self._sci.setText(fd.ucontents)

    def _setupForceViewIndicator(self):
        if self._forceviewindicator is not None:
            return
        self._forceviewindicator = self._sci.indicatorDefine(
            self._sci.IndicatorStyle.PlainIndicator)
        self._sci.setIndicatorDrawUnder(True, self._forceviewindicator)
        self._sci.setIndicatorForegroundColor(
            QColor('blue'), self._forceviewindicator)
        # delay until next event-loop in order to complete mouse release
        self._sci.SCN_INDICATORRELEASE.connect(self._requestForceDisplay,
                                               Qt.ConnectionType.QueuedConnection)

    @pyqtSlot()
    def _requestForceDisplay(self):
        self._sci.setText(_('Please wait while the file is opened ...'))
        # Wait a little to ensure that the "wait message" is displayed
        QTimer.singleShot(10, self.forceDisplayRequested)


class _AnnotateViewControl(_AbstractViewControl):
    """Display annotation margin and colorize file content in HgFileView"""

    showMessage = pyqtSignal(str)

    editSelectedRequested = pyqtSignal(str, int, int)
    grepRequested = pyqtSignal(str, dict)
    searchSelectedTextRequested = pyqtSignal()
    setSourceRequested = pyqtSignal(str, int, int)
    visualDiffRevisionRequested = pyqtSignal(str, int)
    visualDiffToLocalRequested = pyqtSignal(str, int)

    def __init__(self, repoagent, sci, fd, parent=None):
        super().__init__(parent)
        self._repoagent = repoagent
        self._cmdsession = cmdcore.nullCmdSession()
        self._sci = sci
        self._sci.setMarginType(_AnnotateMargin, qsci.MarginType.TextMarginRightJustified)
        self._sci.setMarginSensitivity(_AnnotateMargin, True)
        self._sci.marginClicked.connect(self._onMarginClicked)

        self._fd = fd
        self._links = []  # by line
        self._revmarkers = {}  # by rev
        self._lastrev = -1

        self._lastmarginclick = QElapsedTimer()

        self._initAnnotateOptionActions()
        self._loadAnnotateSettings()

        self._isdarktheme = THEME.enabled or qtlib.isDarkTheme(self._sci.palette())

    def open(self):
        self._sci.viewport().installEventFilter(self)
        if THEME.enabled:
            self._forceDarkViewportLater()

    def close(self):
        self._sci.viewport().removeEventFilter(self)
        self._sci.setMarginWidth(_AnnotateMargin, 0)
        self._sci.markerDeleteAll()
        self._cmdsession.abort()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        # Python wrapper is deleted immediately before QEvent.Destroy
        try:
            sciviewport = self._sci.viewport()
        except RuntimeError:
            sciviewport = None
        if watched is sciviewport:
            if event.type() == QEvent.Type.MouseMove:
                line = self._sci.lineNearPoint(cast(QMouseEvent, event).pos())
                self._emitRevisionHintAtLine(line)
            return False
        return super().eventFilter(watched, event)

    def _loadAnnotateSettings(self):
        s = QSettings()
        wb = "Annotate/"
        for a in self._annoptactions:
            a.setChecked(qtlib.readBool(s, wb + a.data()))
        if not any(a.isChecked() for a in self._annoptactions):
            self._annoptactions[-1].setChecked(True)  # 'rev' by default

    def _saveAnnotateSettings(self):
        s = QSettings()
        wb = "Annotate/"
        for a in self._annoptactions:
            s.setValue(wb + a.data(), a.isChecked())

    def _initAnnotateOptionActions(self):
        self._annoptactions = []
        for name, field in [(_('Show &Author'), 'author'),
                            (_('Show &Date'), 'date'),
                            (_('Show &Revision'), 'rev')]:
            a = QAction(name, self, checkable=True)
            a.setData(field)
            a.triggered.connect(self._updateAnnotateOption)
            self._annoptactions.append(a)

    @pyqtSlot()
    def _updateAnnotateOption(self):
        # make sure at least one option is checked
        if not any(a.isChecked() for a in self._annoptactions):
            self.sender().setChecked(True)

        self._updateView()
        self._saveAnnotateSettings()

    def _buildRevMarginTexts(self):
        def getauthor(fctx):
            return hglib.tounicode(hglib.username(fctx.user()))
        def getdate(fctx):
            return hglib.tounicode(dateutil.shortdate(fctx.date()))
        if self._fd.rev() is None:
            p1rev = self._fd.parentRevs()[0]
            revfmt = '%%%dd%%c' % len(str(p1rev))
            def getrev(fctx):
                if fctx.rev() is None:
                    return revfmt % (p1rev, '+')
                else:
                    return revfmt % (fctx.rev(), ' ')
        else:
            revfmt = '%%%dd' % len(str(self._fd.rev()))
            def getrev(fctx):
                return revfmt % fctx.rev()

        aformat = [str(a.data()) for a in self._annoptactions
                   if a.isChecked()]
        annfields = {
            'rev': getrev,
            'author': getauthor,
            'date': getdate,
        }
        annfunc = [annfields[n] for n in aformat]

        uniqfctxs = {fctx for fctx, _origline in self._links}
        return {fctx.rev(): ' : '.join(f(fctx) for f in annfunc)
                    for fctx in uniqfctxs}

    def _emitRevisionHintAtLine(self, line):
        if line < 0 or line >= len(self._links):
            return
        fctx = self._links[line][0]
        if fctx.rev() != self._lastrev:
            filename = hglib.fromunicode(self._fd.canonicalFilePath())
            s = hglib.get_revision_desc(fctx, filename)
            self.showMessage.emit(s)
            self._lastrev = fctx.rev()

    def _repoAgentForFile(self):
        rpath = self._fd.repoRootPath()
        if not rpath:
            return self._repoagent
        return self._repoagent.subRepoAgent(rpath)

    def display(self, fd):
        if self._fd == fd and self._links:
            self._updateView()
            return
        self._fd = fd
        del self._links[:]
        self._cmdsession.abort()
        repoagent = self._repoAgentForFile()
        cmdline = hglib.buildcmdargs('annotate', fd.canonicalFilePath(),
                                     rev=hglib.escaperev(fd.rev(), 'wdir()'),
                                     text=True, file=True,
                                     number=True, line_number=True, T='pickle')
        self._cmdsession = sess = repoagent.runCommand(cmdline, self)
        sess.setCaptureOutput(True)
        sess.commandFinished.connect(self._onAnnotateFinished)

    @pyqtSlot(int)
    def _onAnnotateFinished(self, ret):
        sess = self._cmdsession
        if not sess.isFinished():
            # new request is already running
            return
        if ret != 0:
            return
        repo = self._repoAgentForFile().rawRepo()
        data = util.pickle.loads(bytes(sess.readAll()))
        links = []
        fctxcache = {}  # (path, rev): fctx
        for l in data[0][b'lines']:
            path, rev, lineno = l[b'path'], l[b'rev'], l[b'lineno']
            try:
                fctx = fctxcache[path, rev]
            except KeyError:
                fctx = fctxcache[path, rev] = repo[rev][path]
            links.append((fctx, lineno))
        self._links = links
        self._updateView()

    def _updateView(self):
        if not self._links:
            return
        revtexts = self._buildRevMarginTexts()
        self._updaterevmargin(revtexts)
        self._updatemarkers()
        self._updatemarginwidth(revtexts)

    def _updaterevmargin(self, revtexts):
        """Update the content of margin area showing revisions"""
        s = self._margin_style
        # Workaround to set style of the current sci widget.
        # QsciStyle sends style data only to the first sci widget.
        # See qscintilla2/Qt4/qscistyle.cpp
        self._sci.SendScintilla(qsci.SCI_STYLESETBACK,
                                s.style(), s.paper())
        self._sci.SendScintilla(qsci.SCI_STYLESETFONT,
                                s.style(),
                                s.font().family().encode('utf-8'))
        self._sci.SendScintilla(qsci.SCI_STYLESETSIZE,
                                s.style(), s.font().pointSize())
                                
        if THEME.enabled:
            max_line = len(str(len(self._links)))

            n = len(self._links)

            # fill only real annotate lines
            for i in range(n):
                fctx, _origline = self._links[i]
                lineno = f"{i + 1:>{max_line}}"
                rev = revtexts.get(fctx.rev(), "")
                self._sci.setMarginText(i, f"{lineno}  {rev}", s)

            # clear any extra Scintilla lines (usually the last blank line)
            for i in range(n, self._sci.lines()):
                self._sci.setMarginText(i, "", s)
            
        else:
            for i, (fctx, _origline) in enumerate(self._links):
                    self._sci.setMarginText(i, revtexts[fctx.rev()], s)
        
    def _updatemarkers(self):
        """Update markers which colorizes each line"""
        self._redefinemarkers()
        for i, (fctx, _origline) in enumerate(self._links):
            m = self._revmarkers.get(fctx.rev())
            if m is not None:
                self._sci.markerAdd(i, m)

    def _make_simple_annotate_palette(self, filectxs, now, maxcolors, background):
        """
        Generate a simple age-based annotate palette.

        Newer revisions are lighter, older are darker.
        Colors are derived from THEME.background.
        Returns dict {QColor: [fctxs]} compatible with colormap.makeannotatepalette.
        """
        uniq = sorted(set(filectxs), key=lambda fctx: -fctx.date()[0])

        if not uniq:
            return {}

        maxcolors = max(1, min(maxcolors, len(uniq)))

        newest = uniq[0].date()[0]
        oldest = uniq[-1].date()[0]
        span = max(1.0, float(newest - oldest))

        h, s, l, a = background.getHslF()

        buckets = {}
        for fctx in uniq:
            age = newest - fctx.date()[0]
            t = age / span
            idx = int(t * (maxcolors - 1))
            buckets.setdefault(idx, []).append(fctx)

        L_MIN = 0.08
        L_MAX = 0.22

        h0, _, _, a = background.getHslF()

        palette = {}
        for idx, fctxs in buckets.items():
            t = idx / max(1, maxcolors - 1)
            l2 = L_MAX * (1.0 - t) + L_MIN * t

            h = (h0 + t * 300.0) % 360.0 / 360.0
            color = QColor.fromHslF(h, 0.35, l2, a)

            palette[color.name()] = fctxs

        return palette

    def _redefinemarkers(self):
        """Redefine line markers according to the current revs"""
        curdate = self._fd.rawContext().date()[0]

        # make sure to colorize at least 1 year
        mindate = curdate - 365 * 24 * 60 * 60

        self._revmarkers.clear()
        filectxs = iter(fctx for fctx, _origline in self._links)
        maxcolors = 32 - _FirstAnnotateLineMarker

        if THEME.enabled:
            palette = self._make_simple_annotate_palette(
                filectxs,
                curdate,
                maxcolors,
                THEME.background
            )
        else:
            palette = colormap.makeannotatepalette(filectxs, curdate,
                                               maxcolors=maxcolors, maxhues=8,
                                               maxsaturations=16,
                                               mindate=mindate,
                                               isdarktheme=self._isdarktheme)
        

        for i, (color, fctxs) in enumerate(palette.items()):
            m = _FirstAnnotateLineMarker + i
            self._sci.markerDefine(qsci.MarkerSymbol.Background, m)
            self._sci.setMarkerBackgroundColor(QColor(color), m)
            for fctx in fctxs:
                self._revmarkers[fctx.rev()] = m

    @util.propertycache
    def _margin_style(self):
        """Style for margin area"""
        s = Qsci.QsciStyle(qscilib.STYLE_FILEVIEW_MARGIN)
        
        if THEME.enabled:
            s.setPaper(THEME.backgroundLighter)
        else:
            s.setPaper(QApplication.palette().color(QPalette.ColorRole.Window))
        
        s.setFont(self._sci.font())
        return s

    def _updatemarginwidth(self, revtexts):
        self._sci.setMarginsFont(self._sci.font())
        # add 2 for margin
        maxwidth = 2 + max(len(s) for s in revtexts.values())
        self._sci.setMarginWidth(_AnnotateMargin, 'M' * maxwidth)

    def setupContextMenu(self, menu, line):
        menu.addSeparator()
        annoptsmenu = menu.addMenu(_('Annotate Op&tions'))
        annoptsmenu.addActions(self._annoptactions)

        if line < 0 or line >= len(self._links):
            return

        menu.addSeparator()

        fctx, line = self._links[line]
        selection = self._sci.selectedText()
        if selection:
            def sreq(**opts):
                return lambda: self.grepRequested.emit(selection, opts)
            menu.addSeparator()
            annsearchmenu = menu.addMenu(_('Search Selected Text'))
            a = annsearchmenu.addAction(_('In Current &File'))
            a.triggered.connect(self.searchSelectedTextRequested)
            annsearchmenu.addAction(_('In &Current Revision'), sreq(rev='.'))
            annsearchmenu.addAction(_('In &Original Revision'),
                                    sreq(rev=fctx.rev()))
            annsearchmenu.addAction(_('In All &History'), sreq(all=True))

        data = [hglib.tounicode(fctx.path()), fctx.rev(), line]

        def annorig():
            self.setSourceRequested.emit(*data)
        def editorig():
            self.editSelectedRequested.emit(*data)
        def difflocal():
            self.visualDiffToLocalRequested.emit(data[0], data[1])
        def diffparent():
            self.visualDiffRevisionRequested.emit(data[0], data[1])

        menu.addSeparator()
        anngotomenu = menu.addMenu(_('Go to'))
        annviewmenu = menu.addMenu(_('View File at'))
        anndiffmenu = menu.addMenu(_('Diff File to'))
        anngotomenu.addAction(_('&Originating Revision'), annorig)
        annviewmenu.addAction(_('&Originating Revision'), editorig)
        anndiffmenu.addAction(_('&Local'), difflocal)
        anndiffmenu.addAction(_('&Parent Revision'), diffparent)
        for pfctx in fctx.parents():
            pdata = [hglib.tounicode(pfctx.path()), pfctx.changectx().rev(),
                     line]
            def annparent(data):
                self.setSourceRequested.emit(*data)
            def editparent(data):
                self.editSelectedRequested.emit(*data)
            for name, func, smenu in [(_('&Parent Revision (%d)') % pdata[1],
                                  annparent, anngotomenu),
                               (_('&Parent Revision (%d)') % pdata[1],
                                  editparent, annviewmenu)]:
                def add(name, func):
                    action = smenu.addAction(name)
                    action.data = pdata
                    action.run = lambda: func(action.data)
                    action.triggered.connect(action.run)
                add(name, func)

    #@pyqtSlot(int, int, Qt.KeyboardModifier)
    def _onMarginClicked(self, margin, line, state):
        if margin != _AnnotateMargin:
            return

        interval = QApplication.doubleClickInterval()
        lastclick = self._lastmarginclick
        if (state == Qt.KeyboardModifier.ControlModifier
            or (lastclick.isValid() and not lastclick.hasExpired(interval))):
            if line >= len(self._links):
                # empty line next to the last line
                return
            fctx, line = self._links[line]
            self.setSourceRequested.emit(
                hglib.tounicode(fctx.path()), fctx.rev(), line)
        else:
            lastclick.restart()

            # mimic the default "border selection" behavior,
            # which is disabled when you use setMarginSensitivity()
            if state == Qt.KeyboardModifier.ShiftModifier:
                r = self._sci.getSelection()
                sellinetop, selchartop, sellinebottom, selcharbottom = r
                if sellinetop <= line:
                    sline = sellinetop
                    eline = line + 1
                else:
                    sline = line
                    eline = sellinebottom
                    if selcharbottom != 0:
                        eline += 1
            else:
                sline = line
                eline = line + 1
            self._sci.setSelection(sline, 0, eline, 0)


class _ChunkSelectionViewControl(_AbstractViewControl):
    """Display chunk selection margin and colorize chunks in HgFileView"""

    chunkSelectionChanged = pyqtSignal()

    def __init__(self, sci, fd, parent=None):
        super().__init__(parent)
        self._sci = sci
        p = qtlib.getcheckboxpixmap(QStyle.StateFlag.State_On, QColor('#B0FFA0'), sci)
        self._sci.markerDefine(p, _IncludedChunkStartMarker)
        p = qtlib.getcheckboxpixmap(QStyle.StateFlag.State_Off, QColor('#B0FFA0'), sci)
        self._sci.markerDefine(p, _ExcludedChunkStartMarker)

        if THEME.enabled:
            # Fix rendering of checkboxes
            p = qtlib.getcheckboxpixmap(QStyle.StateFlag.State_On, THEME.background, sci)
            self._patch_checkbox_pixmap(p)
            self._sci.markerDefine(p, _IncludedChunkStartMarker)

            p = qtlib.getcheckboxpixmap(QStyle.StateFlag.State_Off, THEME.background, sci)
            self._patch_checkbox_pixmap(p)
            self._sci.markerDefine(p, _ExcludedChunkStartMarker)

        self._sci.markerDefine(qsci.MarkerSymbol.Background, _ExcludedLineMarker)
        if THEME.enabled:
            bg, fg = THEME.diff_selected, THEME.diff_text
        elif qtlib.isDarkTheme(self._sci.palette()):
            bg, fg = QColor(44, 44, 44), QColor(86, 86, 86)
        else:
            bg, fg = QColor('lightgrey'), QColor('darkgrey')
        self._sci.setMarkerBackgroundColor(bg, _ExcludedLineMarker)
        self._sci.setMarkerForegroundColor(fg, _ExcludedLineMarker)
        self._sci.setMarginType(_ChunkSelectionMargin, qsci.MarginType.SymbolMargin)
        self._sci.setMarginMarkerMask(_ChunkSelectionMargin,
                                      _ChunkSelectionMarkerMask)
        self._sci.setMarginSensitivity(_ChunkSelectionMargin, True)
        self._sci.marginClicked.connect(self._onMarginClicked)

        self._actmarkexcluded = a = QAction(_('&Mark Excluded Changes'), self)
        a.setCheckable(True)
        a.setChecked(qtlib.readBool(QSettings(), 'changes-mark-excluded'))
        a.triggered.connect(self._updateChunkIndicatorMarks)
        self._excludeindicator = -1
        self._updateChunkIndicatorMarks(a.isChecked())
        self._sci.setIndicatorDrawUnder(True, self._excludeindicator)
        self._sci.setIndicatorForegroundColor(QColor('gray'),
                                              self._excludeindicator)

        self._toggleshortcut = a = QShortcut(Qt.Key.Key_Space, sci)
        a.setContext(Qt.ShortcutContext.WidgetShortcut)
        a.setEnabled(False)
        a.activated.connect(self._toggleCurrentChunk)

        self._fd = fd
        self._chunkatline = {}

    def _patch_checkbox_pixmap(self, pm):
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing, False)

        painter.setPen(QPen(THEME.diff_text))
        painter.drawRect(1, 0, pm.width() - 1 - 2, pm.height() - 1)

        painter.end()

    def open(self):
        self._sci.setMarginWidth(_ChunkSelectionMargin, 15)

        if THEME.enabled:
            self._sci.setMarginWidth(_ChunkSelectionMargin, 14) # Fix artifact on 0-th pixel

        self._toggleshortcut.setEnabled(True)

    def close(self):
        self._sci.setMarginWidth(_ChunkSelectionMargin, 0)
        self._toggleshortcut.setEnabled(False)

    def display(self, fd):
        self._fd = fd
        self._chunkatline.clear()
        if not fd.changes:
            return
        for chunk in fd.changes.hunks:
            self._chunkatline[chunk.lineno] = chunk
            if THEME.enabled:
                self._updateMarkerDark(chunk)
            else:
                self._updateMarker(chunk)

    def _updateMarker(self, chunk):
        excludemsg = ' ' + _('(excluded from the next commit)')
        # markerAdd() does not check if the specified marker is already
        # present, but markerDelete() does
        m = self._sci.markersAtLine(chunk.lineno)
        inclmarked = m & (1 << _IncludedChunkStartMarker)
        exclmarked = m & (1 << _ExcludedChunkStartMarker)

        if chunk.excluded and not exclmarked:
            self._sci.setReadOnly(False)
            llen = self._sci.lineLength(chunk.lineno)  # in bytes
            self._sci.insertAt(excludemsg, chunk.lineno, llen - 1)
            self._sci.setReadOnly(True)

            self._sci.markerDelete(chunk.lineno, _IncludedChunkStartMarker)
            self._sci.markerAdd(chunk.lineno, _ExcludedChunkStartMarker)
            for i in pycompat.xrange(chunk.linecount - 1):
                self._sci.markerAdd(chunk.lineno + i + 1, _ExcludedLineMarker)
            self._sci.fillIndicatorRange(chunk.lineno + 1, 0,
                                         chunk.lineno + chunk.linecount, 0,
                                         self._excludeindicator)

        if not chunk.excluded and exclmarked:
            self._sci.setReadOnly(False)
            llen = self._sci.lineLength(chunk.lineno)  # in bytes
            mlen = len(excludemsg.encode('utf-8'))  # in bytes
            pos = self._sci.positionFromLineIndex(chunk.lineno, llen - mlen - 1)
            self._sci.SendScintilla(qsci.SCI_SETTARGETSTART, pos)
            self._sci.SendScintilla(qsci.SCI_SETTARGETEND, pos + mlen)
            self._sci.SendScintilla(qsci.SCI_REPLACETARGET, 0, b'')
            self._sci.setReadOnly(True)

        if not chunk.excluded and not inclmarked:
            self._sci.markerDelete(chunk.lineno, _ExcludedChunkStartMarker)
            self._sci.markerAdd(chunk.lineno, _IncludedChunkStartMarker)
            for i in pycompat.xrange(chunk.linecount - 1):
                self._sci.markerDelete(chunk.lineno + i + 1,
                                       _ExcludedLineMarker)
            self._sci.clearIndicatorRange(chunk.lineno + 1, 0,
                                          chunk.lineno + chunk.linecount, 0,
                                          self._excludeindicator)
    def _updateMarkerDark(self, chunk):
        # In dark theme, invert visual meaning of included/excluded chunks
        includemsg = ' ' + _('(included in the next commit)')
        m = self._sci.markersAtLine(chunk.lineno)
        inclmarked = m & (1 << _IncludedChunkStartMarker)
        exclmarked = m & (1 << _ExcludedChunkStartMarker)

        if not chunk.excluded and not inclmarked:
            self._sci.setReadOnly(False)
            llen = self._sci.lineLength(chunk.lineno)
            self._sci.insertAt(includemsg, chunk.lineno, llen - 1)
            self._sci.setReadOnly(True)

            self._sci.markerDelete(chunk.lineno, _ExcludedChunkStartMarker)
            self._sci.markerAdd(chunk.lineno, _IncludedChunkStartMarker)
            for i in pycompat.xrange(chunk.linecount - 1):
                self._sci.markerAdd(chunk.lineno + i + 1, _ExcludedLineMarker)
            self._sci.fillIndicatorRange(chunk.lineno + 1, 0,
                                        chunk.lineno + chunk.linecount, 0,
                                        self._excludeindicator)

        if chunk.excluded and inclmarked:
            self._sci.setReadOnly(False)
            llen = self._sci.lineLength(chunk.lineno)
            mlen = len(includemsg.encode('utf-8'))
            pos = self._sci.positionFromLineIndex(chunk.lineno, llen - mlen - 1)
            self._sci.SendScintilla(qsci.SCI_SETTARGETSTART, pos)
            self._sci.SendScintilla(qsci.SCI_SETTARGETEND, pos + mlen)
            self._sci.SendScintilla(qsci.SCI_REPLACETARGET, 0, b'')
            self._sci.setReadOnly(True)

        if chunk.excluded and not exclmarked:
            self._sci.markerDelete(chunk.lineno, _IncludedChunkStartMarker)
            self._sci.markerAdd(chunk.lineno, _ExcludedChunkStartMarker)
            for i in pycompat.xrange(chunk.linecount - 1):
                self._sci.markerDelete(chunk.lineno + i + 1,
                                    _ExcludedLineMarker)
            self._sci.clearIndicatorRange(chunk.lineno + 1, 0,
                                        chunk.lineno + chunk.linecount, 0,
                                        self._excludeindicator)

    #@pyqtSlot(int, int, Qt.KeyboardModifier)
    def _onMarginClicked(self, margin, line, state):
        if margin != _ChunkSelectionMargin:
            return
        if line not in self._chunkatline:
            return
        if state & Qt.KeyboardModifier.ShiftModifier:
            excluded = self._getChunkAtLine(line)
            cl = self._currentChunkLine()
            end = max(line, cl)
            l = min(line, cl)
            lines = []
            while l < end:
                assert l >= 0
                lines.append(l)
                l = self._sci.markerFindNext(l + 1, _ChunkSelectionMarkerMask)
            lines.append(end)
            self._setChunkAtLines(lines, not excluded)
        else:
            self._toggleChunkAtLine(line)

        self._sci.setCursorPosition(line, 0)

    def _getChunkAtLine(self, line):
        return self._chunkatline[line].excluded

    def _setChunkAtLines(self, lines, excluded):
        for l in lines:
            chunk = self._chunkatline[l]
            self._fd.setChunkExcluded(chunk, excluded)
            if THEME.enabled:
                self._updateMarkerDark(chunk)
            else:
                self._updateMarker(chunk)
        self.chunkSelectionChanged.emit()

    def _toggleChunkAtLine(self, line):
        excluded = self._getChunkAtLine(line)
        self._setChunkAtLines([line], not excluded)

    @pyqtSlot()
    def _toggleCurrentChunk(self):
        line = self._currentChunkLine()
        if line >= 0:
            self._toggleChunkAtLine(line)

    def _currentChunkLine(self):
        line = self._sci.getCursorPosition()[0]
        return self._sci.markerFindPrevious(line, _ChunkSelectionMarkerMask)

    def setupContextMenu(self, menu, line):
        menu.addAction(self._actmarkexcluded)

    @pyqtSlot(bool)
    def _updateChunkIndicatorMarks(self, checked):
        '''
        This method has some pre-requisites:
        - self.excludeindicator MUST be set to -1 before calling this
        method for the first time
        '''
        indicatortypes = (qsci.IndicatorStyle.HiddenIndicator, qsci.IndicatorStyle.StrikeIndicator)
        self._excludeindicator = self._sci.indicatorDefine(
            indicatortypes[checked],
            self._excludeindicator)
        QSettings().setValue('changes-mark-excluded', checked)
