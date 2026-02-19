# workbench.py - main TortoiseHg Window
#
# Copyright (C) 2007-2010 Logilab. All rights reserved.
# Copyright (C) 2026 Peter Demcak <majster64@gmail.com> (dark theme)
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

"""
Main Qt4 application for TortoiseHg
"""

from __future__ import annotations

import os
import subprocess
import sys

from .qtcore import (
    QPoint,
    QRect,
    QSettings,
    Qt,
    pyqtSlot,
)
from .qtgui import (
    QAction,
    QActionGroup,
    QApplication,
    QColor,
    QComboBox,
    QFileDialog,
    QKeySequence,
    QMainWindow,
    QMenu,
    QMenuBar,
    QPainter,
    QPalette,
    QPen,
    QProxyStyle,
    QShortcut,
    QStyle,
    QStyleOptionViewItem,
    QSizePolicy,
    QToolBar,
    QWidget
)

from mercurial import (
    pycompat,
)

from ..util import (
    hglib,
    paths,
)
from ..util.i18n import _
from . import (
    cmdcore,
    cmdui,
    mq,
    qtlib,
    repotab,
    serve,
    shortcutsettings,
)
from .docklog import LogDockWidget
from .reporegistry import RepoRegistryView
from .settings import SettingsDialog

from .theme import THEME
import sys

def apply_dark_palette(app):
    pal = QPalette()

    # Basic colors
    pal.setColor(qtlib.QtPaletteRole.Window, THEME.background)
    pal.setColor(qtlib.QtPaletteRole.Base, THEME.background)
    pal.setColor(qtlib.QtPaletteRole.AlternateBase, THEME.backgroundLighter)

    pal.setColor(qtlib.QtPaletteRole.Text, THEME.text)
    pal.setColor(qtlib.QtPaletteRole.WindowText, THEME.text)

    pal.setColor(qtlib.QtPaletteRole.Mid, THEME.backgroundLighter)
    pal.setColor(qtlib.QtPaletteRole.Dark, THEME.background)
    pal.setColor(qtlib.QtPaletteRole.Light, THEME.backgroundLighter)
    
    pal.setColor(qtlib.QtPaletteRole.Highlight, THEME.selection_background)
    pal.setColor(qtlib.QtPaletteRole.HighlightedText, THEME.selection_text)

    # Disabled text
    pal.setColor(qtlib.QtPaletteGroup.Disabled, qtlib.QtPaletteRole.Text, THEME.text_disabled)

    app.setPalette(pal)

def build_dark_stylesheet(THEME):
    c = THEME

    return f"""
    /* === Base widgets === */
    QWidget {{
        background-color: {c.control_background.name()};
        color: {c.control_text.name()};
    }}

    QMainWindow, QDialog {{
        background-color: {c.background.name()};
    }}

    /* === Text inputs & views === */
    QTextEdit, QPlainTextEdit, QLineEdit,
    QListView, QTreeView, QTableView {{
        background-color: {c.background.name()};
        color: {c.control_text.name()};
        border: 1px solid {c.control_border.name()};
        selection-background-color: {c.selection_background.name()};
        selection-color: {c.selection_text.name()};
    }}

    QTreeView, QListView, QTableView {{
        alternate-background-color: {c.backgroundLighter.name()};
    }}

    /* === Buttons === */
    QPushButton {{
        background-color: {c.background.name()};
        color: {c.control_text.name()};
        border: 1px solid {c.control_border.name()};
        padding: 5px 10px;
        border-radius: 2px;
    }}

    QPushButton:hover {{
        background-color: {c.control_hover.name()};
    }}

    QPushButton:pressed {{
        background-color: {c.control_pressed.name()};
    }}

    /* === Menus === */
    QMenuBar, QMenu {{
        background-color: {c.control_background.name()};
        color: {c.control_text.name()};
        border: 1px solid {c.control_border.name()};
    }}

    QMenu::item:selected {{
        background-color: {c.control_pressed.name()};
    }}

    /* === Toolbars & statusbar === */
    QToolBar {{
        background-color: {c.control_background.name()};
        border: 1px solid {c.control_border.name()};
    }}

    QStatusBar {{
        background-color: {c.background.name()};
        color: {c.control_text.name()};
        border-top: 1px solid #2d2d2d;
    }}

    /* === Tabs === */
    QTabWidget::pane {{
        border: 1px solid {c.control_border.name()};
        background-color: {c.background.name()};
    }}

    QTabBar::tab {{
        background-color: {c.control_background.name()};
        color: {c.control_text.name()};
        border: 1px solid {c.control_border.name()};
        padding: 5px;
    }}

    QTabBar::tab:selected {{
        background-color: {c.control_border.name()};
        color: {c.text_selection.name()};
    }}

    /* === ScrollBars === */
    QScrollBar {{
        background: {THEME.control_background.name()};
        border: 1px solid {THEME.control_border.name()};
    }}

    QScrollBar:vertical {{
        width: 18px;
    }}

    QScrollBar:horizontal {{
        height: 18px;
    }}

    /* HANDLE */
    QScrollBar::handle {{
        background: {THEME.backgroundLighter.name()};
        border: 1px solid {THEME.control_border.name()};
        border-radius: 6px;
        margin: 0px; /* critical: avoid dead zones */
    }}

    QScrollBar::handle:vertical {{
        min-height: 34px;
    }}

    QScrollBar::handle:horizontal {{
        min-width: 34px;
    }}

    /* HOVER */
    QScrollBar::handle:hover {{
        background: {THEME.control_hover.name()};
    }}

    /* PRESSED / DRAGGING */
    QScrollBar::handle:pressed,
    QScrollBar::handle:active,
    QScrollBar::handle:active:pressed,
    QScrollBar::handle:vertical:pressed,
    QScrollBar::handle:vertical:active,
    QScrollBar::handle:horizontal:pressed,
    QScrollBar::handle:horizontal:active {{
        background: {THEME.control_pressed.name()};
    }}

    /* REMOVE INVISIBLE HIT AREAS */
    QScrollBar::add-line,
    QScrollBar::sub-line {{
        background: none;
        border: none;
        width: 0px;
        height: 0px;
    }}

    /* REMOVE PAGE AREAS */
    QScrollBar::add-page,
    QScrollBar::sub-page {{
        background: none;
    }}


    /* === Header views === */
    QHeaderView {{
        background-color: {c.background.name()};
    }}

    QHeaderView::section {{
        background-color: {c.backgroundLighter.name()};
        color: {c.header_text.name()};
        padding: 4px 6px;
        border: 1px solid {c.control_border.name()};
        font-size: 9pt;
    }}

    /* === Tooltips === */
    QToolTip {{
        background-color: {c.backgroundLighter.name()};
        color: {c.text.name()};
        border: 1px solid {c.control_border.name()};
    }}
    """

class DarkItemViewCheckStyle(QProxyStyle):
    def drawPrimitive(self, element, option, painter, widget=None):
        if THEME.enabled and element == QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck:
            painter.save()
            painter.setRenderHint(qtlib.QtPainterRenderHint.Antialiasing, False)

            # Use a reliable rect for item-view checkbox
            style = self.baseStyle()
            rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator, option, widget)
            rect = rect.adjusted(2, 2, -2, -2)

            state = option.state
            is_checked = bool(state & QStyle.StateFlag.State_On)
            is_partial = bool(state & QStyle.StateFlag.State_NoChange)
            is_enabled = bool(state & QStyle.StateFlag.State_Enabled)

            # Colors
            border_color = THEME.text if is_enabled else THEME.text_disabled
            mark_color = border_color

            # Border (square)
            pen = QPen(border_color)
            pen.setWidth(1)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

            # Checkmark / tristate
            pen.setWidth(2)
            painter.setPen(pen)

            # Shift checkmark by +1px right and +1px down
            dx = 1
            dy = 1

             # Enable antialiasing
            painter.setRenderHint(qtlib.QtPainterRenderHint.Antialiasing, True)

            if is_checked:
                # Classic checkmark
                p1 = QPoint(rect.left() + 2 + dx, rect.center().y() + 1 + dy)
                p2 = QPoint(rect.center().x() - 1 + dx, rect.bottom() - 2 + dy)
                p3 = QPoint(rect.right() - 2 + dx, rect.top() + 2 + dy)
                painter.drawLine(p1, p2)
                painter.drawLine(p2, p3)
                
            elif is_partial:
                # Small centered square
                size = 7
                cx = rect.center().x()
                cy = rect.center().y()
                half = size // 2
                square = QRect(cx - half + dx, cy - half + dy, size, size)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(mark_color)
                painter.drawRect(square)
                painter.setRenderHint(qtlib.QtPainterRenderHint.Antialiasing, False)

            painter.restore()
            return  # stop Qt from drawing anything else

        super().drawPrimitive(element, option, painter, widget)

    def subElementRect(self, element, option, widget=None):
        rect = super().subElementRect(element, option, widget)
        if THEME.enabled and element == QStyle.SubElement.SE_ItemViewItemCheckIndicator:
            # Fusion style returns a smaller hit rect than Windows style;
            # expand it back so clicks register anywhere on the visible indicator
            rect = rect.adjusted(-1, -1, 1, 1)
            pass
        return rect

class Workbench(QMainWindow):
    """hg repository viewer/browser application"""

    def __init__(self, ui, config, actionregistry, repomanager):
        QMainWindow.__init__(self)
        self.ui = ui
        self._config = config
        self._actionregistry = actionregistry
        self._repomanager = repomanager
        self._repomanager.configChanged.connect(self._setupUrlComboIfCurrent)

        self.setupUi()

        if THEME.enabled:
           # Apply dark mode stylesheet
           app = QApplication.instance()
           apply_dark_palette(app)

           base = app.setStyle("Fusion") # Needed for scrollbars
           app.setStyle(DarkItemViewCheckStyle(base)) # Custom checkbox style for HgFileListView
           app.setStyleSheet(build_dark_stylesheet(THEME))

        repomanager.busyChanged.connect(self._onBusyChanged)
        repomanager.progressReceived.connect(self.statusbar.setRepoProgress)

        self.reporegistry = rr = RepoRegistryView(repomanager, self)
        rr.setObjectName('RepoRegistryView')
        rr.showMessage.connect(self.statusbar.showMessage)
        rr.openRepo.connect(self.openRepo)
        rr.removeRepo.connect(self.repoTabsWidget.closeRepo)
        rr.cloneRepoRequested.connect(self.cloneRepository)
        rr.progressReceived.connect(self.statusbar.progress)
        self._repomanager.repositoryChanged.connect(rr.scanRepo)
        rr.hide()
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, rr)

        self.mqpatches = p = mq.MQPatchesWidget(actionregistry, self)
        p.setObjectName('MQPatchesWidget')
        p.patchSelected.connect(self.gotorev)
        p.hide()
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, p)

        cmdagent = cmdcore.CmdAgent(ui, self)
        self._console = LogDockWidget(repomanager, cmdagent, self)
        self._console.setObjectName('Log')
        self._console.hide()
        self._console.visibilityChanged.connect(self._updateShowConsoleAction)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._console)

        self._setupActions()

        self.restoreSettings()
        self.repoTabChanged()
        self.setAcceptDrops(True)
        self.setIconSize(qtlib.toolBarIconSize())
        if os.name == 'nt':
            # Allow CTRL+Q to close Workbench on Windows
            QShortcut(QKeySequence('CTRL+Q'), self, self.close)
        if sys.platform == 'darwin':
            self.dockMenu = QMenu(self)
            self.dockMenu.addAction(_('New &Workbench'),
                                    self.newWorkbench)
            self.dockMenu.addAction(_('&New Repository...'),
                                    self.newRepository)
            self.dockMenu.addAction(_('Clon&e Repository...'),
                                    self.cloneRepository)
            self.dockMenu.addAction(_('&Open Repository...'),
                                    self.openRepository)
            self.dockMenu.setAsDockMenu()

        self._dialogs = qtlib.DialogKeeper(
            lambda self, dlgmeth: dlgmeth(self), parent=self)

    def setupUi(self):
        desktopgeom = QApplication.primaryScreen().availableGeometry()
        self.resize(desktopgeom.size() * 0.8)

        self.repoTabsWidget = tw = repotab.RepoTabWidget(
            self._config, self._actionregistry, self._repomanager, self)
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        sp.setHorizontalStretch(1)
        sp.setVerticalStretch(1)
        sp.setHeightForWidth(tw.sizePolicy().hasHeightForWidth())
        tw.setSizePolicy(sp)
        tw.currentTabChanged.connect(self.repoTabChanged)
        tw.currentRepoChanged.connect(self._onCurrentRepoChanged)
        tw.currentTaskTabChanged.connect(self._updateTaskViewMenu)
        tw.currentTitleChanged.connect(self._updateWindowTitle)
        tw.historyChanged.connect(self._updateHistoryActions)
        tw.makeLogVisible.connect(self._setConsoleVisible)
        tw.taskTabVisibilityChanged.connect(self._updateTaskTabVisibilityAction)
        tw.toolbarVisibilityChanged.connect(self._updateToolBarActions)

        self.setCentralWidget(tw)
        self.statusbar = cmdui.ThgStatusBar(self)
        self.setStatusBar(self.statusbar)

        tw.progressReceived.connect(self.statusbar.setRepoProgress)
        tw.showMessageSignal.connect(self.statusbar.showMessage)

    def _setupActions(self):
        """Setup actions, menus and toolbars"""
        self.menubar = QMenuBar(self)
        self.setMenuBar(self.menubar)

        self.menuFile = self.menubar.addMenu(_("&File"))
        self.menuView = self.menubar.addMenu(_("&View"))
        self.menuRepository = self.menubar.addMenu(_("&Repository"))
        self.menuHelp = self.menubar.addMenu(_("&Help"))

        self.edittbar = QToolBar(_("&Edit Toolbar"), objectName='edittbar')
        self.addToolBar(self.edittbar)
        self.docktbar = QToolBar(_("&Dock Toolbar"), objectName='docktbar')
        self.addToolBar(self.docktbar)
        self.tasktbar = QToolBar(_('&Task Toolbar'), objectName='taskbar')
        self.addToolBar(self.tasktbar)
        self.customtbar = QToolBar(_('&Custom Toolbar'), objectName='custombar')
        self.addToolBar(self.customtbar)
        self.synctbar = QToolBar(_('S&ync Toolbar'), objectName='synctbar')
        self.addToolBar(self.synctbar)

        # availability map of actions; applied by _updateMenu()
        self._actionavails = {'repoopen': []}
        self._actionvisibles = {'repoopen': []}

        newaction = self._addNewAction
        newnamed = self._addNewNamedAction
        newseparator = self._addNewSeparator

        newnamed('Workbench.newWorkbench', self.newWorkbench,
                 menu='file', icon='hg-log')
        newseparator(menu='file')
        newnamed('Workbench.newRepository', self.newRepository,
                 menu='file', icon='hg-init')
        newnamed('Workbench.cloneRepository', self.cloneRepository,
                 menu='file', icon='hg-clone')
        newseparator(menu='file')
        newnamed('Workbench.openRepository', self.openRepository, menu='file')
        newnamed('Workbench.closeRepository', self.closeCurrentRepoTab,
                 enabled='repoopen', menu='file')
        newseparator(menu='file')
        self.menuFile.addActions(self.repoTabsWidget.tabSwitchActions())
        newseparator(menu='file')
        newnamed('Workbench.openSettings', self.editSettings,
                 icon='thg-userconfig', menu='file')
        newnamed('Workbench.openShortcutSettings',
                 self._openShortcutSettingsDialog, menu='file')
        newseparator(menu='file')
        newnamed('Workbench.quit', self.close, menu='file')

        a = self.reporegistry.toggleViewAction()
        a.setIcon(qtlib.geticon('thg-reporegistry'))
        self._actionregistry.registerAction('Workbench.showRepoRegistry', a)
        self.docktbar.addAction(a)
        self.menuView.addAction(a)

        a = self.mqpatches.toggleViewAction()
        a.setIcon(qtlib.geticon('thg-mq'))
        self._actionregistry.registerAction('Workbench.showPatchQueue', a)
        self.docktbar.addAction(a)
        self.menuView.addAction(a)

        self._actionShowConsole = a = QAction(self)
        a.setCheckable(True)
        a.setIcon(qtlib.geticon('thg-console'))
        a.triggered.connect(self._setConsoleVisible)
        self._actionregistry.registerAction('Workbench.showConsole', a)
        self.docktbar.addAction(a)
        self.menuView.addAction(a)

        self._actionDockedConsole = a = QAction(self)
        a.setText(_('Place Console in Doc&k Area'))
        a.setCheckable(True)
        a.setChecked(True)
        a.triggered.connect(self._updateDockedConsoleMode)

        newseparator(menu='view')
        menu = self.menuView.addMenu(_('R&epository Registry Options'))
        menu.addActions(self.reporegistry.settingActions())

        newseparator(menu='view')
        newnamed('RepoView.setHistoryColumns', self._setHistoryColumns,
                 enabled='repoopen', menu='view')
        self.actionSaveRepos = \
        newaction(_("Save Open Repositories on E&xit"), checkable=True,
                  menu='view')
        self.actionSaveLastSyncPaths = \
        newaction(_("Sa&ve Current Sync Paths on Exit"), checkable=True,
                  menu='view')
        newseparator(menu='view')

        a = newaction(_('Show Tas&k Tab'), shortcut='Alt+0', checkable=True,
                      enabled='repoopen', menu='view')
        a.triggered.connect(self._setRepoTaskTabVisible)
        self.actionTaskTabVisible = a

        self.actionGroupTaskView = QActionGroup(self)
        self.actionGroupTaskView.triggered.connect(self._onSwitchRepoTaskTab)
        def addtaskview(icon, label, name):
            a = newaction(label, icon=None, checkable=True, data=name,
                          enabled='repoopen', menu='view')
            a.setIcon(qtlib.geticon(icon))
            self.actionGroupTaskView.addAction(a)
            self.tasktbar.addAction(a)
            return a

        # note that 'grep' and 'search' are equivalent
        taskdefs = {
            'commit': ('hg-commit', _('&Commit')),
            'log': ('hg-log', _("Revision &Details")),
            'grep': ('hg-grep', _('&Search')),
            'sync': ('thg-sync', _('S&ynchronize')),
            # 'console' is toggled by "Show Console" action
        }
        tasklist = self._config.configStringList(
            'tortoisehg', 'workbench.task-toolbar')
        if tasklist == []:
            tasklist = ['log', 'commit', 'grep', '|', 'sync']

        for taskname in tasklist:
            taskname = taskname.strip()
            taskinfo = taskdefs.get(taskname, None)
            if taskinfo is None:
                newseparator(toolbar='task')
                continue
            addtaskview(taskinfo[0], taskinfo[1], taskname)

        newseparator(menu='view')

        newnamed('Workbench.refresh', self.refresh, icon='view-refresh',
                 enabled='repoopen', menu='view', toolbar='edit',
                 tooltip=_('Refresh current repository'))
        newnamed('Workbench.refreshTaskTabs', self._repofwd('reloadTaskTab'),
                 enabled='repoopen',
                 tooltip=_('Refresh only the current task tab'),
                 menu='view')
        newnamed('RepoView.loadAllRevisions', self.loadall,
                 enabled='repoopen', menu='view',
                 tooltip=_('Load all revisions into graph'))

        self.actionAbort = newnamed('Workbench.abort', self._abortCommands,
                                    icon='process-stop',
                                    toolbar='edit',
                                    tooltip=_('Stop current operation'))
        self.actionAbort.setEnabled(False)

        newseparator(toolbar='edit')
        newnamed('RepoView.goToWorkingParent', self._repofwd('gotoParent'),
                 icon='go-home', tooltip=_('Go to current revision'),
                 enabled='repoopen', toolbar='edit')
        newnamed('RepoView.goToRevision', self._gotorev, icon='go-to-rev',
                 tooltip=_('Go to a specific revision'),
                 enabled='repoopen', menu='view', toolbar='edit')

        self.actionBack = newnamed('RepoView.goBack', self._repofwd('back'),
                                   icon='go-previous',
                                   enabled=False, toolbar='edit')
        self.actionForward = newnamed('RepoView.goForward',
                                      self._repofwd('forward'), icon='go-next',
                                      enabled=False, toolbar='edit')
        newseparator(toolbar='edit', menu='View')

        self.filtertbaction = newnamed('RepoView.showFilterBar',
                                       self._repotogglefwd('toggleFilterBar'),
                                       icon='view-filter', enabled='repoopen',
                                       toolbar='edit', menu='View',
                                       checkable=True,
                                       tooltip=_('Filter graph with revision '
                                                 'sets or branches'))

        menu = QMenu(_('&Workbench Toolbars'), self)
        menu.addAction(self.edittbar.toggleViewAction())
        menu.addAction(self.docktbar.toggleViewAction())
        menu.addAction(self.tasktbar.toggleViewAction())
        menu.addAction(self.synctbar.toggleViewAction())
        menu.addAction(self.customtbar.toggleViewAction())
        self.menuView.addMenu(menu)

        newseparator(toolbar='edit')
        menuSync = self.menuRepository.addMenu(_('S&ynchronize'))
        a = newnamed('Repository.lockFile', self._repofwd('lockTool'),
                     icon='thg-password', enabled='repoopen',
                     menu='repository', toolbar='edit',
                     tooltip=_('Lock or unlock files'))
        self.lockToolAction = a
        newseparator(menu='repository')
        newnamed('Repository.update', self._repofwd('updateToRevision'),
                 icon='hg-update', enabled='repoopen',
                 menu='repository', toolbar='edit',
                 tooltip=_('Update working directory or switch revisions'))
        newnamed('Repository.shelve', self._repofwd('shelve'), icon='hg-shelve',
                 enabled='repoopen', menu='repository')
        newnamed('Repository.import', self._repofwd('thgimport'),
                 icon='hg-import', enabled='repoopen', menu='repository')
        newnamed('Repository.unbundle', self._repofwd('unbundle'),
                 icon='hg-unbundle', enabled='repoopen', menu='repository')
        newseparator(menu='repository')
        newnamed('Repository.merge', self._repofwd('mergeWithOtherHead'),
                 icon='hg-merge', enabled='repoopen',
                 menu='repository', toolbar='edit',
                 tooltip=_('Merge with the other head of the current branch'))
        newnamed('Repository.resolve', self._repofwd('resolve'),
                 enabled='repoopen', menu='repository')
        newseparator(menu='repository')
        newnamed('Repository.rollback', self._repofwd('rollback'),
                 enabled='repoopen', menu='repository')
        newseparator(menu='repository')
        newnamed('Repository.purge', self._repofwd('purge'), enabled='repoopen',
                 icon='hg-purge', menu='repository')
        newseparator(menu='repository')
        newnamed('Repository.bisect', self._repofwd('bisect'),
                 enabled='repoopen', menu='repository')
        newseparator(menu='repository')
        newnamed('Repository.verify', self._repofwd('verify'),
                 enabled='repoopen', menu='repository')
        newnamed('Repository.recover', self._repofwd('recover'),
                 enabled='repoopen', menu='repository')
        newseparator(menu='repository')
        newnamed('Workbench.openFileManager', self.explore,
                 icon='system-file-manager', enabled='repoopen',
                 menu='repository')
        newnamed('Workbench.openTerminal', self.terminal,
                 icon='utilities-terminal', enabled='repoopen',
                 menu='repository')
        newnamed('Workbench.webServer', self.serve, menu='repository',
                 icon='hg-serve')

        newnamed('Workbench.help', self.onHelp, menu='help',
                 icon='help-browser')
        newnamed('Workbench.explorerHelp', self.onHelpExplorer, menu='help')
        visiblereadme = 'repoopen'
        if self._config.configString('tortoisehg', 'readme'):
            visiblereadme = True
        newnamed('Workbench.openReadme', self.onReadme, menu='help',
                 icon='help-readme', visible=visiblereadme)
        newseparator(menu='help')
        newnamed('Workbench.aboutQt', QApplication.aboutQt, menu='help')
        newnamed('Workbench.about', self.onAbout, menu='help', icon='thg')

        syncActionGroup = QActionGroup(self)
        syncActionGroup.triggered.connect(self._runSyncAction)
        newnamed('Repository.incoming', data='incoming', icon='hg-incoming',
                 enabled='repoopen', toolbar='sync', group=syncActionGroup)
        pullAction = newnamed('Repository.pull', data='pull', icon='hg-pull',
                              enabled='repoopen', toolbar='sync',
                              group=syncActionGroup)
        newnamed('Repository.outgoing', data='outgoing', icon='hg-outgoing',
                 enabled='repoopen', toolbar='sync', group=syncActionGroup)
        pushAction = newnamed('Repository.push', data='push', icon='hg-push',
                              enabled='repoopen', toolbar='sync',
                              group=syncActionGroup)
        menuSync.addActions(syncActionGroup.actions())
        menuSync.addSeparator()

        def addSyncActionMenu(parentAction, action):
            tbb = self.synctbar.widgetForAction(parentAction)
            menu = QMenu(self)
            menu.addAction(action)
            tbb.setMenu(menu)
        syncAllTabsActionGroup = QActionGroup(self)
        syncAllTabsActionGroup.triggered.connect(self._runSyncAllTabsAction)
        addSyncActionMenu(pullAction,
                          newnamed('Repository.pullAllTabs',
                                   data='pull', icon='hg-pull',
                                   enabled='repoopen',
                                   group=syncAllTabsActionGroup))
        addSyncActionMenu(pushAction,
                          newnamed('Repository.pushAllTabs',
                                   data='push', icon='hg-push',
                                   enabled='repoopen',
                                   group=syncAllTabsActionGroup))
        menuSync.addActions(syncAllTabsActionGroup.actions())
        menuSync.addSeparator()

        action = QAction(self)
        action.setIcon(qtlib.geticon('thg-sync-bookmarks'))
        self._actionavails['repoopen'].append(action)
        action.triggered.connect(self._runSyncBookmarks)
        self._actionregistry.registerAction('Repository.syncBookmarks', action)
        menuSync.addAction(action)

        self._lastRepoSyncPath = {}
        self.urlCombo = QComboBox(self)
        self.urlCombo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.urlCombo.currentIndexChanged.connect(self._updateSyncUrl)
        self.urlComboAction = self.synctbar.addWidget(self.urlCombo)
        # hide it because workbench could be started without open repo
        self.urlComboAction.setVisible(False)

    def _setupUrlCombo(self, repoagent):
        """repository has been switched, fill urlCombo with URLs"""
        pathdict = dict(repoagent.configStringItems('paths'))
        aliases = list(pathdict.keys())

        combo_setting = repoagent.configString(
            'tortoisehg', 'workbench.target-combo')
        self.urlComboAction.setVisible(len(aliases) > 1
                                       or combo_setting == 'always')

        # 1. Sort the list if aliases
        aliases.sort()
        # 2. Place the default alias at the top of the list
        if 'default' in aliases:
            aliases.remove('default')
            aliases.insert(0, 'default')
        # 3. Make a list of paths that have a 'push path'
        # note that the default path will be first (if it has a push path),
        # followed by the other paths that have a push path, alphabetically
        haspushaliases = [alias for alias in aliases
                         if alias + '-push' in aliases]
        # 4. Place the "-push" paths next to their "pull paths"
        regularaliases = []
        for a in aliases[:]:
            if a.endswith('-push'):
                if a[:-len('-push')] in haspushaliases:
                    continue
            regularaliases.append(a)
            if a in haspushaliases:
                regularaliases.append(a + '-push')
        # 5. Create the list of 'combined aliases'
        combinedaliases = [(a, a + '-push') for a in haspushaliases]
        # 6. Put the combined aliases first, followed by the regular aliases
        aliases = combinedaliases + regularaliases
        # 7. Ensure the first path is a default path (either a
        # combined "default | default-push" path or a regular default path)
        if 'default-push' not in aliases and 'default' in aliases:
            aliases.remove('default')
            aliases.insert(0, 'default')

        self.urlCombo.blockSignals(True)
        self.urlCombo.clear()
        for n, a in enumerate(aliases):
            # text, (pull-alias, push-alias)
            if isinstance(a, tuple):
                itemtext = '\u2193 %s | %s \u2191' % a
                itemdata = tuple(pathdict[alias] for alias in a)
                tooltip = _('pull: %s\npush: %s') % itemdata
            else:
                itemtext = a
                itemdata = (pathdict[a], pathdict[a])
                tooltip = pathdict[a]
            self.urlCombo.addItem(itemtext, itemdata)
            self.urlCombo.setItemData(n, tooltip, Qt.ItemDataRole.ToolTipRole)
        # Try to select the previously selected path, if any
        prevpath = self._lastRepoSyncPath.get(repoagent.rootPath())
        if prevpath:
            idx = self.urlCombo.findText(prevpath)
            if idx >= 0:
                self.urlCombo.setCurrentIndex(idx)
        self.urlCombo.blockSignals(False)
        self._updateSyncUrlToolTip(self.urlCombo.currentIndex())

    @pyqtSlot(str)
    def _setupUrlComboIfCurrent(self, root):
        w = self._currentRepoWidget()
        if w.repoRootPath() == root:
            self._setupUrlCombo(self._repomanager.repoAgent(root))

    def _syncUrlFor(self, op):
        """Current URL for the given sync operation"""
        urlindex = self.urlCombo.currentIndex()
        if urlindex < 0:
            return
        opindex = {'incoming': 0, 'pull': 0, 'outgoing': 1, 'push': 1}[op]
        return self.urlCombo.itemData(urlindex)[opindex]

    @pyqtSlot(int)
    def _updateSyncUrl(self, index):
        self._updateSyncUrlToolTip(index)
        # save the new url for later recovery
        reporoot = self.currentRepoRootPath()
        if not reporoot:
            return
        path = self.urlCombo.currentText()
        self._lastRepoSyncPath[reporoot] = path

    def _updateSyncUrlToolTip(self, index):
        self._updateUrlComboToolTip(index)
        self._updateSyncActionToolTip(index)

    def _updateUrlComboToolTip(self, index):
        if not self.urlCombo.count():
            tooltip = _('There are no configured sync paths.\n'
                        'Open the Synchronize tab to configure them.')
        else:
            tooltip = self.urlCombo.itemData(index, Qt.ItemDataRole.ToolTipRole)
        self.urlCombo.setToolTip(tooltip)

    def _updateSyncActionToolTip(self, index):
        if index < 0:
            tooltips = {
                'incoming': _('Check for incoming changes'),
                'pull':     _('Pull incoming changes'),
                'outgoing': _('Detect outgoing changes'),
                'push':     _('Push outgoing changes'),
                }
        else:
            pullurl, pushurl = self.urlCombo.itemData(index)
            tooltips = {
                'incoming': _('Check for incoming changes from\n%s') % pullurl,
                'pull':     _('Pull incoming changes from\n%s') % pullurl,
                'outgoing': _('Detect outgoing changes to\n%s') % pushurl,
                'push':     _('Push outgoing changes to\n%s') % pushurl,
                }

        for a in self.synctbar.actions():
            op = str(a.data())
            if op in tooltips:
                a.setToolTip(tooltips[op])

    def _setupCustomTools(self, ui):
        tools, toollist = hglib.tortoisehgtools(ui,
            selectedlocation='workbench.custom-toolbar')
        # Clear the existing "custom" toolbar
        self.customtbar.clear()
        # and repopulate it again with the tool configuration
        # for the current repository
        if not tools:
            return
        for name in toollist:
            if name == '|':
                self._addNewSeparator(toolbar='custom')
                continue
            info = tools.get(name, None)
            if info is None:
                continue
            command = info.get('command', None)
            if not command:
                continue
            showoutput = info.get('showoutput', False)
            workingdir = info.get('workingdir', '')
            label = info.get('label', name)
            tooltip = info.get('tooltip', _("Execute custom tool '%s'") % label)
            icon = info.get('icon', 'tools-spanner-hammer')

            self._addNewAction(label,
                self._repofwd('runCustomCommand',
                              [command, showoutput, workingdir]),
                icon=icon, tooltip=tooltip,
                enabled=True, toolbar='custom')

    def _addNewAction(self, text, slot=None, icon=None, shortcut=None,
                  checkable=False, tooltip=None, data=None, enabled=None,
                  visible=None, menu=None, toolbar=None, group=None):
        """Create new action and register it

        :slot: function called if action triggered or toggled.
        :checkable: checkable action. slot will be called on toggled.
        :data: optional data stored on QAction.
        :enabled: bool or group name to enable/disable action.
        :visible: bool or group name to show/hide action.
        :shortcut: QKeySequence, key sequence or name of standard key.
        :menu: name of menu to add this action.
        :toolbar: name of toolbar to add this action.
        """
        action = QAction(text, self, checkable=checkable)
        if slot:
            if checkable:
                action.toggled.connect(slot)
            else:
                action.triggered.connect(slot)
        if icon:
            action.setIcon(qtlib.geticon(icon))
        if shortcut:
            keyseq = qtlib.keysequence(shortcut)
            if isinstance(keyseq, QKeySequence.StandardKey):
                action.setShortcuts(keyseq)
            else:
                action.setShortcut(keyseq)
        if tooltip:
            if action.shortcut():
                tooltip += ' (%s)' % action.shortcut().toString()
            action.setToolTip(tooltip)
        if data is not None:
            action.setData(data)
        if isinstance(enabled, bool):
            action.setEnabled(enabled)
        elif enabled:
            self._actionavails[enabled].append(action)
        if isinstance(visible, bool):
            action.setVisible(visible)
        elif visible:
            self._actionvisibles[visible].append(action)
        if menu:
            getattr(self, 'menu%s' % menu.title()).addAction(action)
        if toolbar:
            getattr(self, '%stbar' % toolbar).addAction(action)
        if group:
            group.addAction(action)
        return action

    def _addNewNamedAction(self, name, slot=None, icon=None, checkable=False,
                           tooltip=None, data=None, enabled=None,
                           visible=None, menu=None, toolbar=None, group=None):
        """Create new action and register it as user-configurable"""
        a = self._addNewAction('', slot=slot, icon=icon, checkable=checkable,
                               tooltip=tooltip, data=data, enabled=enabled,
                               visible=visible, menu=menu, toolbar=toolbar,
                               group=group)
        self._actionregistry.registerAction(name, a)
        return a

    def _addNewSeparator(self, menu=None, toolbar=None):
        """Insert a separator action; returns nothing"""
        if menu:
            getattr(self, 'menu%s' % menu.title()).addSeparator()
        if toolbar:
            getattr(self, '%stbar' % toolbar).addSeparator()

    def createPopupMenu(self):
        """Create new popup menu for toolbars and dock widgets"""
        menu = super().createPopupMenu()
        assert menu  # should have toolbar/dock menu
        # replace default log dock action by customized one
        menu.insertAction(self._console.toggleViewAction(),
                          self._actionShowConsole)
        menu.removeAction(self._console.toggleViewAction())
        menu.addSeparator()
        menu.addAction(self._actionDockedConsole)
        menu.addAction(_('Custom Toolbar &Settings'),
                       self._editCustomToolsSettings)
        return menu

    @pyqtSlot(QAction)
    def _onSwitchRepoTaskTab(self, action):
        rw = self._currentRepoWidget()
        if rw:
            rw.switchToNamedTaskTab(str(action.data()))

    @pyqtSlot(bool)
    def _setRepoTaskTabVisible(self, visible):
        rw = self._currentRepoWidget()
        if not rw:
            return
        rw.setTaskTabVisible(visible)

    @pyqtSlot(bool)
    def _setConsoleVisible(self, visible):
        if self._actionDockedConsole.isChecked():
            self._setDockedConsoleVisible(visible)
        else:
            self._setConsoleTaskTabVisible(visible)

    def _setDockedConsoleVisible(self, visible):
        self._console.setVisible(visible)
        if visible:
            # not hook setVisible() or showEvent() in order to move focus
            # only when console is activated by user action
            self._console.setFocus()

    def _setConsoleTaskTabVisible(self, visible):
        rw = self._currentRepoWidget()
        if not rw:
            return
        if visible:
            rw.switchToNamedTaskTab('console')
        else:
            # it'll be better if it can switch to the last tab
            rw.switchToPreferredTaskTab()

    @pyqtSlot()
    def _updateShowConsoleAction(self):
        if self._actionDockedConsole.isChecked():
            visible = self._console.isVisibleTo(self)
            enabled = True
        else:
            rw = self._currentRepoWidget()
            visible = bool(rw and rw.currentTaskTabName() == 'console')
            enabled = bool(rw)
        self._actionShowConsole.setChecked(visible)
        self._actionShowConsole.setEnabled(enabled)

    @pyqtSlot()
    def _updateDockedConsoleMode(self):
        docked = self._actionDockedConsole.isChecked()
        visible = self._actionShowConsole.isChecked()
        self._console.setVisible(docked and visible)
        self._setConsoleTaskTabVisible(not docked and visible)
        self._updateShowConsoleAction()

    @pyqtSlot(str, bool)
    def openRepo(self, root: str, reuse: bool, bundle=None):
        """Open tab of the specified repo [unicode]"""
        if not root or root.startswith('ssh://'):
            return
        if reuse and self.repoTabsWidget.selectRepo(root):
            return
        if not self.repoTabsWidget.openRepo(root, bundle):
            return

    @pyqtSlot(str)
    def showRepo(self, root):
        """Activate the repo tab or open it if not available [unicode]"""
        self.openRepo(root, True)

    @pyqtSlot(str, str)
    def setRevsetFilter(self, path, filter):
        if self.repoTabsWidget.selectRepo(path):
            w = self.repoTabsWidget.currentWidget()
            w.setFilter(filter)

    def dragEnterEvent(self, event):
        d = event.mimeData()
        for u in d.urls():
            root = paths.find_root(u.toLocalFile())
            if root:
                event.setDropAction(Qt.DropAction.LinkAction)
                event.accept()
                break

    def dropEvent(self, event):
        accept = False
        d = event.mimeData()
        for u in d.urls():
            root = paths.find_root(u.toLocalFile())
            if root:
                self.showRepo(root)
                accept = True
        if accept:
            event.setDropAction(Qt.DropAction.LinkAction)
            event.accept()

    def _updateMenu(self):
        """Enable actions when repoTabs are opened or closed or changed"""

        # Update actions affected by repo open/close
        someRepoOpen = bool(self._currentRepoWidget())
        for action in self._actionavails['repoopen']:
            action.setEnabled(someRepoOpen)
        for action in self._actionvisibles['repoopen']:
            action.setVisible(someRepoOpen)

        # Update actions affected by repo open/close/change
        self._updateTaskViewMenu()
        self._updateTaskTabVisibilityAction()
        self._updateToolBarActions()

    @pyqtSlot()
    def _updateWindowTitle(self):
        w = self._currentRepoWidget()
        if not w:
            self.setWindowTitle(_('TortoiseHg Workbench'))
            return
        repoagent = self._repomanager.repoAgent(w.repoRootPath())
        if repoagent.configBool('tortoisehg', 'fullpath'):
            self.setWindowTitle(_('%s - TortoiseHg Workbench - %s') %
                                (w.title(), w.repoRootPath()))
        else:
            self.setWindowTitle(_('%s - TortoiseHg Workbench') % w.title())

    @pyqtSlot()
    def _updateToolBarActions(self):
        w = self._currentRepoWidget()
        if w:
            self.filtertbaction.setChecked(w.filterBarVisible())

    @pyqtSlot()
    def _updateTaskViewMenu(self):
        'Update task tab menu for current repository'
        repoWidget = self._currentRepoWidget()
        if not repoWidget:
            for a in self.actionGroupTaskView.actions():
                a.setChecked(False)
            self.lockToolAction.setVisible(False)
        else:
            exts = repoWidget.repo.extensions()
            name = repoWidget.currentTaskTabName()
            for action in self.actionGroupTaskView.actions():
                action.setChecked(str(action.data()) == name)
            self.lockToolAction.setVisible('simplelock' in exts)
        self._updateShowConsoleAction()

        for i, a in enumerate(a for a in self.actionGroupTaskView.actions()
                              if a.isVisible()):
            a.setShortcut('Alt+%d' % (i + 1))

    @pyqtSlot()
    def _updateTaskTabVisibilityAction(self):
        rw = self._currentRepoWidget()
        self.actionTaskTabVisible.setChecked(bool(rw) and rw.isTaskTabVisible())

    @pyqtSlot()
    def _updateHistoryActions(self):
        'Update back / forward actions'
        rw = self._currentRepoWidget()
        self.actionBack.setEnabled(bool(rw and rw.canGoBack()))
        self.actionForward.setEnabled(bool(rw and rw.canGoForward()))

    @pyqtSlot()
    def repoTabChanged(self):
        self._updateHistoryActions()
        self._updateMenu()
        self._updateWindowTitle()

    @pyqtSlot(str)
    def _onCurrentRepoChanged(self, curpath: str):
        self._console.setCurrentRepoRoot(curpath or None)
        self.reporegistry.setActiveTabRepo(curpath)
        if curpath:
            repoagent = self._repomanager.repoAgent(curpath)
            repo = repoagent.rawRepo()
            self.mqpatches.setRepoAgent(repoagent)
            self._setupCustomTools(repo.ui)
            self._setupUrlCombo(repoagent)
            self._updateAbortAction(repoagent)
        else:
            self.mqpatches.setRepoAgent(None)
            self.actionAbort.setEnabled(False)

    @pyqtSlot()
    def _setHistoryColumns(self):
        """Display the column selection dialog"""
        w = self._currentRepoWidget()
        assert w
        w.repoview.setHistoryColumns()

    def _repotogglefwd(self, name):
        """Return function to forward action to the current repo tab"""
        def forwarder(checked):
            w = self._currentRepoWidget()
            if w:
                getattr(w, name)(checked)
        return forwarder

    def _repofwd(self, name, params=None, namedparams=None):
        """Return function to forward action to the current repo tab"""
        if params is None:
            params = []
        if namedparams is None:
            namedparams = {}

        def forwarder():
            w = self._currentRepoWidget()
            if w:
                getattr(w, name)(*params, **namedparams)

        return forwarder

    @pyqtSlot()
    def refresh(self):
        clear = QApplication.keyboardModifiers() & qtlib.QtKeyboardModifier.ControlModifier
        w = self._currentRepoWidget()
        if w:
            # check unnoticed changes to emit corresponding signals
            repoagent = self._repomanager.repoAgent(w.repoRootPath())
            if clear:
                repoagent.clearStatus()
            repoagent.pollStatus()
            # TODO if all objects are responsive to repository signals, some
            # of the following actions are not necessary
            w.reload()

    @pyqtSlot(QAction)
    def _runSyncAction(self, action):
        w = self._currentRepoWidget()
        if w:
            op = str(action.data())
            w.setSyncUrl(self._syncUrlFor(op) or '')
            getattr(w, op)()

    @pyqtSlot(QAction)
    def _runSyncAllTabsAction(self, action):
        originalIndex = self.repoTabsWidget.currentIndex()
        for index in range(0, self.repoTabsWidget.count()):
            self.repoTabsWidget.setCurrentIndex(index)
            self._runSyncAction(action)
        self.repoTabsWidget.setCurrentIndex(originalIndex)

    @pyqtSlot()
    def _runSyncBookmarks(self):
        w = self._currentRepoWidget()
        if w:
            # the sync bookmark dialog is bidirectional but is only able to
            # handle one remote location therefore we use the push location
            w.setSyncUrl(self._syncUrlFor('push') or '')
            w.syncBookmark()

    @pyqtSlot()
    def _abortCommands(self):
        root = self.currentRepoRootPath()
        if not root:
            return
        repoagent = self._repomanager.repoAgent(root)
        repoagent.abortCommands()

    def _updateAbortAction(self, repoagent):
        self.actionAbort.setEnabled(repoagent.isBusy())

    @pyqtSlot(str)
    def _onBusyChanged(self, root):
        repoagent = self._repomanager.repoAgent(root)
        self._updateAbortAction(repoagent)
        if not repoagent.isBusy():
            self.statusbar.clearRepoProgress(root)
        self.statusbar.setRepoBusy(root, repoagent.isBusy())

    def serve(self):
        self._dialogs.open(Workbench._createServeDialog)

    def _createServeDialog(self):
        w = self._currentRepoWidget()
        if w:
            return serve.run(w.repo.ui, root=w.repo.root)
        else:
            return serve.run(self.ui)

    def loadall(self):
        w = self._currentRepoWidget()
        if w:
            w.repoview.model().loadall()

    def _gotorev(self):
        rev, ok = qtlib.getTextInput(self,
                                     _("Goto revision"),
                                     _("Enter revision identifier"))
        if ok:
            w = self._currentRepoWidget()
            assert w
            w.gotoRev(rev)

    @pyqtSlot(str)
    def gotorev(self, rev):
        w = self._currentRepoWidget()
        if w:
            w.repoview.goto(rev)

    def newWorkbench(self):
        cmdline = list(paths.get_thg_command())
        cmdline.extend(['workbench', '--nofork', '--newworkbench'])
        subprocess.Popen(cmdline, creationflags=qtlib.openflags)

    def newRepository(self):
        """ Run init dialog """
        from tortoisehg.hgqt.hginit import InitDialog
        path = self.currentRepoRootPath() or '.'
        dlg = InitDialog(self.ui, path, self)
        if dlg.exec() == 0:
            self.openRepo(dlg.destination(), False)

    @pyqtSlot()
    @pyqtSlot(str)
    def cloneRepository(self, uroot=None):
        """ Run clone dialog """
        # it might be better to reuse existing CloneDialog
        dlg = self._dialogs.openNew(Workbench._createCloneDialog)
        if not uroot:
            uroot = self.currentRepoRootPath()
        if uroot:
            dlg.setSource(uroot)
            dlg.setDestination(uroot + '-clone')

    def _createCloneDialog(self):
        from tortoisehg.hgqt.clone import CloneDialog
        dlg = CloneDialog(self.ui, self._config, parent=self)
        dlg.clonedRepository.connect(self._openClonedRepo)
        return dlg

    @pyqtSlot(str, str)
    def _openClonedRepo(self, root: str, sourceroot: str):
        self.reporegistry.addClonedRepo(root, sourceroot)
        self.showRepo(root)

    def openRepository(self):
        """ Open repo from File menu """
        caption = _('Select repository directory to open')
        root = self.currentRepoRootPath()
        if root:
            cwd = os.path.dirname(root)
        else:
            cwd = hglib.getcwdu()
        FD = QFileDialog
        path = FD.getExistingDirectory(self, caption, cwd,
                                       QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.ReadOnly)
        self.openRepo(path, False)

    def _currentRepoWidget(self):
        return self.repoTabsWidget.currentWidget()

    def currentRepoRootPath(self):
        return self.repoTabsWidget.currentRepoRootPath()

    def onAbout(self, *args):
        """ Display about dialog """
        from tortoisehg.hgqt.about import AboutDialog
        ad = AboutDialog(self)
        ad.finished.connect(ad.deleteLater)
        ad.exec()

    def onHelp(self, *args):
        """ Display online help """
        qtlib.openhelpcontents('workbench.html')

    def onHelpExplorer(self, *args):
        """ Display online help for shell extension """
        qtlib.openhelpcontents('explorer.html')

    def onReadme(self, *args):
        """Display the README file or URL for the current repo, or the global
        README if no repo is open"""
        readme = None
        def getCurrentReadme(repo):
            """
            Get the README file that is configured for the current repo.

            README files can be set in 3 ways, which are checked in the
            following order of decreasing priority:
            - From the tortoisehg.readme key on the current repo's configuration
              file
            - An existing "README" file found on the repository root
                * Valid README files are those called README and whose extension
                  is one of the following:
                    ['', '.txt', '.html', '.pdf', '.doc', '.docx', '.ppt', '.pptx',
                     '.markdown', '.textile', '.rdoc', '.org', '.creole',
                     '.mediawiki','.rst', '.asciidoc', '.pod']
                * Note that the match is CASE INSENSITIVE on ALL OSs.
            - From the tortoisehg.readme key on the user's global configuration file
            """
            readme = None
            if repo:
                # Try to get the README configured for the repo of the current tab
                readmeglobal = self.ui.config(b'tortoisehg', b'readme')
                if readmeglobal:
                    # Note that repo.ui.config() falls back to the self.ui.config()
                    # if the key is not set on the current repo's configuration file
                    readme = repo.ui.config(b'tortoisehg', b'readme')
                    if readmeglobal != readme:
                        # The readme is set on the current repo configuration file
                        return readme

                # Otherwise try to see if there is a file at the root of the
                # repository that matches any of the valid README file names
                # (in a non case-sensitive way)
                # Note that we try to match the valid README names in order
                validreadmes = [b'readme.txt', b'read.me', b'readme.html',
                                b'readme.pdf', b'readme.doc', b'readme.docx',
                                b'readme.ppt', b'readme.pptx',
                                b'readme.md', b'readme.markdown', b'readme.mkdn',
                                b'readme.rst', b'readme.textile', b'readme.rdoc',
                                b'readme.asciidoc', b'readme.org', b'readme.creole',
                                b'readme.mediawiki', b'readme.pod', b'readme']

                readmefiles = [filename for filename in os.listdir(repo.root)
                               if filename.lower().startswith(b'read')]
                for validname in validreadmes:
                    for filename in readmefiles:
                        if filename.lower() == validname:
                            return repo.wjoin(filename)

            # Otherwise try use the global setting (or None if readme is just
            # not configured)
            return readmeglobal

        w = self._currentRepoWidget()
        if w:
            # Try to get the help doc from the current repo tap
            readme = getCurrentReadme(w.repo)

        if readme:
            qtlib.openlocalurl(os.path.expanduser(os.path.expandvars(readme)))
        else:
            qtlib.WarningMsgBox(_("README not configured"),
                _("A README file is not configured for the current repository.<p>"
                "To configure a README file for a repository, "
                "open the repository settings file, add a '<i>readme</i>' "
                "key to the '<i>tortoisehg</i>' section, and set it "
                "to the filename or URL of your repository's README file."))

    def _storeSettings(self, repostosave, lastactiverepo):
        s = QSettings()
        wb = "Workbench/"
        s.setValue(wb + 'geometry', self.saveGeometry())
        s.setValue(wb + 'windowState', self.saveState())
        s.setValue(wb + 'dockedConsole', self._actionDockedConsole.isChecked())
        s.setValue(wb + 'saveRepos', self.actionSaveRepos.isChecked())
        s.setValue(wb + 'saveLastSyncPaths',
            self.actionSaveLastSyncPaths.isChecked())
        s.setValue(wb + 'lastactiverepo', lastactiverepo)
        s.setValue(wb + 'openrepos', ','.join(repostosave))
        s.beginWriteArray('lastreposyncpaths')
        lastreposyncpaths = {}
        if self.actionSaveLastSyncPaths.isChecked():
            lastreposyncpaths = self._lastRepoSyncPath
        for n, root in enumerate(sorted(lastreposyncpaths)):
            s.setArrayIndex(n)
            s.setValue('root', root)
            s.setValue('path', self._lastRepoSyncPath[root])
        s.endArray()

    def restoreSettings(self):
        s = QSettings()
        wb = "Workbench/"
        self.restoreGeometry(qtlib.readByteArray(s, wb + 'geometry'))
        self.restoreState(qtlib.readByteArray(s, wb + 'windowState'))
        self._actionDockedConsole.setChecked(
            qtlib.readBool(s, wb + 'dockedConsole', True))

        lastreposyncpaths = {}
        npaths = s.beginReadArray('lastreposyncpaths')
        for n in range(npaths):
            s.setArrayIndex(n)
            root = qtlib.readString(s, 'root')
            lastreposyncpaths[root] = qtlib.readString(s, 'path')
        s.endArray()
        self._lastRepoSyncPath = lastreposyncpaths

        save = qtlib.readBool(s, wb + 'saveRepos')
        self.actionSaveRepos.setChecked(save)
        savelastsyncpaths = qtlib.readBool(s, wb + 'saveLastSyncPaths')
        self.actionSaveLastSyncPaths.setChecked(savelastsyncpaths)

        openreposvalue = qtlib.readString(s, wb + 'openrepos')
        if openreposvalue:
            openrepos = openreposvalue.split(',')
        else:
            openrepos = []
        # Note that if a "root" has been passed to the "thg" command,
        # "lastactiverepo" will have no effect
        lastactiverepo = qtlib.readString(s, wb + 'lastactiverepo')
        self.repoTabsWidget.restoreRepos(openrepos, lastactiverepo)

        # Clear the lastactiverepo and the openrepos list once the workbench state
        # has been reload, so that opening additional workbench windows does not
        # reopen these repos again
        s.setValue(wb + 'openrepos', '')
        s.setValue(wb + 'lastactiverepo', '')

    def goto(self, root, rev):
        if self.repoTabsWidget.selectRepo(hglib.tounicode(root)):
            rw = self.repoTabsWidget.currentWidget()
            rw.goto(rev)

    def closeEvent(self, event):
        repostosave = []
        lastactiverepo = ''
        if self.actionSaveRepos.isChecked():
            tw = self.repoTabsWidget
            repostosave = pycompat.maplist(tw.repoRootPath,
                                           pycompat.xrange(tw.count()))
            lastactiverepo = tw.currentRepoRootPath()
        if not self.repoTabsWidget.closeAllTabs():
            event.ignore()
        else:
            self._storeSettings(repostosave, lastactiverepo)
            self.reporegistry.close()

    @pyqtSlot()
    def closeCurrentRepoTab(self):
        """close the current repo tab"""
        self.repoTabsWidget.closeTab(self.repoTabsWidget.currentIndex())

    def explore(self):
        root = self.currentRepoRootPath()
        if root:
            qtlib.openlocalurl(root)

    def terminal(self):
        w = self._currentRepoWidget()
        if w:
            qtlib.openshell(w.repo.root, hglib.fromunicode(w.repoDisplayName()),
                            w.repo.ui)

    @pyqtSlot()
    def editSettings(self, focus=None):
        sd = SettingsDialog(configrepo=False, focus=focus,
                            parent=self,
                            root=hglib.fromunicode(self.currentRepoRootPath()))
        sd.exec()

    @pyqtSlot()
    def _editCustomToolsSettings(self):
        self.editSettings('tools')

    @pyqtSlot()
    def _openShortcutSettingsDialog(self):
        dlg = shortcutsettings.ShortcutSettingsDialog(
            self._actionregistry, self)
        dlg.exec()
