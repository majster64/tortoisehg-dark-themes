# shortcutregistry.py - manages user-configurable shortcuts
#
# Copyright 2020 Yuya Nishihara <yuya@tcha.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import collections
import typing
import weakref

from .qtcore import (
    QSettings,
)

from .qtgui import (
    QAction,
    QKeySequence,
)

from . import (
    qtlib,
)

from ..util import (
    hglib,
)

from ..util.i18n import _

if typing.TYPE_CHECKING:
    from typing import (
        Dict,
        Iterable,
        List,
        Text,
        Tuple,
        Union,
    )

    _KeySequencesD = Union[
        QKeySequence.StandardKey,
        str,
        Tuple[str, QKeySequence.StandardKey],  # std key with custom modifier
    ]
    _KeySequencesDefs = Union[_KeySequencesD, List[_KeySequencesD], None]

_ACTIONS_TABLE: Dict[str, Tuple[str, _KeySequencesDefs]] = {
    # MQ operations:
    # TODO: merge or give better name to deletePatches_, pushMovePatch_,
    # and renamePatch_
    'PatchQueue.deletePatches': (_('&Delete Patches...'), 'Del'),
    'PatchQueue.finishRevision': (_('&Finish Patch'), None),
    'PatchQueue.foldPatches': (_('Fold patches...'), None),
    'PatchQueue.goToPatch': (_('Go &to Patch'), None),
    'PatchQueue.guardPatch': (_('Set &Guards...'), None),
    'PatchQueue.importRevision': (_('Import to &MQ'), None),
    'PatchQueue.launchOptionsDialog': (_('MQ &Options'), None),
    'PatchQueue.popAllPatches': (_('Pop all'), None),
    'PatchQueue.popOnePatch': (_('Pop'), None),
    'PatchQueue.popPatch': (_('&Unapply Patch'), None),
    'PatchQueue.pushAllPatches': (_('Push all', 'MQ QPush'), None),
    'PatchQueue.pushExactPatch': (_('Apply onto original parent'), None),
    'PatchQueue.pushMovePatch': (_('&Apply Only This Patch'), 'Ctrl+Return'),
    'PatchQueue.pushOnePatch': (_('Push', 'MQ QPush'), None),
    'PatchQueue.pushPatch': (_('Apply patch'), None),
    'PatchQueue.renamePatch': (_('Re&name Patch...'), 'F2'),

    # RepoView/RepoWidget navigation, etc.
    'RepoView.changePhaseMenu': (_('Change &Phase to'), None),
    'RepoView.filterByRevisionsMenu': (_('Filter b&y'), None),
    'RepoView.goBack': (_('Back'), QKeySequence.StandardKey.Back),
    'RepoView.goForward': (_('Forward'), QKeySequence.StandardKey.Forward),
    'RepoView.goToCommonAncestor': (_('Goto common ancestor'), None),
    'RepoView.goToRevision': (_('&Goto Revision...'), 'Ctrl+/'),
    'RepoView.goToWorkingParent': (_('Go to current revision'), 'Ctrl+.'),
    'RepoView.loadAllRevisions': (_('Load &All Revisions'), 'Shift+Ctrl+A'),
    'RepoView.setHistoryColumns': (_('C&hoose Log Columns...'), None),
    'RepoView.showFilterBar': (_('&Filter Toolbar'), 'Ctrl+S'),

    # Repository-level operations:
    'Repository.archiveDagRangeRevisions': (_('Archive DAG Range...'), None),
    'Repository.archiveRevision': (_('&Archive...'), None),
    'Repository.backoutToRevision': (_('&Backout...'), None),
    'Repository.bisect': (_('&Bisect...'), None),
    'Repository.bisectBadGoodRevisionsPair': (_('Bisect - Bad, Good...'), None),
    'Repository.bisectGoodBadRevisionsPair': (_('Bisect - Good, Bad...'), None),
    'Repository.bookmarkRevision': (_('Boo&kmark...'), None),
    'Repository.browseRevision': (_('Bro&wse at Revision'), None),
    'Repository.bundleDagRangeRevisions': (_('Bundle DAG Range...'), None),
    'Repository.bundleRevisions': (_('&Bundle Rev and Descendants...'), None),
    'Repository.closeRevision': (_('&Close Branch'), None),
    'Repository.compressRevisionsPair': (_('Compress History...'), None),
    'Repository.copyGitHash': (_('Full &Git Hash'), None),
    'Repository.copyHash': (_('Full &Hash'), None),
    'Repository.copyPatch': (_('&Copy Patch'), None),
    'Repository.copyShortGitHash': (_('Short Git Hash'), None),
    'Repository.copyShortHash': (_('Short Hash'), None),
    'Repository.emailDagRangeRevisions': (_('Email DAG Range...'), None),
    'Repository.emailRevisions': (_('&Email Patch...'), None),
    'Repository.exportDagRangeRevisions': (_('Export DAG Range...'), None),
    'Repository.exportDiff': (_('Export Diff...'), None),
    'Repository.exportRevisions': (_('E&xport Patch...'), None),
    'Repository.graftRevisions': (_('&Graft to Local...'), None),
    'Repository.import': (_('&Import Patches...'), None),
    'Repository.incoming': (_('&Incoming'), 'Ctrl+Shift+,'),
    'Repository.lockFile': (_('&Lock File...'), None),
    'Repository.merge': (_('&Merge...'), None),
    'Repository.mergeWithRevision': (_('&Merge with Local...'), None),
    'Repository.outgoing': (_('&Outgoing'), 'Ctrl+Shift+.'),
    'Repository.pickRevision': (_('Pick...'), None),
    'Repository.pruneRevisions': (_('&Prune...'), None),
    'Repository.pull': (_('&Pull'), None),
    'Repository.pullAllTabs': (_('Pull &All Tabs'), None),
    'Repository.pullToRevision': (_('Pull to here...'), None),
    'Repository.purge': (_('&Purge...'), None),
    'Repository.push': (_('P&ush'), None),
    'Repository.pushAll': (_('Push &All'), None),
    'Repository.pushAllTabs': (_('Push A&ll Tabs'), None),
    'Repository.pushBranch': (_('Push Selected &Branch'), None),
    'Repository.pushToRevision': (_('Push to &Here'), None),
    'Repository.rebaseRevision': (_('&Rebase...'), None),
    'Repository.rebaseSourceDestRevisionsPair': (_('Rebase...'), None),
    'Repository.recover': (_('Re&cover'), None),
    'Repository.resolve': (_('&Resolve...'), None),
    'Repository.revertToRevision': (_('Revert &All Files...'), None),
    'Repository.rollback': (_('R&ollback/Undo...'), 'Ctrl+U'),
    'Repository.sendToPhabricator': (_('Post to Phabricator...'), None),
    'Repository.sendToReviewBoard': (_('Post to Re&view Board...'), None),
    'Repository.shelve': (_('&Shelve...'), None),
    'Repository.signRevision': (_('Sig&n...'), None),
    'Repository.stripRevision': (_('&Strip...'), None),
    'Repository.syncBookmarks': (_('&Sync Bookmarks...'), None),
    'Repository.tagRevision': (_('&Tag...'), None),
    'Repository.topicRevision': (_('Top&ic...'), None),
    'Repository.unbundle': (_('U&nbundle...'), None),
    'Repository.update': (_('&Update...'), None),
    'Repository.updateToRevision': (_('&Update...'), None),
    'Repository.verify': (_('&Verify'), None),
    'Repository.visualDiff': (_('&Diff to Parent'), None),
    'Repository.visualDiffRevisionsPair': (_('Visual Diff...'), None),
    'Repository.visualDiffToLocal': (_('Diff to &Local'), None),

    # Workbench actions:
    'Workbench.abort': (_('Cancel'), None),
    'Workbench.about': (_("&About TortoiseHg"), None),
    'Workbench.aboutQt': (_('About &Qt'), None),
    'Workbench.cloneRepository': (_('Clon&e Repository...'),
                                  ('Shift', QKeySequence.StandardKey.New)),
    'Workbench.closeRepository': (_("&Close Repository"), QKeySequence.StandardKey.Close),
    'Workbench.explorerHelp': (_('E&xplorer Help'), None),
    'Workbench.help': (_('&Help'), None),
    'Workbench.newRepository': (_('&New Repository...'), QKeySequence.StandardKey.New),
    'Workbench.newWorkbench': (_('New &Workbench'), 'Shift+Ctrl+W'),
    'Workbench.openFileManager': (_('E&xplore'), 'Ctrl+Shift+X'),
    'Workbench.openReadme': (_('&Readme'), 'Ctrl+F1'),
    'Workbench.openRepository': (_('&Open Repository...'), QKeySequence.StandardKey.Open),
    'Workbench.openSettings': (_('&Settings'), QKeySequence.StandardKey.Preferences),
    'Workbench.openShortcutSettings': (_('S&hortcut Settings'), None),
    'Workbench.openTerminal': (_('&Terminal'), 'Ctrl+Shift+T'),
    'Workbench.quit': (_('E&xit'), QKeySequence.StandardKey.Quit),
    'Workbench.refresh': (_('&Refresh'), [QKeySequence.StandardKey.Refresh,
                                          'Ctrl+F5']),  # Ctrl+ to ignore status
    'Workbench.refreshTaskTabs': (_('Refresh &Task Tab'),
                                  ('Shift', QKeySequence.StandardKey.Refresh)),
    'Workbench.showConsole': (_('Show Conso&le'), 'Ctrl+L'),
    'Workbench.showPatchQueue': (_('Show &Patch Queue'), None),
    'Workbench.showRepoRegistry': (_('Sh&ow Repository Registry'),
                                   'Ctrl+Shift+O'),
    'Workbench.webServer': (_('&Web Server'), None),
}

_SETTINGS_GROUP = 'KeyboardShortcuts'

_TOOLTIP_SHORTCUT_START_TAG = '<span class="shortcut" style="color: gray">'
_TOOLTIP_SHORTCUT_END_TAG = '</span>'


def _parseDefaultKeySequences(data: _KeySequencesDefs) -> List[QKeySequence]:
    if data is None:
        return []
    if isinstance(data, list):
        seqs = []
        for d in data:
            seqs.extend(_parseDefaultKeySequences(d))
        return seqs
    if hglib.isbasestring(data):
        return [QKeySequence(data, QKeySequence.SequenceFormat.PortableText)]
    if isinstance(data, tuple):
        mod, key = data
        kstr = QKeySequence(key).toString(QKeySequence.SequenceFormat.PortableText)
        return [QKeySequence('%s+%s' % (mod, kstr), QKeySequence.SequenceFormat.PortableText)]
    return QKeySequence.keyBindings(data)

def _parseUserKeySequences(data: List[str]) -> List[QKeySequence]:
    return [QKeySequence(s, QKeySequence.SequenceFormat.PortableText) for s in data]

def _formatKeySequences(seqs: List[QKeySequence]) -> List[str]:
    return [b.toString(QKeySequence.SequenceFormat.PortableText) for b in seqs]

def _formatToolTip(label: str,
                   toolTip: str,
                   seqs: List[QKeySequence]) -> str:
    """Build tool tip from current label/toolTip and shortcuts

    >>> stext = '%s(%s)%s' % (_TOOLTIP_SHORTCUT_START_TAG, 'A',
    ...                       _TOOLTIP_SHORTCUT_END_TAG)
    >>> _formatToolTip('Label', '', [])
    'Label'
    >>> _formatToolTip('Label', '', [QKeySequence('B')])
    'Label <span class="shortcut" style="color: gray">(B)</span>'
    >>> _formatToolTip('Label', 'ToolTip', [])
    'ToolTip'
    >>> _formatToolTip('Label', 'ToolTip %s' % stext, [])
    'ToolTip'
    >>> _formatToolTip('Label', 'ToolTip %s' % stext, [QKeySequence('B')])
    'ToolTip <span class="shortcut" style="color: gray">(B)</span>'
    >>> _formatToolTip('Label', 'Tool\\nTip', [QKeySequence('B')])
    'Tool\\nTip'
    """
    if toolTip:
        i = toolTip.find(_TOOLTIP_SHORTCUT_START_TAG)
        if i >= 0:
            label = toolTip[:i].rstrip()
        else:
            label = toolTip

    if not seqs:
        return label
    if '\n' in label:
        # multi-line toolTip can't be decorated by HTML tag
        return label
    return '%s %s(%s)%s' % (
        label,
        _TOOLTIP_SHORTCUT_START_TAG,
        seqs[0].toString(QKeySequence.SequenceFormat.NativeText),
        _TOOLTIP_SHORTCUT_END_TAG)


class ShortcutRegistry:
    """Dictionary of user-configurable shortcuts

    This is pure data object. Use ActionRegistry to manage both shortcuts
    and QAction instances.
    """

    _userKeys: Dict[str, List[QKeySequence]]

    def __init__(self):
        self._defaultKeys = {name: _parseDefaultKeySequences(seq)
                             for name, (_label, seq) in _ACTIONS_TABLE.items()}
        self._userKeys = {}

    def copyShortcuts(self) -> ShortcutRegistry:
        """Creates new registry by copying the shortcut configuration"""
        registry = ShortcutRegistry()
        registry._userKeys = self._userKeys.copy()
        return registry

    def updateShortcuts(self, registry: ShortcutRegistry) -> None:
        """Copies shortcut configuration back from the given registry"""
        self._userKeys = registry._userKeys.copy()

    def readSettings(self):
        """Reads user key bindings from settings file"""
        self._userKeys.clear()
        qs = QSettings()
        qs.beginGroup(_SETTINGS_GROUP)
        for name in self.allNames():
            if not qs.contains(name):
                continue
            self._userKeys[name] = _parseUserKeySequences(
                qtlib.readStringList(qs, name))
        qs.endGroup()

    def saveSettings(self):
        """Saves current user key bindings to settings file"""
        qs = QSettings()
        qs.beginGroup(_SETTINGS_GROUP)
        for name in self.allNames():
            if name in self._userKeys:
                qs.setValue(name, _formatKeySequences(self._userKeys[name]))
            else:
                qs.remove(name)
        qs.endGroup()

    def allNames(self) -> List[str]:
        """List of all known action names"""
        return sorted(_ACTIONS_TABLE)

    def actionLabel(self, name: str) -> str:
        label, _default = _ACTIONS_TABLE[name]
        return label

    def defaultKeySequences(self, name: str) -> List[QKeySequence]:
        return self._defaultKeys[name]

    def keySequences(self, name: str) -> List[QKeySequence]:
        if name in self._userKeys:
            return self._userKeys[name]
        return self.defaultKeySequences(name)

    def hasUserKeySequences(self, name: str) -> bool:
        assert name in _ACTIONS_TABLE, name
        return name in self._userKeys

    def setUserKeySequences(self,
                            name: str,
                            seqs: List[QKeySequence]) -> None:
        """Stores new shortcuts of the specified action

        To remove the shortcuts, specify []. To restore the default key
        sequences, use unsetUserKeySequences().

        You'll also want to call saveSettings() and applyChangesToActions().
        """
        assert name in _ACTIONS_TABLE, name
        self._userKeys[name] = seqs

    def unsetUserKeySequences(self, name: str) -> None:
        """Restores the shortcuts of the specified action to default

        You'll also want to call saveSettings() and applyChangesToActions().
        """
        assert name in _ACTIONS_TABLE, name
        self._userKeys.pop(name, None)


class ActionRegistry(ShortcutRegistry):
    """Manages user-configurable shortcuts and QAction instances"""

    _actionsMap: Dict[str, weakref.WeakSet[QAction]]

    def __init__(self):
        super().__init__()
        # QAction instances are owned by QWidget and will be destroyed when
        # C++ object is deleted. Since QAction will be instantiated per
        # context (e.g. window), more than one instances may be registered
        # to the same slot.
        self._actionsMap = collections.defaultdict(weakref.WeakSet)

    def applyChangesToActions(self):
        """Applies changes to registered QAction instances"""
        for name, actions in self._actionsMap.items():
            self._updateActions(name, actions)

    def registerAction(self, name: str, action: QAction) -> None:
        """Register QAction instance to be updated on applyChangesToActions()"""
        assert name in _ACTIONS_TABLE, name
        self._actionsMap[name].add(action)
        self._updateActions(name, [action])

    def _updateActions(self, name: str, actions: Iterable[QAction]) -> None:
        label = self.actionLabel(name)
        seqs = self.keySequences(name)
        for a in actions:
            a.setText(label)
            qtlib.setContextMenuShortcuts(a, seqs)
            if seqs or a.toolTip():
                a.setToolTip(_formatToolTip(label, a.toolTip(), seqs))
