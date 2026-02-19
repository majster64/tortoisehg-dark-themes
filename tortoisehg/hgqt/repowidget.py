# repowidget.py - TortoiseHg repository widget
#
# Copyright (C) 2007-2010 Logilab. All rights reserved.
# Copyright (C) 2010 Adrian Buehlmann <adrian@cadifra.com>
# Copyright (C) 2026 Peter Demcak <majster64@gmail.com> (dark theme)
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import binascii
import os
import shlex  # used by runCustomCommand
import subprocess  # used by runCustomCommand
import typing

from typing import (
    Optional,
)

from typing import (
    List,
)

from .qtcore import (
    QFile,
    QIODevice,
    QItemSelectionModel,
    QMimeData,
    QPoint,
    QSettings,
    QTimer,
    QUrl,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from .qtgui import (
    QAction,
    QApplication,
    QDesktopServices,
    QFileDialog,
    QIcon,
    QKeySequence,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mercurial import (
    error,
    node as nodemod,
    phases,
    pycompat,
    scmutil,
    util,
)
from mercurial.utils import (
    procutil,
)

from ..util import (
    hglib,
    paths,
    shlib,
)
from ..util.i18n import _
from . import (
    archive,
    backout,
    bisect,
    bookmark,
    close_branch,
    cmdcore,
    cmdui,
    compress,
    graft,
    hgemail,
    infobar,
    matching,
    merge,
    mq,
    pick,
    phabreview,
    postreview,
    prune,
    purge,
    qtlib,
    rebase,
    repomodel,
    resolve,
    revdetails,
    settings,
    shelve,
    sign,
    tag,
    thgimport,
    thgstrip,
    topic,
    update,
    visdiff,
)
from .commit import CommitWidget
from .docklog import ConsoleWidget
from .grep import SearchWidget
from .qtlib import (
    DemandWidget,
    InfoMsgBox,
    QuestionMsgBox,
    WarningMsgBox,
)
from .repofilter import RepoFilterBar
from .repoview import HgRepoView
from .sync import SyncWidget

if typing.TYPE_CHECKING:
    from typing import (
        Callable,
        Dict,
        List,
        Optional,
        Sequence,
        Set,
        Text,
        Tuple,
        Union,
    )

from tortoisehg.hgqt.qtgui import QApplication, QPalette, QColor
from .theme import THEME

_SELECTION_SINGLE = 'single'
_SELECTION_PAIR = 'pair'
_SELECTION_SOME = 'some'

_SELECTION_INCOMING = 'incoming'
_SELECTION_OUTGOING = 'outgoing'

# iswd = working directory
# isrev = the changeset has an integer revision number
# isctx = changectx or workingctx
# ispatch = applied revision or unapplied patch
# fixed = the changeset is considered permanent
# applied = an applied patch
# unapplied = unapplied patch
# qfold = unapplied patch and at least one applied patch exists
# qgoto = applied patch or qparent
# qpush = unapplied patch and can qpush
# qpushmove = unapplied patch and can qpush --move to reorder patches
# isdraftorwd = working directory or changset is draft
_SELECTION_ISREV = 'isrev'
_SELECTION_ISWD = 'iswd'
_SELECTION_ISCTX = 'isctx'
_SELECTION_ISPATCH = 'ispatch'
_SELECTION_FIXED = 'fixed'
_SELECTION_APPLIED = 'applied'
_SELECTION_UNAPPLIED = 'unapplied'
_SELECTION_QFOLD = 'qfold'
_SELECTION_QGOTO = 'qgoto'
_SELECTION_QPUSH = 'qpush'
_SELECTION_QPUSHMOVE = 'qpushmove'
_SELECTION_ISTRUE = 'istrue'
_SELECTION_ISDRAFTORWD = 'isdraftorwd'

_KNOWN_SELECTION_ATTRS: Set[str] = {
    _SELECTION_SINGLE,
    _SELECTION_PAIR,
    _SELECTION_SOME,

    _SELECTION_INCOMING,
    _SELECTION_OUTGOING,

    _SELECTION_ISREV,
    _SELECTION_ISWD,
    _SELECTION_ISCTX,
    _SELECTION_ISPATCH,
    _SELECTION_FIXED,
    _SELECTION_APPLIED,
    _SELECTION_UNAPPLIED,
    _SELECTION_QFOLD,
    _SELECTION_QGOTO,
    _SELECTION_QPUSH,
    _SELECTION_QPUSHMOVE,
    _SELECTION_ISTRUE,
    _SELECTION_ISDRAFTORWD,
}

# selection attributes which may be specified by user
_CUSTOM_TOOLS_SELECTION_ATTRS: Set[str] = {
    _SELECTION_ISREV,
    _SELECTION_ISWD,
    _SELECTION_ISCTX,
    _SELECTION_FIXED,
    _SELECTION_APPLIED,
    _SELECTION_QGOTO,
    _SELECTION_ISTRUE,
    _SELECTION_ISDRAFTORWD,
}


class RepoWidget(QWidget):

    currentTaskTabChanged = pyqtSignal()
    showMessageSignal = pyqtSignal(str)
    taskTabVisibilityChanged = pyqtSignal(bool)
    toolbarVisibilityChanged = pyqtSignal(bool)

    # TODO: progress can be removed if all actions are run as hg command
    progress = pyqtSignal(str, object, str, str, object)
    makeLogVisible = pyqtSignal(bool)

    revisionSelected = pyqtSignal(object)

    titleChanged = pyqtSignal(str)
    """Emitted when changed the expected title for the RepoWidget tab"""

    busyIconChanged = pyqtSignal()

    repoLinkClicked = pyqtSignal(str)
    """Emitted when clicked a link to open repository"""

    _actions: Dict[str, Tuple[QAction, Set[str], Set[str]]]

    def __init__(self, actionregistry, repoagent, parent=None, bundle=None):
        QWidget.__init__(self, parent, acceptDrops=True)

        self._actionregistry = actionregistry
        self._repoagent = repoagent
        self.bundlesource = None  # source URL of incoming bundle [unicode]
        self.outgoingMode = False
        self._busyIconNames = []
        self._namedTabs = {}
        self.destroyed.connect(self.repo.thginvalidate)

        self.currentMessage = ''

        self.setupUi()
        self._actions = {}
        self.createActions()
        self.loadSettings()
        self._initModel()

        self._lastTaskTabVisible = self.isTaskTabVisible()
        self.repotabs_splitter.splitterMoved.connect(self._onSplitterMoved)

        if bundle:
            self.setBundle(bundle)

        self._dialogs = qtlib.DialogKeeper(
            lambda self, dlgmeth, *args: dlgmeth(self, *args), parent=self)

        # listen to change notification after initial settings are loaded
        repoagent.repositoryChanged.connect(self.repositoryChanged)
        repoagent.configChanged.connect(self.configChanged)
        
        if THEME.enabled:
            repo = self._repoagent.rawRepo()
            qtlib.configstyles(repo.ui)

        self._updateNamedActions()
        QTimer.singleShot(0, self._initView)

    def setupUi(self):
        self.repotabs_splitter = QSplitter(orientation=Qt.Orientation.Vertical)
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(0)

        # placeholder to shift repoview while infobar is overlaid
        self._repoviewFrame = infobar.InfoBarPlaceholder(self._repoagent, self)
        self._repoviewFrame.linkActivated.connect(self._openLink)

        self.filterbar = RepoFilterBar(self._repoagent, self)
        self.layout().addWidget(self.filterbar)

        self.filterbar.branchChanged.connect(self.setBranch)
        self.filterbar.showHiddenChanged.connect(self.setShowHidden)
        self.filterbar.showGraftSourceChanged.connect(self.setShowGraftSource)
        self.filterbar.setRevisionSet.connect(self.setRevisionSet)
        self.filterbar.filterToggled.connect(self.filterToggled)
        self.filterbar.visibilityChanged.connect(self.toolbarVisibilityChanged)
        self.filterbar.hide()

        self.layout().addWidget(self.repotabs_splitter)

        cs = ('Workbench', _('Workbench Log Columns'))
        self.repoview = view = HgRepoView(self._repoagent, 'repoWidget', cs,
                                          self)
        view.clicked.connect(self._clearInfoMessage)
        view.revisionSelected.connect(self.onRevisionSelected)
        view.revisionActivated.connect(self.onRevisionActivated)
        view.showMessage.connect(self.showMessage)
        view.menuRequested.connect(self._popupSelectionMenu)
        self._repoviewFrame.setView(view)

        self.repotabs_splitter.addWidget(self._repoviewFrame)
        self.repotabs_splitter.setCollapsible(0, True)
        self.repotabs_splitter.setStretchFactor(0, 1)

        self.taskTabsWidget = tt = QTabWidget()
        self.repotabs_splitter.addWidget(self.taskTabsWidget)
        self.repotabs_splitter.setStretchFactor(1, 1)
        tt.setDocumentMode(True)
        self.updateTaskTabs()
        tt.currentChanged.connect(self.currentTaskTabChanged)

        w = revdetails.RevDetailsWidget(self._repoagent, self)
        self.revDetailsWidget = w
        self.revDetailsWidget.filelisttbar.setStyleSheet(qtlib.tbstylesheet)
        w.linkActivated.connect(self._openLink)
        w.revisionSelected.connect(self.repoview.goto)
        w.grepRequested.connect(self.grep)
        w.showMessage.connect(self.showMessage)
        w.revsetFilterRequested.connect(self.setFilter)
        w.runCustomCommandRequested.connect(
            self.handleRunCustomCommandRequest)
        idx = tt.addTab(w, qtlib.geticon('hg-log'), '')
        self._namedTabs['log'] = idx
        tt.setTabToolTip(idx, _("Revision details", "tab tooltip"))

        self.commitDemand = w = DemandWidget('createCommitWidget', self)
        idx = tt.addTab(w, qtlib.geticon('hg-commit'), '')
        self._namedTabs['commit'] = idx
        tt.setTabToolTip(idx, _("Commit", "tab tooltip"))

        self.grepDemand = w = DemandWidget('createGrepWidget', self)
        idx = tt.addTab(w, qtlib.geticon('hg-grep'), '')
        self._namedTabs['grep'] = idx
        tt.setTabToolTip(idx, _("Search", "tab tooltip"))

        w = ConsoleWidget(self._repoagent, self)
        self.consoleWidget = w
        w.closeRequested.connect(self.switchToPreferredTaskTab)
        idx = tt.addTab(w, qtlib.geticon('thg-console'), '')
        self._namedTabs['console'] = idx
        tt.setTabToolTip(idx, _("Console log", "tab tooltip"))

        self.syncDemand = w = DemandWidget('createSyncWidget', self)
        idx = tt.addTab(w, qtlib.geticon('thg-sync'), '')
        self._namedTabs['sync'] = idx
        tt.setTabToolTip(idx, _("Synchronize", "tab tooltip"))

    @pyqtSlot()
    def _initView(self):
        self._updateRepoViewForModel()
        # restore column widths when model is initially loaded.  For some
        # reason, this needs to be deferred after updating the view.  Otherwise
        # repoview.HgRepoView.resizeEvent() fires as the vertical scrollbar is
        # added, which causes the last column to grow by the scrollbar width on
        # each restart (and steal from the description width).
        QTimer.singleShot(0, self.repoview.resizeColumns)

        # select the widget chosen by the user
        name = self._repoagent.configString('tortoisehg', 'defaultwidget')
        if name:
            name = {'revdetails': 'log', 'search': 'grep'}.get(name, name)
            self.taskTabsWidget.setCurrentIndex(self._namedTabs.get(name, 0))

    def currentTaskTabName(self):
        indexmap = {idx: name
                        for name, idx in self._namedTabs.items()}
        return indexmap.get(self.taskTabsWidget.currentIndex())

    @pyqtSlot(str)
    def switchToNamedTaskTab(self, tabname: str) -> None:
        if tabname in self._namedTabs:
            idx = self._namedTabs[tabname]
            # refresh status even if current widget is already a 'commit'
            if (tabname == 'commit'
                and self.taskTabsWidget.currentIndex() == idx):
                self._refreshCommitTabIfNeeded()
            self.taskTabsWidget.setCurrentIndex(idx)

            # restore default splitter position if task tab is invisible
            self.setTaskTabVisible(True)

    def isTaskTabVisible(self):
        return self.repotabs_splitter.sizes()[1] > 0

    def setTaskTabVisible(self, visible):
        if visible == self.isTaskTabVisible():
            return
        if visible:
            self.repotabs_splitter.setSizes([1, 1])
        else:
            self.repotabs_splitter.setSizes([1, 0])
        self._updateLastTaskTabState(visible)

    @pyqtSlot()
    def _onSplitterMoved(self):
        visible = self.isTaskTabVisible()
        if self._lastTaskTabVisible == visible:
            return
        self._updateLastTaskTabState(visible)

    def _updateLastTaskTabState(self, visible):
        self._lastTaskTabVisible = visible
        self.taskTabVisibilityChanged.emit(visible)

    @property
    def repo(self):
        return self._repoagent.rawRepo()

    def repoRootPath(self):
        return self._repoagent.rootPath()

    def repoDisplayName(self):
        return self._repoagent.displayName()

    def title(self):
        """Returns the expected title for this widget [unicode]"""
        name = self._repoagent.shortName()
        if self._repoagent.overlayUrl():
            return _('%s <incoming>') % name
        elif self.repomodel.branch():
            return '%s [%s]' % (name, self.repomodel.branch())
        else:
            return name

    def busyIcon(self):
        if self._busyIconNames:
            return qtlib.geticon(self._busyIconNames[-1])
        else:
            return QIcon()

    def filterBar(self):
        return self.filterbar

    def filterBarVisible(self):
        return self.filterbar.isVisible()

    @pyqtSlot(bool)
    def toggleFilterBar(self, checked):
        """Toggle display repowidget filter bar"""
        if self.filterbar.isVisibleTo(self) == checked:
            return
        self.filterbar.setVisible(checked)
        if checked:
            self.filterbar.setFocus()

    def _openRepoLink(self, upath):
        path = hglib.fromunicode(upath)
        if not os.path.isabs(path):
            path = self.repo.wjoin(path)
        self.repoLinkClicked.emit(hglib.tounicode(path))

    @pyqtSlot(str)
    def _openLink(self, link: str):
        handlers = {'cset': self.goto,
                    'log': lambda a: self.makeLogVisible.emit(True),
                    'repo': self._openRepoLink,
                    'shelve' : self.shelve}
        if ':' in link:
            scheme, param = link.split(':', 1)
            hdr = handlers.get(scheme)
            if hdr:
                return hdr(param)
        if os.path.isabs(link):
            qtlib.openlocalurl(link)
        else:
            QDesktopServices.openUrl(QUrl(link))

    def setInfoBar(self, cls, *args, **kwargs):
        return self._repoviewFrame.setInfoBar(cls, *args, **kwargs)

    def clearInfoBar(self, priority=None):
        return self._repoviewFrame.clearInfoBar(priority)

    def createCommitWidget(self):
        pats = []
        opts = {}
        cw = CommitWidget(self._repoagent, pats, opts, self, rev=self.rev)
        cw.buttonHBox.addWidget(cw.commitSetupButton())
        cw.loadSettings(QSettings(), 'Workbench')

        cw.progress.connect(self.progress)
        cw.linkActivated.connect(self._openLink)
        cw.showMessage.connect(self.showMessage)
        cw.grepRequested.connect(self.grep)
        cw.runCustomCommandRequested.connect(
            self.handleRunCustomCommandRequest)
        QTimer.singleShot(0, self._initCommitWidgetLate)
        return cw

    @pyqtSlot()
    def _initCommitWidgetLate(self):
        cw = self.commitDemand.get()
        cw.reload()
        # auto-refresh should be enabled after initial reload(); otherwise
        # refreshWctx() can be doubled
        self.taskTabsWidget.currentChanged.connect(
            self._refreshCommitTabIfNeeded)

    def createSyncWidget(self):
        sw = SyncWidget(self._repoagent, self)
        sw.newCommand.connect(self._handleNewSyncCommand)
        sw.outgoingNodes.connect(self.setOutgoingNodes)
        sw.showMessage.connect(self.showMessage)
        sw.showMessage.connect(self._repoviewFrame.showMessage)
        sw.incomingBundle.connect(self.setBundle)
        sw.pullCompleted.connect(self.onPullCompleted)
        sw.pushCompleted.connect(self.clearRevisionSet)
        sw.refreshTargets(self.rev)
        sw.switchToRequest.connect(self.switchToNamedTaskTab)
        return sw

    @pyqtSlot(cmdcore.CmdSession)
    def _handleNewSyncCommand(self, sess):
        self._handleNewCommand(sess)
        if sess.isFinished():
            return
        sess.commandFinished.connect(self._onSyncCommandFinished)
        self._setBusyIcon('thg-sync')

    @pyqtSlot()
    def _onSyncCommandFinished(self):
        self._clearBusyIcon('thg-sync')

    def _setBusyIcon(self, iconname):
        self._busyIconNames.append(iconname)
        self.busyIconChanged.emit()

    def _clearBusyIcon(self, iconname):
        if iconname in self._busyIconNames:
            self._busyIconNames.remove(iconname)
        self.busyIconChanged.emit()

    @pyqtSlot(str)
    def setFilter(self, filter):
        self.filterbar.setQuery(filter)
        self.filterbar.setVisible(True)
        self.filterbar.runQuery()

    def isBundleSet(self) -> bool:
        return (bool(self._repoagent.overlayUrl())
                and self.repomodel.revset() == 'bundle()')

    @pyqtSlot(str, str)
    def setBundle(self, bfile: str, bsource: Optional[str] = None) -> None:
        if self._repoagent.overlayUrl():
            self.clearBundle()
        self.bundlesource = bsource
        oldlen = len(self.repo)
        # no "bundle:<bfile>" because bfile may contain "+" separator
        self._repoagent.setOverlay(bfile)
        self.filterbar.setQuery('bundle()')
        self.filterbar.runQuery()
        self.titleChanged.emit(self.title())
        newlen = len(self.repo)

        w = self.setInfoBar(infobar.ConfirmInfoBar,
            _('Found %d incoming changesets') % (newlen - oldlen))
        assert w
        w.acceptButton.setText(_('Pull'))
        w.acceptButton.setToolTip(_('Pull incoming changesets into '
                                    'your repository'))
        w.rejectButton.setText(_('Cancel'))
        w.rejectButton.setToolTip(_('Reject incoming changesets'))
        w.accepted.connect(self.acceptBundle)
        w.rejected.connect(self.clearBundle)

    @pyqtSlot()
    def clearBundle(self):
        self.clearRevisionSet()
        self.bundlesource = None
        self._repoagent.clearOverlay()
        self.titleChanged.emit(self.title())

    @pyqtSlot()
    def onPullCompleted(self):
        if self._repoagent.overlayUrl():
            self.clearBundle()

    @pyqtSlot()
    def acceptBundle(self):
        bundle = self._repoagent.overlayUrl()
        if bundle:
            w = self.syncDemand.get()
            w.pullBundle(bundle, None, self.bundlesource)

    @pyqtSlot()
    def pullBundleToRev(self):
        bundle = self._repoagent.overlayUrl()
        if bundle:
            # manually remove infobar to work around unwanted clearBundle
            # during pull operation (issue #2596)
            self._repoviewFrame.discardInfoBar()

            w = self.syncDemand.get()
            w.pullBundle(bundle, self.repo[self.rev].hex(), self.bundlesource)

    @pyqtSlot()
    def clearRevisionSet(self):
        self.filterbar.setQuery('')
        self.setRevisionSet('')

    def setRevisionSet(self, revspec):
        self.repomodel.setRevset(revspec)
        if not revspec and self.outgoingMode:
            self.outgoingMode = False
            self._updateNamedActions()

    @pyqtSlot(bool)
    def filterToggled(self, checked):
        self.repomodel.setFilterByRevset(checked)

    def setOutgoingNodes(self, nodes):
        self.filterbar.setQuery('outgoing()')
        revs = [self.repo[n].rev() for n in nodes]
        self.setRevisionSet(hglib.compactrevs(revs))
        self.outgoingMode = True
        numnodes = len(nodes)
        numoutgoing = numnodes

        if self.syncDemand.get().isTargetSelected():
            # Outgoing preview is already filtered by target selection
            defaultpush = None
        else:
            # Read the tortoisehg.defaultpush setting to determine what to push
            # by default, and set the button label and action accordingly
            defaultpush = self._repoagent.configString(
                'tortoisehg', 'defaultpush')
        rev = None
        branch = None
        pushall = False
        # note that we assume that none of the revisions
        # on the nodes/revs lists is secret
        if defaultpush == 'branch':
            branch = self.repo[b'.'].branch()
            ubranch = hglib.tounicode(branch)
            # Get the list of revs that will be actually pushed
            outgoingrevs = self.repo.revs(b'%ld and branch(.)', revs)
            numoutgoing = len(outgoingrevs)
        elif defaultpush == 'revision':
            rev = self.repo[b'.'].rev()
            # Get the list of revs that will be actually pushed
            # excluding (potentially) the current rev
            outgoingrevs = self.repo.revs(b'%ld and ::.', revs)
            numoutgoing = len(outgoingrevs)
            maxrev = rev
            if numoutgoing > 0:
                maxrev = max(outgoingrevs)
        else:
            pushall = True

        # Set the default acceptbuttontext
        # Note that the pushall case uses the default accept button text
        if branch is not None:
            acceptbuttontext = _('Push current branch (%s)') % ubranch
        elif rev is not None:
            if maxrev == rev:
                acceptbuttontext = _('Push up to current revision (#%d)') % rev
            else:
                acceptbuttontext = _('Push up to revision #%d') % maxrev
        else:
            acceptbuttontext = _('Push all')

        if numnodes == 0:
            msg = _('no outgoing changesets')
        elif numoutgoing == 0:
            if branch:
                msg = _('no outgoing changesets in current branch (%s) '
                    '/ %d in total') % (ubranch, numnodes)
            elif rev is not None:
                if maxrev == rev:
                    msg = _('no outgoing changesets up to current revision '
                            '(#%d) / %d in total') % (rev, numnodes)
                else:
                    msg = _('no outgoing changesets up to revision #%d '
                            '/ %d in total') % (maxrev, numnodes)
        elif numoutgoing == numnodes:
            # This case includes 'Push all' among others
            msg = _('%d outgoing changesets') % numoutgoing
        elif branch:
            msg = _('%d outgoing changesets in current branch (%s) '
                    '/ %d in total') % (numoutgoing, ubranch, numnodes)
        elif rev:
            if maxrev == rev:
                msg = _('%d outgoing changesets up to current revision (#%d) '
                        '/ %d in total') % (numoutgoing, rev, numnodes)
            else:
                msg = _('%d outgoing changesets up to revision #%d '
                        '/ %d in total') % (numoutgoing, maxrev, numnodes)
        else:
            # This should never happen but we leave this else clause
            # in case there is a flaw in the logic above (e.g. due to
            # a future change in the code)
            msg = _('%d outgoing changesets') % numoutgoing

        w = self.setInfoBar(infobar.ConfirmInfoBar, msg.strip())
        assert w

        if numoutgoing == 0:
            acceptbuttontext = _('Nothing to push')
            w.acceptButton.setEnabled(False)
        w.acceptButton.setText(acceptbuttontext)
        w.accepted.connect(lambda: self.push(False,
            rev=rev, branch=branch, pushall=pushall))  # TODO: to the same URL
        w.rejected.connect(self.clearRevisionSet)
        self._updateNamedActions()

    def createGrepWidget(self):
        upats = {}
        gw = SearchWidget(self._repoagent, upats, self)
        gw.setRevision(self.repoview.current_rev)
        gw.showMessage.connect(self.showMessage)
        gw.progress.connect(self.progress)
        gw.revisionSelected.connect(self.goto)
        return gw

    @property
    def rev(self):
        """Returns the current active revision"""
        return self.repoview.current_rev

    def gotoRev(self, revspec):
        """Select and scroll to the specified revision"""
        try:
            # try instant look up
            if scmutil.isrevsymbol(self.repo, hglib.fromunicode(revspec)):
                self.repoview.goto(revspec)
                return
        except error.LookupError:
            pass  # ambiguous node

        cmdline = hglib.buildcmdargs('log', rev=revspec, template='{rev}\n')
        sess = self._runCommand(cmdline)
        sess.setCaptureOutput(True)
        sess.commandFinished.connect(self._onGotoRevQueryFinished)

    @pyqtSlot(int)
    def _onGotoRevQueryFinished(self, ret):
        sess = self.sender()
        if ret != 0:
            return
        output = bytes(sess.readAll())
        if not output:
            # TODO: maybe this should be a warning bar since there would be no
            # information in log window.
            self.setInfoBar(infobar.CommandErrorInfoBar, _('No revision found'))
            return
        rev = int(output.splitlines()[-1])  # pick last rev as "hg update" does
        self.repoview.goto(rev)

    def showMessage(self, msg):
        self.currentMessage = msg
        if self.isVisible():
            self.showMessageSignal.emit(msg)

    def keyPressEvent(self, event):
        if self._repoviewFrame.activeInfoBar() and event.key() == Qt.Key.Key_Escape:
            self.clearInfoBar(infobar.INFO)
        else:
            QWidget.keyPressEvent(self, event)

    def showEvent(self, event):
        QWidget.showEvent(self, event)
        self.showMessageSignal.emit(self.currentMessage)
        if not event.spontaneous():
            # RepoWidget must be the main widget in any window, so grab focus
            # when it gets visible at start-up or by switching tabs.
            self.repoview.setFocus()

    def createActions(self):
        self._mqActions = None
        if b'mq' in self.repo.extensions():
            self._mqActions = mq.PatchQueueActions(self)
            self._mqActions.setRepoAgent(self._repoagent)

        self._setUpNamedActions()

    def detectPatches(self, paths):
        filepaths = []
        for p in paths:
            if not os.path.isfile(p):
                continue
            try:
                pf = open(p, 'rb')
                earlybytes = pf.read(4096)
                if b'\0' in earlybytes:
                    continue
                pf.seek(0)
                with hglib.extractpatch(self.repo.ui, pf) as data:
                    if data.get('filename'):
                        filepaths.append(p)
            except OSError:
                pass
        return filepaths

    def dragEnterEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        if self.detectPatches(paths):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        patches = self.detectPatches(paths)
        if not patches:
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self.thgimport(patches)

    ## Begin Workbench event forwards

    def back(self):
        self.repoview.back()

    def forward(self):
        self.repoview.forward()

    def bisect(self):
        self._dialogs.open(RepoWidget._createBisectDialog)

    @pyqtSlot()
    def bisectGoodBadRevisionsPair(self):
        revA, revB = self._selectedIntRevisionsPair()
        dlg = self._dialogs.open(RepoWidget._createBisectDialog)
        dlg.restart(str(revA), str(revB))

    @pyqtSlot()
    def bisectBadGoodRevisionsPair(self):
        revA, revB = self._selectedIntRevisionsPair()
        dlg = self._dialogs.open(RepoWidget._createBisectDialog)
        dlg.restart(str(revB), str(revA))

    def _createBisectDialog(self):
        dlg = bisect.BisectDialog(self._repoagent, self)
        dlg.newCandidate.connect(self.gotoParent)
        return dlg

    def resolve(self):
        dlg = resolve.ResolveDialog(self._repoagent, self)
        dlg.exec()

    def thgimport(self, paths=None):
        dlg = thgimport.ImportDialog(self._repoagent, self)
        if paths:
            dlg.setfilepaths(paths)
        if dlg.exec() == 0:
            self.gotoTip()

    def unbundle(self):
         w = self.syncDemand.get()
         w.unbundle()

    def shelve(self, arg=None):
        self._dialogs.open(RepoWidget._createShelveDialog)

    def _createShelveDialog(self):
        dlg = shelve.ShelveDialog(self._repoagent)
        dlg.finished.connect(self._refreshCommitTabIfNeeded)
        return dlg

    def verify(self):
        cmdline = ['verify', '--verbose']
        dlg = cmdui.CmdSessionDialog(self)
        dlg.setWindowIcon(qtlib.geticon('hg-verify'))
        dlg.setWindowTitle(_('%s - verify repository') % self.repoDisplayName())
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)
        dlg.setSession(self._repoagent.runCommand(cmdline, self))
        dlg.exec()

    def recover(self):
        cmdline = ['recover', '--verbose']
        dlg = cmdui.CmdSessionDialog(self)
        dlg.setWindowIcon(qtlib.geticon('hg-recover'))
        dlg.setWindowTitle(_('%s - recover repository')
                           % self.repoDisplayName())
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)
        dlg.setSession(self._repoagent.runCommand(cmdline, self))
        dlg.exec()

    def rollback(self):
        desc, oldlen = hglib.readundodesc(self.repo)
        if not desc:
            InfoMsgBox(_('No transaction available'),
                       _('There is no rollback transaction available'))
            return
        elif desc == 'commit':
            if not QuestionMsgBox(_('Undo last commit?'),
                   _('Undo most recent commit (%d), preserving file changes?') %
                   oldlen):
                return
        else:
            if not QuestionMsgBox(_('Undo last transaction?'),
                    _('Rollback to revision %d (undo %s)?') %
                    (oldlen - 1, desc)):
                return
            try:
                rev = self.repo[b'.'].rev()
            except error.LookupError as e:
                InfoMsgBox(_('Repository Error'),
                           _('Unable to determine working copy revision\n') +
                           hglib.tounicode(bytes(e)))
                return
            if rev >= oldlen and not QuestionMsgBox(
                    _('Remove current working revision?'),
                    _('Your current working revision (%d) will be removed '
                      'by this rollback, leaving uncommitted changes.\n '
                      'Continue?') % rev):
                return
        cmdline = ['rollback', '--verbose']
        sess = self._runCommand(cmdline)
        sess.commandFinished.connect(self._notifyWorkingDirChanges)

    def purge(self):
        dlg = purge.PurgeDialog(self._repoagent, self)
        dlg.setWindowFlags(Qt.WindowType.Sheet)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.showMessage.connect(self.showMessage)
        dlg.progress.connect(self.progress)
        dlg.exec()
        # ignores result code of PurgeDialog because it's unreliable
        self._refreshCommitTabIfNeeded()

    ## End workbench event forwards

    @pyqtSlot(str, dict)
    def grep(self, pattern='', opts=None):
        """Open grep task tab"""
        if opts is None:
            opts = {}
        opts = {str(k): str(v) for k, v in opts.items()}
        self.taskTabsWidget.setCurrentIndex(self._namedTabs['grep'])
        self.grepDemand.setSearch(pattern, **opts)
        self.grepDemand.runSearch()

    def _initModel(self):
        self.repomodel = repomodel.HgRepoListModel(self._repoagent, self)
        self.repomodel.setBranch(self.filterbar.branch(),
                                 self.filterbar.branchAncestorsIncluded())
        self.repomodel.setFilterByRevset(self.filterbar.filtercb.isChecked())
        self.repomodel.setShowGraftSource(self.filterbar.getShowGraftSource())
        self.repomodel.showMessage.connect(self.showMessage)
        self.repomodel.showMessage.connect(self._repoviewFrame.showMessage)
        self.repoview.setModel(self.repomodel)
        self.repomodel.revsUpdated.connect(self._updateRepoViewForModel)

        selmodel = self.repoview.selectionModel()
        assert selmodel is not None
        selmodel.selectionChanged.connect(self._onSelectedRevisionsChanged)

    @pyqtSlot()
    def _updateRepoViewForModel(self):
        model = self.repoview.model()
        selmodel = self.repoview.selectionModel()
        assert model is not None
        assert selmodel is not None
        index = selmodel.currentIndex()
        if not (index.flags() & Qt.ItemFlag.ItemIsEnabled):
            index = model.defaultIndex()
            f = QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows
            selmodel.setCurrentIndex(index, f)
        self.repoview.scrollTo(index)
        self.repoview.enablefilterpalette(bool(model.revset()))
        self.clearInfoBar(infobar.INFO)  # clear progress message

    @pyqtSlot()
    def _clearInfoMessage(self):
        self.clearInfoBar(infobar.INFO)

    @pyqtSlot()
    def switchToPreferredTaskTab(self):
        tw = self.taskTabsWidget
        rev = self.rev
        ctx = self.repo[rev]
        if rev is None or (b'mq' in self.repo.extensions()
                           and b'qtip' in ctx.tags()
                           and self.repo[b'.'].rev() == rev):
            # Clicking on working copy or on the topmost applied patch
            # (_if_ it is also the working copy parent) switches to the commit tab
            tw.setCurrentIndex(self._namedTabs['commit'])
        else:
            # Clicking on a normal revision switches from commit tab
            tw.setCurrentIndex(self._namedTabs['log'])

    def onRevisionSelected(self, rev):
        'View selection changed, could be a reload'
        self.showMessage('')
        try:
            self.revDetailsWidget.onRevisionSelected(rev)
            self.revisionSelected.emit(rev)
            if not isinstance(rev, str):
                # Regular patch or working directory
                self.grepDemand.forward('setRevision', rev)
                self.syncDemand.forward('refreshTargets', rev)
                self.commitDemand.forward('setRev', rev)
        except (IndexError, error.RevlogError, error.Abort) as e:
            self.showMessage(hglib.exception_str(e))

        cw = self.taskTabsWidget.currentWidget()
        if cw.canswitch():
            self.switchToPreferredTaskTab()

    @pyqtSlot()
    def _onSelectedRevisionsChanged(self):
        self._updateNamedActions()

    @pyqtSlot()
    def gotoParent(self):
        self.goto('.')

    def gotoTip(self):
        self.repoview.clearSelection()
        self.goto('tip')

    def _gotoAncestor(self):
        revs = self._selectedIntRevisions()
        if not revs:
            return
        ancestor = self.repo[revs[0]]
        for rev in revs[1:]:
            ctx = self.repo[rev]
            ancestor = ancestor.ancestor(ctx)
        self.goto(ancestor.rev())

    def goto(self, rev):
        self.repoview.goto(rev)

    def onRevisionActivated(self, rev):
        qgoto = False

        # Normal revisions and applied patches are int values, while unapplied
        # patches seem to always(?) be bytes.  The python2 code used to check
        # for the basestring type, which can be either bytes or unicode on py2,
        # so there may be a few cases of unicode.
        if isinstance(rev, (bytes, str)):
            qgoto = True
        else:
            ctx = self.repo[rev]
            if b'qparent' in ctx.tags() or ctx.thgmqappliedpatch():
                qgoto = True
            if b'qtip' in ctx.tags():
                qgoto = False
        if qgoto:
            self.qgotoSelectedRevision()
        else:
            self.visualDiffRevision()

    def reload(self, invalidate=True):
        'Initiate a refresh of the repo model, rebuild graph'
        try:
            if invalidate:
                self.repo.thginvalidate()
            self.rebuildGraph()
            self.reloadTaskTab()
        except OSError as e:
            self.showMessage(hglib.exception_str(e))

    def rebuildGraph(self):
        'Called by repositoryChanged signals, and during reload'
        self.showMessage('')
        self.filterbar.refresh()
        self.repoview.saveSettings()

    def reloadTaskTab(self):
        w = self.taskTabsWidget.currentWidget()
        w.reload()

    @pyqtSlot()
    def repositoryChanged(self):
        'Repository has detected a changelog / dirstate change'
        try:
            self.rebuildGraph()
        except (error.RevlogError, error.RepoError) as e:
            self.showMessage(hglib.exception_str(e))
        self._updateNamedActions()

    @pyqtSlot()
    def configChanged(self):
        'Repository is reporting its config files have changed'
        self.revDetailsWidget.reload()
        self.titleChanged.emit(self.title())
        self.updateTaskTabs()

    def updateTaskTabs(self):
        val = self._repoagent.configString('tortoisehg', 'tasktabs').lower()
        if val == 'east':
            self.taskTabsWidget.setTabPosition(QTabWidget.TabPosition.East)
            self.taskTabsWidget.tabBar().show()
        elif val == 'west':
            self.taskTabsWidget.setTabPosition(QTabWidget.TabPosition.West)
            self.taskTabsWidget.tabBar().show()
        else:
            self.taskTabsWidget.tabBar().hide()

    @pyqtSlot(str, bool)
    def setBranch(self, branch, allparents):
        self.repomodel.setBranch(branch, allparents=allparents)
        self.titleChanged.emit(self.title())

    @pyqtSlot(bool)
    def setShowHidden(self, showhidden):
        self._repoagent.setHiddenRevsIncluded(showhidden)

    @pyqtSlot(bool)
    def setShowGraftSource(self, showgraftsource):
        self.repomodel.setShowGraftSource(showgraftsource)

    ##
    ## Workbench methods
    ##

    def canGoBack(self):
        return self.repoview.canGoBack()

    def canGoForward(self):
        return self.repoview.canGoForward()

    def loadSettings(self):
        s = QSettings()
        repoid = hglib.shortrepoid(self.repo)
        self.revDetailsWidget.loadSettings(s)
        self.filterbar.loadSettings(s)
        self._repoagent.setHiddenRevsIncluded(self.filterbar.getShowHidden())
        self.repotabs_splitter.restoreState(
            qtlib.readByteArray(s, 'repoWidget/splitter-' + repoid))

    def okToContinue(self):
        if self._repoagent.isBusy():
            r = QMessageBox.question(self, _('Confirm Exit'),
                                     _('Mercurial command is still running.\n'
                                       'Are you sure you want to terminate?'),
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
            if r == QMessageBox.StandardButton.Yes:
                self._repoagent.abortCommands()
            return False
        for i in pycompat.xrange(self.taskTabsWidget.count()):
            w = self.taskTabsWidget.widget(i)
            if w.canExit():
                continue
            self.taskTabsWidget.setCurrentWidget(w)
            self.showMessage(_('Tab cannot exit'))
            return False
        return True

    def closeRepoWidget(self):
        '''returns False if close should be aborted'''
        if not self.okToContinue():
            return False
        s = QSettings()
        if self.isVisible():
            try:
                repoid = hglib.shortrepoid(self.repo)
                s.setValue('repoWidget/splitter-' + repoid,
                           self.repotabs_splitter.saveState())
            except OSError:
                pass
        self.revDetailsWidget.saveSettings(s)
        self.commitDemand.forward('saveSettings', s, 'workbench')
        self.grepDemand.forward('saveSettings', s)
        self.filterbar.saveSettings(s)
        self.repoview.saveSettings(s)
        return True

    def setSyncUrl(self, url):
        """Change the current peer-repo url of the sync widget; url may be
        a symbolic name defined in [paths] section"""
        self.syncDemand.get().setUrl(url)

    def incoming(self):
        self.syncDemand.get().incoming()

    def pull(self):
        self.syncDemand.get().pull()
    def outgoing(self):
        self.syncDemand.get().outgoing()
    def push(self, confirm=None, **kwargs):
        """Call sync push.

        If confirm is False, the user will not be prompted for
        confirmation. If confirm is True, the prompt might be used.
        """
        self.syncDemand.get().push(confirm, **kwargs)
        self.outgoingMode = False
        self._updateNamedActions()

    def syncBookmark(self):
        self.syncDemand.get().syncBookmark()

    ##
    ## Repoview context menu
    ##

    def _isRevisionSelected(self) -> bool:
        """True if the selection includes change/workingctx revision"""
        return any(r is None or isinstance(r, int)
                   for r in self.repoview.selectedRevisions())

    def _isUnappliedPatchSelected(self) -> bool:
        """True if the selection includes unapplied patch"""
        return any(r is not None and not isinstance(r, int)
                   for r in self.repoview.selectedRevisions())

    def _isRevisionsPairSelected(self) -> bool:
        """True if exactly two change/workingctx revisions are selected"""
        return (len(self.repoview.selectedRevisions()) == 2
                and not self._isUnappliedPatchSelected())

    def _selectionAttributes(self) -> Set[str]:
        """Returns a set of keywords that describe the selected revisions"""
        attributes = {_SELECTION_ISTRUE}

        revisions, patches = self._selectedIntRevisionsAndUnappliedPatches()

        if len(revisions) + len(patches) == 1:
            attributes.add(_SELECTION_SINGLE)
        if len(revisions) + len(patches) == 2:
            attributes.add(_SELECTION_PAIR)
        if revisions or patches:
            attributes.add(_SELECTION_SOME)

        # In incoming/outgoing mode, unrelated revisions and patches are
        # filtered out. So we don't have to test each selected revision.
        if self.isBundleSet():
            attributes.add(_SELECTION_INCOMING)
        if self.outgoingMode:
            attributes.add(_SELECTION_OUTGOING)

        if not patches and revisions:
            attributes.add(_SELECTION_ISCTX)

            haswdir = max(revisions) == nodemod.wdirrev
            if not haswdir:
                attributes.add(_SELECTION_ISREV)
            if len(revisions) == 1 and haswdir:
                attributes.add(_SELECTION_ISWD)

            ctxs = [self.repo[rev] for rev in revisions]
            if all(c.phase() >= phases.draft or c.rev() is None for c in ctxs):
                attributes.add(_SELECTION_ISDRAFTORWD)
            if not haswdir and not any(c.thgmqappliedpatch() for c in ctxs):
                attributes.add(_SELECTION_FIXED)
            if all(c.thgmqappliedpatch() for c in ctxs):
                attributes.add(_SELECTION_ISPATCH)
                attributes.add(_SELECTION_APPLIED)
            if all(c.thgmqappliedpatch() or b'qparent' in c.tags()
                   for c in ctxs):
                attributes.add(_SELECTION_QGOTO)

        if not revisions and patches:
            attributes.add(_SELECTION_ISPATCH)
            attributes.add(_SELECTION_UNAPPLIED)
            if b'qtip' in self.repo.tags():
                attributes.add(_SELECTION_QFOLD)

            # TODO: maybe better to not scan all patches and test selection?
            q = self.repo.mq
            ispushable = False
            qnext = ''
            unapplied = 0
            for i in pycompat.xrange(q.seriesend(), len(q.series)):
                pushable, reason = q.pushable(i)
                if pushable:
                    if unapplied == 0:
                        qnext = hglib.tounicode(q.series[i])
                    if self.rev == q.series[i]:
                        ispushable = True
                    unapplied += 1

            if ispushable:
                attributes.add(_SELECTION_QPUSH)
            if ispushable and len(patches) == 1 and patches[0] != qnext:
                attributes.add(_SELECTION_QPUSHMOVE)

        return attributes

    def _selectedIntRevisionsAndUnappliedPatches(self) -> Tuple[List[int], List[str]]:
        """Returns lists of selected change/workingctx revisions and unapplied
        patches"""
        revisions = []
        patches = []
        for r in self.repoview.selectedRevisions():
            if r is None:
                revisions.append(nodemod.wdirrev)
            elif isinstance(r, int):
                revisions.append(r)
            else:
                assert isinstance(r, bytes)
                patches.append(hglib.tounicode(r))
        return revisions, patches

    def _selectedIntRevisions(self) -> List[int]:
        """Returns a list of selected change/workingctx revisions

        Unapplied patches are excluded.
        """
        revisions, _patches = self._selectedIntRevisionsAndUnappliedPatches()
        return revisions

    def _selectedIntRevisionsPair(self) -> Tuple[int, int]:
        """Returns a pair of change/workingctx revisions if exactly two
        revisions are selected

        Otherwise returns (nullrev, nullrev) for convenience. Use
        _isRevisionsPairSelected() if you need to check it strictly.
        """
        if not self._isRevisionsPairSelected():
            return nodemod.nullrev, nodemod.nullrev
        rev0, rev1 = self._selectedIntRevisions()
        return rev0, rev1

    def _selectedDagRangeRevisions(self) -> List[int]:
        """Returns a list of revisions in the DAG range specified by the
        selected revisions pair

        If no revisions pair selected, returns an empty list.
        """
        if not self._isRevisionsPairSelected():
            return []
        rev0, rev1 = sorted(self._selectedIntRevisions())
        # simply disable lazy evaluation as we won't handle slow query
        return list(self.repo.revs(b'%d::%d', rev0, rev1))

    def _selectedUnappliedPatches(self) -> List[str]:
        """Returns a list of selected unapplied patches"""
        _revisions, patches = self._selectedIntRevisionsAndUnappliedPatches()
        return patches

    @pyqtSlot(QPoint)
    def _popupSelectionMenu(self, point):
        'User requested a context menu in repo view widget'

        selection = self.repoview.selectedRevisions()
        if not selection:
            return

        if self.isBundleSet():
            self._popupIncomingBundleMenu(point)
        elif not self._isRevisionSelected():
            self._popupUnappliedPatchMenu(point)
        elif len(selection) == 1:
            self._popupSingleSelectionMenu(point)
        elif len(selection) == 2:
            self._popupPairSelectionMenu(point)
        else:
            self._popupMultipleSelectionMenu(point)

    def _popupSingleSelectionMenu(self, point):
        menu = QMenu(self)

        if self.outgoingMode:
            submenu = menu.addMenu(_('Pus&h'))
            self._addNamedActionsToMenu(submenu, [
                'Repository.pushToRevision',
                'Repository.pushBranch',
                'Repository.pushAll',
            ])
            menu.addSeparator()

        self._addNamedActionsToMenu(menu, [
            'Repository.updateToRevision',
            None,
            'Repository.visualDiff',
            'Repository.visualDiffToLocal',
            'Repository.browseRevision',
            'RepoView.filterByRevisionsMenu',
            None,
            'Repository.mergeWithRevision',
            'Repository.closeRevision',
            'Repository.tagRevision',
            'Repository.bookmarkRevision',
            'Repository.topicRevision',
            'Repository.signRevision',
            None,
            'Repository.backoutToRevision',
            'Repository.revertToRevision',
            None,
        ])

        submenu = menu.addMenu(_('Copy &Hash'))
        self._addNamedActionsToMenu(submenu, [
            'Repository.copyHash',
            'Repository.copyShortHash',
            None,
            'Repository.copyGitHash',
            'Repository.copyShortGitHash',
        ])
        menu.addSeparator()

        submenu = menu.addMenu(_('E&xport'))
        self._addNamedActionsToMenu(submenu, [
            'Repository.exportRevisions',
            'Repository.emailRevisions',
            'Repository.archiveRevision',
            'Repository.bundleRevisions',
            'Repository.copyPatch',
        ])
        menu.addSeparator()

        self._addNamedActionsToMenu(menu, [
            'RepoView.changePhaseMenu',
            None,
            'Repository.graftRevisions',
        ])

        submenu = menu.addMenu(_('Modi&fy History'))
        self._addNamedActionsToMenu(submenu, [
            'PatchQueue.popPatch',
            'PatchQueue.importRevision',
            'PatchQueue.finishRevision',
            'PatchQueue.renamePatch',
            None,
            'PatchQueue.launchOptionsDialog',
            None,
            'Repository.pickRevision',
            'Repository.rebaseRevision',
            None,
            'Repository.pruneRevisions',
            'Repository.stripRevision'
        ])
        submenu.menuAction().setVisible(not submenu.isEmpty())

        self._addNamedActionsToMenu(menu, [
            'Repository.sendToReviewBoard',
            'Repository.sendToPhabricator',
        ])

        self._addCustomToolsSubMenu(menu, 'workbench.revdetails.custom-menu')

        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.popup(point)

    def _popupPairSelectionMenu(self, point):
        menu = QMenu(self)

        self._addNamedActionsToMenu(menu, [
            'Repository.visualDiffRevisionsPair',
            'Repository.exportDiff',
            None,
            'Repository.exportRevisions',
            'Repository.emailRevisions',
            'Repository.copyPatch',
            None,
            'Repository.archiveDagRangeRevisions',
            'Repository.exportDagRangeRevisions',
            'Repository.emailDagRangeRevisions',
            'Repository.bundleDagRangeRevisions',
            None,
            'Repository.bisectGoodBadRevisionsPair',
            'Repository.bisectBadGoodRevisionsPair',
            'Repository.compressRevisionsPair',
            'Repository.rebaseSourceDestRevisionsPair',
            None,
            'RepoView.goToCommonAncestor',
            'RepoView.filterByRevisionsMenu',
            None,
            'Repository.graftRevisions',
            None,
            'Repository.pruneRevisions',
            None,
            'Repository.sendToReviewBoard',
            None,
            'Repository.sendToPhabricator',
        ])

        self._addCustomToolsSubMenu(menu, 'workbench.pairselection.custom-menu')

        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.popup(point)

    def _popupMultipleSelectionMenu(self, point):
        menu = QMenu(self)

        self._addNamedActionsToMenu(menu, [
            'Repository.exportRevisions',
            'Repository.emailRevisions',
            'Repository.copyPatch',
            None,
            'RepoView.goToCommonAncestor',
            'RepoView.filterByRevisionsMenu',
            None,
            'Repository.graftRevisions',
            None,
            'Repository.pruneRevisions',
            'Repository.sendToReviewBoard',
            'Repository.sendToPhabricator',
        ])

        self._addCustomToolsSubMenu(menu,
                                    'workbench.multipleselection.custom-menu')

        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.popup(point)

    def _popupIncomingBundleMenu(self, point):
        menu = QMenu(self)

        self._addNamedActionsToMenu(menu, [
            'Repository.pullToRevision',
            'Repository.visualDiff',
        ])

        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.popup(point)

    def _popupUnappliedPatchMenu(self, point):
        menu = QMenu(self)

        self._addNamedActionsToMenu(menu, [
            'PatchQueue.pushPatch',
            'PatchQueue.pushExactPatch',
            'PatchQueue.pushMovePatch',
            'PatchQueue.foldPatches',
            'PatchQueue.deletePatches',
            'PatchQueue.renamePatch',
            None,
            'PatchQueue.launchOptionsDialog',
        ])

        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.popup(point)

    def _createNamedAction(self,
                           name: str,
                           attrs: Set[str],
                           exts: Optional[Set[str]] = None,
                           icon: Optional[str] = None,
                           cb: Optional[Callable] = None) -> QAction:
        act = QAction(self)
        act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        if icon:
            act.setIcon(qtlib.geticon(icon))
        if cb:
            act.triggered.connect(cb)
        self._addNamedAction(name, act, attrs, exts)
        return act

    def _addNamedAction(self,
                        name: str,
                        act: QAction,
                        attrs: Set[str],
                        exts: Optional[Set[str]] = None) -> None:
        assert name not in self._actions, name
        assert attrs.issubset(_KNOWN_SELECTION_ATTRS), attrs
        # RepoWidget actions act on revisions selected in the graph view, so
        # the shortcuts should not be enabled for task tabs.
        self.repoview.addAction(act)
        self._actionregistry.registerAction(name, act)
        if not exts:
            exts = set()
        self._actions[name] = (act, attrs, exts)


    def _addNamedActionsToMenu(self,
                               menu: QMenu,
                               names: List[Optional[str]]) -> None:
        for n in names:
            if n:
                menu.addAction(self._actions[n][0])
            else:
                menu.addSeparator()

    def _updateNamedActions(self):
        selattrs = self._selectionAttributes()
        enabledexts = set(map(pycompat.sysstr, self.repo.extensions()))

        for act, attrs, exts in self._actions.values():
            act.setEnabled(attrs.issubset(selattrs))
            act.setVisible(not exts or bool(exts & enabledexts))

    def _addCustomToolsSubMenu(self, menu: QMenu, location: str) -> None:
        tools, toollist = hglib.tortoisehgtools(self.repo.ui,
                            selectedlocation=location)

        if not tools:
            return

        selattrs = self._selectionAttributes()

        menu.addSeparator()
        submenu = menu.addMenu(_('Custom Tools'))
        submenu.triggered.connect(self._runCustomCommandByMenu)
        for name in toollist:
            if name == '|':
                submenu.addSeparator()
                continue
            info = tools.get(name, None)
            if info is None:
                continue
            command = info.get('command', None)
            if not command:
                continue
            workingdir = info.get('workingdir', '')
            showoutput = info.get('showoutput', False)
            label = info.get('label', name)
            icon = info.get('icon', 'tools-spanner-hammer')
            enable = info.get('enable', 'istrue').lower()  # pytype: disable=attribute-error
            if enable not in _CUSTOM_TOOLS_SELECTION_ATTRS:
                continue
            a = submenu.addAction(label)
            if icon:
                a.setIcon(qtlib.geticon(icon))
            a.setData((command, showoutput, workingdir))
            a.setEnabled(enable in selattrs)

    def _setUpNamedActions(self):
        entry = self._createNamedAction

        SINGLE = _SELECTION_SINGLE
        PAIR = _SELECTION_PAIR
        SOME = _SELECTION_SOME

        INCOMING = _SELECTION_INCOMING
        OUTGOING = _SELECTION_OUTGOING

        ISREV = _SELECTION_ISREV
        ISCTX = _SELECTION_ISCTX
        ISPATCH = _SELECTION_ISPATCH
        FIXED = _SELECTION_FIXED
        APPLIED = _SELECTION_APPLIED
        UNAPPLIED = _SELECTION_UNAPPLIED
        QFOLD = _SELECTION_QFOLD
        QPUSH = _SELECTION_QPUSH
        QPUSHMOVE = _SELECTION_QPUSHMOVE
        ISDRAFTORWD = _SELECTION_ISDRAFTORWD

        entry('Repository.pullToRevision', {SINGLE, INCOMING, ISREV}, None,
              'hg-pull-to-here', self.pullBundleToRev)

        pushtypeicon = {'all': None, 'branch': None, 'revision': None}
        defaultpush = self._repoagent.configString('tortoisehg', 'defaultpush')
        pushtypeicon[defaultpush] = 'hg-push'
        entry('Repository.pushToRevision', {SINGLE, OUTGOING, ISREV}, None,
              pushtypeicon['revision'], self.pushToRevision)
        entry('Repository.pushBranch', {SINGLE, OUTGOING, ISREV}, None,
              pushtypeicon['branch'], self.pushBranch)
        entry('Repository.pushAll', {SINGLE, OUTGOING, ISREV}, None,
              pushtypeicon['all'], self.pushAll)

        # TODO: unify to Repository.update action of Workbench?
        entry('Repository.updateToRevision', {SINGLE, ISREV}, None,
              'hg-update', self.updateToRevision)

        entry('Repository.visualDiff', {SINGLE, ISCTX}, None,
              'visualdiff', self.visualDiffRevision)
        entry('Repository.visualDiffToLocal', {SINGLE, ISREV}, None,
              'ldiff', self.visualDiffToLocal)
        # TODO: visdiff can't handle wdir dest
        entry('Repository.visualDiffRevisionsPair', {PAIR, ISREV}, None,
              'visualdiff', self.visualDiffRevisionsPair)

        entry('Repository.browseRevision', {SINGLE, ISCTX}, None,
              'hg-annotate', self.manifestRevision)

        self._addNamedAction('RepoView.filterByRevisionsMenu',
                             self._createFilterBySelectedRevisionsMenu(),
                             {SOME, ISREV})

        entry('Repository.mergeWithRevision', {SINGLE, FIXED}, None,
              'hg-merge', self.mergeWithRevision)
        entry('Repository.closeRevision', {SINGLE, ISREV}, {'closehead'},
              'hg-close-head', self.closeRevision)

        entry('Repository.tagRevision', {SINGLE, FIXED}, None,
              'hg-tag', self.tagToRevision)
        entry('Repository.bookmarkRevision', {SINGLE, ISREV}, None,
              'hg-bookmarks', self.bookmarkRevision)
        entry('Repository.topicRevision', {SINGLE, ISDRAFTORWD}, {'topic'},
              'topic', self.topicRevision)
        entry('Repository.signRevision', {SINGLE, FIXED}, {'gpg'},
              'hg-sign', self.signRevision)

        entry('Repository.backoutToRevision', {SINGLE, FIXED}, None,
              'hg-revert', self.backoutToRevision)
        entry('Repository.revertToRevision', {SINGLE, ISCTX}, None,
              'hg-revert', self.revertToRevision)

        entry('Repository.copyHash', {SINGLE, ISREV}, None,
              'copy-hash', self.copyHash)
        entry('Repository.copyShortHash', {SINGLE, ISREV}, None,
              None, self.copyShortHash)
        entry('Repository.copyGitHash', {SINGLE, ISREV}, {'hggit'}, None,
              self.copyGitHash)
        entry('Repository.copyShortGitHash', {SINGLE, ISREV}, {'hggit'}, None,
              self.copyShortGitHash)

        entry('Repository.exportDiff', {PAIR, ISCTX}, None,
              'hg-export', self.exportDiff)
        entry('Repository.exportRevisions', {SOME, ISREV}, None,
              'hg-export', self.exportSelectedRevisions)
        entry('Repository.exportDagRangeRevisions', {PAIR, ISREV}, None,
              'hg-export', self.exportDagRangeRevisions)
        entry('Repository.emailRevisions', {SOME, ISREV}, None,
              'mail-forward', self.emailSelectedRevisions)
        entry('Repository.emailDagRangeRevisions', {PAIR, ISREV}, None,
              'mail-forward', self.emailDagRangeRevisions)
        entry('Repository.archiveRevision', {SINGLE, ISREV}, None,
              'hg-archive', self.archiveRevision)
        entry('Repository.archiveDagRangeRevisions', {PAIR, ISREV}, None,
              'hg-archive', self.archiveDagRangeRevisions)
        entry('Repository.bundleRevisions', {SINGLE, ISREV}, None,
              'hg-bundle', self.bundleRevisions)
        entry('Repository.bundleDagRangeRevisions', {PAIR, ISREV}, None,
              'hg-bundle', self.bundleDagRangeRevisions)
        entry('Repository.copyPatch', {SOME, ISCTX}, None,
              'copy-patch', self.copyPatch)

        entry('Repository.bisectGoodBadRevisionsPair', {PAIR, ISREV}, None,
              'hg-bisect-good-bad', self.bisectGoodBadRevisionsPair)
        entry('Repository.bisectBadGoodRevisionsPair', {PAIR, ISREV}, None,
              'hg-bisect-bad-good', self.bisectBadGoodRevisionsPair)

        entry('RepoView.goToCommonAncestor', {SOME, ISCTX}, None,
              'hg-merge', self._gotoAncestor)

        submenu = QMenu(self)
        submenu.triggered.connect(self._changePhaseByMenu)
        # TODO: filter out hidden names better
        for pnum, pname in enumerate(phases.cmdphasenames):
            a = submenu.addAction(pycompat.sysstr(pname))
            a.setData(pnum)
        self._addNamedAction('RepoView.changePhaseMenu', submenu.menuAction(),
                             {SINGLE, ISREV})

        entry('Repository.compressRevisionsPair', {PAIR, ISREV}, None,
              'hg-compress', self.compressRevisionsPair)
        entry('Repository.graftRevisions', {SOME, ISREV}, None,
              'hg-transplant', self.graftRevisions)

        entry('PatchQueue.popPatch', {SINGLE, APPLIED}, {'mq'},
              'hg-qgoto', self.qgotoParentRevision)
        entry('PatchQueue.importRevision', {SINGLE, FIXED}, {'mq'},
              'qimport', self.qimportRevision)
        entry('PatchQueue.finishRevision', {SINGLE, APPLIED}, {'mq'},
              'qfinish', self.qfinishRevision)
        entry('PatchQueue.renamePatch', {SINGLE, ISPATCH}, {'mq'},
              None, self.qrename)

        entry('PatchQueue.pushPatch', {SINGLE, QPUSH}, {'mq'},
              'hg-qpush', self.qpushRevision)
        entry('PatchQueue.pushExactPatch', {SINGLE, QPUSH}, {'mq'},
              None, self.qpushExactRevision)
        entry('PatchQueue.pushMovePatch', {SINGLE, QPUSHMOVE}, {'mq'},
              None, self.qpushMoveRevision)
        entry('PatchQueue.foldPatches', {SOME, QFOLD}, {'mq'},
              'hg-qfold', self.qfoldPatches)
        entry('PatchQueue.deletePatches', {SOME, UNAPPLIED}, {'mq'},
              'hg-qdelete', self.qdeletePatches)

        a = entry('PatchQueue.launchOptionsDialog', set(), {'mq'})
        if self._mqActions:
            a.triggered.connect(self._mqActions.launchOptionsDialog)

        entry('Repository.pickRevision', {SINGLE, ISREV}, {'evolve'},
              None, self._pickRevision)
        entry('Repository.rebaseRevision', {SINGLE, ISREV}, {'rebase'},
              'hg-rebase', self.rebaseRevision)
        entry('Repository.rebaseSourceDestRevisionsPair', {PAIR, ISREV},
              {'rebase'}, 'hg-rebase', self.rebaseSourceDestRevisionsPair)

        entry('Repository.pruneRevisions', {SOME, FIXED}, {'evolve'},
              'edit-cut', self._pruneSelected)
        entry('Repository.stripRevision', {SINGLE, FIXED}, {'mq', 'strip'},
              'hg-strip', self.stripRevision)

        entry('Repository.sendToReviewBoard', {SOME, ISREV}, {'reviewboard'},
              'reviewboard', self.sendToReviewBoard)
        entry('Repository.sendToPhabricator', {SOME, ISREV}, {'phabricator'},
              'phabricator', self.sendToPhabricator)

    @pyqtSlot()
    def exportDiff(self):
        rev0, rev1 = self._selectedIntRevisionsPair()
        root = self.repo.root
        filename = b'%s_%d_to_%d.diff' % (os.path.basename(root), rev0, rev1)
        file, _filter = QFileDialog.getSaveFileName(
            self, _('Write diff file'),
            hglib.tounicode(os.path.join(root, filename)))
        if not file:
            return
        f = QFile(file)
        if not f.open(QIODevice.OpenModeFlag.WriteOnly | QIODevice.OpenModeFlag.Truncate):
            WarningMsgBox(_('Repository Error'),
                          _('Unable to write diff file'))
            return
        cmdline = hglib.buildcmdargs('diff', rev=[rev0, rev1])
        sess = self._runCommand(cmdline)
        sess.setOutputDevice(f)

    @pyqtSlot()
    def exportSelectedRevisions(self):
        self._exportRevisions(self.repoview.selectedRevisions())

    @pyqtSlot()
    def exportDagRangeRevisions(self):
        l = self._selectedDagRangeRevisions()
        if l:
            self._exportRevisions(l)

    def _exportRevisions(self, revisions):
        if not revisions:
            return
        if len(revisions) == 1:
            if isinstance(self.rev, int):
                defaultpath = os.path.join(self.repoRootPath(),
                                           '%d.patch' % self.rev)
            else:
                defaultpath = self.repoRootPath()

            ret, _filter = QFileDialog.getSaveFileName(
                self, _('Export patch'), defaultpath,
                _('Patch Files (*.patch)'))
            if not ret:
                return
            epath = ret
            udir = os.path.dirname(epath)
            custompath = True
        else:
            udir = QFileDialog.getExistingDirectory(self, _('Export patch'),
                                                   hglib.tounicode(self.repo.root))
            if not udir:
                return

            ename = self._repoagent.shortName() + '_%r.patch'
            epath = os.path.join(udir, ename)
            custompath = False

        cmdline = hglib.buildcmdargs('export', verbose=True, output=epath,
                                     rev=hglib.compactrevs(sorted(revisions)))

        existingRevisions = []
        for rev in revisions:
            if custompath:
                path = epath
            else:
                path = epath % rev
            if os.path.exists(path):
                if os.path.isfile(path):
                    existingRevisions.append(rev)
                else:
                    QMessageBox.warning(self,
                        _('Cannot export revision'),
                        (_('Cannot export revision %s into the file named:'
                        '\n\n%s\n') % (rev, epath % rev)) + \
                        _('There is already an existing folder '
                        'with that same name.'))
                    return

        if existingRevisions:
            buttonNames = [_("Replace"), _("Append"), _("Abort")]

            warningMessage = \
                _('There are existing patch files for %d revisions (%s) '
                'in the selected location (%s).\n\n') \
                % (len(existingRevisions),
                    " ,".join([str(rev) for rev in existingRevisions]),
                    udir)

            warningMessage += \
                _('What do you want to do?\n') + '\n' + \
                '- ' + _('Replace the existing patch files.\n') + \
                '- ' + _('Append the changes to the existing patch files.\n') + \
                '- ' + _('Abort the export operation.\n')

            res = qtlib.CustomPrompt(_('Patch files already exist'),
                warningMessage,
                self,
                buttonNames, 0, 2).run()

            if buttonNames[res] == _("Replace"):
                # Remove the existing patch files
                for rev in existingRevisions:
                    if custompath:
                        os.remove(epath)
                    else:
                        os.remove(epath % rev)
            elif buttonNames[res] == _("Abort"):
                return

        self._runCommand(cmdline)

        if len(revisions) == 1:
            # Show a message box with a link to the export folder and to the
            # exported file
            rev = revisions[0]
            patchfilename = os.path.normpath(epath)
            patchdirname = os.path.normpath(os.path.dirname(epath))
            patchshortname = os.path.basename(patchfilename)
            if patchdirname.endswith(os.path.sep):
                patchdirname = patchdirname[:-1]
            qtlib.InfoMsgBox(_('Patch exported'),
                _('Revision #%d (%s) was exported to:<p>'
                '<a href="file:///%s">%s</a>%s'
                '<a href="file:///%s">%s</a>') \
                % (rev, str(self.repo[rev]),
                   patchdirname, patchdirname, os.path.sep,
                   patchfilename, patchshortname))
        else:
            # Show a message box with a link to the export folder
            qtlib.InfoMsgBox(_('Patches exported'),
                _('%d patches were exported to:<p>'
                '<a href="file:///%s">%s</a>') \
                % (len(revisions), udir, udir))

    def visualDiffRevision(self):
        opts = dict(change=self.rev)
        dlg = visdiff.visualdiff(self.repo.ui, self.repo, [], opts)
        if dlg:
            dlg.exec()

    def visualDiffToLocal(self):
        if self.rev is None:
            return
        opts = dict(rev=['rev(%d)' % self.rev])
        dlg = visdiff.visualdiff(self.repo.ui, self.repo, [], opts)
        if dlg:
            dlg.exec()

    @pyqtSlot()
    def visualDiffRevisionsPair(self):
        revA, revB = self._selectedIntRevisionsPair()
        dlg = visdiff.visualdiff(self.repo.ui, self.repo, [],
                                 {'rev': (str(revA), str(revB))})
        if dlg:
            dlg.exec()

    @pyqtSlot()
    def updateToRevision(self):
        rev = None
        if isinstance(self.rev, int):
            rev = hglib.getrevisionlabel(self.repo, self.rev)
        dlg = update.UpdateDialog(self._repoagent, rev, self)
        r = dlg.exec()
        if r in (0, 1):
            self.gotoParent()

    @pyqtSlot()
    def lockTool(self):
        from .locktool import LockDialog
        dlg = LockDialog(self._repoagent, self)
        if dlg:
            dlg.exec()

    @pyqtSlot()
    def revertToRevision(self):
        if not qtlib.QuestionMsgBox(
                _('Confirm Revert'),
                _('Reverting all files will discard changes and '
                  'leave affected files in a modified state.<br>'
                  '<br>Are you sure you want to use revert?<br><br>'
                  '(use update to checkout another revision)'),
                parent=self):
            return
        cmdline = hglib.buildcmdargs('revert', all=True, rev=self.rev)
        sess = self._runCommand(cmdline)
        sess.commandFinished.connect(self._refreshCommitTabIfNeeded)

    def _createFilterBySelectedRevisionsMenu(self):
        menu = QMenu(_('Filter b&y'), self)
        menu.setIcon(qtlib.geticon('view-filter'))
        menu.triggered.connect(self._filterBySelectedRevisions)
        for t, r in [(_('&Ancestors and Descendants'),
                      "ancestors({revs}) or descendants({revs})"),
                     (_('A&uthor'), "matching({revs}, 'author')"),
                     (_('&Branch'), "branch({revs})"),
                     ]:
            a = menu.addAction(t)
            a.setData(r)
        menu.addSeparator()
        menu.addAction(_('&More Options...'))
        return menu.menuAction()

    @pyqtSlot(QAction)
    def _filterBySelectedRevisions(self, action):
        revs = hglib.compactrevs(sorted(self.repoview.selectedRevisions()))
        expr = action.data()
        if not expr:
            self._filterByMatchDialog(revs)
            return
        self.setFilter(expr.format(revs=revs))

    def _filterByMatchDialog(self, revlist):
        dlg = matching.MatchDialog(self._repoagent, revlist, self)
        if dlg.exec():
            self.setFilter(dlg.revsetexpression)

    def pushAll(self):
        self.syncDemand.forward('push', False, pushall=True)

    def pushToRevision(self):
        # Do not ask for confirmation
        self.syncDemand.forward('push', False, rev=self.rev)

    def pushBranch(self):
        # Do not ask for confirmation
        self.syncDemand.forward('push', False,
            branch=self.repo[self.rev].branch())

    def manifestRevision(self):
        if QApplication.keyboardModifiers() & qtlib.QtKeyboardModifier.ShiftModifier:
            self._dialogs.openNew(RepoWidget._createManifestDialog)
        else:
            dlg = self._dialogs.open(RepoWidget._createManifestDialog)
            dlg.setRev(self.rev)

    def _createManifestDialog(self):
        return revdetails.createManifestDialog(self._repoagent, self.rev)

    def mergeWithOtherHead(self):
        """Open dialog to merge with the other head of the current branch"""
        cmdline = hglib.buildcmdargs('merge', preview=True,
                                     config=r'ui.logtemplate={rev}\n')
        sess = self._runCommand(cmdline)
        sess.setCaptureOutput(True)
        sess.commandFinished.connect(self._onMergePreviewFinished)

    @pyqtSlot(int)
    def _onMergePreviewFinished(self, ret):
        sess = self.sender()
        if ret == 255 and 'hg heads' in sess.errorString():
            # multiple heads
            self.filterbar.setQuery('head() - .')
            self.filterbar.runQuery()
            msg = '\n'.join(sess.errorString().splitlines()[:-1])  # drop hint
            w = self.setInfoBar(infobar.ConfirmInfoBar, msg)
            assert w
            w.acceptButton.setText(_('Merge'))
            w.accepted.connect(self.mergeWithRevision)
            w.finished.connect(self.clearRevisionSet)
            return
        if ret != 0:
            return
        revs = pycompat.maplist(int, bytes(sess.readAll()).splitlines())
        if not revs:
            return
        self._dialogs.open(RepoWidget._createMergeDialog, revs[-1])

    @pyqtSlot()
    def mergeWithRevision(self):
        # Don't use self.rev (i.e. the current revision.) This is a context
        # menu handler, and the menu is open for the selected rows, not for
        # the current row.
        revisions = self.repoview.selectedRevisions()
        if len(revisions) != 1:
            QMessageBox.warning(self, _('Unable to merge'),
                                _('Please select a revision to merge.'))
            return
        rev = revisions[0]
        if not isinstance(rev, int):
            QMessageBox.warning(self, _('Unable to merge'),
                                _('Cannot merge with a pseudo revision %r.')
                                % rev)
            return
        pctx = self.repo[b'.']
        octx = self.repo[rev]
        if pctx == octx:
            QMessageBox.warning(self, _('Unable to merge'),
                _('You cannot merge a revision with itself'))
            return
        self._dialogs.open(RepoWidget._createMergeDialog, rev)

    def _createMergeDialog(self, rev):
        return merge.MergeDialog(self._repoagent, rev, self)

    def tagToRevision(self):
        dlg = tag.TagDialog(self._repoagent, rev=str(self.rev), parent=self)
        dlg.exec()

    def closeRevision(self):
        dlg = close_branch.createCloseBranchDialog(self._repoagent, self.rev,
                                                   parent=self)
        dlg.exec()

    def bookmarkRevision(self):
        dlg = bookmark.BookmarkDialog(self._repoagent, self.rev, self)
        dlg.exec()

    def topicRevision(self):
        dlg = topic.TopicDialog(self._repoagent, self.rev, self)
        dlg.exec()

    def signRevision(self):
        dlg = sign.SignDialog(self._repoagent, self.rev, self)
        dlg.exec()

    def graftRevisions(self):
        """Graft selected revision on top of working directory parent"""
        revlist = []
        for rev in sorted(self.repoview.selectedRevisions()):
            revlist.append(str(rev))
        if not revlist:
            revlist = [self.rev]
        dlg = graft.GraftDialog(self._repoagent, self, source=revlist)
        if dlg.valid:
            dlg.exec()

    def backoutToRevision(self):
        msg = backout.checkrev(self._repoagent.rawRepo(), self.rev)
        if msg:
            qtlib.InfoMsgBox(_('Unable to backout'), msg, parent=self)
            return
        dlg = backout.BackoutDialog(self._repoagent, self.rev, self)
        dlg.finished.connect(dlg.deleteLater)
        dlg.exec()

    @pyqtSlot()
    def _pruneSelected(self):
        revspec = hglib.compactrevs(sorted(self.repoview.selectedRevisions()))
        dlg = prune.createPruneDialog(self._repoagent, revspec, self)
        dlg.exec()

    def stripRevision(self):
        'Strip the selected revision and all descendants'
        dlg = thgstrip.createStripDialog(self._repoagent, rev=str(self.rev),
                                         parent=self)
        dlg.exec()

    def sendToReviewBoard(self):
        self._dialogs.open(RepoWidget._createPostReviewDialog,
                           tuple(self.repoview.selectedRevisions()))

    def _createPostReviewDialog(self,
                                revs: Sequence[int]) -> postreview.PostReviewDialog:
        return postreview.PostReviewDialog(self.repo.ui, self._repoagent, revs)

    @pyqtSlot()
    def sendToPhabricator(self):
        self._dialogs.open(RepoWidget._createPhabReviewDialog,
                           tuple(self.repoview.selectedRevisions()))

    def _createPhabReviewDialog(self, revs):
        return phabreview.PhabReviewDialog(self._repoagent, revs)

    @pyqtSlot()
    def emailSelectedRevisions(self):
        self._emailRevisions(self.repoview.selectedRevisions())

    @pyqtSlot()
    def emailDagRangeRevisions(self):
        l = self._selectedDagRangeRevisions()
        if l:
            self._emailRevisions(l)

    def _emailRevisions(self, revs):
        self._dialogs.open(RepoWidget._createEmailDialog, tuple(revs))

    def _createEmailDialog(self, revs):
        return hgemail.EmailDialog(self._repoagent, revs)

    def archiveRevision(self):
        rev = hglib.getrevisionlabel(self.repo, self.rev)
        dlg = archive.createArchiveDialog(self._repoagent, rev, self)
        dlg.exec()

    @pyqtSlot()
    def archiveDagRangeRevisions(self):
        l = self._selectedDagRangeRevisions()
        if l:
            self.archiveRevisions(l)

    def archiveRevisions(self, revs):
        rev = hglib.getrevisionlabel(self.repo, max(revs))
        minrev = '%d' % min(revs)
        dlg = archive.createArchiveDialog(self._repoagent, rev=rev, minrev=minrev,
                                          parent=self)
        dlg.exec()

    @pyqtSlot()
    def bundleDagRangeRevisions(self):
        l = self._selectedDagRangeRevisions()
        if l:
            self.bundleRevisions(base=l[0], tip=l[-1])

    def bundleRevisions(self, base=None, tip=None):
        root = self.repoRootPath()
        if base is None or base is False:
            base = self.rev
        data = dict(name=os.path.basename(root), base=base)
        if tip is None:
            filename = '%(name)s_%(base)s_and_descendants.hg' % data
        else:
            data.update(rev=tip)
            filename = '%(name)s_%(base)s_to_%(rev)s.hg' % data

        file, _filter = QFileDialog.getSaveFileName(
            self, _('Write bundle'), os.path.join(root, filename))
        if not file:
            return

        cmdline = ['bundle', '--verbose']
        parents = [hglib.escaperev(r.rev()) for r in self.repo[base].parents()]
        for p in parents:
            cmdline.extend(['--base', p])
        if tip:
            cmdline.extend(['--rev', str(tip)])
        else:
            cmdline.extend(['--rev', 'heads(descendants(%s))' % base])
        cmdline.append(file)
        self._runCommand(cmdline)

    @pyqtSlot()
    def copyPatch(self):
        # patches should be in chronological order
        revs = sorted(self._selectedIntRevisions())
        cmdline = hglib.buildcmdargs('export', rev=hglib.compactrevs(revs))
        sess = self._runCommand(cmdline)
        sess.setCaptureOutput(True)
        sess.commandFinished.connect(self._copyPatchOutputToClipboard)

    @pyqtSlot(int)
    def _copyPatchOutputToClipboard(self, ret):
        if ret == 0:
            sess = self.sender()
            output = sess.readAll()
            mdata = QMimeData()
            mdata.setData('text/x-diff', output)  # for lossless import
            mdata.setText(hglib.tounicode(bytes(output)))
            QApplication.clipboard().setMimeData(mdata)

    def copyHash(self):
        clip = QApplication.clipboard()
        clip.setText(
            hglib.tounicode(binascii.hexlify(self.repo[self.rev].node())))

    def copyShortHash(self):
        clip = QApplication.clipboard()
        clip.setText(
            hglib.tounicode(nodemod.short(self.repo[self.rev].node())))

    @pyqtSlot()
    def copyGitHash(self):
        fullGitHash = hglib.gitcommit_full(self.repo[self.rev])
        if fullGitHash is None:
            return
        clip = QApplication.clipboard()
        clip.setText(fullGitHash)

    @pyqtSlot()
    def copyShortGitHash(self):
        shortGitHash = hglib.gitcommit_short(self.repo[self.rev])
        if shortGitHash is None:
            return
        clip = QApplication.clipboard()
        clip.setText(shortGitHash)

    def changePhase(self, phase):
        currentphase = self.repo[self.rev].phase()
        if currentphase == phase:
            # There is nothing to do, we are already in the target phase
            return
        phasestr = pycompat.sysstr(phases.phasenames[phase])
        cmdline = ['phase', '--rev', '%s' % self.rev, '--%s' % phasestr]
        if currentphase < phase:
            # Ask the user if he wants to force the transition
            title = _('Backwards phase change requested')
            if currentphase == phases.draft and phase == phases.secret:
                # Here we are sure that the current phase is draft and the target phase is secret
                # Nevertheless we will not hard-code those phase names on the dialog strings to
                # make sure that the proper phase name translations are used
                main = _('Do you really want to make this revision <i>secret</i>?')
                text = _('Making a "<i>draft</i>" revision "<i>secret</i>" '
                         'is generally a safe operation.\n\n'
                         'However, there are a few caveats:\n\n'
                         '- "secret" revisions are not pushed. '
                         'This can cause you trouble if you\n'
                         'refer to a secret subrepo revision.\n\n'
                         '- If you pulled this revision from '
                         'a non publishing server it may be\n'
                         'moved back to "<i>draft</i>" if you pull '
                         'again from that particular server.\n\n'
                         'Please be careful!')
                labels = ((QMessageBox.StandardButton.Yes, _('&Make secret')),
                          (QMessageBox.StandardButton.No, _('&Cancel')))
            else:
                currentphasestr = pycompat.sysstr(
                    phases.phasenames[currentphase])
                main = _('Do you really want to <i>force</i> a backwards phase transition?')
                text = _('You are trying to move the phase of revision %d backwards,\n'
                         'from "<i>%s</i>" to "<i>%s</i>".\n\n'
                         'However, "<i>%s</i>" is a lower phase level than "<i>%s</i>".\n\n'
                         'Moving the phase backwards is not recommended.\n'
                         'For example, it may result in having multiple heads\nif you '
                         'modify a revision that you have already pushed\nto a server.\n\n'
                         'Please be careful!') % (self.rev, currentphasestr,
                                                  phasestr, phasestr,
                                                  currentphasestr)
                labels = ((QMessageBox.StandardButton.Yes, _('&Force')),
                          (QMessageBox.StandardButton.No, _('&Cancel')))
            if not qtlib.QuestionMsgBox(title, main, text,
                    labels=labels, parent=self):
                return
            cmdline.append('--force')
        self._runCommand(cmdline)

    @pyqtSlot(QAction)
    def _changePhaseByMenu(self, action):
        phasenum = action.data()
        self.changePhase(phasenum)

    @pyqtSlot()
    def compressRevisionsPair(self):
        reva, revb = self._selectedIntRevisionsPair()
        ctxa, ctxb = map(self.repo.hgchangectx, [reva, revb])
        if ctxa.ancestor(ctxb).rev() == ctxb.rev():
            revs = [reva, revb]
        elif ctxa.ancestor(ctxb).rev() == ctxa.rev():
            revs = [revb, reva]
        else:
            InfoMsgBox(_('Unable to compress history'),
                       _('Selected changeset pair not related'))
            return
        dlg = compress.CompressDialog(self._repoagent, revs, self)
        dlg.exec()

    def _pickRevision(self):
        """Pick selected revision on top of working directory parent"""
        opts = {'rev': self.rev}
        dlg = pick.PickDialog(self._repoagent, self, **opts)
        dlg.exec()

    def rebaseRevision(self):
        """Rebase selected revision on top of working directory parent"""
        opts = {'source' : self.rev, 'dest': self.repo[b'.'].rev()}
        dlg = rebase.RebaseDialog(self._repoagent, self, **opts)
        dlg.exec()

    @pyqtSlot()
    def rebaseSourceDestRevisionsPair(self):
        source, dest = self._selectedIntRevisionsPair()
        dlg = rebase.RebaseDialog(self._repoagent, self,
                                  source=source, dest=dest)
        dlg.exec()

    def qimportRevision(self):
        """QImport revision and all descendents to MQ"""
        if b'qparent' in self.repo.tags():
            endrev = b'qparent'
        else:
            endrev = b''

        # Check whether there are existing patches in the MQ queue whose name
        # collides with the revisions that are going to be imported
        revList = self.repo.revs(b'%s::%s and not hidden()' %
                                 (hglib.fromunicode(str(self.rev)), endrev))

        if endrev and not revList:
            # There is a qparent but the revision list is empty
            # This means that the qparent is not a descendant of the
            # selected revision
            QMessageBox.warning(self, _('Cannot import selected revision'),
                _('The selected revision (rev #%d) cannot be imported '
                'because it is not a descendant of ''qparent'' (rev #%d)') \
                % (self.rev, hglib.revsymbol(self.repo, b'qparent').rev()))
            return

        patchdir = hglib.tounicode(self.repo.vfs.join(b'patches'))
        def patchExists(p):
            return os.path.exists(os.path.join(patchdir, p))

        # Note that the following two arrays are both ordered by "rev"
        defaultPatchNames = ['%d.diff' % rev for rev in revList]
        defaultPatchesExist = [patchExists(p) for p in defaultPatchNames]
        if any(defaultPatchesExist):
            # We will qimport each revision one by one, starting from the newest
            # To do so, we will find a valid and unique patch name for each
            # revision that we must qimport (i.e. a filename that does not
            # already exist)
            # and then we will import them one by one starting from the newest
            # one, using these unique names
            def getUniquePatchName(baseName):
                maxRetries = 99
                for n in range(1, maxRetries):
                    patchName = baseName + '_%02d.diff' % n
                    if not patchExists(patchName):
                        return patchName
                return baseName

            patchNames = {}
            for n, rev in enumerate(revList):
                if defaultPatchesExist[n]:
                    patchNames[rev] = getUniquePatchName(str(rev))
                else:
                    # The default name is safe
                    patchNames[rev] = defaultPatchNames[n]

            # qimport each revision individually, starting from the topmost one
            revList.reverse()
            cmdlines = []
            for rev in revList:
                cmdlines.append(['qimport', '--rev', '%s' % rev,
                                 '--name', patchNames[rev]])
            self._runCommandSequence(cmdlines)
        else:
            # There were no collisions with existing patch names, we can
            # simply qimport the whole revision set in a single go
            cmdline = ['qimport', '--rev',
                       '%s::%s' % (self.rev, hglib.tounicode(endrev))]
            self._runCommand(cmdline)

    def qfinishRevision(self):
        """Finish applied patches up to and including selected revision"""
        self._mqActions.finishRevision(str(self.rev))

    @pyqtSlot()
    def qgotoParentRevision(self):
        """Apply an unapplied patch, or qgoto the parent of an applied patch"""
        self.qgotoRevision(self.repo[self.rev].p1().rev())

    @pyqtSlot()
    def qgotoSelectedRevision(self):
        self.qgotoRevision(self.rev)

    def qgotoRevision(self, rev):
        """Make REV the top applied patch"""
        mqw = self._mqActions
        ctx = self.repo[rev]
        if b'qparent' in ctx.tags():
            mqw.popAllPatches()
        else:
            mqw.gotoPatch(hglib.tounicode(ctx.thgmqpatchname()))

    @pyqtSlot()
    def qdeletePatches(self):
        """Delete unapplied patch(es)"""
        patches = self._selectedUnappliedPatches()
        self._mqActions.deletePatches(patches)

    @pyqtSlot()
    def qfoldPatches(self):
        patches = self._selectedUnappliedPatches()
        self._mqActions.foldPatches(patches)

    def qrename(self):
        patches = self._selectedUnappliedPatches()
        revs = self._selectedIntRevisions()
        if patches:
            pname = patches[0]
        elif revs:
            pname = hglib.tounicode(self.repo[revs[0]].thgmqpatchname())
        else:
            return
        self._mqActions.renamePatch(pname)

    def _qpushRevision(self, move=False, exact=False):
        """QPush REV with the selected options"""
        ctx = self.repo[self.rev]
        patchname = hglib.tounicode(ctx.thgmqpatchname())
        self._mqActions.pushPatch(patchname, move=move, exact=exact)

    def qpushRevision(self):
        """Call qpush with no options"""
        self._qpushRevision(move=False, exact=False)

    def qpushExactRevision(self):
        """Call qpush using the exact flag"""
        self._qpushRevision(exact=True)

    def qpushMoveRevision(self):
        """Make REV the top applied patch"""
        self._qpushRevision(move=True)

    def runCustomCommand(self, command: str, showoutput: bool = False, workingdir: str = '',
            files: Optional[List[str]] = None) -> Optional[Union[int, subprocess.Popen]]:
        """Execute 'custom commands', on the selected repository"""
        # Perform variable expansion
        # This is done in two steps:
        # 1. Expand environment variables
        command = os.path.expandvars(command).strip()
        if not command:
            InfoMsgBox(_('Invalid command'),
                       _('The selected command is empty'))
            return

        if workingdir:
            workingdir = os.path.expandvars(workingdir).strip()

        # 2. Expand internal workbench variables
        def filelist2str(filelist: List[str]) -> str:
            return hglib.tounicode(b' '.join(
                procutil.shellquote(
                os.path.normpath(self.repo.wjoin(hglib.fromunicode(filename))))
                for filename in filelist
            ))

        if files is None:
            files = []

        selection = self.repoview.selectedRevisions()

        def selectionfiles2str(source: str) -> str:
            files = set()
            for rev in selection:
                files.update(
                    hglib.tounicode(f)
                    for f in getattr(self.repo[rev], source)()
                )
            return filelist2str(sorted(files))

        vars = {
            'ROOT': lambda: hglib.tounicode(self.repo.root),
            'REVID': lambda: '+'.join(str(self.repo[rev]) for rev in selection),
            'REV': lambda: '+'.join(str(rev) for rev in selection),
            'FILES': lambda: selectionfiles2str('files'),
            'ALLFILES': lambda: selectionfiles2str('manifest'),
            'SELECTEDFILES': lambda: filelist2str(files),
        }

        if len(selection) == 2:
            pairvars = {
                'REV_A': lambda: selection[0],
                'REV_B': lambda: selection[1],
                'REVID_A': lambda: str(self.repo[selection[0]]),
                'REVID_B': lambda: str(self.repo[selection[1]]),
            }
            vars.update(pairvars)

        for var in vars:
            bracedvar = '{%s}' % var
            if bracedvar in command:
                command = command.replace(bracedvar, str(vars[var]()))
            if workingdir and bracedvar in workingdir:
                workingdir = workingdir.replace(bracedvar, str(vars[var]()))
        if not workingdir:
            workingdir = hglib.tounicode(self.repo.root)

        # Show the Output Log if configured to do so
        if showoutput:
            self.makeLogVisible.emit(True)

        # If the user wants to run mercurial,
        # do so via our usual runCommand method
        cmd = shlex.split(command)
        cmdtype = cmd[0].lower()
        if cmdtype == 'hg':
            sess = self._runCommand(pycompat.maplist(hglib.tounicode, cmd[1:]))
            sess.commandFinished.connect(self._notifyWorkingDirChanges)
            return
        elif cmdtype == 'thg':
            cmd = cmd[1:]
            if '--repository' in cmd:
                _ui = hglib.loadui()
            else:
                cmd += ['--repository', self.repo.root]
                _ui = self.repo.ui.copy()
            _ui.ferr = util.bytesio()
            # avoid circular import of hgqt.run by importing it inplace
            from . import run
            cmdb = []
            for part in cmd:
                if isinstance(part, str):
                    cmdb.append(hglib.fromunicode(part))
                else:
                    cmdb.append(part)
            res = run.dispatch(cmdb, u=_ui)
            if res:
                errormsg = _ui.ferr.getvalue().strip()
                if errormsg:
                    errormsg = \
                        _('The following error message was returned:'
                          '\n\n<b>%s</b>') % hglib.tounicode(errormsg)
                errormsg +=\
                    _('\n\nPlease check that the "thg" command is valid.')
                qtlib.ErrorMsgBox(
                    _('Failed to execute custom TortoiseHg command'),
                    _('The command "%s" failed (code %d).')
                    % (hglib.tounicode(command), res), errormsg)
            return res

        # Otherwise, run the selected command in the background
        try:
            res = subprocess.Popen(command, cwd=workingdir, shell=True)
        except OSError as ex:
            res = 1
            qtlib.ErrorMsgBox(_('Failed to execute custom command'),
                _('The command "%s" could not be executed.') % hglib.tounicode(command),
                _('The following error message was returned:\n\n"%s"\n\n'
                'Please check that the command path is valid and '
                'that it is a valid application') % hglib.tounicode(ex.strerror))
        return res

    @pyqtSlot(QAction)
    def _runCustomCommandByMenu(self, action):
        command, showoutput, workingdir = action.data()
        self.runCustomCommand(command, showoutput, workingdir)

    @pyqtSlot(str, list)
    def handleRunCustomCommandRequest(
        self, toolname: str, files: List[str]
    ) -> None:
        tools, toollist = hglib.tortoisehgtools(self.repo.ui)
        if not tools or toolname not in toollist:
            return
        command = tools[toolname].get('command', '')
        showoutput = tools[toolname].get('showoutput', False)
        workingdir = tools[toolname].get('workingdir', '')
        self.runCustomCommand(command, showoutput, workingdir, files)

    def _runCommand(self, cmdline):
        sess = self._repoagent.runCommand(cmdline, self)
        self._handleNewCommand(sess)
        return sess

    def _runCommandSequence(self, cmdlines):
        sess = self._repoagent.runCommandSequence(cmdlines, self)
        self._handleNewCommand(sess)
        return sess

    def _handleNewCommand(self, sess):
        self.clearInfoBar()
        sess.outputReceived.connect(self._repoviewFrame.showOutput)

    @pyqtSlot()
    def _notifyWorkingDirChanges(self):
        shlib.shell_notify([self.repo.root])

    @pyqtSlot()
    def _refreshCommitTabIfNeeded(self):
        """Refresh the Commit tab if the user settings require it"""
        if self.taskTabsWidget.currentIndex() != self._namedTabs['commit']:
            return

        refreshwd = self._repoagent.configString(
            'tortoisehg', 'refreshwdstatus')
        # Valid refreshwd values are 'auto', 'always' and 'alwayslocal'
        if refreshwd != 'auto':
            if refreshwd == 'always' \
                    or paths.is_on_fixed_drive(self.repo.root):
                self.commitDemand.forward('refreshWctx')


class LightRepoWindow(QMainWindow):
    def __init__(self, actionregistry, repoagent):
        super().__init__()
        self._repoagent = repoagent
        self.setIconSize(qtlib.smallIconSize())

        repo = repoagent.rawRepo()
        val = repo.ui.config(b'tortoisehg', b'tasktabs').lower()
        if val not in (b'east', b'west'):
            repo.ui.setconfig(b'tortoisehg', b'tasktabs', b'east')
        rw = RepoWidget(actionregistry, repoagent, self)
        self.setCentralWidget(rw)

        self._edittbar = tbar = self.addToolBar(_('&Edit Toolbar'))
        tbar.setObjectName('edittbar')
        a = tbar.addAction(qtlib.geticon('view-refresh'), _('&Refresh'))
        a.setShortcuts(QKeySequence.StandardKey.Refresh)
        a.triggered.connect(self.refresh)

        tbar = rw.filterBar()
        tbar.setObjectName('filterbar')
        tbar.setWindowTitle(_('&Filter Toolbar'))
        self.addToolBar(tbar)

        stbar = cmdui.ThgStatusBar(self)
        repoagent.progressReceived.connect(stbar.setProgress)
        rw.showMessageSignal.connect(stbar.showMessage)
        rw.progress.connect(stbar.progress)
        self.setStatusBar(stbar)

        s = QSettings()
        s.beginGroup('LightRepoWindow')
        self.restoreGeometry(qtlib.readByteArray(s, 'geometry'))
        self.restoreState(qtlib.readByteArray(s, 'windowState'))
        stbar.setVisible(qtlib.readBool(s, 'statusBar', True))
        s.endGroup()

        self.setWindowTitle(_('TortoiseHg: %s') % repoagent.displayName())

    def createPopupMenu(self):
        menu = super().createPopupMenu()
        assert menu  # should have toolbar
        stbar = self.statusBar()
        a = menu.addAction(_('S&tatus Bar'))
        a.setCheckable(True)
        a.setChecked(stbar.isVisibleTo(self))
        a.triggered.connect(stbar.setVisible)
        menu.addSeparator()
        menu.addAction(_('&Settings'), self._editSettings)
        return menu

    def closeEvent(self, event):
        rw = self.centralWidget()
        if not rw.closeRepoWidget():
            event.ignore()
            return
        s = QSettings()
        s.beginGroup('LightRepoWindow')
        s.setValue('geometry', self.saveGeometry())
        s.setValue('windowState', self.saveState())
        s.setValue('statusBar', self.statusBar().isVisibleTo(self))
        s.endGroup()
        event.accept()

    @pyqtSlot()
    def refresh(self):
        self._repoagent.pollStatus()
        rw = self.centralWidget()
        rw.reload()

    def setSyncUrl(self, url):
        rw = self.centralWidget()
        rw.setSyncUrl(url)

    @pyqtSlot()
    def _editSettings(self):
        dlg = settings.SettingsDialog(parent=self)
        dlg.exec()
