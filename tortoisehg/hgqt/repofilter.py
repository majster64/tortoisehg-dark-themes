# repofilter.py - TortoiseHg toolbar for filtering changesets
#
# Copyright (C) 2007-2010 Logilab. All rights reserved.
# Copyright (C) 2010 Yuya Nishihara <yuya@tcha.org>
# Copyright (C) 2026 Peter Demcak <majster64@gmail.com> (dark theme)
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import os
import typing

from .qtcore import (
    QEvent,
    QTimer,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from .qtgui import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QFontMetrics,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QSizePolicy,
    QStyle,
    QToolBar,
    QToolButton,
)

from mercurial import (
    error,
    pycompat,
    repoview,
    scmutil,
)

from ..util import hglib
from ..util.i18n import _
from . import (
    qtlib,
    revset,
)

if typing.TYPE_CHECKING:
    from typing import (
        List,
        Optional,
        Text,
    )
    from mercurial import (
        localrepo,
    )
    from .qtcore import (
        QSettings,
    )
    from .qtgui import (
        QWidget,
    )
    from . import (
        thgrepo,
    )

from .theme import THEME

_permanent_queries = ('head()', 'merge()',
                      'tagged()', 'bookmark()',
                      'file(".hgsubstate") or file(".hgsub")')

def _firstword(query: str) -> Optional[bytes]:
    lquery = hglib.fromunicode(query)
    try:
        for token, value, _pos in hglib.tokenizerevspec(lquery):
            if token == b'symbol' or token == b'string':
                return value  # localstr
    except error.ParseError:
        pass

def _querytype(repo: localrepo.localrepository, query: str) -> Optional[str]:
    r"""
    >>> # TODO: maybe replace with real repo
    >>> origisrevsymbol = scmutil.isrevsymbol
    >>> scmutil.isrevsymbol = lambda repo, changeid: changeid in repo
    >>> repo = set(b'0 1 2 3 . stable'.split())
    >>> _querytype(repo, u'') is None
    True
    >>> _querytype(repo, u'quick fox')
    'keyword'
    >>> _querytype(repo, u'0')
    'revset'
    >>> _querytype(repo, u'stable')
    'revset'
    >>> _querytype(repo, u'0::2')  # symbol
    'revset'
    >>> _querytype(repo, u'::"stable"')  # string
    'revset'
    >>> _querytype(repo, u'"')  # unterminated string
    'keyword'
    >>> _querytype(repo, u'tagged()')
    'revset'
    >>> _querytype(repo, u'\u3000')  # UnicodeEncodeError
    'revset'
    >>> scmutil.isrevsymbol = origisrevsymbol
    """
    if not query:
        return
    if '(' in query:
        return 'revset'
    try:
        changeid = _firstword(query)
    except UnicodeEncodeError:
        return 'revset'  # avoid further error on formatspec()
    if not changeid:
        return 'keyword'
    try:
        if scmutil.isrevsymbol(repo, changeid):
            return 'revset'
    except error.LookupError:  # ambiguous changeid
        pass
    return 'keyword'


class SelectAllLineEdit(QLineEdit):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.just_got_focus = False

    def focusInEvent(self, ev):
        if ev.reason() == Qt.FocusReason.MouseFocusReason:
            self.just_got_focus = True
        super().focusInEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self.just_got_focus:
            self.just_got_focus = False
            self.selectAll()
        super().mouseReleaseEvent(ev)


class RepoFilterBar(QToolBar):
    """Toolbar for RepoWidget to filter changesets"""

    setRevisionSet = pyqtSignal(str)
    filterToggled = pyqtSignal(bool)

    branchChanged = pyqtSignal(str, bool)
    """Emitted (branch, allparents) when branch selection changed"""

    showHiddenChanged = pyqtSignal(bool)
    showGraftSourceChanged = pyqtSignal(bool)

    _allBranchesLabel = '\u2605 ' + _('Show all') + ' \u2605'

    def __init__(self,
                 repoagent: thgrepo.RepoAgent,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.setIconSize(qtlib.smallIconSize())
        self._repoagent = repoagent
        self._permanent_queries = list(_permanent_queries)
        repo = repoagent.rawRepo()
        username = hglib.configuredusername(repo.ui)
        if username:
            self._permanent_queries.insert(0,
                hglib.formatrevspec(
                    'author(%s)',
                    hglib.tounicode(os.path.expandvars(username))
                )
            )
        self.filterEnabled = True

        #Check if the font contains the glyph needed by the branch combo
        if not QFontMetrics(self.font()).inFont('\u2605'):
            self._allBranchesLabel = '*** %s ***' % _('Show all')

        self.entrydlg = revset.RevisionSetQuery(repoagent, self)
        self.entrydlg.queryIssued.connect(self.queryIssued)
        self.entrydlg.hide()

        self.revsetcombo = combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        # don't calculate size hint from history contents, just use as much
        # space as possible. this way, the branch combo can be enlarged up
        # to its preferred width.
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        combo.setMinimumContentsLength(20)
        qtlib.allowCaseChangingInput(combo)
        le = combo.lineEdit()
        le.returnPressed.connect(self.runQuery)
        le.selectionChanged.connect(self.selectionChanged)
        le.setPlaceholderText(_('### revision set query ###'))
        combo.activated.connect(self.runQuery)

        self._revsettypelabel = QLabel(le)
        self._revsettypetimer = QTimer(self, interval=200, singleShot=True)
        self._revsettypetimer.timeout.connect(self._updateQueryType)
        combo.editTextChanged.connect(self._revsettypetimer.start)
        self._updateQueryType()
        le.installEventFilter(self)

        self.clearBtn = QToolButton(self)
        self.clearBtn.setIcon(qtlib.geticon('hg-remove'))
        self.clearBtn.setToolTip(_('Clear current query and query text'))
        self.clearBtn.clicked.connect(self.onClearButtonClicked)
        self.addWidget(self.clearBtn)
        self.addWidget(qtlib.Spacer(2, 2))
        self.addWidget(combo)
        self.addWidget(qtlib.Spacer(2, 2))

        self.searchBtn = QToolButton(self)
        self.searchBtn.setIcon(qtlib.geticon('view-filter'))
        self.searchBtn.setToolTip(_('Trigger revision set query'))
        self.searchBtn.clicked.connect(self.runQuery)
        self.addWidget(self.searchBtn)

        self.editorBtn = QToolButton()
        self.editorBtn.setText('...')
        self.editorBtn.setToolTip(_('Open advanced query editor'))
        self.editorBtn.clicked.connect(self.openEditor)
        self.addWidget(self.editorBtn)

        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
        self.deleteBtn = QToolButton()
        self.deleteBtn.setIcon(icon)
        self.deleteBtn.setToolTip(_('Delete selected query from history'))
        self.deleteBtn.clicked.connect(self.deleteFromHistory)
        self.deleteBtn.setEnabled(False)
        self.addWidget(self.deleteBtn)
        self.addSeparator()

        self.filtercb = f = QCheckBox(_('filter'))
        f.clicked.connect(self.filterToggled)
        f.setToolTip(_('Toggle filtering of non-matched changesets'))
        self.addWidget(f)
        self.addSeparator()

        self.showHiddenBtn = QToolButton()
        self.showHiddenBtn.setIcon(qtlib.geticon('view-hidden'))
        self.showHiddenBtn.setCheckable(True)
        self.showHiddenBtn.setToolTip(_('Show/Hide hidden changesets'))
        self.showHiddenBtn.clicked.connect(self.showHiddenChanged)
        self.addWidget(self.showHiddenBtn)

        self.showGraftSourceBtn = QToolButton()
        self.showGraftSourceBtn.setIcon(qtlib.geticon('hg-transplant'))
        self.showGraftSourceBtn.setCheckable(True)
        self.showGraftSourceBtn.setChecked(True)
        self.showGraftSourceBtn.setToolTip(_('Toggle graft relations visibility'))
        self.showGraftSourceBtn.clicked.connect(self.showGraftSourceChanged)
        self.addWidget(self.showGraftSourceBtn)
        self.addSeparator()

        self._initBranchFilter()
        self.setFocusProxy(self.revsetcombo)
        self.refresh()

    @property
    def _repo(self):
        return self._repoagent.rawRepo()

    def onClearButtonClicked(self):
        if self.revsetcombo.currentText():
            self.revsetcombo.clearEditText()
        elif not isinstance(self.parentWidget(), QMainWindow):
            # act as "close" button because this isn't managed as toolbar
            self.hide()
        # always request to clear filter; model may still be filtered even
        # if edit box is empty
        self.runQuery()

    def selectionChanged(self):
        selection = self.revsetcombo.lineEdit().selectedText()
        self.deleteBtn.setEnabled(selection in self.revsethist)

    def deleteFromHistory(self):
        selection = self.revsetcombo.lineEdit().selectedText()
        if selection not in self.revsethist:
            return
        self.revsethist.remove(selection)
        full = self.revsethist + self._permanent_queries
        self.revsetcombo.clear()
        self.revsetcombo.addItems(full)
        self.revsetcombo.setCurrentIndex(-1)

    def eventFilter(self, watched, event):
        if watched is self.revsetcombo.lineEdit():
            if event.type() == QEvent.Type.Resize:
                self._updateQueryTypeGeometry()
            return False
        return super().eventFilter(watched, event)

    def openEditor(self):
        query = self._prepareQuery()
        self.entrydlg.entry.setText(query)
        self.entrydlg.entry.setCursorPosition(0, len(query))
        self.entrydlg.entry.setFocus()
        self.entrydlg.setVisible(True)

    def queryIssued(self, query: str) -> None:
        self.revsetcombo.setEditText(query)
        self.runQuery()

    def _prepareQuery(self) -> str:
        query = self.revsetcombo.currentText().strip()
        if _querytype(self._repo, query) == 'keyword':
            return hglib.formatrevspec('keyword(%s)', query)
        else:
            return query

    def _isUnsavedQuery(self) -> bool:
        return self.revsetcombo.currentText().startswith(' ')

    @pyqtSlot()
    def _updateQueryType(self):
        query = self.revsetcombo.currentText().strip()
        qtype = _querytype(self._repo, query)
        if not qtype:
            self._revsettypelabel.hide()
            self._updateQueryTypeGeometry()
            return

        name, bordercolor, bgcolor = {
            'keyword': (_('Keyword Search'), '#cccccc', '#eeeeee'),
            'revset':  (_('Revision Set'),   '#f6dd82', '#fcf1ca'),
            }[qtype]

        if self._isUnsavedQuery():
            name += ' ' + _('(unsaved)')

        label = self._revsettypelabel
        label.setText(name)

        # Override colors only when THEME is enabled
        if THEME.enabled:
            bordercolor = THEME.text_disabled.name()
            textcolor = THEME.text.name()
            if qtype == 'keyword':
                bgcolor = THEME.background.name()
            else:
                bgcolor = THEME.backgroundLighter.name()
        else:
            textcolor = 'black'

        label.setStyleSheet('border: 1px solid %s; background-color: %s;'
                            'color: %s;' % (bordercolor, bgcolor, textcolor)
        )

        label.show()
        self._updateQueryTypeGeometry()

    def _updateQueryTypeGeometry(self):
        le = self.revsetcombo.lineEdit()
        label = self._revsettypelabel
        # show label in right corner
        w = label.minimumSizeHint().width()
        label.setGeometry(le.width() - w - 1, 1, w, le.height() - 2)
        # right margin for label
        margins = le.contentsMargins()
        if label.isHidden():
            margins.setRight(0)
        else:
            margins.setRight(w + 1)
        le.setContentsMargins(margins)

    def setQuery(self, query: str) -> None:
        self.revsetcombo.setCurrentIndex(self.revsetcombo.findText(query))
        self.revsetcombo.setEditText(query)

    @pyqtSlot()
    def runQuery(self):
        'Run the current revset query or request to clear the previous result'
        query = self._prepareQuery()
        self.setRevisionSet.emit(query)
        if query:
            self.saveQuery()
            self.revsetcombo.lineEdit().selectAll()

    def saveQuery(self):
        query = self.revsetcombo.currentText()
        if self._isUnsavedQuery():
            return
        if query in self.revsethist:
            self.revsethist.remove(query)
        if query not in self._permanent_queries:
            self.revsethist.insert(0, query)
            self.revsethist = self.revsethist[:20]
        full = self.revsethist + self._permanent_queries
        self.revsetcombo.clear()
        self.revsetcombo.addItems(full)
        self.revsetcombo.setCurrentIndex(self.revsetcombo.findText(query))

    def loadSettings(self, s: QSettings) -> None:
        repoid = hglib.shortrepoid(self._repo)
        s.beginGroup('revset/' + repoid)
        self.entrydlg.restoreGeometry(qtlib.readByteArray(s, 'geom'))
        self.revsethist = list(qtlib.readStringList(s, 'queries'))
        self.filtercb.setChecked(qtlib.readBool(s, 'filter', True))
        full = self.revsethist + self._permanent_queries
        self.revsetcombo.clear()
        self.revsetcombo.addItems(full)
        self.revsetcombo.setCurrentIndex(-1)
        self.setVisible(qtlib.readBool(s, 'showrepofilterbar'))
        self.showHiddenBtn.setChecked(qtlib.readBool(s, 'showhidden'))
        self.showGraftSourceBtn.setChecked(
            qtlib.readBool(s, 'showgraftsource', True))
        self._loadBranchFilterSettings(s)
        s.endGroup()

    def saveSettings(self, s: QSettings) -> None:
        try:
            repoid = hglib.shortrepoid(self._repo)
        except OSError:
            return
        s.beginGroup('revset/' + repoid)
        s.setValue('geom', self.entrydlg.saveGeometry())
        s.setValue('queries', self.revsethist)
        s.setValue('filter', self.filtercb.isChecked())
        s.setValue('showrepofilterbar', not self.isHidden())
        self._saveBranchFilterSettings(s)
        s.setValue('showhidden', self.showHiddenBtn.isChecked())
        s.setValue('showgraftsource', self.showGraftSourceBtn.isChecked())
        s.endGroup()

    def _initBranchFilter(self):
        self._branchLabel = QToolButton(
            text=_('Branch'), popupMode=QToolButton.ToolButtonPopupMode.InstantPopup,
            statusTip=_('Display graph the named branch only'))
        self._branchMenu = QMenu(self._branchLabel)
        self._abranchAction = self._branchMenu.addAction(
            _('Display only active branches'), self.refresh)
        self._abranchAction.setCheckable(True)
        self._cbranchAction = self._branchMenu.addAction(
            _('Display closed branches'), self.refresh)
        self._cbranchAction.setCheckable(True)
        self._allparAction = self._branchMenu.addAction(
            _('Include all ancestors'), self._emitBranchChanged)
        self._allparAction.setCheckable(True)
        self._branchLabel.setMenu(self._branchMenu)

        self._branchCombo = QComboBox()
        self._branchCombo.setEditable(True)
        self._branchCombo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._branchCombo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._branchCombo.setLineEdit(SelectAllLineEdit())
        self._branchCombo.lineEdit().editingFinished.connect(
            self._lineBranchChanged)
        self._branchCombo.setMinimumContentsLength(10)
        self._branchCombo.setMaxVisibleItems(30)
        self._branchCombo.currentIndexChanged.connect(self._emitBranchChanged)
        completer = QCompleter(self._branchCombo.model(), self._branchCombo)
        if hasattr(completer, 'setFilterMode'):  # Qt>=5.2
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._branchCombo.setCompleter(completer)

        self.addWidget(self._branchLabel)
        self.addWidget(qtlib.Spacer(2, 2))
        self.addWidget(self._branchCombo)

    def _loadBranchFilterSettings(self, s: QSettings) -> None:
        branch = qtlib.readString(s, 'branch')
        if branch == '.':
            branch = hglib.tounicode(self._repo.dirstate.branch())
        self._branchCombo.blockSignals(True)
        self.setBranch(branch)
        self._branchCombo.blockSignals(False)

        self._allparAction.setChecked(qtlib.readBool(s, 'branch_allparents'))

    def _saveBranchFilterSettings(self, s: QSettings) -> None:
        branch = self.branch()
        if branch == hglib.tounicode(self._repo.dirstate.branch()):
            # special case for working branch: it's common to have multiple
            # clones which are updated to particular branches.
            branch = '.'
        s.setValue('branch', branch)

        s.setValue('branch_allparents', self.branchAncestorsIncluded())

    def _updateBranchFilter(self):
        """Update the list of branches"""
        curbranch = self.branch()

        if self._abranchAction.isChecked():
            branches = sorted({self._repo[n].branch()
                for n in self._repo.heads()
                if not self._repo[n].extra().get(b'close')})
        elif self._cbranchAction.isChecked():
            branches: List[bytes] = sorted(self._repo.branchmap())
        else:
            branches = hglib.namedbranches(self._repo)

        # easy access to common branches (Python sorted() is stable)
        priomap = {self._repo.dirstate.branch(): -2, b'default': -1}
        branches = sorted(branches, key=lambda e: priomap.get(e, 0))

        self._branchCombo.blockSignals(True)
        self._branchCombo.clear()
        self._branchCombo.addItem(self._allBranchesLabel)
        self._branchCombo.setItemData(self._branchCombo.count() - 1, '')
        for i, branch in enumerate(pycompat.maplist(hglib.tounicode, branches),
                                   self._branchCombo.count()):
            self._branchCombo.addItem(branch)
            self._branchCombo.setItemData(i, branch, Qt.ItemDataRole.ToolTipRole)
            self._branchCombo.setItemData(i, branch)
        self._branchCombo.setEnabled(self.filterEnabled and bool(branches))
        self.setBranch(curbranch)
        self._branchCombo.blockSignals(False)

        if self.branch() != curbranch:
            self._emitBranchChanged()  # falls back to "show all"

    @pyqtSlot(str)
    def setBranch(self, branch: str) -> None:
        """Change the current branch by name [unicode]"""
        index = self._branchCombo.findData(branch)
        if index >= 0:
            self._branchCombo.setCurrentIndex(index)

    def branch(self) -> str:
        """Return the current branch name [unicode]"""
        index = self._branchCombo.currentIndex()
        branch = self._branchCombo.itemData(index)
        return branch

    def branchAncestorsIncluded(self) -> bool:
        return self._allparAction.isChecked()

    def getShowHidden(self) -> bool:
        return self.showHiddenBtn.isChecked()

    def getShowGraftSource(self) -> bool:
        return self.showGraftSourceBtn.isChecked()

    @pyqtSlot()
    def _emitBranchChanged(self):
        self.branchChanged.emit(self.branch(),
                                self.branchAncestorsIncluded())

    @pyqtSlot()
    def _lineBranchChanged(self):
        written_branch = self._branchCombo.currentText()
        if not written_branch:
            self._branchCombo.setCurrentIndex(0)

    @pyqtSlot()
    def refresh(self):
        self._updateBranchFilter()
        self._updateShowHiddenBtnState()

    def _updateShowHiddenBtnState(self):
        hashidden = bool(repoview.filterrevs(self._repo, b'visible'))
        self.showHiddenBtn.setEnabled(hashidden)
