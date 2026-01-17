# settings.py - Configuration dialog for TortoiseHg and Mercurial
#
# Copyright 2010 Steve Borho <steve@borho.org>
# Copyright (C) 2026 Peter Demcak <majster64@gmail.com> (dark theme)
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import os
import typing

from typing import (
    Optional,
)

from .qtcore import (
    QEvent,
    QSettings,
    QTimer,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from .qtgui import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileSystemModel,
    QFont,
    QFontDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QIntValidator,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPalette,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QStyle,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import theme

from mercurial import (
    error,
    extensions,
    phases,
    pycompat,
    util,
)

from mercurial.utils import (
    stringutil,
)

from ..util import (
    editor,
    gpg,
    hglib,
    i18n,
    paths,
    terminal,
    wconfig,
)
from ..util.i18n import _
from . import (
    customtools,
    fileencoding,
    qscilib,
    qtlib,
    thgrepo,
)

if typing.TYPE_CHECKING:
    from typing import (
        List,
        Text,
    )

    from ..util.typelib import (
        IniConfig,
    )

if os.name == 'nt':
    from ..util import bugtraq
    _hasbugtraq = True
else:
    _hasbugtraq = False

# Technical Debt
#   stacked widget or pages need to be scrollable
#   we need a consistent icon set
#   connect to thgrepo.configChanged signal and refresh

_unspecstr = _('<unspecified>')
ENTRY_WIDTH = 300

def hasExtension(extname):
    for name, module in extensions.extensions():
        if hglib.tounicode(name) == extname:
            return True
    return False

class SettingsCombo(QComboBox):

    defaults: List[str]

    def __init__(self, parent=None, **opts):
        QComboBox.__init__(self, parent, toolTip=opts['tooltip'])
        self.opts = opts
        self.setEditable(opts.get('canedit', False))
        self.setValidator(opts.get('validator', None))
        self.defaults = opts.get('defaults', [])
        if self.defaults and self.isEditable():
            self.setCompleter(QCompleter(self.defaults, self))
        self.curvalue = None
        self.loaded = False
        if 'nohist' in opts:
            self.previous = []
        else:
            settings = opts['settings']
            slist = qtlib.readStringList(settings, 'settings/' + opts['cpath'])
            self.previous = [s for s in slist if s]
        self.setMinimumWidth(ENTRY_WIDTH)

    def resetList(self):
        self.clear()
        ucur = hglib.tounicode(self.curvalue)
        if self.opts.get('defer') and not self.loaded:
            if self.curvalue is None:  # unspecified
                self.addItem(_unspecstr)
            else:
                self.addItem(ucur or '...')
            return
        self.addItem(_unspecstr)
        curindex = None
        for s in self.defaults:
            if ucur == s:
                curindex = self.count()
            self.addItem(s)
        if self.defaults and self.previous:
            self.insertSeparator(len(self.defaults)+1)
        for m in self.previous:
            if ucur == m and not curindex:
                curindex = self.count()
            self.addItem(m)
        if curindex is not None:
            self.setCurrentIndex(curindex)
        elif self.curvalue is None:
            self.setCurrentIndex(0)
        elif self.curvalue:
            self.addItem(ucur)
            self.setCurrentIndex(self.count()-1)
        else:  # empty string
            self.setEditText(ucur)

    def showPopup(self):
        if self.opts.get('defer') and not self.loaded:
            self.defaults = self.opts['defer']()
            self.loaded = True
            self.resetList()
        QComboBox.showPopup(self)

    ## common APIs for all edit widgets

    def setValue(self, curvalue):
        self.curvalue = curvalue
        self.resetList()

    def value(self):
        utext = self.currentText()
        if utext == _unspecstr:
            return None
        if ('nohist' in self.opts or utext in self.defaults + self.previous
            or not utext):
            return hglib.fromunicode(utext)
        self.previous.insert(0, utext)
        self.previous = self.previous[:10]
        settings = QSettings()
        settings.setValue('settings/'+self.opts['cpath'], self.previous)
        return hglib.fromunicode(utext)

    def isDirty(self):
        return self.value() != self.curvalue

class BoolRBGroup(QWidget):
    def __init__(self, parent=None, **opts):
        QWidget.__init__(self, parent, toolTip=opts['tooltip'])
        self.opts = opts
        self.curvalue = None

        self.trueRB = QRadioButton(_('&True'))
        self.falseRB = QRadioButton(_('&False'))
        self.unspecRB = QRadioButton(_('&Unspecified'))

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.trueRB)
        layout.addWidget(self.falseRB)
        layout.addWidget(self.unspecRB)
        self.setLayout(layout)

    ## common APIs for all edit widgets
    def setValue(self, curvalue):
        # Store parsed value, so a compare of 1 to True isn't flagged as dirty
        if curvalue:
            value = str(stringutil.parsebool(curvalue))
            self.curvalue = hglib.fromunicode(value)
        else:
            self.curvalue = None

        if self.curvalue == b'True':
            self.trueRB.setChecked(True)
        elif self.curvalue == b'False':
            self.falseRB.setChecked(True)
        else:
            self.unspecRB.setChecked(True)

    def value(self):
        if self.trueRB.isChecked():
            return b'True'
        elif self.falseRB.isChecked():
            return b'False'
        else:
            return None

    def isDirty(self):
        return self.value() != self.curvalue

class LineEditBox(QLineEdit):
    def __init__(self, parent=None, **opts):
        QLineEdit.__init__(self, parent, toolTip=opts['tooltip'])
        self.opts = opts
        self.curvalue = None
        self.setMinimumWidth(ENTRY_WIDTH)

    ## common APIs for all edit widgets
    def setValue(self, curvalue):
        self.curvalue = curvalue
        if curvalue:
            self.setText(hglib.tounicode(curvalue))
        else:
            self.setText('')

    def value(self):
        utext = self.text()
        return utext and hglib.fromunicode(utext) or None

    def isDirty(self):
        return self.value() != self.curvalue

class PasswordEntry(LineEditBox):
    def __init__(self, parent=None, **opts):
        QLineEdit.__init__(self, parent, toolTip=opts['tooltip'])
        self.opts = opts
        self.curvalue = None
        self.setEchoMode(QLineEdit.EchoMode.Password)
        self.setMinimumWidth(ENTRY_WIDTH)

class TextEntry(QTextEdit):
    def __init__(self, parent=None, **opts):
        QTextEdit.__init__(self, parent, toolTip=opts['tooltip'])
        self.opts = opts
        self.curvalue = None
        self.setMinimumWidth(ENTRY_WIDTH)

    ## common APIs for all edit widgets
    def setValue(self, curvalue):
        self.curvalue = curvalue
        if curvalue:
            self.setPlainText(hglib.tounicode(curvalue))
        else:
            self.setPlainText('')

    def value(self):
        # It is not possible to set a multi-line value with an empty line
        utext = self.removeEmptyLines(self.toPlainText())
        return utext and hglib.fromunicode(utext) or None

    def isDirty(self):
        return self.value() != self.curvalue

    def removeEmptyLines(self, text: str) -> str:
        if not text:
            return text
        rawlines = text.splitlines()
        lines = []
        for line in rawlines:
            if not line.strip():
                continue
            lines.append(line)
        return os.linesep.join(lines)


def _describeFont(font: Optional[QFont]) -> str:
    if not font:
        return _unspecstr

    s = font.family()
    s += ", "  + _("%dpt") % font.pointSize()
    if font.bold():
        s += ", " + _("Bold")
    if font.italic():
        s += ", " + _("Italic")
    if font.strikeOut():
        s += ", " + _("Strike")
    if font.underline():
        s += ", " + _("Underline")
    return s

class FontEntry(QWidget):
    def __init__(self, parent=None, **opts):
        QWidget.__init__(self, parent, toolTip=opts['tooltip'])
        self.opts = opts
        self.curvalue = None
        self.font = None

        self.label = QLabel()
        self.setButton = QPushButton(_('&Set...'))
        self.clearButton = QPushButton(_('&Clear'))

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        layout.addStretch()
        layout.addWidget(self.setButton)
        layout.addWidget(self.clearButton)
        self.setLayout(layout)

        self.setButton.clicked.connect(self.onSetClicked)
        self.clearButton.clicked.connect(self.onClearClicked)

        cpath = self.opts['cpath']
        assert cpath.startswith('tortoisehg.'), cpath
        self.fname = cpath[11:]
        self.setMinimumWidth(ENTRY_WIDTH)

    def onSetClicked(self):
        thgf = qtlib.getfont(self.fname)
        origfont = self.font or thgf.font()
        font, isok = QFontDialog.getFont(origfont, self)
        if not isok:
            return
        self.setCurrentFont(font)
        thgf.setFont(font)

    def onClearClicked(self):
        self.setCurrentFont(None)

    def setCurrentFont(self, font):
        self.font = font
        self.label.setText(_describeFont(self.font))

    ## common APIs for all edit widgets

    def setValue(self, curvalue):
        if curvalue:
            self.curvalue = QFont()
            self.curvalue.fromString(hglib.tounicode(curvalue))
        else:
            self.curvalue = None
        self.setCurrentFont(self.curvalue)

    def value(self):
        if not self.font:
            return None

        utext = self.font.toString()
        return hglib.fromunicode(utext)

    def isDirty(self):
        return self.font != self.curvalue

class SettingsCheckBox(QCheckBox):
    def __init__(self, parent=None, **opts):
        QCheckBox.__init__(self, parent, toolTip=opts['tooltip'])
        self.opts = opts
        self.curvalue = None
        self.setText(opts['label'])

    def setValue(self, curvalue):
        if self.curvalue is None:
            self.curvalue = curvalue
        self.setChecked(curvalue)

    def value(self):
        return self.isChecked()  # applyChangesForExtensions can handle booleans

    def isDirty(self):
        return self.value() != self.curvalue

# When redesigning the structure of SettingsForm, consider to replace Spacer
# by QGroupBox.
class Spacer(QWidget):
    """Dummy widget for group separator"""

    def __init__(self, parent=None, **opts):
        super().__init__(parent)
        if opts.get('cpath'):
            raise ValueError('do not set cpath for spacer')
        self.opts = opts

    def setValue(self, curvalue):
        raise NotImplementedError

    def value(self):
        raise NotImplementedError

    def isDirty(self):
        return False

class BugTraqConfigureEntry(QPushButton):
    def __init__(self, parent=None, **opts):
        QPushButton.__init__(self, parent, toolTip=opts['tooltip'])

        self.opts = opts
        self.curvalue = None
        self.options = None

        self.tracker = None
        self.master = None
        self.setText(opts['label'])
        self.clicked.connect(self.on_clicked)

    def on_clicked(self, checked):
        parameters = self.options
        self.options = self.tracker.show_options_dialog(parameters)

    def master_updated(self):
        self.setEnabled(False)
        if self.master is None:
            return
        if self.master.value() is None:
            return
        if len(self.master.value()) == 0:
            return

        try:
            setting = self.master.value().split(' ', 1)
            trackerid = setting[0]
            name = setting[1]
            self.tracker = bugtraq.BugTraq(trackerid)
        except:
            # failed to load bugtraq module or parse the setting:
            # swallow the error and leave the widget disabled
            return

        try:
            self.setEnabled(self.tracker.has_options())
        except Exception as e:
            qtlib.ErrorMsgBox(_('Issue Tracker'),
                              _('Failed to load issue tracker: \'%s\': %s. '
                                % (name, e)),
                              parent=self)

    ## common APIs for all edit widgets
    def setValue(self, curvalue):
        if self.master is None:
            self.master = self.opts['master']
            self.master.currentIndexChanged.connect(self.master_updated)
        self.master_updated()
        self.curvalue = curvalue
        self.options = curvalue

    def value(self):
        return self.options

    def isDirty(self):
        return self.value() != self.curvalue


class PathBrowser(QWidget):
    def __init__(self, parent=None, **opts):
        QWidget.__init__(self, parent, toolTip=opts['tooltip'])
        self.opts = opts

        self.lineEdit = QLineEdit()
        # use QCompleter(model, parent) to avoid ownership bug of
        # QCompleter(parent /TransferBack/) in PyQt<4.11.4
        completer = QCompleter(None, self)
        fsModel = QFileSystemModel(completer)
        fsModel.setRootPath("")
        completer.setModel(fsModel)
        self.lineEdit.setCompleter(completer)

        self.browseButton = QPushButton(_('&Browse...'))
        self.browseButton.clicked.connect(self.browse)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.lineEdit)
        layout.addWidget(self.browseButton)
        self.setLayout(layout)

    def browse(self):
        dir = QFileDialog.getExistingDirectory(self, '', self.lineEdit.text())
        if dir:
            self.lineEdit.setText(dir)

    ## common APIs for all edit widgets
    def setValue(self, curvalue):
        self.curvalue = curvalue
        if curvalue:
            self.lineEdit.setText(hglib.tounicode(curvalue))
        else:
            self.lineEdit.setText('')

    def value(self):
        utext = self.lineEdit.text()
        return utext and hglib.fromunicode(utext) or None

    def isDirty(self):
        return self.value() != self.curvalue

def genEditCombo(opts, defaults=None):
    if defaults is None:
        defaults = []
    opts['canedit'] = True
    opts['defaults'] = defaults
    return SettingsCombo(**opts)

def genIntEditCombo(opts, defaults=None):
    'EditCombo, only allows integer values'
    opts['canedit'] = True
    opts['validator'] = QIntValidator(None)  # missing parent=None on PyQt4.6
    if defaults:
        opts['defaults'] = ['%d' % n for n in defaults]
    return SettingsCombo(**opts)

def genLineEditBox(opts):
    'Generate a single line text entry box'
    return LineEditBox(**opts)

def genPasswordEntry(opts):
    'Generate a password entry box'
    return PasswordEntry(**opts)
def genTextEntry(opts):
    'Generate a multi-line text input entry box'
    return TextEntry(**opts)

def genDefaultCombo(opts, defaults=None):
    'user must select from a list'
    if defaults is None:
        defaults = []
    opts['defaults'] = defaults
    opts['nohist'] = True
    return SettingsCombo(**opts)

def genBoolRBGroup(opts):
    'true, false, unspecified'
    return BoolRBGroup(**opts)

def genDeferredCombo(opts, func):
    'Values retrieved from a function at popup time'
    opts['defer'] = func
    opts['nohist'] = True
    return SettingsCombo(**opts)

def genEditableDeferredCombo(opts, func):
    'Values retrieved from a function at popup time'
    opts['canedit'] = True
    return genDeferredCombo(opts, func)

def genFontEdit(opts):
    return FontEntry(**opts)

def genSpacer(opts):
    return Spacer(**opts)

def genBugTraqEdit(opts):
    return BugTraqConfigureEntry(**opts)

def genPathBrowser(opts):
    return PathBrowser(**opts)

def findIssueTrackerPlugins():
    plugins = bugtraq.get_issue_plugins_with_names()
    names = [("%s %s" % (key[0], key[1])) for key in plugins]
    return names

def issuePluginVisible():
    if not _hasbugtraq:
        return False
    try:
        # quick test to see if we're able to load the bugtraq module
        test = bugtraq.BugTraq('')
        return True
    except:
        return False

def findThemes():
    # UI strings (capitalized) are handled by SettingsCombo
    return [name.capitalize() for name in theme.available_themes()]

def findDiffTools():
    return sorted(hglib.tounicode(t) for t in hglib.difftools(hglib.loadui()))

def findMergeTools():
    return [hglib.tounicode(t) for t in hglib.mergetools(hglib.loadui())]

def findEditors():
    return [hglib.tounicode(e) for e in editor.findeditors(hglib.loadui())]

def findTerminals():
    return [hglib.tounicode(t) for t in terminal.findterminals(hglib.loadui())]

def findGpg():
    return [hglib.tounicode(t) for t in gpg.findgpg(hglib.loadui())]

def genCheckBox(opts):
    opts['nohist'] = True
    return SettingsCheckBox(**opts)

class _fi:
    """Information of each field"""
    __slots__ = ('label', 'cpath', 'values', 'tooltip',
                 'restartneeded', 'globalonly', 'noglobal',
                 'master', 'visible')

    def __init__(self, label, cpath, values, tooltip,
                 restartneeded=False, globalonly=False, noglobal=False,
                 master=None, visible=None):
        self.label = label
        self.cpath = cpath
        self.values = values
        self.tooltip = tooltip
        self.restartneeded = restartneeded
        self.globalonly = globalonly
        self.noglobal = noglobal
        self.master = master
        self.visible = visible

    def isVisible(self):
        if self.visible is None:
            return True
        else:
            return self.visible()

INFO = (
({'name': 'general', 'label': 'TortoiseHg', 'icon': 'thg'}, (
    _fi(_('UI Language'), 'tortoisehg.ui.language',
        (genDeferredCombo, i18n.availablelanguages),
        _('Specify your preferred user interface language (restart needed)'),
        restartneeded=True, globalonly=True),
    _fi(_('Three-way Merge Tool'), 'ui.merge',
        (genDeferredCombo, findMergeTools),
        _('Graphical merge program for resolving merge conflicts.  If left '
          'unspecified, Mercurial will use the first applicable tool it finds '
          'on your system or use its internal merge tool that leaves conflict '
          'markers in place.  Choose internal:merge to force conflict markers, '
          'internal:prompt to always select local or other, or internal:dump '
          'to leave files in the working directory for manual merging')),
    _fi(_('Visual Diff Tool'), 'tortoisehg.vdiff',
        (genDeferredCombo, findDiffTools),
        _('Specify visual diff tool, as described in the [merge-tools] '
          'section of your Mercurial configuration files.  If left '
          'unspecified, TortoiseHg will use the selected merge tool. '
          'Failing that it uses the first applicable tool it finds.')),
    _fi(_('Visual Editor'), 'tortoisehg.editor',
        (genEditableDeferredCombo, findEditors),
        _('Specify visual editor, as described in the [editor-tools] '
          'section of your Mercurial configuration files.  If left '
          'unspecified, TortoiseHg will use the first applicable tool '
          'it finds.')),
    _fi(_('CLI Editor'), 'ui.editor',
        (genEditableDeferredCombo, findEditors),
        _('The editor used by Mercurial command line commands to '
          'collect multiline input from the user.  Most notably, '
          'commit messages.')),
    _fi(_('Shell'), 'tortoisehg.shell',
        (genEditableDeferredCombo, findTerminals),
        _('Specify the command to launch your preferred terminal shell '
          'application. If the value includes the string %(reponame)s, the '
          'name of the repository will be substituted in place of '
          '%(reponame)s. Similarly, %(root)s will be the full path to the '
          'repository. (restart needed)<br>'
          'Default, Windows: cmd.exe /K title %(reponame)s<br>'
          'Default, OS X: not set<br>'
          'Default, other: xterm -T "%(reponame)s"'),
        globalonly=True),
    _fi(_('Immediate Operations'), 'tortoisehg.immediate', genEditCombo,
        _('Space separated list of shell operations you would like '
          'to be performed immediately, without user interaction. '
          'Commands are "add remove revert forget". '
          'Default: None (leave blank)')),
    _fi(_('Tab Width'), 'tortoisehg.tabwidth', genIntEditCombo,
        _('Specify the number of spaces that tabs expand to in various '
          'TortoiseHg windows. '
          'Default: 8')),
    _fi(_('Force Repo Tab'), 'tortoisehg.forcerepotab', genBoolRBGroup,
        _('Always show repo tabs, even for a single repo. Default: False'),
        globalonly=True),
    _fi(_('Monitor Repo Changes'), 'tortoisehg.monitorrepo',
        (genDefaultCombo, ['always', 'localonly', 'never']),
        _('Specify the target filesystem where TortoiseHg monitors changes. '
          'Default: localonly')),
    _fi(_('Max Diff Size'), 'tortoisehg.maxdiff', genIntEditCombo,
        _('The maximum size file (in KB) that TortoiseHg will '
          'show changes for in the changelog, status, and commit windows. '
          'A value of zero implies no limit.  Default: 1024 (1MB)')),
    _fi(_('Fork GUI'), 'tortoisehg.guifork', genBoolRBGroup,
        _('When running from the command line, fork a background '
          'process to run graphical dialogs. This setting is ignored when '
          'TortoiseHg runs as an OS X application bundle.  Default: True'),
        globalonly=True),
    _fi(_('Full Path Title'), 'tortoisehg.fullpath', genBoolRBGroup,
        _('Show a full directory path of the repository in the dialog title '
          'instead of just the root directory name.  Default: False')),
    _fi(_('Auto-resolve merges'), 'tortoisehg.autoresolve', genBoolRBGroup,
        _('Indicates whether TortoiseHg should attempt to automatically '
          'resolve changes from both sides to the same file, and only report '
          'merge conflicts when this is not possible. When False, all files '
          'with changes on both sides of the merge will report as conflicting, '
          'even if the edits are to different parts of the file. In either '
          'case, when conflicts occur, the user will be invited to review and '
          'resolve changes manually. Default: True.')),
    _fi(_('New Repo Skeleton'), 'tortoisehg.initskel', genPathBrowser,
        _('If specified, files in the directory, e.g. .hgignore, are copied '
          'to the newly-created repository.'),
        globalonly=True),
    _fi(_('Default Clone Destination'), 'tortoisehg.defaultclonedest',
        genPathBrowser,
        _('If specified, the destination field in the clone widget will be '
          'pre-filled with this path. Otherwise, it will be pre-filled with '
          'the current working directory.'),
        globalonly=True),
    )),

({'name': 'log', 'label': _('Workbench'), 'icon': 'hg-log'}, (
    _fi(_('Single Workbench Window'), 'tortoisehg.workbench.single',
        genBoolRBGroup,
        _('Select whether you want to have a single workbench window. '
          'If you disable this setting you will get a new workbench window '
          'everytime that you use the "Hg Workbench" command on the explorer '
          'context menu. Default: True'),
        restartneeded=True, globalonly=True),
    _fi(_('Default widget'), 'tortoisehg.defaultwidget', (genDefaultCombo,
        ['revdetails', 'commit', 'sync', 'search']),
        _('Select the initial widget that will be shown when opening a '
          'repository. '
          'Default: revdetails')),
    _fi(_('Initial revision'), 'tortoisehg.initialrevision',
        (genDefaultCombo, ['current', 'tip', 'workingdir']),
        _('Select the initial revision that will be selected when opening a '
          'repository.  You can select the "current" (i.e. the working '
          'directory parent), the current "tip" or the working directory '
          '("workingdir"). '
          'Default: current')),
    _fi(_('Open new tabs next\nto the current tab'),
        'tortoisehg.opentabsaftercurrent', genBoolRBGroup,
        _('Should new tabs be open next to the current tab? '
          'If False new tabs will be open after the last tab. '
          'Default: True'),
        globalonly=True),
    _fi(_('Author Coloring'), 'tortoisehg.authorcolor', genBoolRBGroup,
        _('Color changesets by author name. '
          'Default: False')),
    _fi(_('Full Authorname'), 'tortoisehg.fullauthorname', genBoolRBGroup,
        _('Show full authorname in Logview. If not enabled, '
          'only a short part, usually name without email is shown. '
          'Default: False')),
    _fi(_('Theme'), 'ui.theme', (genDeferredCombo, findThemes),
        _('Select application theme. '
        'Unspecified uses the default light theme. '
        'Theme changes require application restart.'),
        restartneeded=True, globalonly=True),
    _fi(_('Task Tabs'), 'tortoisehg.tasktabs',
        (genDefaultCombo, ['east', 'west', 'off']),
        _('Show tabs along the side of the bottom half of each repo '
          'widget allowing one to switch task tabs without using the toolbar. '
          'Default: off')),
    _fi(_('Task Toolbar Order'), 'tortoisehg.workbench.task-toolbar',
        genEditCombo,
        _('Specify which task buttons you want to show on the task toolbar '
          'and in which order.<br>Type a list of the task button names. '
          'Add separators by putting "|" between task button names.<br>'
          'Valid names are: log commit sync grep.<br>'
          'Default: log commit grep | sync'),
        restartneeded=True, globalonly=True),
    _fi(_('Long Summary'), 'tortoisehg.longsummary', genBoolRBGroup,
        _('If true, concatenate multiple lines of changeset summary '
          'and truncate them at 80 characters as necessary. '
          'Default: False')),
    _fi(_('Log Batch Size'), 'tortoisehg.graphlimit', genIntEditCombo,
        _('The number of revisions to read and display in the '
          'changelog viewer in a single batch. '
          'Default: 500')),
    _fi(_('Dead Branches'), 'tortoisehg.deadbranch', genEditCombo,
        _('Comma separated list of branch names that should be ignored '
          'when building a list of branch names for a repository. '
          'Default: None (leave blank)')),
    _fi(_('Branch Colors'), 'tortoisehg.branchcolors', genEditCombo,
        _('Space separated list of branch names and colors of the form '
          'branch:#XXXXXX. Spaces and colons in the branch name must be '
          'escaped using a backslash (\\). Likewise some other characters '
          'can be escaped in this way, e.g. \\u0040 will be decoded to the '
          '@ character, and \\n to a linefeed. '
          'Default: None (leave blank)')),
    _fi(_('Hide Tags'), 'tortoisehg.hidetags', genEditCombo,
        _('Space separated list of tags that will not be shown. '
          'Useful example: Specify "qbase qparent qtip" to hide the '
          'standard tags inserted by the Mercurial Queues Extension. '
          'Default: None (leave blank)')),
    _fi(_('Activate Bookmarks'), 'tortoisehg.activatebookmarks',
        (genDefaultCombo, ['auto', 'prompt', 'never']),
        _('Select when TortoiseHg will show a prompt to activate a bookmark '
          'when updating to a revision that has one or more bookmarks.'
          '<ul><li><b>auto</b>: Try to automatically activate bookmarks. When '
          'updating to a revision that has a single bookmark it will be '
          'activated automatically. Show a prompt if there is more than one '
          'bookmark on the revision that is being updated to.'
          '<li><b>prompt</b>: The default. Show a prompt when updating to a '
          'revision that has one or more bookmarks.'
          '<li><b>never</b>: Never show any prompt to activate any bookmarks.'
          '</ul><p>'
          'Default: prompt')),
    _fi(_('Show Family Line'), 'tortoisehg.showfamilyline', genBoolRBGroup,
        _('Show indirect revision dependency on the revision graph '
          'when filtered by revset. Default: True<p>'
          '<b>Note</b>: Calculating family line may be slow in some cases. '
          'This option is expected to be removed if the performance issue is '
          'solved.')),
    _fi(_('Use optimized graph layouter'), 'tortoisehg.graphopt',
        genBoolRBGroup,
        _('Use alternative graph layouter for large repositories. '
          'Default: False<p>'
          '<b>Note</b>: This layouter colors edges using branch information '
          'and does not display graft edges, regardless of whether they are '
          'requested or not.')),
    _fi(_('Copy Hash to Selection'), 'tortoisehg.copy_hash_selection',
        genBoolRBGroup,
        _('When a revision is selected in Workbench, copy hash to primary '
          'selection clipboard (X11). Default: False')),
    )),
({'name': 'commit', 'label': _('Commit', 'config item'), 'icon': 'hg-commit'},
 (
    _fi(_('Username'), 'ui.username', genEditCombo,
        _('Name associated with commits.  The common format is:<br>'
          'Full Name &lt;email@example.com&gt;')),
    _fi(_('Ask Username'), 'ui.askusername', genBoolRBGroup,
        _('If no username has been specified, the user will be prompted to '
          'enter a username.  Default: False')),
    _fi(_('Summary Line Length'), 'tortoisehg.summarylen', genIntEditCombo,
        _('Suggested length of commit message lines. A red vertical '
          'line will mark this length.  CTRL-E will reflow the current '
          'paragraph to the specified line length. Default: 80')),
    _fi(_('Close After Commit'), 'tortoisehg.closeci', genBoolRBGroup,
        _('Close the commit tool after every successful '
          'commit.  Default: False')),
    _fi(_('Push After Commit'), 'tortoisehg.cipushafter',
        (genEditCombo, ['default-push', 'default']),
        _('Attempt to push to specified URL or alias after each successful '
          'commit.  Default: No push')),
    _fi(_('Auto Commit List'), 'tortoisehg.autoinc', genEditCombo,
        _('Comma separated list of files that are automatically included '
          'in every commit.  Intended for use only as a repository setting. '
          'Default: None (leave blank)')),
    _fi(_('Auto Exclude List'), 'tortoisehg.ciexclude', genEditCombo,
        _('Comma separated list of files that are automatically unchecked '
          'when the status, and commit dialogs are opened. '
          'Default: None (leave blank)')),
    _fi(_('English Messages'), 'tortoisehg.engmsg', genBoolRBGroup,
        _('Generate English commit messages even if LANGUAGE or LANG '
          'environment variables are set to a non-English language. '
          'This setting is used by the Merge, Tag and Backout dialogs. '
          'Default: False')),
    _fi(_('New Commit Phase'), 'phases.new-commit',
        (genDefaultCombo,
         [pycompat.sysstr(ph) for ph in phases.cmdphasenames]),
        _('The phase of new commits. Default: draft')),
    _fi(_('Secret MQ Patches'), 'mq.secret', genBoolRBGroup,
        _('Make MQ patches secret (instead of draft). '
          'Default: False')),
    _fi(_('Check Subrepo Phase'), 'phases.checksubrepos',
        (genDefaultCombo, ['ignore', 'follow', 'abort']),
        _('Check the phase of the current revision of each subrepository.  '
          'For settings other than "ignore", the phase of the current '
          'revision of each subrepository is checked before committing the '
          'parent repository.  '
          'Default: follow')),
    _fi(_('Monitor working<br>directory changes'),
        'tortoisehg.refreshwdstatus',
        (genDefaultCombo, ['auto', 'always', 'alwayslocal']),
        _('Select when the working directory status list will be refreshed:<br>'
          '- <b>auto</b>: [<i>default</i>] let TortoiseHg decide when to '
          'refresh the working directory status list.<br>'
          'TortoiseHg will refresh the status list whenever it performs an '
          'action that may potentially modify the working directory. '
          "<i>This may miss any changes that happen outside of TortoiseHg's "
          'control;</i><br>'
          '- <b>always</b>: in addition to the automatic updates above, also '
          'refresh the status list whenever the user clicks on the "working '
          'dir revision" or on the "Commit icon" on the workbench task bar;<br>'
          '- <b>alwayslocal</b>: same as "<b>always</b>" but restricts forced '
          'refreshes to <i>local repos</i>.<br>'
          'Default: auto')),
    _fi(_('Confirm adding unknown files'), 'tortoisehg.confirmaddfiles',
        genBoolRBGroup,
        _('Determines if TortoiseHg should show a confirmation dialog '
          'before adding new files in a commit. '
          'If True, a confirmation dialog will be shown. '
          'If False, selected new files will be included in the '
          'commit with no confirmation dialog.  Default: True')),
    _fi(_('Confirm deleting files'), 'tortoisehg.confirmdeletefiles',
        genBoolRBGroup,
        _('Determines if TortoiseHg should show a confirmation dialog '
          'before removing files in a commit. '
          'If True, a confirmation dialog will be shown. '
          'If False, selected deleted files will be included in the '
          'commit with no confirmation dialog.  Default: True')),
    )),

({'name': 'sync', 'label': _('Sync'), 'icon': 'thg-sync'}, (
    _fi(_('After Pull Operation'), 'tortoisehg.postpull', (genDefaultCombo,
        ['none', 'update', 'fetch', 'rebase', 'updateorrebase']),
        _('Operation which is performed directly after a successful pull. '
          'update equates to pull --update, fetch equates to the fetch '
          'extension, rebase equates to pull --rebase, '
          'updateorrebase equates to pull -u --rebase.  Default: none')),
    _fi(_('Default Push'), 'tortoisehg.defaultpush',
        (genDefaultCombo, ['all', 'branch', 'revision']),
        _('Select the revisions that will be pushed by default, '
          'whenever you click the Push button.'
          '<ul><li><b>all</b>: The default. Push all changes in '
          '<i>all branches</i>.'
          '<li><b>branch</b>: Push all changes in the <i>current branch</i>.'
          '<li><b>revision</b>: Push the changes in the current branch '
          '<i><u>up to</u> the current revision</i>.</ul><p>'
          'Default: all')),
    _fi(_('Confirm Push'), 'tortoisehg.confirmpush', genBoolRBGroup,
        _('Determines if TortoiseHg should show a confirmation dialog '
          'before pushing changesets. '
          'If False, push will be performed without any confirmation dialog. '
          'Default: True')),
    _fi(_('Target Combo'), 'tortoisehg.workbench.target-combo',
        (genDefaultCombo, ['auto', 'always']),
        _('Select if TortoiseHg will show a target combo in the sync toolbar.'
          '<ul><li><b>auto</b>: The default. Show the combo if more than one '
          'target configured.'
          '<li><b>always</b>: Always show the combo.'
          '</ul><p>'
          'Default: auto')),
    _fi(_('SSH Command'), 'ui.ssh', genEditCombo,
        _('Command to use for SSH connections.<p>'
          'Default: "ssh" or "TortoisePlink.exe -ssh -2" (Windows)')),
    _fi(_('<b>Subrepository Features:</b>'), None, genSpacer, ''),
    _fi(_('Allow Hg Subrepos'), 'subrepos.hg:allowed', genBoolRBGroup,
        _('Whether Mercurial subrepositories are allowed in the working '
          'directory. '
          'Default: True')),
    _fi(_('Allow Git Subrepos'), 'subrepos.git:allowed', genBoolRBGroup,
        _('Whether Git subrepositories are allowed in the working '
          'directory. '
          'Default: False'
          '<p><a href="%s">See the security note</a> before enabling '
          'Git subrepos.')
        % 'https://www.mercurial-scm.org/doc/hgrc.5.html#subrepos'),
    _fi(_('Allow SVN Subrepos'), 'subrepos.svn:allowed', genBoolRBGroup,
        _('Whether Subversion subrepositories are allowed in the working '
          'directory. '
          'Default: False'
          '<p><a href="%s">See the security note</a> before enabling '
          'Subversion subrepos.')
        % 'https://www.mercurial-scm.org/doc/hgrc.5.html#subrepos'),
    )),

({'name': 'web', 'label': _('Server'), 'icon': 'hg-serve'}, (
    _fi(_('<b>Repository Details:</b>'), None, genSpacer, ''),
    _fi(_('Name'), 'web.name', genEditCombo,
        _('Repository name to use in the web interface, and by TortoiseHg '
          'as a shorthand name.  Default is the working directory.'),
        noglobal=True),
    _fi(_('Encoding'), 'web.encoding',
        (genEditCombo, fileencoding.knownencodings()),
        _('Character encoding of files in the repository, used by the web '
          'interface and TortoiseHg.')),
    _fi(_("'Publishing' repository"), 'phases.publish', genBoolRBGroup,
        _('Controls draft phase behavior when working as a server. When true, '
          'pushed changesets are set to public in both client and server and '
          'pulled or cloned changesets are set to public in the client. '
          'Default: True')),
    _fi(_('<b>Web Server:</b>'), None, genSpacer, ''),
    _fi(_('Description'), 'web.description', genEditCombo,
        _("Textual description of the repository's purpose or "
          'contents.')),
    _fi(_('Contact'), 'web.contact', genEditCombo,
        _('Name or email address of the person in charge of the '
          'repository.')),
    _fi(_('Style'), 'web.style', (genDefaultCombo,
        ['paper', 'monoblue', 'coal', 'spartan', 'gitweb', 'old']),
        _('Which template map style to use')),
    _fi(_('Archive Formats'), 'web.allow_archive',
        (genEditCombo, ['bz2', 'gz', 'zip']),
        _('Comma separated list of archive formats allowed for '
          'downloading')),
    _fi(_('Port'), 'web.port', genIntEditCombo, _('Port to listen on')),
    _fi(_('Push Requires SSL'), 'web.push_ssl', genBoolRBGroup,
        _('Whether to require that inbound pushes be transported '
          'over SSL to prevent password sniffing.')),
    _fi(_('Stripes'), 'web.stripes', genIntEditCombo,
        _('How many lines a "zebra stripe" should span in multiline output. '
          'Default is 1; set to 0 to disable.')),
    _fi(_('Max Files'), 'web.maxfiles', genIntEditCombo,
        _('Maximum number of files to list per changeset. Default: 10')),
    _fi(_('Max Changes'), 'web.maxchanges', genIntEditCombo,
        _('Maximum number of changes to list on the changelog. '
          'Default: 10')),
    _fi(_('Allow Push'), 'web.allow_push', (genEditCombo, ['*']),
        _('Whether to allow pushing to the repository. If empty or not '
          'set, push is not allowed. If the special value "*", any remote '
          'user can push, including unauthenticated users. Otherwise, the '
          'remote user must have been authenticated, and the authenticated '
          'user name must be present in this list (separated by whitespace '
          'or ","). The contents of the allow_push list are examined after '
          'the deny_push list.')),
    _fi(_('Deny Push'), 'web.deny_push', (genEditCombo, ['*']),
        _('Whether to deny pushing to the repository. If empty or not set, '
          'push is not denied. If the special value "*", all remote users '
          'are denied push. Otherwise, unauthenticated users are all '
          'denied, and any authenticated user name present in this list '
          '(separated by whitespace or ",") is also denied. The contents '
          'of the deny_push list are examined before the allow_push list.')),
    )),

({'name': 'proxy', 'label': _('Proxy'), 'icon': QStyle.StandardPixmap.SP_DriveNetIcon}, (
    _fi(_('Host'), 'http_proxy.host', genEditCombo,
        _('Host name and (optional) port of proxy server, for '
          'example "myproxy:8000"')),
    _fi(_('Bypass List'), 'http_proxy.no', genEditCombo,
        _('Optional. Comma-separated list of host names that '
          'should bypass the proxy')),
    _fi(_('User'), 'http_proxy.user', genEditCombo,
        _('Optional. User name to authenticate with at the proxy server')),
    _fi(_('Password'), 'http_proxy.passwd', genPasswordEntry,
        _('Optional. Password to authenticate with at the proxy server')),
    )),

({'name': 'email', 'label': _('Email'), 'icon': 'mail-forward'}, (
    _fi(_('From'), 'email.from', genEditCombo,
        _('Email address to use in the "From" header and for '
          'the SMTP envelope')),
    _fi(_('To'), 'email.to', genEditCombo,
        _('Comma-separated list of recipient email addresses')),
    _fi(_('Cc'), 'email.cc', genEditCombo,
        _('Comma-separated list of carbon copy recipient email addresses')),
    _fi(_('Bcc'), 'email.bcc', genEditCombo,
        _('Comma-separated list of blind carbon copy recipient '
          'email addresses')),
    _fi(_('method'), 'email.method', (genEditCombo, ['smtp']),
        _('Optional. Method to use to send email messages. If value is '
          '"smtp" (default), use SMTP (configured below).  Otherwise, use as '
          'name of program to run that acts like sendmail (takes "-f" option '
          'for sender, list of recipients on command line, message on stdin). '
          'Normally, setting this to "sendmail" or "/usr/sbin/sendmail" '
          'is enough to use sendmail to send messages.')),
    _fi(_('SMTP Host'), 'smtp.host', genEditCombo,
        _('Host name of mail server')),
    _fi(_('SMTP Port'), 'smtp.port', (genIntEditCombo, [25, 465, 587]),
        _('Port to connect to on mail server. '
          'Default: 25')),
    _fi(_('SMTP TLS'), 'smtp.tls',
        (genDefaultCombo, ['starttls', 'smtps', 'none']),
        _('Method to enable TLS when connecting to mail server. '
          'Default: none')),
    _fi(_('SMTP Username'), 'smtp.username', genEditCombo,
        _('Username to authenticate to mail server with')),
    _fi(_('SMTP Password'), 'smtp.password', genPasswordEntry,
        _('Password to authenticate to mail server with')),
    _fi(_('Local Hostname'), 'smtp.local_hostname', genEditCombo,
        _('Hostname the sender can use to identify itself to the '
          'mail server.')),
    )),

({'name': 'diff', 'label': _('Diff and Annotate'),
  'icon': QStyle.StandardPixmap.SP_FileDialogContentsView}, (
    _fi(_('Patch EOL'), 'patch.eol',
        (genDefaultCombo, ['auto', 'strict', 'crlf', 'lf']),
        _('Normalize file line endings during and after patch to lf or '
          'crlf.  Strict does no normalization.  Auto does per-file '
          'detection, and is the recommended setting. '
          'Default: strict')),
    _fi(_('Git Format'), 'diff.git', genBoolRBGroup,
        _('Use git extended diff header format. '
          'Default: False')),
    _fi(_('MQ Git Format'), 'mq.git',
        (genDefaultCombo, ['auto', 'keep', 'yes', 'no']),
        _("When set to 'auto', mq will automatically use git patches when "
          "required  to avoid losing changes to file modes, copy records or "
          "binary files. If set to 'keep', mq will obey the [diff] section "
          "configuration while preserving existing git patches upon qrefresh. "
          "If set to 'yes' or 'no', mq will override the [diff] section and "
          "always generate git or regular patches, possibly losing data in the "
          "second case. "
          "Default: auto")),
    _fi(_('No Dates'), 'diff.nodates', genBoolRBGroup,
        _('Do not include modification dates in diff headers. '
          'Default: False')),
    _fi(_('Show Function'), 'diff.showfunc', genBoolRBGroup,
        _('Show which function each change is in. '
          'Default: False')),
    _fi(_('Ignore White Space'), 'diff.ignorews', genBoolRBGroup,
        _('Ignore white space when comparing lines in diff views. '
          'Default: False')),
    _fi(_('Ignore WS Amount'), 'diff.ignorewsamount', genBoolRBGroup,
        _('Ignore changes in the amount of white space in diff views. '
          'Default: False')),
    _fi(_('Ignore Blank Lines'), 'diff.ignoreblanklines', genBoolRBGroup,
        _('Ignore changes whose lines are all blank in diff views. '
          'Default: False')),
    _fi(_('<b>Annotate:</b>'), None, genSpacer, ''),
    _fi(_('Ignore White Space'), 'annotate.ignorews', genBoolRBGroup,
        _('Ignore white space when comparing lines in the annotate view. '
          'Default: False')),
    _fi(_('Ignore WS Amount'), 'annotate.ignorewsamount', genBoolRBGroup,
        _('Ignore changes in the amount of white space in the annotate view. '
          'Default: False')),
    _fi(_('Ignore Blank Lines'), 'annotate.ignoreblanklines', genBoolRBGroup,
        _('Ignore changes whose lines are all blank in the annotate view. '
          'Default: False')),
    )),

({'name': 'fonts', 'label': _('Fonts'), 'icon': 'preferences-desktop-font'}, (
    _fi(_('Message Font'), 'tortoisehg.fontcomment', genFontEdit,
        _('Font used to display commit messages. Default: monospace 10'),
       globalonly=True),
    _fi(_('Diff Font'), 'tortoisehg.fontdiff', genFontEdit,
        _('Font used to display text differences. Default: monospace 10'),
        globalonly=True),
    _fi(_('Editor Font'), 'tortoisehg.fonteditor', genFontEdit,
        _('Font used for the text editor. Default: monospace 10'),
        globalonly=True),
    _fi(_('ChangeLog Font'), 'tortoisehg.fontlog', genFontEdit,
        _('Font used to display changelog data. Default: monospace 10'),
        globalonly=True),
    _fi(_('Output Font'), 'tortoisehg.fontoutputlog', genFontEdit,
        _('Font used to display output messages. Default: sans 8'),
        globalonly=True),
    )),

({'name': 'extensions', 'label': _('Extensions'), 'icon': 'hg-extensions'}, (
    )),

({'name': 'tools', 'label': _('Tools'), 'icon': 'tools-spanner-hammer'}, (
    )),

({'name': 'hooks', 'label': _('Hooks'), 'icon': 'tools-hooks'}, (
    )),

({'name': 'issue', 'label': _('Issue Tracking'), 'icon': 'edit-file'}, (
    _fi(_('Issue Regex'), 'tortoisehg.issue.regex', genEditCombo,
        _('Defines the regex to match when picking up issue numbers.')),
    _fi(_('Issue Link'), 'tortoisehg.issue.link', genEditCombo,
        _('Defines the command to run when an issue number is recognized. '
          'You may include groups in issue.regex, and corresponding {n} '
          'tokens in issue.link (where n is a non-negative integer). '
          '{0} refers to the entire string matched by issue.regex, '
          'while {1} refers to the first group and so on. If no {n} tokens '
          'are found in issue.link, the entire matched string is appended '
          'instead.')),
    _fi(_('Inline Tags'), 'tortoisehg.issue.inlinetags', genBoolRBGroup,
        _('Show tags at start of commit message.')),
    _fi(_('Mandatory Issue Reference'), 'tortoisehg.issue.linkmandatory',
        genBoolRBGroup,
        _('When committing, require that a reference to an issue be specified. '
          'If enabled, the regex configured in \'Issue Regex\' must find a '
          'match in the commit message.')),
    _fi(_('Issue Tracker Plugin'), 'tortoisehg.issue.bugtraqplugin',
        (genDeferredCombo, findIssueTrackerPlugins),
        _('Configures a COM IBugTraqProvider or IBugTraqProvider2 issue '
          'tracking plugin.'), visible=issuePluginVisible),
    _fi(_('Configure Issue Tracker'), 'tortoisehg.issue.bugtraqparameters',
        genBugTraqEdit,
        _('Configure the selected COM Bug Tracker plugin.'),
        master='tortoisehg.issue.bugtraqplugin', visible=issuePluginVisible),
    _fi(_('Issue Tracker Trigger'), 'tortoisehg.issue.bugtraqtrigger',
        (genDefaultCombo, ['never', 'commit']),
        _('Determines when the issue tracker state will be updated by '
          'TortoiseHg. Valid settings values are:'
          '<ul><li><b>never</b>: Do not update the Issue Tracker state '
          'automatically.'
          '<li><b>commit</b>: Update the Issue Tracker state after a '
          'successful commit.</ul><p>'
          'Default: never'),
        master='tortoisehg.issue.bugtraqplugin', visible=issuePluginVisible),
    _fi(_('Changeset Link'), 'tortoisehg.changeset.link', genEditCombo,
        _('A template string that, when set, turns the revision number and '
          'short hashes that are shown on the revision panels into links.<br>'
          'The template string uses a normal '
          '<a href="https://www.mercurial-scm.org/repo/hg/help/templates">'
          'mercurial template syntax</a>, such as:'
          '<ul>'
          '<li>{node|short} : replaced by the 12 digit revision id.'
          '<li>{rev} : replaced by the revision number.'
          '<li>{gitnode} : if the hg-git extension is enabled, and the repo is '
          'a git clone, this is replaced by the git commit hash.'
          '</ul>'
          'For example, in order to link to Heptapod commit pages you can '
          'set this to:<br>'
          'https://foss.heptapod.net/mercurial/tortoisehg/thg/-/commit/{node}<br>'
          'You can also to link to a GitHub/GitLab repo (provided hg-git is '
          'installed): <br>'
          'https://github.com/torvalds/linux/commit/{gitnode}<br>'
          'https://gitlab.com/tortoisegit/tortoisegit/commit/{gitnode}<br>'
        )),
    )),

({'name': 'reviewboard', 'label': _('Review Board'), 'icon': 'reviewboard',
  'extension': 'reviewboard'}, (
    _fi(_('Server'), 'reviewboard.server', genEditCombo,
        _('Path to review board '
          'example "http://demo.reviewboard.org"')),
    _fi(_('User'), 'reviewboard.user', genEditCombo,
        _('User name to authenticate with review board')),
    _fi(_('Password'), 'reviewboard.password', genPasswordEntry,
        _('Password to authenticate with review board')),
    _fi(_('Server Repository ID'), 'reviewboard.repoid', genEditCombo,
        _('The default repository id for this repo on the review board '
          'server')),
    _fi(_('Target Groups'), 'reviewboard.target_groups', genEditCombo,
        _('A comma separated list of target groups')),
    _fi(_('Target People'), 'reviewboard.target_people', genEditCombo,
        _('A comma separated list of target people')),
    )),

({'name': 'kbfiles', 'label': _('Kiln Bfiles'), 'icon': 'kiln',
  'extension': 'kbfiles'}, (
    _fi(_('Patterns'), 'kilnbfiles.patterns', genEditCombo,
        _('Files with names meeting the specified patterns will be '
          'automatically added as bfiles')),
    _fi(_('Size'), 'kilnbfiles.size', genEditCombo,
        _('Files of at least the specified size (in megabytes) will be added '
          'as bfiles')),
    _fi(_('System Cache'), 'kilnbfiles.systemcache', genPathBrowser,
        _('Path to the directory where a system-wide cache of bfiles will be '
          'stored')),
    )),

({'name': 'simplelock', 'label': _('Simplelock'), 'icon': 'thg-password',
  'extension': 'simplelock'}, (
    _fi(_('Lock Clone'), 'simplelock.repo', genEditCombo,
        _('Location of local clone of organizational lock repository.<p>'
          'This repository must contain a "locked" text file')),
    )),

({'name': 'largefiles', 'label': _('Largefiles'), 'icon': 'kiln',
  'extension': 'largefiles'}, (
    _fi(_('Patterns'), 'largefiles.patterns', genEditCombo,
        _('Files with names meeting the specified patterns will be '
          'automatically added as largefiles')),
    _fi(_('Minimum Size'), 'largefiles.minsize', genEditCombo,
        _('Files of at least the specified size (in megabytes) will be added '
          'as largefiles')),
    _fi(_('User Cache'), 'largefiles.usercache', genPathBrowser,
        _('Path to the directory where a user\'s cache of largefiles will be '
          'stored')),
    )),

({'name': 'projrc', 'label': _('Projrc'), 'icon': 'settings_projrc',
  'extension': 'projrc'}, (
    _fi(_('Require confirmation'), 'projrc.confirm',
        (genDefaultCombo, ['always', 'first', 'never']),
        _('When to ask the user to confirm the update of the local "projrc" '
          'configuration file when the remote projrc file changes. Possible '
          'values are:'
          '<ul><li><b>always</b>: [<i>default</i>] '
          'Always show a confirmation prompt before updating the local '
          '.hg/projrc file.'
          '<li><b>first</b>: Show a confirmation dialog when the repository is '
          'cloned or when a remote projrc file is found for the first time.'
          '<li><b>never</b>: Update the local .hg/projrc file automatically, '
          'without requiring any user confirmation.</ul>')),
    _fi(_('Servers'), 'projrc.servers', genEditCombo,
        _('List of Servers from which "projrc" configuration files must be '
          'pulled. Set it to "*" to pull from all servers. Set it to "default" '
          'to pull from the default sync path. '
          'Default is pull from NO servers.')),
    _fi(_('Include'), 'projrc.include', genEditCombo,
        _('List of settings that will be pulled from the project configuration '
          'file. Default is include NO settings.')),
    _fi(_('Exclude'), 'projrc.exclude', genEditCombo,
        _('List of settings that will NOT be pulled from the project '
          'configuration file. '
          'Default is exclude none of the included settings.')),
    _fi(_('Update on incoming'), 'projrc.updateonincoming',
        (genDefaultCombo, ['never', 'prompt', 'auto']),
        _('Let the user update the projrc on incoming:'
          '<ul><li><b>never</b>: [<i>default</i>] '
          'Show whether the remote projrc file has changed, '
          'but do not update (nor ask to update) the local projrc file.'
          '<li><b>prompt</b>: Look for changes to the projrc file. '
          'If there are changes _always_ show a confirmation prompt, '
          'asking the user if it wants to update its local projrc file.'
          '<li><b>auto</b>: Look for changes to the projrc file. '
          'Use the value of the "projrc.confirm" configuration key to '
          'determine whether to show a confirmation dialog or not '
          'before updating the local projrc file.</ul><p>'
          'Default: never')),
    )),

({'name': 'gnupg', 'label': _('GnuPG'), 'icon': 'gnupg',
  'extension': 'gpg'}, (
    _fi(_('Command'), 'gpg.cmd', (genEditableDeferredCombo, findGpg),
        _('Specify the path to GPG. Default: gpg')),
    _fi(_('Key ID'), 'gpg.key', genEditCombo,
        _('GPG key ID associated with user. Default: None (leave blank)')),
    )),
)

CONF_GLOBAL = 0
CONF_REPO   = 1

class SettingsDialog(QDialog):
    'Dialog for editing Mercurial.ini or hgrc'
    def __init__(self, configrepo=False, focus=None, parent=None, root=None):
        QDialog.__init__(self, parent)
        self.setWindowTitle(_('TortoiseHg Settings'))
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
                            | Qt.WindowType.WindowMaximizeButtonHint)
        self.setWindowIcon(qtlib.geticon('thg-repoconfig'))

        if not hasattr(wconfig.config(), 'write'):
            qtlib.ErrorMsgBox(_('Iniparse package not found'),
                         _("Can't change settings without iniparse package - "
                           'view is readonly.'), parent=self)
            print('Please install https://github.com/candlepin/python-iniparse')

        if not focus:
            focus = qtlib.readString(QSettings(), 'settings/lastpage', 'log')

        layout = QVBoxLayout()
        self.setLayout(layout)

        s = QSettings()
        self.settings = s
        self.restoreGeometry(qtlib.readByteArray(s, 'settings/geom'))

        def username():
            name = util.username()
            if name:
                return hglib.tounicode(name)
            name = os.environ.get('USERNAME')
            if name:
                return hglib.tounicode(name)
            return _('User')

        self._activeformidx = configrepo and CONF_REPO or CONF_GLOBAL
        self.conftabs = QTabWidget()
        layout.addWidget(self.conftabs)
        if qtlib.IS_RETINA:
            self.conftabs.setIconSize(qtlib.barRetinaIconSize())
        utab = SettingsForm(
            rcpath=[hglib.tounicode(rcpath) for rcpath in hglib.userrcpath()],
            focus=focus)
        self.conftabs.addTab(utab, qtlib.geticon('thg-userconfig'),
                             _("%s's global settings") % username())
        utab.restartRequested.connect(self._pushRestartRequest)

        try:
            if root is None:
                root = paths.find_root_bytes()
            if root:
                repo = thgrepo.repository(hglib.loadui(), root)
            else:
                repo = None
        except error.RepoError:
            repo = None
            if configrepo:
                uroot = hglib.tounicode(root)
                qtlib.ErrorMsgBox(_('No repository found'),
                                  _('no repo at ') + uroot, parent=self)

        if repo:
            repoagent = repo._pyqtobj  # TODO
            if b'projrc' in repo.extensions():
                projrcpath = os.path.join(hglib.tounicode(repo.root),
                                          '.hg', 'projrc')
                if os.path.exists(projrcpath):
                    rtab = SettingsForm(rcpath=[projrcpath], focus=focus,
                                        readonly=True)
                    self.conftabs.addTab(rtab, qtlib.geticon('settings_projrc'),
                                         _('%s project settings (.hg/projrc)')
                                         % repoagent.shortName())
                    rtab.restartRequested.connect(self._pushRestartRequest)

            reporcpath = os.path.join(hglib.tounicode(repo.root), '.hg', 'hgrc')
            rtab = SettingsForm(rcpath=[reporcpath], focus=focus)
            self.conftabs.addTab(rtab, qtlib.geticon('thg-repoconfig'),
                                 _('%s repository settings')
                                 % repoagent.shortName())
            rtab.restartRequested.connect(self._pushRestartRequest)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)
        self.bb = bb

        self._restartreqs = set()
        self.conftabs.setCurrentIndex(self._activeformidx)
        self.conftabs.currentChanged.connect(self._currentFormChanged)

    def isDirty(self):
        return any(self.conftabs.widget(i).isDirty()
                   for i in pycompat.xrange(self.conftabs.count()))

    @pyqtSlot(str)
    def _pushRestartRequest(self, key: str) -> None:
        self._restartreqs.add(key)

    def applyChanges(self):
        results = [self.conftabs.widget(i).applyChanges()
                   for i in pycompat.xrange(self.conftabs.count())]
        if self._restartreqs:
            qtlib.InfoMsgBox(_('Settings'),
                             _('Restart all TortoiseHg applications '
                               'for the following changes to take effect:'),
                             ', '.join(sorted(self._restartreqs)))
            self._restartreqs.clear()
        return all(results)

    def canExit(self):
        if self.isDirty():
            ret = qtlib.CustomPrompt(_('Confirm Exit'),
                            _('Apply changes before exit?'), self,
                            (_('&Yes'), _('&No (discard changes)'),
                         _  ('Cancel')), default=2, esc=2).run()
            if ret == 2:
                return False
            elif ret == 0:
                return self.applyChanges()
        return True

    def accept(self):
        if not self.applyChanges():
            return
        s = self.settings
        s.setValue('settings/geom', self.saveGeometry())
        s.setValue('settings/lastpage', self._getactivepagename())
        s.sync()
        QDialog.accept(self)

    def reject(self):
        if not self.canExit():
            return
        s = self.settings
        s.setValue('settings/geom', self.saveGeometry())
        s.setValue('settings/lastpage', self._getactivepagename())
        s.sync()
        QDialog.reject(self)

    def _getactivepagename(self):
        if self._activeformidx is None:
            return ''
        activeform = self.conftabs.widget(self._activeformidx)
        if not activeform:
            return ''
        return activeform._activepagename

    def _currentFormChanged(self, idx):
        activepagename = self._getactivepagename()
        if activepagename:
            self.conftabs.widget(idx).focusPage(activepagename)
        self._activeformidx = idx

class SettingsForm(QWidget):
    """Widget for each settings file"""

    restartRequested = pyqtSignal(str)

    def __init__(self, rcpath, focus=None, parent=None, readonly=False):
        super().__init__(parent)

        # If forcereadonly is false, the settings form will be readonly
        # if the corresponding ini file is readonly
        self.forcereadonly = readonly

        self.rcpath = rcpath

        layout = QVBoxLayout()
        self.setLayout(layout)

        tophbox = QHBoxLayout()
        layout.addLayout(tophbox)

        self.fnedit = QLineEdit()
        self.fnedit.setReadOnly(True)
        self.fnedit.setFrame(False)
        self.fnedit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        p = self.fnedit.palette()
        p.setColor(QPalette.ColorRole.Base, Qt.GlobalColor.transparent)
        self.fnedit.setPalette(p)
        edit = QPushButton(_('Edit File'))
        edit.clicked.connect(self.editClicked)
        self.editbtn = edit
        reload = QPushButton(_('Reload'))
        reload.clicked.connect(self.reloadClicked)
        self.reloadbtn = reload
        tophbox.addWidget(QLabel(_('Settings File:')))
        tophbox.addWidget(self.fnedit)
        tophbox.addWidget(edit)
        tophbox.addWidget(reload)

        bothbox = QHBoxLayout()
        layout.addLayout(bothbox, 8)
        pageList = QListWidget()
        pageList.setResizeMode(QListView.ResizeMode.Fixed)
        if qtlib.IS_RETINA:
            pageList.setIconSize(qtlib.listviewRetinaIconSize())
        stack = QStackedWidget()
        bothbox.addWidget(pageList, 0)
        bothbox.addWidget(stack, 1)
        pageList.currentRowChanged.connect(self.activatePage)

        self.pages = {}
        self.stack = stack
        self.pageList = pageList
        self.pageListIndexToStack = {}

        desctext = QTextBrowser()
        desctext.setOpenExternalLinks(True)
        layout.addWidget(desctext, 2)
        self.desctext = desctext

        self.settings = QSettings()

        # add page items to treeview
        for meta, info in INFO:
            if 'extension' in meta and not hasExtension(meta['extension']):
                continue
            if isinstance(meta['icon'], str):
                icon = qtlib.geticon(meta['icon'])
            else:
                icon = self.style().standardIcon(meta['icon'])
            item = QListWidgetItem(icon, meta['label'])
            pageList.addItem(item)

        self.refresh()

        if not self.focusField(focus):
            # The selected setting may not exist
            # (e.g. if an extension has been disabled)
            self.pageList.setCurrentRow(0)

    @pyqtSlot(int)
    def activatePage(self, index):
        if index >= 0:
            self._activepagename = INFO[index][0]['name']

        stackindex = self.pageListIndexToStack.get(index, -1)
        if stackindex >= 0:
            self.stack.setCurrentIndex(stackindex)
            return

        item = self.pageList.item(index)
        for data in INFO:
            if item.text() == data[0]['label']:
                meta, info = data
                break

        stackindex = self.stack.count()
        pagename = meta['name']
        page = self.createPage(pagename, info)
        self.refreshPage(page)
        # better to call stack.addWidget() here, not by fillFrame()
        assert self.stack.count() > stackindex, 'page must be added to stack'
        self.pageListIndexToStack[index] = stackindex
        self.stack.setCurrentIndex(stackindex)

    def editClicked(self):
        'Open internal editor in stacked widget'
        if self.isDirty():
            ret = qtlib.CustomPrompt(_('Confirm Save'),
                    _('Save changes before editing?'), self,
                    (_('&Save'), _('&Discard'), _('Cancel')),
                    default=2, esc=2).run()
            if ret == 0:
                self.applyChanges()
            elif ret == 2:
                return
        qscilib.fileEditor(self.fn, foldable=True)
        self.refresh()

    def refresh(self, *args):
        # refresh config values
        self.ini = self.loadIniFile(self.rcpath)  # TODO: type this attr
        self.readonly = self.forcereadonly or not (hasattr(self.ini, 'write')
                                and os.access(self.fn, os.W_OK))
        self.stack.setDisabled(self.readonly)
        self.fnedit.setText(self.fn)
        for page in self.pages.values():
            self.refreshPage(page)

    def refreshPage(self, page):
        name, info, widgets = page
        if name == 'extensions':
            for row, w in enumerate(widgets):
                key = w.opts['label']
                for fullkey in (key, 'hgext.%s' % key, 'hgext/%s' % key):
                    val = self.readCPath('extensions.' + fullkey)
                    if val is not None:
                        break
                if val is None:
                    curvalue = False
                elif val.startswith(b'!'):
                    curvalue = False
                else:
                    curvalue = True
                w.setValue(curvalue)
                if val is None:
                    w.opts['cpath'] = 'extensions.' + key
                else:
                    w.opts['cpath'] = 'extensions.' + fullkey
            self.validateextensions()
        elif name == 'tools':
            self.toolsFrame.refresh()
        elif name == 'hooks':
            self.hooksFrame.refresh()
        else:
            for row, e in enumerate(info):
                if not e.cpath:
                    continue  # a dummy field
                curvalue = self.readCPath(e.cpath)
                widgets[row].setValue(curvalue)

    def isDirty(self):
        if self.readonly:
            return False
        for name, info, widgets in self.pages.values():
            for w in widgets:
                if w.isDirty():
                    return True
        return False

    def reloadClicked(self):
        if self.isDirty():
            d = QMessageBox.question(self, _('Confirm Reload'),
                            _('Unsaved changes will be lost.\n'
                            'Do you want to reload?'),
                            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            if d != QMessageBox.StandardButton.Ok:
                return
        self.refresh()

    def focusPage(self, focuspage):
        'Set change page to focuspage'
        for i, (meta, info) in enumerate(INFO):
            if meta['name'] == focuspage:
                self._activepagename = meta['name']
                self.pageList.setCurrentRow(i)
                return True
        return False

    def focusField(self, focusfield):
        'Set page and focus to requested datum'
        if not focusfield:
            return False
        if focusfield.find('.') < 0:
            return self.focusPage(focusfield)
        for i, (meta, info) in enumerate(INFO):
            for n, e in enumerate(info):
                if e.cpath == focusfield:
                    self.pageList.setCurrentRow(i)
                    QTimer.singleShot(0, lambda:
                            self.pages[meta['name']][2][n].setFocus())
                    return True
        return False

    def fillFrame(self, info):
        widgets = []
        frame = QFrame()
        form = QFormLayout()
        form.setContentsMargins(5, 5, 0, 5)
        frame.setLayout(form)
        self.stack.addWidget(frame)

        for e in info:
            opts = {'label': e.label, 'cpath': e.cpath, 'tooltip': e.tooltip,
                    'master': e.master, 'settings':self.settings}
            if isinstance(e.values, tuple):
                func = e.values[0]
                w = func(opts, e.values[1])
            else:
                func = e.values
                w = func(opts)
            rcpath = [hglib.tounicode(rcpath) for rcpath in hglib.userrcpath()]
            if e.globalonly:
                w.setEnabled(self.rcpath == rcpath)
            elif e.noglobal:
                w.setEnabled(self.rcpath != rcpath)
            lbl = QLabel(e.label)
            lbl.setToolTip(e.tooltip)
            widgets.append(w)
            if e.isVisible():
                lbl.installEventFilter(self)
                w.installEventFilter(self)
                form.addRow(lbl, w)

        # assign the master to widgets that have a master
        for w in widgets:
            if w.opts['master'] is not None:
                for dep in widgets:
                    if dep.opts['cpath'] == w.opts['master']:
                        w.opts['master'] = dep
        return widgets

    def fillExtensionsFrame(self):
        widgets = []
        frame = QFrame()
        grid = QGridLayout()
        grid.setContentsMargins(5, 5, 0, 5)
        frame.setLayout(grid)
        self.stack.addWidget(frame)
        allexts = hglib.allextensions()
        allextslist = list(allexts)
        MAXCOLUMNS = 3
        maxrows = (len(allextslist) + MAXCOLUMNS - 1) // MAXCOLUMNS
        i = 0
        extsinfo = ()
        for i, name in enumerate(sorted(allexts)):
            tt = hglib.tounicode(allexts[name])
            opts = {'label':name, 'cpath':'extensions.' + name, 'tooltip':tt}
            w = genCheckBox(opts)
            w.installEventFilter(self)
            w.clicked.connect(self.validateextensions)

            row, col = i // maxrows, i % maxrows
            grid.addWidget(w, col, row)
            widgets.append(w)
        return extsinfo, widgets

    def fillToolsFrame(self):
        self.toolsFrame = frame = customtools.ToolsFrame(self.ini, parent=self)
        self.stack.addWidget(frame)
        return (), [frame]

    def fillHooksFrame(self):
        self.hooksFrame = frame = customtools.HooksFrame(self.ini, parent=self)
        self.stack.addWidget(frame)
        return (), [frame]

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Enter, QEvent.Type.FocusIn):
            self.desctext.setHtml(obj.toolTip())
        if event.type() == QEvent.Type.ToolTip:
            return True  # tooltip is shown in self.desctext
        return False

    def createPage(self, name, info):
        if name == 'extensions':
            extsinfo, widgets = self.fillExtensionsFrame()
            self.pages[name] = name, extsinfo, widgets
        elif name == 'tools':
            toolsinfo, widgets = self.fillToolsFrame()
            self.pages[name] = name, toolsinfo, widgets
        elif name == 'hooks':
            hooksinfo, widgets = self.fillHooksFrame()
            self.pages[name] = name, hooksinfo, widgets
        else:
            widgets = self.fillFrame(info)
            self.pages[name] = name, info, widgets
        return self.pages[name]

    def readCPath(self, cpath):
        'Retrieve a value from the parsed config file'
        # Presumes single section/key level depth
        section, key = pycompat.sysbytes(cpath).split(b'.', 1)
        return self.ini.get(section, key)

    def loadIniFile(self, rcpath: List[str]) -> IniConfig:
        for fn in rcpath:
            if os.path.exists(fn):
                break
        else:
            for fn in rcpath:
                # Try to create a file from rcpath
                try:
                    with open(fn, 'w') as f:
                        f.write('# Generated by TortoiseHg settings dialog\n')
                    break
                except OSError:
                    pass
            else:
                qtlib.WarningMsgBox(_('Unable to create a Mercurial.ini file'),
                       _('Insufficient access rights, reverting to read-only '
                         'mode.'), parent=self)
                from mercurial import config
                self.fn = rcpath[0]
                return config.config()
        self.fn = fn
        return wconfig.readfile(hglib.fromunicode(self.fn))

    def recordNewValue(self, cpath, newvalue):
        """Set the given value to ini; returns True if changed"""
        # 'newvalue' is in local encoding
        section, key = pycompat.sysbytes(cpath).split(b'.', 1)
        if newvalue == self.ini.get(section, key):
            return False
        if newvalue is None:
            try:
                del self.ini[section][key]
            except KeyError:
                pass
        else:
            self.ini.set(section, key, newvalue)
        return True

    def applyChanges(self):
        if self.readonly:
            return True  # safely skipped because all fields are disabled

        for name, info, widgets in self.pages.values():
            if name == 'extensions':
                self.applyChangesForExtensions()
            elif name == 'tools':
                self.applyChangesForTools()
            elif name == 'hooks':
                self.applyChangesForHooks()
            else:
                for row, e in enumerate(info):
                    if not e.cpath:
                        continue  # a dummy field
                    newvalue = widgets[row].value()
                    changed = self.recordNewValue(e.cpath, newvalue)
                    if changed and e.restartneeded:
                        self.restartRequested.emit(e.label)

        try:
            wconfig.writefile(self.ini, hglib.fromunicode(self.fn))
            return True
        except OSError as e:
            qtlib.WarningMsgBox(_('Unable to write configuration file'),
                                hglib.exception_str(e), parent=self)
            return False

    def applyChangesForExtensions(self):
        assert 'extensions' in self.pages
        emitChanged = False
        enabledexts = hglib.enabledextensions()
        for chk in self.pages['extensions'][2]:
            if (not emitChanged) and chk.isDirty():
                self.restartRequested.emit(_('Extensions'))
                emitChanged = True
            name = chk.opts['label']
            cpath = chk.opts['cpath']
            section, key = pycompat.sysbytes(cpath).split(b'.', 1)
            newvalue = chk.value()
            if newvalue and (name in enabledexts):
                continue    # unchanged
            if newvalue:
                self.ini.set(section, key, b'')
            else:
                try:
                    del self.ini[section][key]
                except KeyError:
                    pass

    @pyqtSlot()
    def validateextensions(self):
        enabledexts = hglib.enabledextensions()
        selectedexts = {chk.opts['label']
                           for chk in self.pages['extensions'][2]
                           if chk.isChecked()}
        invalidexts = hglib.validateextensions(selectedexts)

        def getinival(cpath):
            if b'extensions' not in self.ini:
                return None
            sect, key = pycompat.sysbytes(cpath).split(b'.', 1)
            try:
                return self.ini.get(sect, key)
            except KeyError:
                pass

        def changable(name, cpath):
            curval = getinival(cpath)
            if curval not in (b'', None):
                # enabled or unspecified, official extensions only
                return False
            elif name in enabledexts and curval is None:
                # re-disabling ext is not supported
                return False
            elif name in invalidexts and name not in selectedexts:
                # disallow to enable bad exts, but allow to disable it
                return False
            else:
                return True

        allexts = hglib.allextensions()
        for chk in self.pages['extensions'][2]:
            name = chk.opts['label']
            if not changable(name, chk.opts['cpath']):
                chk.setEnabled(False)
                cpath = chk.opts['cpath']
                sect, key = pycompat.sysbytes(cpath).split(b'.', 1)
                if hglib.loadui().config(sect, key, None) is not None:
                    chk.setValue(True)
                    # XXX: setValue(False) was called in refreshPage() prior to
                    #      this.  SettingsCheckBox only sets its value if the
                    #      current value is None, but this needs to be forced on
                    #      because the dirty check looks at the checkbox state
                    #      and will flag the item as dirty otherwise.
                    chk.curvalue = True
            else:
                chk.setEnabled(True)
            invalmsg = invalidexts.get(name)
            if invalmsg:
                invalmsg = invalmsg.decode('utf-8')
            chk.setToolTip(invalmsg or hglib.tounicode(allexts[name]))

    def applyChangesForTools(self):
        if self.toolsFrame.applyChanges(self.ini):
            self.restartRequested.emit(_('Tools'))

    def applyChangesForHooks(self):
        if self.hooksFrame.applyChanges(self.ini):
            self.restartRequested.emit(_('Hooks'))
