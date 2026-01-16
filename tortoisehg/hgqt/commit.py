# commit.py - TortoiseHg's commit widget and standalone dialog
#
# Copyright 2010 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import typing

import os
import re
import tempfile
import time
from typing import (
    cast,
)

from .qsci import (
    QsciAPIs,
)
from .qtcore import (
    QSettings,
    QSize,
    QTimer,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from .qtgui import (
    QAction,
    QActionGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFont,
    QFrame,
    QHBoxLayout,
    QKeySequence,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSizePolicy,
    QStyle,
    QStyleFactory,
    QStyleOptionToolButton,
    QSplitter,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mercurial import (
    cmdutil,
    error,
    obsolete,  # delete if obsolete becomes enabled by default
    phases,
    pycompat,
)
from mercurial.utils import (
    dateutil,
)

from ..util import (
    hglib,
    i18n,
    shlib,
    wconfig,
)
from ..util.i18n import _
from . import (
    branchop,
    cmdcore,
    cmdui,
    hgrcutil,
    lfprompt,
    qscilib,
    qtlib,
    revpanel,
    status,
    topic,
    thgrepo,
)
from .messageentry import MessageEntry

if typing.TYPE_CHECKING:
    from typing import (
        Callable,
        Dict,
        Iterable,
        List,
        Optional,
        Text,
        Tuple,
        Union,
    )
    from mercurial import localrepo
    from .qtcore import QObject
    from .thgrepo import RepoAgent

    # `opts` arg type internal to this module.  Public classes use
    # Dict[Text, bytes] to support @command.
    CommitOpts = Dict[str, Union[bool, str]]

    # TODO: simplify to just be List[Text]?
    CmdLines = List[List[Union[bytes, str]]]

if os.name == 'nt':
    from ..util import bugtraq
    _hasbugtraq = True
else:
    _hasbugtraq = False

def readopts(repoagent: RepoAgent) -> CommitOpts:
    opts = {
        'ciexclude': repoagent.configString('tortoisehg', 'ciexclude'),
        'pushafter': repoagent.configString('tortoisehg', 'cipushafter'),
        'autoinc': repoagent.configString('tortoisehg', 'autoinc'),
        'recurseinsubrepos': repoagent.configBool('tortoisehg', 'recurseinsubrepos'),
        'bugtraqplugin': repoagent.configString('tortoisehg',
                                                'issue.bugtraqplugin'),
        'bugtraqparameters': repoagent.configString('tortoisehg',
                                                    'issue.bugtraqparameters')
    }
    if opts['bugtraqparameters']:
        opts['bugtraqparameters'] = os.path.expandvars(
            opts['bugtraqparameters'])
    opts['bugtraqtrigger'] = repoagent.configString('tortoisehg', 'issue.bugtraqtrigger')

    return opts

def commitopts2str(opts: CommitOpts, mode: str = 'commit') -> str:
    optslist = []
    for opt, value in opts.items():
        if opt in ['user', 'date', 'pushafter', 'autoinc',
                   'recurseinsubrepos']:
            if mode == 'merge' and opt == 'autoinc':
                # autoinc does not apply to merge commits
                continue
            if value is True:
                optslist.append('--' + opt)
            elif value:
                optslist.append('--%s=%s' % (opt, value))
    return ' '.join(optslist)

def mergecommitmessage(repo: localrepo.localrepository) -> str:
    wctx = repo[None]
    engmsg = repo.ui.configbool(b'tortoisehg', b'engmsg')
    bcustommsg = repo.ui.config(b'tortoisehg', b'mergecommitmessage')
    if bcustommsg:
        text = hglib.tounicode(cmdutil.rendertemplate(wctx, bcustommsg))
    elif wctx.p1().branch() == wctx.p2().branch():
        msgset = i18n.keepgettext()._('Merge')
        text = engmsg and msgset['id'] or msgset['str']
    else:
        msgset = i18n.keepgettext()._('Merge with %s')
        text = engmsg and msgset['id'] or msgset['str']
        text = text % hglib.tounicode(wctx.p2().branch())
    return text

def _getUserOptions(opts: CommitOpts,
                    optionlist: Iterable[str]) -> List[str]:
    out = []
    for opt in optionlist:
        if opt not in opts:
            continue
        val = opts[opt]
        if val is False:
            continue
        elif val is True:
            out.append('--' + opt)
        else:
            out.append('--' + opt)
            assert isinstance(val, str)  # help pytype
            # Even with the assertion, the cast is needed or pytype thinks `out`
            # is List[Union[bool, str]]
            out.append(str(val))
    return out

def _mqNewRefreshCommand(repo: localrepo.localrepository,
                         isnew: bool,
                         stwidget: status.StatusWidget,
                         pnwidget: QLineEdit,
                         message: str,
                         opts: CommitOpts,
                         olist: Iterable[str]) -> Optional[CmdLines]:
    # TODO: convert all bytes to unicode in the return value
    if isnew:
        name = pnwidget.text()
        if not name:
            qtlib.ErrorMsgBox(_('Patch Name Required'),
                              _('You must enter a patch name'))
            pnwidget.setFocus()
            return
        cmdline = ['qnew', name]
    else:
        cmdline = ['qrefresh']
    if message:
        cmdline += ['--message=' + hglib.tounicode(message)]
    cmdline += _getUserOptions(opts, olist)
    files = ['--'] + [repo.wjoin(x) for x in stwidget.getChecked()]
    addrem = [repo.wjoin(x) for x in stwidget.getChecked('!?')]
    if len(files) > 1:
        cmdline += files
    else:
        cmdline += ['--exclude', repo.root]
    if addrem:
        cmdlines = [['addremove'] + addrem, cmdline]
    else:
        cmdlines = [cmdline]
    return cmdlines

_topicmap = {
    'amend': _('Commit', 'start progress'),
    'commit': _('Commit', 'start progress'),
    'qnew': _('MQ Action', 'start progress'),
    'qref': _('MQ Action', 'start progress'),
    'rollback': _('Rollback', 'start progress'),
    }

# Technical Debt for CommitWidget
#  disable commit button while no message is entered or no files are selected
#  qtlib decode failure dialog (ask for retry locale, suggest HGENCODING)
#  spell check / tab completion
#  in-memory patching / committing chunk selected files

class CommitWidget(QWidget, qtlib.TaskWidget):
    'A widget that encompasses a StatusWidget and commit extras'
    commitButtonEnable = pyqtSignal(bool)
    linkActivated = pyqtSignal(str)
    showMessage = pyqtSignal(str)
    grepRequested = pyqtSignal(str, dict)
    runCustomCommandRequested = pyqtSignal(str, list)
    commitComplete = pyqtSignal()

    progress = pyqtSignal(str, object, str, str, object)

    def __init__(self,
                 repoagent: RepoAgent,
                 pats: List[bytes],
                 opts: Dict[str, bytes],
                 parent: Optional[QObject] = None,
                 rev: Optional[str] = None) -> None:
        # TODO: drop `opts`, since it is immediately overwritten?
        # TODO: make `rev` a byte string?
        QWidget.__init__(self, parent)

        repoagent.configChanged.connect(self.refresh)
        repoagent.repositoryChanged.connect(self.repositoryChanged)
        self._repoagent = repoagent
        repo = repoagent.rawRepo()
        self._cmdsession = cmdcore.nullCmdSession()
        self._rev = rev
        self.lastAction = None
        # Dictionary storing the last (commit message, modified flag)
        # 'commit' is used for 'commit' and 'qnew', while
        # 'amend' is used for 'amend' and 'qrefresh'
        self.lastCommitMsgs = {'commit': ('', False), 'amend': ('', False)}
        self.currentAction = None

        self.opts = opts = readopts(repoagent) # user, date

        self.stwidget = status.StatusWidget(repoagent, pats, opts, self)
        self.stwidget.showMessage.connect(self.showMessage)
        self.stwidget.progress.connect(self.progress)
        self.stwidget.linkActivated.connect(self.linkActivated)
        self.stwidget.fileDisplayed.connect(self.fileDisplayed)
        self.stwidget.grepRequested.connect(self.grepRequested)
        self.stwidget.runCustomCommandRequested.connect(
            self.runCustomCommandRequested)
        self.msghistory = []

        layout = QVBoxLayout()
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)
        layout.addWidget(self.stwidget)
        self.setLayout(layout)

        vbox = QVBoxLayout()
        vbox.setSpacing(0)
        vbox.setContentsMargins(*(0,)*4)

        hbox = QHBoxLayout()
        hbox.setContentsMargins(*(0,)*4)
        tbar = QToolBar(_("Commit Dialog Toolbar"), self)
        tbar.setStyleSheet(qtlib.tbstylesheet)
        hbox.addWidget(tbar)

        self.branchbutton = tbar.addAction(_('Branch: '))
        font = self.branchbutton.font()
        font.setBold(True)
        self.branchbutton.setFont(font)
        self.branchbutton.triggered.connect(self.branchOp)
        self.branchop = None

        if hglib.hastopicext(repo):
            self.topicbutton = tbar.addAction(_('Topic: '))
            font = self.topicbutton.font()
            font.setBold(True)
            self.topicbutton.setFont(font)
            self.topicbutton.triggered.connect(self.topicOp)
        else:
            self.topicbutton = None

        self.recentMessagesButton = QToolButton(
            text=_('Copy message'),
            popupMode=QToolButton.ToolButtonPopupMode.InstantPopup,
            toolTip=_('Copy one of the recent commit messages'))
        m = QMenu(self.recentMessagesButton)
        m.triggered.connect(self.msgSelected)
        self.recentMessagesButton.setMenu(m)
        tbar.addWidget(self.recentMessagesButton)
        self.updateRecentMessages()

        tbar.addAction(_('Options')).triggered.connect(self.details)
        tbar.setIconSize(qtlib.smallIconSize())

        if _hasbugtraq and self.opts['bugtraqplugin']:
            # We create the "Show Issues" button, but we delay its setup
            # because creating the bugtraq object is slow and blocks the GUI,
            # which would result in a noticeable slow down while creating
            # the commit widget
            self.showIssues = tbar.addAction(_('Show Issues'))
            self.showIssues.setEnabled(False)
            self.showIssues.setToolTip(_('Please wait...'))
            def setupBugTraqButton():
                self.bugtraq = self.createBugTracker()
                try:
                    parameters = self.opts['bugtraqparameters']
                    linktext = self.bugtraq.get_link_text(parameters)
                except Exception as e:
                    # TODO: use TypedDict instead of casting?
                    tracker = cast(str, self.opts['bugtraqplugin']).split(' ', 1)[1]
                    errormsg =  _('Failed to load issue tracker \'%s\': %s') \
                                 % (tracker, hglib.exception_str(e))
                    self.showIssues.setToolTip(errormsg)
                    qtlib.ErrorMsgBox(_('Issue Tracker'), errormsg,
                                      parent=self)
                    self.bugtraq = None
                else:
                    # connect UI because we have a valid bug tracker
                    self.commitComplete.connect(self.bugTrackerPostCommit)
                    self.showIssues.setText(linktext)
                    self.showIssues.triggered.connect(
                        self.getBugTrackerCommitMessage)
                    self.showIssues.setToolTip(_('Show Issues...'))
                    self.showIssues.setEnabled(True)
            QTimer.singleShot(100, setupBugTraqButton)

        self.stopAction = tbar.addAction(_('Stop'))
        self.stopAction.triggered.connect(self.stop)
        self.stopAction.setIcon(qtlib.geticon('process-stop'))
        self.stopAction.setEnabled(False)

        hbox.addStretch(1)

        vbox.addLayout(hbox, 0)
        self.buttonHBox = hbox

        if b'mq' in self.repo.extensions():
            self.hasmqbutton = True
            pnhbox = QHBoxLayout()
            self.pnlabel = QLabel()
            pnhbox.addWidget(self.pnlabel)
            self.pnedit = QLineEdit()
            self.pnedit.setPlaceholderText(_('### patch name ###'))
            self.pnedit.setMaximumWidth(250)
            pnhbox.addWidget(self.pnedit)
            pnhbox.addStretch()
            vbox.addLayout(pnhbox)
        else:
            self.hasmqbutton = False

        self.optionslabel = QLabel()
        self.optionslabel.setSizePolicy(QSizePolicy.Policy.Ignored,
                                        QSizePolicy.Policy.Preferred)
        vbox.addWidget(self.optionslabel, 0)

        self.wdirinfo = revpanel.WDirInfoWidget(repo)
        vbox.addWidget(self.wdirinfo, 0)

        msgte = MessageEntry(self, self.stwidget.getChecked)
        msgte.installEventFilter(qscilib.KeyPressInterceptor(self))
        vbox.addWidget(msgte, 1)
        upperframe = QFrame()

        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        sp.setHorizontalStretch(1)
        upperframe.setSizePolicy(sp)
        upperframe.setLayout(vbox)

        self.split = QSplitter(Qt.Orientation.Vertical)
        if os.name == 'nt':
            self.split.setStyle(QStyleFactory.create('Plastique'))
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        sp.setHorizontalStretch(1)
        sp.setVerticalStretch(0)
        self.split.setSizePolicy(sp)
        # Add our widgets to the top of our splitter
        self.split.addWidget(upperframe)
        self.split.setCollapsible(0, False)
        # Add status widget document frame below our splitter
        # this reparents the docf from the status splitter
        self.split.addWidget(self.stwidget.docf)

        # add our splitter where the docf used to be
        self.stwidget.split.addWidget(self.split)
        self.msgte = msgte

    @property
    def repo(self) -> localrepo.localrepository:
        return self._repoagent.rawRepo()

    @property
    def rev(self) -> str:
        """Return current revision"""
        return self._rev

    def selectRev(self, rev: str) -> None:
        """
        Select the revision that must be set when the dialog is shown again
        """
        self._rev = rev

    @pyqtSlot(int)
    @pyqtSlot(object)
    def setRev(self, rev):
        # TODO: is int even possible here?
        """Change revision to show"""
        self.selectRev(rev)
        if self.hasmqbutton:
            preferredActionName = self._getPreferredActionName()
            curractionName = self.mqgroup.checkedAction()._name
            if curractionName != preferredActionName:
                self.commitSetAction(refresh=True,
                    actionName=preferredActionName)

    def _getPreferredActionName(self) -> str:
        """Select the preferred action, depending on the selected revision"""
        if not self.hasmqbutton:
            return 'commit'
        else:
            pctx = self.repo[b'.']
            ispatch = b'qtip' in pctx.tags()
            if not ispatch:
                # Set the button to Commit
                return 'commit'
            elif self.rev is None:
                # Set the button to QNew
                return 'qnew'
            else:
                # Set the button to QRefresh
                return 'qref'

    def commitSetupButton(self) -> QToolButton:
        ispatch: Callable[[localrepo.localrepository], bool] = lambda r: b'qtip' in r[b'.'].tags()
        notpatch: Callable[[localrepo.localrepository], bool] = lambda r: b'qtip' not in r[b'.'].tags()
        def canamend(r: localrepo.localrepository) -> bool:
            if ispatch(r):
                return False
            ctx = r[b'.']
            return (ctx.phase() != phases.public) \
                and len(r[None].parents()) < 2 \
                and (obsolete.isenabled(self.repo, obsolete.allowunstableopt)
                     or not ctx.children())

        acts = [
            ('commit', _('Commit changes'), _('Commit'), notpatch),
            ('amend', _('Amend current revision'), _('Amend'), canamend),
        ]
        if self.hasmqbutton:
            acts += [
                ('qnew', _('Create a new patch'), _('QNew'), None),
                ('qref', _('Refresh current patch'), _('QRefresh'), ispatch),
            ]
        acts = tuple(acts)

        class CommitToolButton(QToolButton):
            def styleOption(self):
                opt = QStyleOptionToolButton()
                opt.initFrom(self)
                return opt
            def menuButtonWidth(self):
                style = self.style()
                opt = self.styleOption()
                opt.features = QStyleOptionToolButton.ToolButtonFeature.MenuButtonPopup
                rect = style.subControlRect(QStyle.ComplexControl.CC_ToolButton, opt,
                                            QStyle.SubControl.SC_ToolButtonMenu, self)
                return rect.width()
            def setBold(self):
                f = self.font()
                f.setWeight(QFont.Weight.Bold)
                self.setFont(f)
            def sizeHint(self):
                # Set the desired width to keep the button from resizing
                return QSize(self._width, QToolButton.sizeHint(self).height())

        self.committb = committb = CommitToolButton(self)
        committb.setBold()
        committb.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        fmk = lambda s: committb.fontMetrics().horizontalAdvance(hglib.tounicode(s[2]))
        committb._width = (max(pycompat.maplist(fmk, acts))
                           + 4*committb.menuButtonWidth())

        class CommitButtonMenu(QMenu):
            def __init__(self, parent, repo):
                self.repo = repo
                QMenu.__init__(self, parent)
            def getActionByName(self, act: str) -> QAction:
                return [a for a in self.actions() if a._name == act][0]
            def showEvent(self, event):
                for a in self.actions():
                    if a._enablefunc:
                        a.setEnabled(a._enablefunc(self.repo))
                return QMenu.showEvent(self, event)
        self.mqgroup = QActionGroup(self)
        commitbmenu = CommitButtonMenu(committb, self.repo)
        menurefresh = lambda: self.commitSetAction(refresh=True)
        for a in acts:
            action = QAction(a[1], self.mqgroup)
            action._name = a[0]
            action._text = a[2]
            action._enablefunc = a[3]
            action.triggered.connect(menurefresh)
            action.setCheckable(True)
            commitbmenu.addAction(action)
        committb.setMenu(commitbmenu)
        committb.clicked.connect(self.mqPerformAction)
        self.commitButtonEnable.connect(committb.setEnabled)
        self.commitSetAction(actionName=self._getPreferredActionName())
        sc = QShortcut(QKeySequence('Ctrl+Return'), self, self.mqPerformAction)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc = QShortcut(QKeySequence('Ctrl+Enter'), self, self.mqPerformAction)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        return committb

    @pyqtSlot(bool)
    def commitSetAction(self,
                        refresh: bool = False,
                        actionName: Optional[str] = None) -> None:
        # TODO: should this slot also be declared with str arg?
        allowcs = False
        if actionName:
            selectedAction = \
                [act for act in self.mqgroup.actions() \
                    if act._name == actionName][0]
            selectedAction.setChecked(True)
        curraction = self.mqgroup.checkedAction()
        oldpctx = self.stwidget.pctx
        pctx = self.repo[b'.']
        if curraction._name == 'qnew':
            self.pnlabel.setVisible(True)
            self.pnedit.setVisible(True)
            self.pnedit.setFocus()
            pn = time.strftime('%Y-%m-%d_%H-%M-%S')
            pn += '_r%d+.diff' % self.repo[b'.'].rev()
            self.pnedit.setText(pn)
            self.pnedit.selectAll()
            self.stwidget.setPatchContext(None)
            refreshwctx = refresh and oldpctx is not None
        else:
            if self.hasmqbutton:
                self.pnlabel.setVisible(False)
                self.pnedit.setVisible(False)
            ispatch = b'qtip' in pctx.tags()
            def switchAction(action, name):
                action.setChecked(False)
                action = self.committb.menu().getActionByName(name)
                action.setChecked(True)
                return action
            if curraction._name == 'qref' and not ispatch:
                curraction = switchAction(curraction, 'commit')
            elif curraction._name == 'commit' and ispatch:
                curraction = switchAction(curraction, 'qref')
            if curraction._name in ('qref', 'amend'):
                refreshwctx = refresh
                self.stwidget.setPatchContext(pctx)
            elif curraction._name == 'commit':
                refreshwctx = refresh and oldpctx is not None
                self.stwidget.setPatchContext(None)
                allowcs = len(self.repo[None].parents()) == 1
        if curraction._name in ('qref', 'amend'):
            if self.lastAction not in ('qref', 'amend'):
                self.lastCommitMsgs['commit'] = (self.msgte.text(),
                                                 self.msgte.isModified())
            if self.lastCommitMsgs['amend'][0]:
                self.setMessage(*self.lastCommitMsgs['amend'])
            elif oldpctx is None or oldpctx.node() != pctx.node():
                # pctx must be refreshed if hash changes
                self.setMessage(hglib.tounicode(pctx.description()))
        else:
            if self.lastAction in ('qref', 'amend'):
                self.lastCommitMsgs['amend'] = (self.msgte.text(),
                                                self.msgte.isModified())
                self.setMessage(*self.lastCommitMsgs['commit'])
            elif len(self.repo[None].parents()) > 1:
                self.setMessage(mergecommitmessage(self.repo))
        if curraction._name == 'amend':
            self.stwidget.defcheck = 'amend'
        else:
            self.stwidget.defcheck = 'commit'
        self.stwidget.fileview.enableChangeSelection(allowcs)
        if not allowcs:
            self.stwidget.partials = {}
        if refreshwctx:
            self.stwidget.refreshWctx()
        custom = self.wdirinfo.custom.copy()
        custom['isAmend'] = curraction._name == 'amend'
        self.wdirinfo.updateItems(custom=custom)
        self.committb.setText(curraction._text)
        self.lastAction = curraction._name

    def getBranchCommandLine(self) -> Tuple[Optional[CmdLines], bool]:
        '''
        Create the command line to change or create the selected branch unless
        it is the selected branch

        Verify whether a branch exists on a repo. If it doesn't ask the user
        to confirm that it wants to create the branch. If it does and it is not
        the current branch as the user whether it wants to change to that branch.
        Depending on the user input, create the command line which will perform
        the selected action
        '''
        # This function is used both by commit() and mqPerformAction()
        repo = self.repo
        commandlines = []
        newbranch = False
        branch = hglib.fromunicode(self.branchop)
        if branch in repo.branchmap():
            # response: 0=Yes, 1=No, 2=Cancel
            if branch in [p.branch() for p in repo[None].parents()]:
                resp = 0
            else:
                # TODO: should look up only in branch namespace
                rev = hglib.revsymbol(repo, branch).rev()
                resp = qtlib.CustomPrompt(_('Confirm Branch Change'),
                    _('Named branch "%s" already exists, '
                      'last used in revision %d\n'
                      ) % (self.branchop, rev),
                    self,
                    (_('Restart &Branch'),
                     _('&Commit to current branch'),
                     _('Cancel')), 2, 2).run()
        else:
            resp = qtlib.CustomPrompt(_('Confirm New Branch'),
                _('Create new named branch "%s" with this commit?\n'
                  ) % self.branchop,
                self,
                (_('Create &Branch'),
                 _('&Commit to current branch'),
                 _('Cancel')), 2, 2).run()
        if resp == 0:
            newbranch = True
            assert branch is not None
            commandlines.append(['branch', '--force', branch])
        elif resp == 2:
            return None, False
        return commandlines, newbranch

    @pyqtSlot()
    def mqPerformAction(self) -> Optional[bool]:
        # TODO: drop bool return?
        curraction = self.mqgroup.checkedAction()
        if curraction._name == 'commit':
            return self.commit()
        elif curraction._name == 'amend':
            return self.commit(amend=True)

        # Check if we need to change branch first
        wholecmdlines = []  # [[cmd1, ...], [cmd2, ...], ...]
        if self.branchop:
            cmdlines, newbranch = self.getBranchCommandLine()
            if cmdlines is None:
                return
            wholecmdlines.extend(cmdlines)

        olist = ('user', 'date')
        cmdlines = _mqNewRefreshCommand(self.repo,
                                        curraction._name == 'qnew',
                                        self.stwidget, self.pnedit,
                                        self.msgte.text(), self.opts,
                                        olist)
        if not cmdlines:
            return
        wholecmdlines.extend(cmdlines)
        self._runCommand(curraction._name, wholecmdlines)

    @pyqtSlot(str, str)
    def fileDisplayed(self, wfile: str, contents: str) -> None:
        'Status widget is displaying a new file'
        if not (wfile and contents):
            return
        if self.msgte.autoCompletionThreshold() <= 0:
            # do not search for tokens if auto completion is disabled
            # pygments has several infinite loop problems we'd like to avoid
            return
        if self.msgte.lexer() is None:
            # qsci will crash if None is passed to QsciAPIs constructor
            return

        self._apis = QsciAPIs(self.msgte.lexer())
        tokens = set()
        for e in self.stwidget.getChecked():
            e = hglib.tounicode(e)
            tokens.add(e)
            tokens.add(os.path.basename(e))
        tokens.add(wfile)
        tokens.add(os.path.basename(wfile))
        try:
            from pygments.lexers import (  # pytype: disable=import-error
                guess_lexer_for_filename
            )
            from pygments.token import Token  # pytype: disable=import-error
            from pygments.util import (  # pytype: disable=import-error
                ClassNotFound
            )

            try:
                lexer = guess_lexer_for_filename(wfile, contents)
                assert lexer is not None  # help pytype

                # pytype: disable=attribute-error
                for tokentype, value in lexer.get_tokens(contents):
                    # pytype: enable=attribute-error
                    if tokentype in Token.Name and len(value) > 4:
                        tokens.add(value)
            except (ClassNotFound, TypeError):
                pass
        except ImportError:
            pass
        for n in sorted(list(tokens)):
            self._apis.add(n)
        self._apis.apiPreparationFinished.connect(self.apiPrepFinished)
        self._apis.prepare()

    def apiPrepFinished(self) -> None:
        'QsciAPIs has finished parsing displayed file'
        self.msgte.lexer().setAPIs(self._apis)

    def bugTrackerPostCommit(self) -> None:
        if not _hasbugtraq or self.opts['bugtraqtrigger'] != 'commit':
            return
        # commit already happened, get last message in history
        message = self.lastmessage
        error = self.bugtraq.on_commit_finished(message)
        if error:
            qtlib.ErrorMsgBox(_('Issue Tracker'), error, parent=self)
        # recreate bug tracker to get new COM object for next commit
        self.bugtraq = self.createBugTracker()

    def createBugTracker(self):
        opt = self.opts['bugtraqplugin']
        assert isinstance(opt, str)  # help pytype
        bugtraqid = opt.split(' ', 1)[0]
        result = bugtraq.BugTraq(bugtraqid)
        return result

    def getBugTrackerCommitMessage(self) -> None:
        parameters = self.opts['bugtraqparameters']
        message = self.getMessage(True)
        newMessage = self.bugtraq.get_commit_message(parameters, message)
        self.setMessage(newMessage)

    def details(self) -> None:
        mode = 'commit'
        if len(self.repo[None].parents()) > 1:
            mode = 'merge'
        dlg = DetailsDialog(self._repoagent, self.opts, self.userhist, self,
                            mode=mode)
        dlg.finished.connect(dlg.deleteLater)
        dlg.setWindowFlags(Qt.WindowType.Sheet)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.opts.update(dlg.outopts)
            self.refresh()

    @pyqtSlot(int)
    def repositoryChanged(self, flags: int) -> None:
        if flags & thgrepo.WorkingParentChanged:
            self._refreshWorkingState()
        elif flags & thgrepo.WorkingBranchChanged:
            self.refresh()

    def _refreshWorkingState(self) -> None:
        curraction = self.mqgroup.checkedAction()
        if curraction._name == 'commit' and not self.msgte.isModified():
            # default merge or close-branch message is outdated if new commit
            # was made by other widget or process
            self.msgte.clear()
        self.lastCommitMsgs['amend'] = ('', False)  # avoid loading stale cache
        # refresh() may load/save the stale 'amend' message in commitSetAction()
        self.refresh()

        # Updating after refreshWctx() leaves stale files in the commit list
        # after amend + evolve + update.
        if curraction._name == 'amend':
            self.commitSetAction()

        self.stwidget.refreshWctx() # Trigger reload of working context
        # clear the last 'amend' message
        # do not clear the last 'commit' message because there are many cases
        # in which we may write a commit message first, modify the repository
        # (e.g. amend or update and merge uncommitted changes) and then do the
        # actual commit
        self.lastCommitMsgs['amend'] = ('', False)  # clear saved stale cache

    @pyqtSlot()
    def refreshWctx(self) -> None:
        'User has requested a working context refresh'
        self.stwidget.refreshWctx() # Trigger reload of working context

    @pyqtSlot()
    def reload(self) -> None:
        'User has requested a reload'
        self.repo.thginvalidate()
        self.refresh()
        self.stwidget.refreshWctx() # Trigger reload of working context

    @pyqtSlot()
    def refresh(self) -> None:
        ispatch = self.repo[b'.'].thgmqappliedpatch()
        if not self.hasmqbutton:
            self.commitButtonEnable.emit(not ispatch)
        self.msgte.refresh(self.repo)

        # Update branch operation button
        branchu = hglib.tounicode(self.repo[None].branch())
        if self.branchop is None:
            title = _('Branch: ') + branchu
        elif self.branchop == False:
            title = _('Close Branch: ') + branchu
        else:
            title = _('New Branch: ') + self.branchop
        self.branchbutton.setText(title)

        if self.topicbutton:
            topic = getattr(self.repo, 'currenttopic')
            if topic:
                topic = hglib.tounicode(topic)
            else:
                topic = "None"
            self.topicbutton.setText(_('Topic: ') + topic)

        # Update options label, showing only whitelisted options.
        opts = commitopts2str(self.opts)
        self.optionslabelfmt = _('<b>Selected Options:</b> %s')
        self.optionslabel.setText(self.optionslabelfmt
                                  % qtlib.htmlescape(hglib.tounicode(opts),
                                                     False))
        self.optionslabel.setVisible(bool(opts))

        # Update wdir csinfo widget
        self.wdirinfo.set_revision(None)
        self.wdirinfo.updateItems()

        # This is ugly, but want pnlabel to have the same alignment/style/etc
        # as wdirinfo, so extract the needed parts of wdirinfo's markup.  Would
        # be nicer if csinfo exposed this information, or if csinfo could hold
        # widgets like pnlabel.
        if self.hasmqbutton:
            parent = _('Parent:')
            patchname = _('Patch name:')
            text = self.wdirinfo.revlabel.text()
            cellend = '</td>'
            firstidx = text.find(cellend) + len(cellend)
            secondidx = text[firstidx:].rfind('</tr>')
            if firstidx >= 0 and secondidx >= 0:
                start = text[0:firstidx].replace(parent, patchname)
                self.pnlabel.setText(start + text[firstidx+secondidx:])
            else:
                self.pnlabel.setText(patchname)
            self.commitSetAction()

    def branchOp(self) -> None:
        d = branchop.BranchOpDialog(self._repoagent, self.branchop, self)
        d.setWindowFlags(Qt.WindowType.Sheet)
        d.setWindowModality(Qt.WindowModality.WindowModal)
        if d.exec() == QDialog.DialogCode.Accepted:
            self.branchop = d.branchop
            if self.branchop is False:
                if not self.getMessage(True).strip():
                    engmsg = self._repoagent.configBool(
                        'tortoisehg', 'engmsg')
                    msgset = i18n.keepgettext()._('Close %s branch')
                    text = engmsg and msgset['id'] or msgset['str']
                    self.setMessage(text %
                                    hglib.tounicode(self.repo[None].branch()))
            self.msgte.setFocus()
            self.refresh()

    def topicOp(self) -> None:
        d = topic.TopicDialog(self._repoagent, None, self)
        d.setWindowFlags(Qt.WindowType.Sheet)
        d.setWindowModality(Qt.WindowModality.WindowModal)
        d.exec()
        self.msgte.setFocus()
        self.refresh()

    def canUndo(self) -> Optional[str]:
        'Returns undo description or None if not valid'
        desc, oldlen = hglib.readundodesc(self.repo)
        if desc == 'commit':
            return _('Rollback commit to revision %d') % (oldlen - 1)
        return None

    def rollback(self) -> None:
        msg = self.canUndo()
        if not msg:
            return
        d = QMessageBox.question(self, _('Confirm Undo'), msg,
                                 QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if d != QMessageBox.StandardButton.Ok:
            return
        self._runCommand('rollback', [['rollback']])

    def updateRecentMessages(self) -> None:
        # Define a menu that lists recent messages
        m = self.recentMessagesButton.menu()
        m.clear()
        for s in self.msghistory:
            title = s.split('\n', 1)[0][:70]
            a = m.addAction(title)
            a.setData(s)

    def getMessage(self, allowreplace: bool) -> bytes:
        text = self.msgte.text()
        try:
            return hglib.fromunicode(text, 'strict')
        except UnicodeEncodeError:
            if allowreplace:
                return hglib.fromunicode(text, 'replace')
            else:
                raise

    @pyqtSlot(QAction)
    def msgSelected(self, action: QAction) -> None:
        if self.msgte.text() and self.msgte.isModified():
            d = QMessageBox.question(self, _('Confirm Discard Message'),
                        _('Discard current commit message?'),
                        QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            if d != QMessageBox.StandardButton.Ok:
                return
        message = action.data()
        self.setMessage(message)
        self.msgte.setFocus()

    def setMessage(self, msg: str, modified: bool = False) -> None:
        self.msgte.setText(msg)
        self.msgte.moveCursorToEnd()
        self.msgte.setModified(modified)

    def canExit(self) -> bool:
        if not self.stwidget.canExit():
            return False
        return self._cmdsession.isFinished()

    def loadSettings(self, s: QSettings, prefix: str) -> None:
        'Load history, etc, from QSettings instance'
        repoid = hglib.shortrepoid(self.repo)
        lpref = prefix + '/commit/' # local settings (splitter, etc)
        gpref = 'commit/'           # global settings (history, etc)
        # message history is stored in unicode
        self.split.restoreState(qtlib.readByteArray(s, lpref + 'split'))
        self.msgte.loadSettings(s, lpref+'msgte')
        self.stwidget.loadSettings(s, lpref+'status')
        self.msghistory = qtlib.readStringList(s, gpref + 'history-' + repoid)
        self.msghistory = [m for m in self.msghistory if m]
        self.updateRecentMessages()
        self.userhist = qtlib.readStringList(s, gpref + 'userhist')
        self.userhist = [u for u in self.userhist if u]
        try:
            curmsg = self.repo.vfs(b'cur-message.txt').read()
            self.setMessage(hglib.tounicode(curmsg))
        except OSError:
            pass
        try:
            curmsg = self.repo.vfs(b'last-message.txt').read()
            if curmsg:
                self.addMessageToHistory(hglib.tounicode(curmsg))
        except OSError:
            pass

    def saveSettings(self, s: QSettings, prefix: str) -> None:
        'Save history, etc, in QSettings instance'
        try:
            repoid = hglib.shortrepoid(self.repo)
            lpref = prefix + '/commit/'
            gpref = 'commit/'
            s.setValue(lpref+'split', self.split.saveState())
            self.msgte.saveSettings(s, lpref+'msgte')
            self.stwidget.saveSettings(s, lpref+'status')
            s.setValue(gpref+'history-'+repoid, self.msghistory)
            s.setValue(gpref+'userhist', self.userhist)
            msg = self.getMessage(True)
            self.repo.vfs(b'cur-message.txt', b'w').write(msg)
        except OSError:
            pass

    def addMessageToHistory(self, umsg: str) -> None:
        if umsg in self.msghistory:
            self.msghistory.remove(umsg)
        self.msghistory.insert(0, umsg)
        self.msghistory = self.msghistory[:10]
        self.updateRecentMessages()

    def addUsernameToHistory(self, user: str) -> None:
        if user in self.userhist:
            self.userhist.remove(user)
        self.userhist.insert(0, user)
        self.userhist = self.userhist[:10]

    def commit(self, amend: bool = False) -> Optional[bool]:
        # TODO: drop the bool return?
        repo = self.repo
        try:
            msg = self.getMessage(False)
        except UnicodeEncodeError:
            res = qtlib.CustomPrompt(
                    _('Message Translation Failure'),
                    _('Unable to translate message to local encoding.\n'
                      'Consider setting HGENCODING environment variable.\n\n'
                      'Replace untranslatable characters with "?"?\n'), self,
                     (_('&Replace'), _('Cancel')), 0, 1, []).run()
            if res == 0:
                msg = self.getMessage(True)
                msg = str(msg)  # drop round-trip utf8 data
                self.msgte.setText(hglib.tounicode(msg))
            self.msgte.setFocus()
            return

        if not msg:
            qtlib.WarningMsgBox(_('Nothing Committed'),
                                _('Please enter commit message'),
                                parent=self)
            self.msgte.setFocus()
            return

        linkmandatory = self._repoagent.configBool('tortoisehg',
                                                   'issue.linkmandatory')
        if linkmandatory:
            issueregex = None
            s = self.repo.ui.config(b'tortoisehg', b'issue.regex')
            if s:
                try:
                    issueregex = re.compile(s)
                except re.error:
                    pass
            if issueregex:
                m = issueregex.search(msg)
                if not m:
                    qtlib.WarningMsgBox(_('Nothing Committed'),
                                        _('No issue link was found in the '
                                          'commit message.  The commit message '
                                          'should contain an issue link.  '
                                          "Configure this in the 'Issue "
                                          "Tracking' section of the settings."),
                                        parent=self)
                    self.msgte.setFocus()
                    return False

        commandlines = []

        brcmd = []
        newbranch = False
        if self.branchop is None:
            newbranch = repo[None].branch() != repo[b'.'].branch()
        elif self.branchop == False:
            brcmd = ['--close-branch']
        else:
            commandlines, newbranch = self.getBranchCommandLine()
            if commandlines is None:
                return
        partials = []
        if len(repo[None].parents()) > 1:
            merge = True
            self.files = []
        else:
            merge = False
            files = self.stwidget.getChecked('MAR?!IS')
            # make list of files with partial change selections
            for fname, c in self.stwidget.partials.items():
                if c.excludecount > 0 and c.excludecount < len(c.hunks):
                    partials.append(fname)
            self.files = set(files + partials)
            self.files.update(self.stwidget.getCheckedAmends())
        canemptycommit = bool(brcmd or newbranch or amend)
        if not (self.files or canemptycommit or merge):
            qtlib.WarningMsgBox(_('No files checked'),
                                _('No modified files checkmarked for commit'),
                                parent=self)
            self.stwidget.tv.setFocus()
            return

        # username will be prompted as necessary by hg if ui.askusername
        user = self.opts.get('user')
        if not amend and not self._repoagent.configBool('ui', 'askusername'):
            # TODO: no need to specify --user if it was read from ui
            user = qtlib.getCurrentUsername(self, self.repo, self.opts)
            if not user:
                return
            self.addUsernameToHistory(user)

        checkedUnknowns = self.stwidget.getChecked('?I')
        if checkedUnknowns and b'largefiles' in repo.extensions():
            result = lfprompt.promptForLfiles(self, repo.ui, repo,
                                              checkedUnknowns)
            if not result:
                return
            checkedUnknowns, lfiles = result
            if lfiles:
                cmd = ['add', '--large', '--']
                cmd.extend(pycompat.maplist(hglib.escapepath,
                                            pycompat.maplist(hglib.tounicode,
                                                             lfiles)))
                commandlines.append(cmd)
        if checkedUnknowns:
            confirm = self._repoagent.configBool('tortoisehg',
                                                 'confirmaddfiles')
            if confirm:
                res = qtlib.CustomPrompt(
                        _('Confirm Add'),
                        _('Add selected untracked files?'), self,
                        (_('&Add'), _('Cancel')), 0, 1,
                        checkedUnknowns).run()
            else:
                res = 0
            if res == 0:
                cmd = ['add', '--']
                cmd.extend(pycompat.maplist(hglib.escapepath,
                                            pycompat.maplist(hglib.tounicode,
                                                             checkedUnknowns)))
                commandlines.append(cmd)
            else:
                return
        checkedMissing = self.stwidget.getChecked('!')
        if checkedMissing:
            confirm = self._repoagent.configBool('tortoisehg',
                                                 'confirmdeletefiles')
            if confirm:
                res = qtlib.CustomPrompt(
                        _('Confirm Remove'),
                        _('Remove selected deleted files?'), self,
                        (_('&Remove'), _('Cancel')), 0, 1,
                        checkedMissing).run()
            else:
                res = 0
            if res == 0:
                cmd = ['remove', '--']
                cmd.extend(pycompat.maplist(hglib.escapepath,
                                            pycompat.maplist(hglib.tounicode,
                                                             checkedMissing)))
                commandlines.append(cmd)
            else:
                return
        cmdline = ['commit', '--verbose', '--message=' + hglib.tounicode(msg)]
        if user:
            cmdline.extend(['--user', user])
        date = self.opts.get('date')
        if date:
            cmdline += ['--date', date]
        cmdline += brcmd

        if partials:
            # write patch for partial change selections to temp file
            fd, tmpname = tempfile.mkstemp(prefix='thg-patch-')
            fp = os.fdopen(fd, 'wb')
            for fname in partials:
                changes = self.stwidget.partials[fname]
                changes.write(fp)
                for chunk in changes.hunks:
                    if not chunk.excluded:
                        chunk.write(fp)
            fp.close()

            cmdline.append('--partials')
            cmdline.append(tmpname)
            assert not amend

        if self.opts.get('recurseinsubrepos'):
            cmdline.append('--subrepos')

        if amend:
            cmdline.append('--amend')

        if not self.files and canemptycommit and not merge:
            # make sure to commit empty changeset by excluding all files
            cmdline.extend(['--exclude', repo.root])
            assert not self.stwidget.partials, self.stwidget.partials

        cmdline.append('--')
        cmdline.extend(pycompat.maplist(hglib.escapepath,
                                        pycompat.maplist(hglib.tounicode,
                                                         self.files)))
        if len(repo[None].parents()) == 1:
            opt = self.opts.get('autoinc', '')
            assert isinstance(opt, str)  # help pytype
            for fname in opt.split(','):
                fname = fname.strip()
                if fname:
                    cmdline.append('glob:%s' % fname)
        commandlines.append(cmdline)

        if self.opts.get('pushafter'):
            cmd = ['push', self.opts['pushafter']]
            if newbranch:
                cmd.append('--new-branch')
            commandlines.append(cmd)

        self._runCommand(amend and 'amend' or 'commit', commandlines)

    def stop(self):
        self._cmdsession.abort()

    def _runCommand(self, action: str, cmdlines: CmdLines) -> None:
        self.currentAction = action
        self.progress.emit(*cmdui.startProgress(_topicmap[action], ''))
        self.commitButtonEnable.emit(False)
        ucmdlines = [pycompat.maplist(hglib.tounicode, xs) for xs in cmdlines]
        self._cmdsession = sess = self._repoagent.runCommandSequence(ucmdlines,
                                                                     self)
        sess.commandFinished.connect(self.commandFinished)

    def commandFinished(self, ret: int) -> None:
        self.progress.emit(*cmdui.stopProgress(_topicmap[self.currentAction]))
        self.stopAction.setEnabled(False)
        self.commitButtonEnable.emit(True)
        if ret == 0:
            self.stwidget.partials = {}
            if self.currentAction == 'rollback':
                shlib.shell_notify([self.repo.root])
                return
            self.branchop = None
            umsg = self.msgte.text()
            if self.currentAction not in ('qref', 'amend'):
                self.lastCommitMsgs['commit'] = ('', False)
                if self.currentAction == 'commit':
                    # capture last message for BugTraq plugin
                    self.lastmessage = self.getMessage(True)
                if umsg:
                    self.addMessageToHistory(umsg)
                self.setMessage('')
                if self.currentAction == 'commit':
                    shlib.shell_notify(self.files)
                    self.commitComplete.emit()
        elif ret == 1 and self.currentAction in ('amend', 'commit'):
            qtlib.WarningMsgBox(_('Nothing Committed'),
                                _('Nothing changed.'),
                                parent=self)
        else:
            cmdui.errorMessageBox(self._cmdsession, self,
                                  _('Commit', 'window title'))

class DetailsDialog(QDialog):
    'Utility dialog for configuring uncommon settings'
    def __init__(self,
                 repoagent: RepoAgent,
                 opts: CommitOpts,
                 userhistory: List[str],
                 parent: QWidget,
                 mode: str = 'commit') -> None:
        QDialog.__init__(self, parent)
        self.setWindowTitle(_('%s - commit options') % repoagent.displayName())
        self._repoagent = repoagent

        layout = QVBoxLayout()
        self.setLayout(layout)

        hbox = QHBoxLayout()
        self.usercb = QCheckBox(_('Set username:'))

        usercombo = QComboBox()
        usercombo.setEditable(True)
        usercombo.setEnabled(False)

        usercombo.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        self.usercb.toggled.connect(usercombo.setEnabled)
        self.usercb.toggled.connect(lambda s: s and usercombo.setFocus())

        l = []
        v = opts.get('user')
        if v:
            # TODO: should already be Text
            val = hglib.tounicode(v)
            self.usercb.setChecked(True)
            l.append(val)
        try:
            val = hglib.tounicode(self.repo.ui.username())
            l.append(val)
        except error.Abort:
            pass
        for name in userhistory:
            if name not in l:
                l.append(name)
        for name in l:
            usercombo.addItem(name)
        self.usercombo = usercombo

        usersaverepo = QPushButton(_('Save in Repo'))
        usersaverepo.clicked.connect(self.saveInRepo)
        usersaverepo.setEnabled(False)
        self.usercb.toggled.connect(usersaverepo.setEnabled)

        usersaveglobal = QPushButton(_('Save Global'))
        usersaveglobal.clicked.connect(self.saveGlobal)
        usersaveglobal.setEnabled(False)
        self.usercb.toggled.connect(usersaveglobal.setEnabled)

        hbox.addWidget(self.usercb)
        hbox.addWidget(self.usercombo)
        hbox.addWidget(usersaverepo)
        hbox.addWidget(usersaveglobal)
        layout.addLayout(hbox)

        hbox = QHBoxLayout()
        self.datecb = QCheckBox(_('Set Date:'))
        self.datele = QLineEdit()
        self.datele.setEnabled(False)
        self.datecb.toggled.connect(self.datele.setEnabled)
        curdate = QPushButton(_('Update'))
        curdate.setEnabled(False)
        self.datecb.toggled.connect(curdate.setEnabled)
        self.datecb.toggled.connect(lambda s: s and curdate.setFocus())
        curdate.clicked.connect( lambda: self.datele.setText(
                hglib.tounicode(hglib.displaytime(dateutil.makedate()))))
        v = opts.get('date')
        if v:
            self.datele.setText(v)
            self.datecb.setChecked(True)
        else:
            self.datecb.setChecked(False)
            curdate.clicked.emit(True)

        hbox.addWidget(self.datecb)
        hbox.addWidget(self.datele)
        hbox.addWidget(curdate)
        layout.addLayout(hbox)

        hbox = QHBoxLayout()
        self.pushaftercb = QCheckBox(_('Push After Commit:'))
        self.pushafterle = QLineEdit()
        self.pushafterle.setEnabled(False)
        self.pushaftercb.toggled.connect(self.pushafterle.setEnabled)
        self.pushaftercb.toggled.connect(lambda s:
                s and self.pushafterle.setFocus())

        pushaftersave = QPushButton(_('Save in Repo'))
        pushaftersave.clicked.connect(self.savePushAfter)
        pushaftersave.setEnabled(False)
        self.pushaftercb.toggled.connect(pushaftersave.setEnabled)

        if opts.get('pushafter'):
            val = hglib.tounicode(opts['pushafter'])
            self.pushafterle.setText(val)
            self.pushaftercb.setChecked(True)

        hbox.addWidget(self.pushaftercb)
        hbox.addWidget(self.pushafterle)
        hbox.addWidget(pushaftersave)
        layout.addLayout(hbox)

        hbox = QHBoxLayout()
        self.autoinccb = QCheckBox(_('Auto Includes:'))
        self.autoincle = QLineEdit()
        self.autoincle.setEnabled(False)
        self.autoinccb.toggled.connect(self.autoincle.setEnabled)
        self.autoinccb.toggled.connect(lambda s:
                s and self.autoincle.setFocus())

        autoincsave = QPushButton(_('Save in Repo'))
        autoincsave.clicked.connect(self.saveAutoInc)
        autoincsave.setEnabled(False)
        self.autoinccb.toggled.connect(autoincsave.setEnabled)

        if opts.get('autoinc'):
            val = hglib.tounicode(opts['autoinc'])
            self.autoincle.setText(val)
            self.autoinccb.setChecked(True)

        hbox.addWidget(self.autoinccb)
        hbox.addWidget(self.autoincle)
        hbox.addWidget(autoincsave)
        if mode != 'merge':
            #self.autoinccb.setVisible(False)
            layout.addLayout(hbox)

        hbox = QHBoxLayout()
        recursesave = QPushButton(_('Save in Repo'))
        recursesave.clicked.connect(self.saveRecurseInSubrepos)
        self.recursecb = QCheckBox(_('Recurse into subrepositories '
                                     '(--subrepos)'))

        self.recursecb.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        #self.recursecb.toggled.connect(recursesave.setEnabled)

        if opts.get('recurseinsubrepos'):
            self.recursecb.setChecked(True)

        hbox.addWidget(self.recursecb)
        hbox.addWidget(recursesave)
        layout.addLayout(hbox)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        self.bb = bb
        layout.addWidget(bb)

    @property
    def repo(self) -> localrepo.localrepository:
        return self._repoagent.rawRepo()

    def saveInRepo(self):
        fn = os.path.join(self.repo.root, b'.hg', b'hgrc')
        self.saveToPath([fn])

    def saveGlobal(self):
        self.saveToPath(hglib.userrcpath())

    def saveToPath(self, path: List[bytes]) -> None:
        fn, cfg = hgrcutil.loadIniFile(path, self)
        if not hasattr(cfg, 'write'):
            qtlib.WarningMsgBox(_('Unable to save username'),
                   _('Iniparse must be installed.'), parent=self)
            return
        if fn is None:
            return
        try:
            user = hglib.fromunicode(self.usercombo.currentText())
            if user:
                cfg.set(b'ui', b'username', user)
            else:
                try:
                    del cfg[b'ui'][b'username']
                except KeyError:
                    pass
            wconfig.writefile(cfg, fn)
        except OSError as e:
            qtlib.WarningMsgBox(_('Unable to write configuration file'),
                                hglib.exception_str(e), parent=self)

    def savePushAfter(self):
        path = os.path.join(self.repo.root, b'.hg', b'hgrc')
        fn, cfg = hgrcutil.loadIniFile([path], self)
        if not hasattr(cfg, 'write'):
            qtlib.WarningMsgBox(_('Unable to save after commit push'),
                   _('Iniparse must be installed.'), parent=self)
            return
        if fn is None:
            return
        try:
            remote = hglib.fromunicode(self.pushafterle.text())
            if remote:
                cfg.set(b'tortoisehg', b'cipushafter', remote)
            else:
                try:
                    del cfg[b'tortoisehg'][b'cipushafter']
                except KeyError:
                    pass
            wconfig.writefile(cfg, fn)
        except OSError as e:
            qtlib.WarningMsgBox(_('Unable to write configuration file'),
                                hglib.exception_str(e), parent=self)

    def saveAutoInc(self):
        path = os.path.join(self.repo.root, b'.hg', b'hgrc')
        fn, cfg = hgrcutil.loadIniFile([path], self)
        if not hasattr(cfg, 'write'):
            qtlib.WarningMsgBox(_('Unable to save auto include list'),
                   _('Iniparse must be installed.'), parent=self)
            return
        if fn is None:
            return
        try:
            list = hglib.fromunicode(self.autoincle.text())
            if list:
                cfg.set(b'tortoisehg', b'autoinc', list)
            else:
                try:
                    del cfg[b'tortoisehg'][b'autoinc']
                except KeyError:
                    pass
            wconfig.writefile(cfg, fn)
        except OSError as e:
            qtlib.WarningMsgBox(_('Unable to write configuration file'),
                                hglib.exception_str(e), parent=self)

    def saveRecurseInSubrepos(self):
        path = os.path.join(self.repo.root, b'.hg', b'hgrc')
        fn, cfg = hgrcutil.loadIniFile([path], self)
        if not hasattr(cfg, 'write'):
            qtlib.WarningMsgBox(_('Unable to save recurse in subrepos.'),
                   _('Iniparse must be installed.'), parent=self)
            return
        if fn is None:
            return
        try:
            state = self.recursecb.isChecked()
            if state:
                cfg.set(b'tortoisehg', b'recurseinsubrepos', b'True')
            else:
                try:
                    del cfg[b'tortoisehg'][b'recurseinsubrepos']
                except KeyError:
                    pass
            wconfig.writefile(cfg, fn)
        except OSError as e:
            qtlib.WarningMsgBox(_('Unable to write configuration file'),
                                hglib.exception_str(e), parent=self)

    def accept(self):
        outopts: Dict[str, str] = {}
        if self.datecb.isChecked():
            udate = self.datele.text()
            date = hglib.fromunicode(udate)
            try:
                dateutil.parsedate(date)
            except error.ParseError as e:
                err = hglib.exception_str(e, show_hint=True)
                qtlib.WarningMsgBox(_('Invalid date format'), err, parent=self)
                return
            outopts['date'] = udate
        else:
            outopts['date'] = ''

        if self.usercb.isChecked():
            user = self.usercombo.currentText()
        else:
            user = ''
        outopts['user'] = user
        if not user:
            try:
                self.repo.ui.username()
            except error.Abort as e:
                err = hglib.exception_str(e, show_hint=True)
                qtlib.WarningMsgBox(_('No username configured'),
                                    err, parent=self)
                return

        if self.pushaftercb.isChecked():
            remote = self.pushafterle.text()
            outopts['pushafter'] = remote
        else:
            outopts['pushafter'] = ''

        if self.autoinccb.isChecked():
            outopts['autoinc'] = self.autoincle.text()
        else:
            outopts['autoinc'] = ''

        if self.recursecb.isChecked():
            outopts['recurseinsubrepos'] = 'true'
        else:
            outopts['recurseinsubrepos'] = ''

        self.outopts = outopts
        QDialog.accept(self)


class CommitDialog(QDialog):
    'Standalone commit tool, a wrapper for CommitWidget'

    def __init__(self,
                 repoagent: RepoAgent,
                 pats: List[bytes],
                 opts: Dict[str, bytes],
                 parent: Optional[QObject] = None) -> None:
        QDialog.__init__(self, parent)
        self.setWindowFlags(Qt.WindowType.Window)
        self.setWindowIcon(qtlib.geticon('hg-commit'))
        self._repoagent = repoagent
        self.pats = pats
        self.opts = opts

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        toplayout = QVBoxLayout()
        toplayout.setContentsMargins(5, 5, 5, 0)
        layout.addLayout(toplayout)

        commit = CommitWidget(repoagent, pats, opts, self, rev='.')
        toplayout.addWidget(commit, 1)

        self.statusbar = cmdui.ThgStatusBar(self)
        commit.showMessage.connect(self.statusbar.showMessage)
        commit.progress.connect(self.statusbar.progress)
        commit.linkActivated.connect(self.linkActivated)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close|QDialogButtonBox.StandardButton.Discard)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.StandardButton.Discard).setText('Undo')
        bb.button(QDialogButtonBox.StandardButton.Discard).clicked.connect(commit.rollback)
        bb.button(QDialogButtonBox.StandardButton.Close).setDefault(False)
        bb.button(QDialogButtonBox.StandardButton.Discard).setDefault(False)
        self.commitButton = commit.commitSetupButton()
        bb.addButton(self.commitButton, QDialogButtonBox.ButtonRole.AcceptRole)

        self.bb = bb

        toplayout.addWidget(self.bb)
        layout.addWidget(self.statusbar)

        self._subdialogs = qtlib.DialogKeeper(CommitDialog._createSubDialog,
                                              parent=self)

        s = QSettings()
        self.restoreGeometry(qtlib.readByteArray(s, 'commit/geom'))
        commit.loadSettings(s, 'committool')
        repoagent.repositoryChanged.connect(self.updateUndo)
        commit.commitComplete.connect(self.postcommit)

        self.setWindowTitle(_('%s - commit') % repoagent.displayName())
        self.commit = commit
        self.commit.reload()
        self.updateUndo()
        self.commit.msgte.setFocus()
        qtlib.newshortcutsforstdkey(QKeySequence.StandardKey.Refresh, self, self.refresh)

    def linkActivated(self, link: str) -> None:
        if link.startswith('repo:'):
            self._subdialogs.open(link[len('repo:'):])

    def _createSubDialog(self, uroot: str) -> CommitDialog:
        repoagent = self._repoagent.subRepoAgent(uroot)
        return CommitDialog(repoagent, [], {}, parent=self)

    @pyqtSlot()
    def updateUndo(self) -> None:
        undomsg = self.commit.canUndo()
        if undomsg:
            self.bb.button(QDialogButtonBox.StandardButton.Discard).setEnabled(True)
            self.bb.button(QDialogButtonBox.StandardButton.Discard).setToolTip(undomsg)
        else:
            self.bb.button(QDialogButtonBox.StandardButton.Discard).setEnabled(False)
            self.bb.button(QDialogButtonBox.StandardButton.Discard).setToolTip('')

    def refresh(self) -> None:
        self.updateUndo()
        self.commit.reload()

    def postcommit(self) -> None:
        repo = self.commit.stwidget.repo
        if repo.ui.configbool(b'tortoisehg', b'closeci'):
            if self.commit.canExit():
                self.reject()
            else:
                self.commit.stwidget.refthread.wait()
                QTimer.singleShot(0, self.reject)

    def promptExit(self) -> bool:
        exit = self.commit.canExit()
        if not exit:
            exit = qtlib.QuestionMsgBox(_('TortoiseHg Commit'),
                _('Are you sure that you want to cancel the commit operation?'),
                parent=self)
        if exit:
            s = QSettings()
            s.setValue('commit/geom', self.saveGeometry())
            self.commit.saveSettings(s, 'committool')
        return exit

    def accept(self) -> None:
        self.commit.commit()

    def reject(self) -> None:
        if self.promptExit():
            QDialog.reject(self)
