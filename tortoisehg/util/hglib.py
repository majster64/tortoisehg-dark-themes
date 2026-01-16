# hglib.py - Mercurial API wrappers for TortoiseHg
#
# Copyright 2007 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import glob
import io
import os
import re
import shlex
import sys
import time
import typing

from typing import (
    cast,
    Iterable,
    List,
    Union,
)

from hgext import mq as mqmod
from mercurial import (
    cmdutil,
    config,
    context,
    dispatch as dispatchmod,
    encoding,
    error,
    extensions,
    fancyopts,
    filemerge,
    filesetlang,
    mdiff,
    mergestate as mergestatemod,
    patch as patchmod,
    pathutil,
    pycompat,
    revset as revsetmod,
    revsetlang,
    scmutil,
    state as statemod,
    subrepo,
    subrepoutil,
    ui as uimod,
    util,
)
from mercurial.utils import (
    dateutil,
    stringutil,
)
from mercurial.node import nullrev

from . import (
    hgversion as hgversionmod,
    paths,
)
from .i18n import (
    _ as _gettext,
    ngettext as _ngettext,
)

# pytype: disable=import-error
try:
    from mercurial.configuration import rcutil
    userrcpath = rcutil.userrcpath  # defeat demandimport
except (ImportError, AttributeError):
    # hg < 7.0 (0a81f3ef054c)
    from mercurial import rcutil
    userrcpath = rcutil.userrcpath

    if typing.TYPE_CHECKING:
        def userrcpath() -> list[bytes]:
            raise NotImplementedError
# pytype: enable=import-error

if typing.TYPE_CHECKING:
    from typing import (
        AbstractSet,
        Callable,
        Dict,
        Optional,
        Text,
        Tuple,
        overload,
    )
    from mercurial import (
        context,
        localrepo,
    )
    from .typelib import (
        DiffTools,
        HgContext,
        IniConfig,
    )

nullsubrepostate = subrepoutil.nullstate
_encoding = pycompat.sysstr(encoding.encoding)
_fallbackencoding = pycompat.sysstr(encoding.fallbackencoding)
hgversion = pycompat.sysstr(hgversionmod.hgversion)

# extensions which can cause problem with TortoiseHg
_extensions_blacklist = (
    b'blackbox',  # mucks uimod.ui (hg 851c41a21869, issue #4489)
    b'color',
    b'pager',
    b'progress',
    b'zeroconf',
)

extractpatch = patchmod.extract
tokenizerevspec = revsetlang.tokenize

# TODO: use unicode version globally
def _(message: str, context: str = '') -> bytes:
    return _gettext(message, context).encode('utf-8')
def ngettext(singular: str, plural: str, n: int) -> bytes:
    return _ngettext(singular, plural, n).encode('utf-8')

if typing.TYPE_CHECKING:
    @overload
    def tounicode(s: Union[bytes, pycompat.unicode]) -> pycompat.unicode:
        pass
    @overload
    def tounicode(s: None) -> None:
        pass

def tounicode(s):
    """
    Convert the encoding of string from MBCS to Unicode.

    Based on mercurial.util.tolocal().
    Return 'unicode' type string.
    """
    if s is None:
        return None
    if isinstance(s, pycompat.unicode):
        return s
    if isinstance(s, encoding.localstr):
        return s._utf8.decode('utf-8')
    try:
        return s.decode(_encoding, 'strict')
    except UnicodeDecodeError:
        pass
    return s.decode(_fallbackencoding, 'replace')

def to_unicode_list(seq: Iterable[Union[bytes, str]]) -> List[str]:
    return cast(List[str], pycompat.maplist(tounicode, seq))

if typing.TYPE_CHECKING:
    @overload
    def fromunicode(s: str, errors: str = 'strict') -> bytes:
        pass
    @overload
    def fromunicode(s: None, errors: str = 'strict') -> None:
        pass

def fromunicode(s, errors='strict'):
    """
    Convert the encoding of string from Unicode to MBCS.

    Return 'str' type string.

    If you don't want an exception for conversion failure,
    specify errors='replace'.
    """
    if s is None:
        return None

    for enc in (_encoding, _fallbackencoding):
        try:
            l = s.encode(enc)
            if s == l.decode(enc):
                return l  # non-lossy encoding
            return encoding.localstr(s.encode('utf-8'), l)
        except UnicodeEncodeError:
            pass

    l = s.encode(_encoding, errors)  # last ditch
    return encoding.localstr(s.encode('utf-8'), l)

if typing.TYPE_CHECKING:
    @overload
    def toutf(s: bytes) -> bytes:
        pass
    @overload
    def toutf(s: None) -> None:
        pass

def toutf(s):
    """
    Convert the encoding of string from MBCS to UTF-8.

    Return 'str' type string.
    """
    if s is None:
        return None
    if isinstance(s, encoding.localstr):
        return s._utf8
    return tounicode(s).encode('utf-8').replace(b'\0', b'')

if typing.TYPE_CHECKING:
    @overload
    def fromutf(s: bytes) -> bytes:
        pass
    @overload
    def fromutf(s: None) -> None:
        pass

def fromutf(s):
    """
    Convert the encoding of string from UTF-8 to MBCS

    Return 'str' type string.
    """
    if s is None:
        return None
    try:
        return fromunicode(s.decode('utf-8'), 'replace')
    except UnicodeDecodeError:
        # can't round-trip
        return bytes(fromunicode(s.decode('utf-8', 'replace'), 'replace'))


getcwdb = encoding.getcwd

def isbasestring(x):
    return isinstance(x, str)
getcwdu = os.getcwd

def activebookmark(repo) -> bytes:
    return repo._activebookmark

def namedbranches(repo) -> List[bytes]:
    branchmap = repo.branchmap()
    dead = repo.deadbranches
    return sorted(br for br, _heads, _tip, isclosed
                  in branchmap.iterbranches()
                  if not isclosed and br not in dead)

def _firstchangectx(repo):
    try:
        # try fast path, which may be hidden
        return repo[0]
    except error.RepoLookupError:
        pass
    for rev in revsetmod.spanset(repo):
        return repo[rev]
    return repo[nullrev]

def revsymbol(repo: localrepo.localrepository, rev: bytes) -> HgContext:
    ctx = scmutil.revsymbol(repo, rev)
    assert isinstance(ctx, (context.changectx, context.workingctx))
    return ctx

def shortrepoid(repo) -> str:
    """Short hash of the first root changeset; can be used for settings key"""
    return str(_firstchangectx(repo))

def repoidnode(repo) -> bytes:
    """Hash of the first root changeset in binary form"""
    return _firstchangectx(repo).node()

def _getfirstrevisionlabel(repo, ctx) -> Optional[bytes]:
    # see context.changectx for look-up order of labels

    bookmarks = ctx.bookmarks()
    if ctx in repo[None].parents():
        # keep bookmark unchanged when updating to current rev
        if activebookmark(repo) in bookmarks:
            return activebookmark(repo)
    else:
        # more common switching bookmark, rather than deselecting it
        if bookmarks:
            return bookmarks[0]

    tags = ctx.tags()
    if tags:
        return tags[0]

    branch = ctx.branch()
    if repo.branchtip(branch) == ctx.node():
        return branch

def getrevisionlabel(repo, rev: Optional[int]) -> Optional[str]:
    """Return symbolic name for the specified revision or stringfy it"""
    if rev is None:
        return None  # no symbol for working revision

    ctx = repo[rev]
    label = _getfirstrevisionlabel(repo, ctx)
    if label and ctx == revsymbol(repo, label):
        return tounicode(label)

    return '%d' % rev

def getmqpatchtags(repo) -> List[bytes]:
    '''Returns all tag names used by MQ patches, or []'''
    if hasattr(repo, 'mq'):
        repo.mq.parseseries()
        return repo.mq.series[:]
    else:
        return []

def getcurrentqqueue(repo) -> Optional[bytes]:
    """Return the name of the current patch queue."""
    if not hasattr(repo, 'mq'):
        return None
    cur: bytes = os.path.basename(repo.mq.path)
    if cur.startswith(b'patches-'):
        cur = cur[8:]
    return cur

def gitcommit_full(ctx) -> Optional[str]:
    """
    If the hggit extension is loaded, and the repository is a git clone,
    returns the complete git commit hash of the current revision
    """

    repo = ctx.repo()

    if b'hggit' not in repo.extensions():
        return None

    fullgitnode = tounicode(repo.githandler.map_git_get(ctx.hex()))
    if fullgitnode is None:
        return None

    return fullgitnode

def gitcommit_short(ctx) -> Optional[str]:
    """
    If the hggit extension is loaded, and the repository is a git clone,
    returns the short (12 digits) git commit hash of the current revision
    """

    fullgitnode = gitcommit_full(ctx)
    return None if fullgitnode is None else fullgitnode[:12]

def getqqueues(repo) -> List[str]:
    ui = repo.ui.copy()
    ui.quiet = True  # don't append "(active)"
    ui.pushbuffer()
    try:
        opts = {'list': True}
        mqmod.qqueue(ui, repo, None, **opts)
        qqueues = tounicode(ui.popbuffer()).splitlines()
    except (error.Abort, OSError):
        qqueues = []
    return qqueues

def readgraftstate(repo) -> Optional[List[bytes]]:
    """Read a list of nodes from graftstate; or None if nothing in progress"""
    graftstate = statemod.cmdstate(repo, b'graftstate')
    if graftstate.exists():
        return cmdutil.readgraftstate(repo, graftstate)[b'nodes']

    return None


mergestate = mergestatemod.mergestate
readmergestate = mergestate.read


def operation_in_progress(repo: localrepo.localrepository,
                          statename: bytes) -> bool:
    """Indicates if a long term operation is in progress"""
    return statemod.cmdstate(repo, statename).exists()


def graft_in_progress(repo: localrepo.localrepository) -> bool:
    return operation_in_progress(repo, b"graftstate")


def pick_in_progress(repo: localrepo.localrepository) -> bool:
    return operation_in_progress(repo, b"pickstate")


def rebase_in_progress(repo: localrepo.localrepository) -> bool:
    return operation_in_progress(repo, b"rebasestate")


def readundodesc(repo) -> Tuple[str, int]:
    """Read short description and changelog size of last transaction"""
    if os.path.exists(repo.sjoin(b'undo')):
        try:
            args = repo.vfs(b'undo.desc', b'r').read().splitlines()
            return tounicode(args[1]), int(args[0])
        except (OSError, IndexError, ValueError):
            pass
    return '', len(repo)

def unidifftext(a: bytes,
                ad: bytes,
                b: bytes,
                bd: bytes,
                fn1: bytes,
                fn2: bytes,
                opts: mdiff.diffopts = mdiff.defaultopts) -> bytes:
    binary = stringutil.binary(a) or stringutil.binary(b)
    headers, hunks = mdiff.unidiff(a, ad, b, bd, fn1, fn2,
                                   binary=binary, opts=opts)
    if not hunks:
        return b''
    text = b''.join(sum((list(hlines) for _hrange, hlines in hunks), []))
    return b'\n'.join(headers) + b'\n' + text

def enabledextensions() -> Dict[str, bytes]:
    """Return the {name: shortdesc} dict of enabled extensions

    shortdesc is in local encoding.
    """
    return {pycompat.sysstr(k): v for k, v in extensions.enabled().items()}

def disabledextensions() -> Dict[str, bytes]:
    return {pycompat.sysstr(k): v for k, v in extensions.disabled().items()}

def allextensions() -> Dict[str, bytes]:
    """Return the {name: shortdesc} dict of known extensions

    shortdesc is in local encoding.
    """
    enabledexts = enabledextensions()
    disabledexts = disabledextensions()
    exts = (disabledexts or {}).copy()
    exts.update(enabledexts)
    exts.pop('configitems')   # tortoisehg.util.configitems
    if hasattr(sys, "frozen"):
        if 'hggit' not in exts:
            exts['hggit'] = _('hggit packaged with thg')
        if 'evolve' not in exts:
            exts['evolve'] = _('evolve packaged with thg')
        if 'topic' not in exts:
            exts['topic'] = _('topic packaged with thg')

    exts.pop('mercurial_extension_utils', None)  # Part of keyring extension
    exts.pop('hooklib', None)  # Internal, but also needs [hooks] setup

    return exts

def validateextensions(enabledexts: AbstractSet[str]) -> Dict[str, bytes]:
    """Report extensions which should be disabled

    Returns the dict {name: message} of extensions expected to be disabled.
    message is 'utf-8'-encoded string.
    """
    exts = {}
    if os.name != 'posix':
        exts['inotify'] = _('inotify is not supported on this platform')
    if 'win32text' in enabledexts:
        exts['eol'] = _('eol is incompatible with win32text')
    if 'eol' in enabledexts:
        exts['win32text'] = _('win32text is incompatible with eol')
    if 'perfarce' in enabledexts:
        exts['hgsubversion'] = _('hgsubversion is incompatible with perfarce')
    if 'hgsubversion' in enabledexts:
        exts['perfarce'] = _('perfarce is incompatible with hgsubversion')
    return exts

def _loadextensionwithblacklist(orig, ui, name, path, *args, **kwargs):
    if name.startswith(b'hgext.') or name.startswith(b'hgext/'):
        shortname = name[6:]
    else:
        shortname = name
    if shortname in _extensions_blacklist and not path:  # only bundled ext
        return

    return orig(ui, name, path, *args, **kwargs)

def _wrapextensionsloader():
    """Wrap extensions.load(ui, name) for blacklist to take effect"""
    extensions.wrapfunction(extensions, 'load',
                            _loadextensionwithblacklist)

def loadextensions(ui: uimod.ui) -> None:
    """Load and setup extensions for GUI process"""
    _wrapextensionsloader()  # enable blacklist of extensions
    extensions.loadall(ui)


def hastopicext(repo: localrepo.localrepository) -> bool:
    """Indicate is the topic extension is enabled for ``repo``."""
    hastopicext = False

    try:
        hastopicext = extensions.find(b'topic').hastopicext(repo)
    except (KeyError, AttributeError):
        pass

    return hastopicext

# TODO: provide singular canonpath() wrapper instead?
def canonpaths(list: Iterable[bytes]) -> List[bytes]:
    'Get canonical paths (relative to root) for list of files'
    # This is a horrible hack.  Please remove this when HG acquires a
    # decent case-folding solution.
    canonpats = []
    cwd = getcwdb()
    root = paths.find_root_bytes(cwd)
    for f in list:
        try:
            canonpats.append(pathutil.canonpath(root, cwd, f))
        except error.Abort:
            # Attempt to resolve case folding conflicts.
            fu = f.upper()
            cwdu = cwd.upper()
            if fu.startswith(cwdu):
                canonpats.append(
                    pathutil.canonpath(root, cwd, f[len(cwd + pycompat.ossep):])
                )
            else:
                # May already be canonical
                canonpats.append(f)
    return canonpats


def normpath(path: str) -> str:
    """Normalize a path in the same manner as mercurial.util.normpath()."""
    # TODO: Do a unicode port of util.normpath() here.
    return tounicode(util.normpath(fromunicode(path)))


def normreporoot(path: str) -> str:
    """Normalize repo root path in the same manner as localrepository"""
    # see localrepo.localrepository and scmutil.vfs
    lpath = fromunicode(path)
    lpath = os.path.realpath(util.expandpath(lpath))
    return tounicode(lpath)


def mergetools(ui: uimod.ui) -> List[bytes]:
    '''returns a list of bytestring names of the configured and internal merge
    tools'''
    values = []
    seen = set()
    for key, value in ui.configitems(b'merge-tools'):
        t = key.split(b'.')[0]
        if t not in seen:
            seen.add(t)
            # Ensure the tool is installed
            if filemerge._findtool(ui, t):
                values.append(t)
    values.append(b'internal:merge')
    values.append(b'internal:prompt')
    values.append(b'internal:dump')
    values.append(b'internal:local')
    values.append(b'internal:other')
    values.append(b'internal:fail')
    return values


_difftools: Optional[DiffTools] = None
def difftools(ui: uimod.ui) -> DiffTools:
    '''Return mapping from tool name to tuples with tool name,
    diff args, and merge args, all as bytecode strings'''
    global _difftools
    if _difftools:
        return _difftools

    def fixup_extdiff(diffopts):
        if b'$child' not in diffopts:
            diffopts.append(b'$parent1')
            diffopts.append(b'$child')
        if b'$parent2' in diffopts:
            mergeopts = diffopts[:]
            diffopts.remove(b'$parent2')
        else:
            mergeopts = []
        return diffopts, mergeopts

    tools: DiffTools = {}
    for cmd, path in ui.configitems(b'extdiff'):
        if cmd.startswith(b'cmd.'):
            cmd = cmd[4:]
            if not path:
                path = cmd
            diffopts = ui.config(b'extdiff', b'opts.' + cmd)
            diffopts = pycompat.shlexsplit(diffopts) if diffopts else []
            diffopts, mergeopts = fixup_extdiff(diffopts)
            tools[cmd] = (path, diffopts, mergeopts)
        elif cmd.startswith(b'opts.'):
            continue
        else:
            # command = path opts
            if path:
                diffopts = pycompat.shlexsplit(path)
                path = diffopts.pop(0)
            else:
                path, diffopts = cmd, []
            diffopts, mergeopts = fixup_extdiff(diffopts)
            tools[cmd] = (path, diffopts, mergeopts)
    for t in mergetools(ui):
        if t.startswith(b'internal:'):
            continue
        dopts = ui.config(b'merge-tools', t + b'.diffargs')
        mopts = ui.config(b'merge-tools', t + b'.diff3args')
        tools[t] = (
            filemerge._findtool(ui, t),
            pycompat.shlexsplit(dopts or b''),
            pycompat.shlexsplit(mopts or b''))
    _difftools = tools
    return tools


tortoisehgtoollocations = (
    ('workbench.custom-toolbar', _('Workbench custom toolbar')),
    ('workbench.revdetails.custom-menu', _('Revision details context menu')),
    ('workbench.pairselection.custom-menu', _('Pair selection context menu')),
    ('workbench.multipleselection.custom-menu', _('Multiple selection context menu')),
    ('workbench.commit.custom-menu', _('Commit context menu')),
    ('workbench.filelist.custom-menu', _('File context menu (on manifest '
                                         'and revision details)')),
)

def tortoisehgtools(uiorconfig: Union[IniConfig, uimod.ui],
                    selectedlocation: Optional[str] = None) -> Tuple[Dict[str, Dict[str, Union[str, bool]]], List[str]]:
    """Parse 'tortoisehg-tools' section of ini file.

    >>> from pprint import pprint
    >>> from mercurial import config
    >>> class memui(uimod.ui):
    ...     def readconfig(self, filename, root=None, trust=False,
    ...                    sections=None, remap=None):
    ...         pass  # avoid reading settings from file-system

    Changes:

    >>> hgrctext = b'''
    ... [tortoisehg-tools]
    ... update_to_tip.icon = hg-update
    ... update_to_tip.command = hg update tip
    ... update_to_tip.tooltip = Update to tip
    ... '''
    >>> uiobj = memui()
    >>> uiobj._tcfg.parse(b'<hgrc>', hgrctext)

    into the following dictionary

    >>> tools, toollist = tortoisehgtools(uiobj)
    >>> pprint(tools) #doctest: +NORMALIZE_WHITESPACE
    {'update_to_tip': {'command': 'hg update tip',
                       'icon': 'hg-update',
                       'tooltip': 'Update to tip'}}
    >>> toollist
    ['update_to_tip']

    If selectedlocation is set, only return those tools that have been
    configured to be shown at the given "location".
    Tools are added to "locations" by adding them to one of the
    "extension lists", which are lists of tool names, which follow the same
    format as the workbench.task-toolbar setting, i.e. a list of tool names,
    separated by spaces or "|" to indicate separators.

    >>> hgrctext_full = hgrctext + b'''
    ... update_to_null.icon = hg-update
    ... update_to_null.command = hg update null
    ... update_to_null.tooltip = Update to null
    ... explore_wd.command = explorer.exe /e,{ROOT}
    ... explore_wd.enable = iswd
    ... explore_wd.label = Open in explorer
    ... explore_wd.showoutput = True
    ...
    ... [tortoisehg]
    ... workbench.custom-toolbar = update_to_tip | explore_wd
    ... workbench.revdetails.custom-menu = update_to_tip update_to_null
    ... '''
    >>> uiobj = memui()
    >>> uiobj._tcfg.parse(b'<hgrc>', hgrctext_full)

    >>> tools, toollist = tortoisehgtools(
    ...     uiobj, selectedlocation='workbench.custom-toolbar')
    >>> sorted(tools.keys())
    ['explore_wd', 'update_to_tip']
    >>> toollist
    ['update_to_tip', '|', 'explore_wd']

    >>> tools, toollist = tortoisehgtools(
    ...     uiobj, selectedlocation='workbench.revdetails.custom-menu')
    >>> sorted(tools.keys())
    ['update_to_null', 'update_to_tip']
    >>> toollist
    ['update_to_tip', 'update_to_null']

    Valid "locations lists" are:
        - workbench.custom-toolbar
        - workbench.revdetails.custom-menu

    >>> tortoisehgtools(uiobj, selectedlocation='invalid.location')
    Traceback (most recent call last):
      ...
    ValueError: invalid location 'invalid.location'

    This function can take a ui object or a config object as its input.

    >>> cfg = config.config()
    >>> cfg.parse(b'<hgrc>', hgrctext)
    >>> tools, toollist = tortoisehgtools(cfg)
    >>> pprint(tools) #doctest: +NORMALIZE_WHITESPACE
    {'update_to_tip': {'command': 'hg update tip',
                        'icon': 'hg-update',
                        'tooltip': 'Update to tip'}}
    >>> toollist
    ['update_to_tip']

    >>> cfg = config.config()
    >>> cfg.parse(b'<hgrc>', hgrctext_full)
    >>> tools, toollist = tortoisehgtools(
    ...     cfg, selectedlocation='workbench.custom-toolbar')
    >>> sorted(tools.keys())
    ['explore_wd', 'update_to_tip']
    >>> toollist
    ['update_to_tip', '|', 'explore_wd']

    No error for empty config:

    >>> emptycfg = config.config()
    >>> tortoisehgtools(emptycfg)
    ({}, [])
    >>> tortoisehgtools(emptycfg, selectedlocation='workbench.custom-toolbar')
    ({}, [])
    """
    if isinstance(uiorconfig, uimod.ui):
        configitems = uiorconfig.configitems
        configlist = uiorconfig.configlist
    else:
        configitems = uiorconfig.items
        assert not isinstance(uiorconfig, uimod.ui)  # help pytype

        def configlist(section, name):
            return uiorconfig.get(section, name, b'').split()

    tools: Dict[str, Dict[str, Union[str, bool]]] = {}
    for key, value in configitems(b'tortoisehg-tools'):
        toolname, field = tounicode(key).split('.', 1)
        if toolname not in tools:
            tools[toolname] = {}
        if field == 'showoutput':
            bvalue = stringutil.parsebool(value)
            if bvalue is not None:
                value = bvalue
            else:
                value = True
        else:
            value = tounicode(value)
        tools[toolname][field] = value

    if selectedlocation is None:
        return tools, sorted(tools.keys())

    # Only return the tools that are linked to the selected location
    if selectedlocation not in dict(tortoisehgtoollocations):
        raise ValueError('invalid location %r' % selectedlocation)

    guidef = configlist(b'tortoisehg',
                        pycompat.sysbytes(selectedlocation)) or []
    toollist: List[str] = []
    selectedtools: Dict[str, Dict[str, Union[str, bool]]] = {}
    for name in guidef:
        name = tounicode(name)
        if name != '|':
            info = tools.get(name, None)
            if info is None:
                continue
            selectedtools[name] = info
        toollist.append(name)
    return selectedtools, toollist

loadui: Callable[[], uimod.ui] = uimod.ui.load

def copydynamicconfig(srcui: uimod.ui, destui: uimod.ui) -> None:
    """Copy config values that come from command line or code

    >>> srcui = uimod.ui()
    >>> srcui.setconfig(b'paths', b'default', b'http://example.org/',
    ...                 b'/repo/.hg/hgrc:2')
    >>> srcui.setconfig(b'patch', b'eol', b'auto', b'eol')
    >>> destui = uimod.ui()
    >>> copydynamicconfig(srcui, destui)
    >>> destui.config(b'paths', b'default') is None
    True
    >>> destui.config(b'patch', b'eol'), destui.configsource(b'patch', b'eol')
    (b'auto', b'eol')
    """
    for section, name, value in srcui.walkconfig():
        source = srcui.configsource(section, name)
        if b':' in source:
            # path:line
            continue
        if source == b'none':
            # ui.configsource returns 'none' by default
            source = b''
        destui.setconfig(section, name, value, source)

def shortreponame(ui: uimod.ui) -> Optional[bytes]:
    name = ui.config(b'web', b'name', None)
    if not name:
        return
    src = ui.configsource(b'web', b'name')  # path:line
    if b'/.hg/hgrc:' not in util.pconvert(src):
        # global web.name will set the same name to all repositories
        ui.debug(b'ignoring global web.name defined at %s\n' % src)
        return
    return name

def extractchoices(prompttext: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Extract prompt message and list of choice (char, label) pairs

    This is slightly different from ui.extractchoices() in that
    a. prompttext may be a unicode
    b. choice label includes &-accessor

    >>> extractchoices("awake? $$ &Yes $$ &No")
    ('awake? ', [('y', '&Yes'), ('n', '&No')])
    >>> extractchoices("line\\nbreak? $$ &Yes $$ &No")
    ('line\\nbreak? ', [('y', '&Yes'), ('n', '&No')])
    >>> extractchoices("want lots of $$money$$?$$Ye&s$$N&o")
    ('want lots of $$money$$?', [('s', 'Ye&s'), ('o', 'N&o')])
    """
    m = re.match(r'(?s)(.+?)\$\$([^\$]*&[^ \$].*)', prompttext)
    assert m is not None, 'invalid prompt message'
    msg = m.group(1)
    choices = [p.strip(' ') for p in m.group(2).split('$$')]
    resps = [p[p.index('&') + 1].lower() for p in choices]
    return msg, pycompat.ziplist(resps, choices)

def displaytime(date: Optional[Tuple[float, int]]) -> bytes:
    return dateutil.datestr(date, b'%Y-%m-%d %H:%M:%S %1%2')

def utctime(date: Tuple[float, int]) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(date[0]))

agescales = [
    ((lambda n: ngettext("%d year", "%d years", n)), 3600 * 24 * 365),
    ((lambda n: ngettext("%d month", "%d months", n)), 3600 * 24 * 30),
    ((lambda n: ngettext("%d week", "%d weeks", n)), 3600 * 24 * 7),
    ((lambda n: ngettext("%d day", "%d days", n)), 3600 * 24),
    ((lambda n: ngettext("%d hour", "%d hours", n)), 3600),
    ((lambda n: ngettext("%d minute", "%d minutes", n)), 60),
    ((lambda n: ngettext("%d second", "%d seconds", n)), 1),
    ]

def age(date: Tuple[int, int]) -> bytes:
    '''turn a (timestamp, tzoff) tuple into an age string.'''
    # This is i18n-ed version of mercurial.templatefilters.age().

    now = time.time()
    then = date[0]
    if then > now:
        return _('in the future')

    delta = int(now - then)
    if delta == 0:
        return _('now')
    if delta > agescales[0][1] * 2:
        return dateutil.shortdate(date)

    for t, s in agescales:
        n = delta // s
        if n >= 2 or s == 1:
            return t(n) % n

    assert False, 'unreachable'

def exception_str(e: Exception, show_hint: bool = False) -> str:
    """Convert an exception object to a user consumable string.

    If ``e`` is a core exception that implements ``mercurial.error.Hint``, the
    optional hint message can be included by specifying ``show_hint=True``.
    """

    # Most (but not all) core API exceptions have ``__bytes__()`` to stringify
    # the exception like BaseException, but in bytes instead of unicode.  Use
    # that if available, and convert to unicode explicitly.  The ``__str()__``
    # method on ``mercurial.error.Error`` calls ``__bytes__()``, but is only
    # capable of decoding ascii.  The other core exceptions generally don't
    # provide a ``__str()__`` override, which causes the raw bytes to be
    # displayed (with the b"" prefix).
    #
    # Since there are multiple core exceptions that subclass builtin exceptions
    # (instead of a single base core exception for everything else), assume the
    # presence of ``__bytes__()`` means it's a core exception.
    tobytes = getattr(e, "__bytes__", None)
    if tobytes:
        tostr = lambda: tounicode(tobytes())
    else:
        tostr = e.__str__

    if show_hint and isinstance(e, error.Hint):
        hint = getattr(e, "hint")

        if hint:
            return _gettext('%s (hint: %s)') % (tostr(), tounicode(hint))

    return tostr()


def configuredusername(ui: uimod.ui) -> Optional[bytes]:
    # need to check the existence before calling ui.username(); otherwise it
    # may fall back to the system default.
    if (not os.environ.get('HGUSER')
        and not ui.config(b'ui', b'username')
        and not os.environ.get('EMAIL')):
        return None
    try:
        return ui.username()
    except error.Abort:
        return None

def username(user: bytes) -> bytes:
    author = stringutil.person(user)
    if not author:
        author = stringutil.shortuser(user)
    return author

def user(ctx) -> bytes:
    '''
    Get the username of the change context. Does not abort and just returns
    an empty string if ctx is a working context and no username has been set
    in mercurial's config.
    '''
    try:
        user = ctx.user()
    except error.Abort:
        if ctx._rev is not None:
            raise
        # ctx is a working context and probably no username has
        # been configured in mercurial's config
        user = b''
    return user

def getestimatedsize(fctx) -> int:
    """Return the size of the given fctx without loading the revision text"""
    FILE_SIZE_UNKNOWN = 2147483647

    if fctx.rev() is None:
        return fctx.size()
    else:
        # fctx.size() can read all data into memory in rename cases so
        # we read the size directly from the filelog, this is deeper
        # under the API than I prefer to go, but seems necessary
        try:
            return fctx._filelog._revlog.rawsize(fctx.filerev())
        except AttributeError:
            # _filelog is not available when using the remotefilelog extension
            # on a shallow repo
            return FILE_SIZE_UNKNOWN

def get_revision_desc(fctx,
                      curpath: Optional[bytes] = None) -> pycompat.unicode:
    """return the revision description as a string"""
    author = tounicode(username(fctx.user()))
    rev = fctx.rev()
    # If the source path matches the current path, don't bother including it.
    if curpath and curpath == fctx.path():
        source = ''
    else:
        source = '(%s)' % tounicode(fctx.path())
    date = age(fctx.date()).decode('utf-8')
    l = tounicode(fctx.description()).splitlines()
    summary = l and l[0] or ''
    return '%s@%s%s:%s "%s"' % (author, rev, source, date, summary)

def longsummary(description: Union[bytes, str],
                limit: Optional[int] = None) -> str:
    summary = tounicode(description)
    lines = summary.splitlines()
    if not lines:
        return ''
    summary = lines[0].strip()
    add_ellipsis = False
    if limit:
        for raw_line in lines[1:]:
            if len(summary) >= limit:
                break
            line = raw_line.strip().replace('\t', ' ')
            if line:
                summary += '  ' + line
        if len(summary) > limit:
            add_ellipsis = True
            summary = summary[0:limit]
    elif len(lines) > 1:
        add_ellipsis = True
    if add_ellipsis:
        summary += ' \u2026' # ellipsis ...
    return summary

def getDeepestSubrepoContainingFile(wfile: bytes,
                                    ctx: context.changectx) -> Tuple[Optional[bytes], bytes, context.changectx]:
    """
    Given a filename and context, get the deepest subrepo that contains the file

    Also return the corresponding subrepo context and the filename relative to
    its containing subrepo
    """
    if wfile in ctx:
        return b'', wfile, ctx

    # TODO: figure out how to annotate ctx.substate so that pytype sees the
    #   correct type instead of Any.
    for wsub in ctx.substate:  # type: bytes
        if wfile.startswith(wsub):
            srev: bytes = ctx.substate[wsub][1]
            stype: bytes = ctx.substate[wsub][2]
            if stype != b'hg':
                continue
            if not os.path.exists(ctx.repo().wjoin(wsub)):
                # Maybe the repository does not exist in the working copy?
                continue
            try:
                s = ctx.sub(wsub)
                assert isinstance(s, subrepo.hgsubrepo)  # help pytype
                sctx: context.changectx = s._repo[srev]
            except:
                # The selected revision does not exist in the working copy
                continue
            wfileinsub =  wfile[len(wsub)+1:]
            if wfileinsub in sctx.substate or wfileinsub in sctx:
                return wsub, wfileinsub, sctx
            else:
                wsubsub, wfileinsub, sctx = \
                    getDeepestSubrepoContainingFile(wfileinsub, sctx)
                if wsubsub is None:
                    return None, wfile, ctx
                else:
                    return os.path.join(wsub, wsubsub), wfileinsub, sctx
    return None, wfile, ctx

def getLineSeparator(line: str) -> str:
    """Get the line separator used on a given line"""
    # By default assume the default OS line separator
    linesep = os.linesep
    lineseptypes = ['\r\n', '\n', '\r']
    for sep in lineseptypes:
        if line.endswith(sep):
            linesep = sep
            break
    return linesep

def parseconfigopts(ui: uimod.ui,
                    args: List[bytes]) -> List[Tuple[bytes, bytes, bytes]]:
    """Pop the --config options from the command line and apply them

    >>> u = uimod.ui()
    >>> args = [b'log', b'--config', b'extensions.mq=!']
    >>> parseconfigopts(u, args)
    [(b'extensions', b'mq', b'!')]
    >>> args
    [b'log']
    >>> u.config(b'extensions', b'mq')
    b'!'
    """
    config = dispatchmod._earlyparseopts(ui, args)[b'config']
    # drop --config from args
    args[:] = fancyopts.earlygetopt(args, b'', [b'config='],
                                    gnu=True, keepsep=True)[1]
    return dispatchmod._parseconfig(ui, config)


# (unicode, QString) -> unicode, otherwise -> str
_stringify = '%s'.__mod__

# ASCII code -> escape sequence (see PyString_Repr())
_escapecharmap = []
_escapecharmap.extend('\\x%02x' % x for x in pycompat.xrange(32))
_escapecharmap.extend(chr(x) for x in pycompat.xrange(32, 127))
_escapecharmap.append('\\x7f')
_escapecharmap[0x09] = '\\t'
_escapecharmap[0x0a] = '\\n'
_escapecharmap[0x0d] = '\\r'
_escapecharmap[0x27] = "\\'"
_escapecharmap[0x5c] = '\\\\'
_escapecharre = re.compile(r'[\x00-\x1f\x7f\'\\]')

def _escapecharrepl(m):
    return _escapecharmap[ord(m.group(0))]

def escapeascii(s: str) -> str:
    r"""Escape string to be embedded as a literal; like Python string_escape,
    but keeps 8bit characters and can process unicode

    >>> escapeascii("\0 \x0b \x7f \t \n \r ' \\")
    "\\x00 \\x0b \\x7f \\t \\n \\r \\' \\\\"
    >>> escapeascii(u'\xc0\n')
    u'\xc0\\n'
    """
    s = _stringify(s)
    return _escapecharre.sub(_escapecharrepl, s)

def escapepath(path: str) -> str:
    r"""Convert path to command-line-safe string; path must be relative to
    the repository root

    >>> escapepath('foo/[bar].txt')
    'path:foo/[bar].txt'
    >>> escapepath(u'\xc0')
    u'\xc0'
    """
    p = _stringify(path)
    if '[' in p or '{' in p or '*' in p or '?' in p:
        # bare path is expanded by scmutil.expandpats() on Windows
        return 'path:' + p
    else:
        return p

def escaperev(rev: int, default: Optional[str] = None) -> str:
    """Convert revision number to command-line-safe string"""
    if rev is None:
        return default
    if rev == nullrev:
        return 'null'
    assert rev >= 0
    return '%d' % rev

def _escaperevrange(a, b):
    if a == b:
        return escaperev(a)
    else:
        return '%s:%s' % (escaperev(a), escaperev(b))

def compactrevs(revs: List[int]) -> str:
    """Build command-line-safe revspec from list of revision numbers; revs
    should be sorted in ascending order to get compact form

    >>> compactrevs([])
    ''
    >>> compactrevs([0])
    '0'
    >>> compactrevs([0, 1])
    '0:1'
    >>> compactrevs([-1, 0, 1, 3])
    'null:1 + 3'
    >>> compactrevs([0, 4, 5, 6, 8, 9])
    '0 + 4:6 + 8:9'
    """
    if not revs:
        return ''
    specs = []
    k = m = revs[0]
    for n in revs[1:]:
        if m + 1 == n:
            m = n
        else:
            specs.append(_escaperevrange(k, m))
            k = m = n
    specs.append(_escaperevrange(k, m))
    return ' + '.join(specs)

# subset of revsetlang.formatspec(), but can process unicode
def _formatspec(expr, args, lparse, listfuncs):
    def argtype(c, arg):
        if c == 'd':
            return '%d' % int(arg)
        elif c == 's':
            return "'%s'" % escapeascii(arg)
        elif c == 'r':
            s = _stringify(arg)
            if isinstance(s, pycompat.unicode):
                # 8-bit characters aren't important; just avoid encoding error
                s = s.encode('utf-8')
            lparse(s)  # make sure syntax errors are confined
            return '(%s)' % arg
        raise ValueError('invalid format character %c' % c)

    def listexp(c, arg):
        l = len(arg)
        if l == 0:
            if 's' not in listfuncs:
                raise ValueError('cannot process empty list')
            return "%s('')" % listfuncs['s']
        elif l == 1:
            return argtype(c, arg[0])
        elif c in listfuncs:
            f = listfuncs[c]
            a = '\0'.join(map(_stringify, arg))
            # packed argument is escaped so it is command-line safe
            return "%s('%s')" % (f, escapeascii(a))

        m = l // 2
        return '(%s or %s)' % (listexp(c, arg[:m]), listexp(c, arg[m:]))

    expr = _stringify(expr)
    argiter = iter(args)
    ret = []
    pos = 0
    while pos < len(expr):
        q = expr.find('%', pos)
        if q < 0:
            ret.append(expr[pos:])
            break
        ret.append(expr[pos:q])
        pos = q + 1
        c = expr[pos]
        if c == '%':
            ret.append(c)
        elif c == 'l':
            pos += 1
            d = expr[pos]
            ret.append(listexp(d, list(next(argiter))))
        else:
            ret.append(argtype(c, next(argiter)))
        pos += 1

    return ''.join(ret)

def formatfilespec(expr: str, *args) -> str:
    """Build fileset expression by template and positional arguments

    Supported arguments:

    %r = fileset expression, parenthesized
    %d = int(arg), no quoting
    %s = string(arg), escaped and single-quoted
    %% = a literal '%'

    Prefixing the type with 'l' specifies a parenthesized list of that type,
    but the list must not be empty.
    """
    listfuncs = {}
    return _formatspec(expr, args, filesetlang.parse, listfuncs)

def formatrevspec(expr: str, *args) -> str:
    r"""Build revset expression by template and positional arguments

    Supported arguments:

    %r = revset expression, parenthesized
    %d = int(arg), no quoting
    %s = string(arg), escaped and single-quoted
    %% = a literal '%'

    Prefixing the type with 'l' specifies a parenthesized list of that type.

    >>> formatrevspec('%r:: and %lr', u'10 or "\xe9"', ("this()", "that()"))
    u'(10 or "\xe9"):: and ((this()) or (that()))'
    >>> formatrevspec('%d:: and not %d::', 10, 20)
    '10:: and not 20::'
    >>> formatrevspec('%ld or %ld', [], [1])
    "_list('') or 1"
    >>> formatrevspec('keyword(%s)', u'foo\xe9')
    u"keyword('foo\xe9')"
    >>> formatrevspec('root(%ls)', ['a', 'b', 'c', 'd'])
    "root(_list('a\\x00b\\x00c\\x00d'))"
    """
    listfuncs = {'d': '_intlist', 's': '_list'}
    return _formatspec(expr, args, revsetlang.parse, listfuncs)

def buildcmdargs(name: str, *args, **opts) -> List[str]:
    r"""Build list of command-line arguments

    >>> buildcmdargs('push', branch='foo')
    ['push', '--branch=foo']
    >>> buildcmdargs('graft', r=['0', '1'])
    ['graft', '-r0', '-r1']
    >>> buildcmdargs('diff', r=[0, None])
    ['diff', '-r0']
    >>> buildcmdargs('log', no_merges=True, quiet=False, limit=None)
    ['log', '--no-merges']
    >>> buildcmdargs('commit', user='')
    ['commit', '--user=']
    >>> buildcmdargs('log', keyword=['', 'foo'])
    ['log', '--keyword=', '--keyword=foo']
    >>> buildcmdargs('log', k='')
    ['log', '-k', '']
    >>> buildcmdargs('log', k=['', 'foo'])
    ['log', '-k', '', '-kfoo']

    positional arguments:

    >>> buildcmdargs('add', 'foo', 'bar')
    ['add', 'foo', 'bar']
    >>> buildcmdargs('cat', '-foo', rev='0')
    ['cat', '--rev=0', '--', '-foo']
    >>> buildcmdargs('qpush', None)
    ['qpush']
    >>> buildcmdargs('update', '')
    ['update', '']
    >>> buildcmdargs('update', 25)
    ['update', '25']

    type conversion to string:

    >>> buildcmdargs('email', r=[0, 1])
    ['email', '-r0', '-r1']
    >>> buildcmdargs('grep', 'foo', rev=2)
    ['grep', '--rev=2', 'foo']
    >>> buildcmdargs('tag', u'\xc0', message=u'\xc1')
    ['tag', u'--message=\xc1', u'\xc0']
    """
    fullargs = [_stringify(name)]
    for k, v in opts.items():
        if v is None:
            continue

        ashort = (len(k) == 1)
        if ashort:
            aname = '-%s' % k
            apref = aname
        else:
            aname = '--%s' % k.replace('_', '-')
            apref = aname + '='
        if isinstance(v, bool):
            if v:
                fullargs.append(aname)
        elif isinstance(v, list):
            for e in v:
                if e is None:
                    continue
                s = _stringify(e)
                if s or not ashort:
                    fullargs.append(apref + s)
                else:
                    fullargs.extend([aname, s])
        else:
            s = _stringify(v)
            if s or not ashort:
                fullargs.append(apref + s)
            else:
                fullargs.extend([aname, s])

    args = [_stringify(v) for v in args if v is not None]
    if any(e.startswith('-') for e in args):
        fullargs.append('--')
    fullargs.extend(args)

    return fullargs

_urlpassre = re.compile(r'^([a-zA-Z0-9+.\-]+://[^:@/]*):[^@/]+@')

def _reprcmdarg(arg):
    arg = _urlpassre.sub(r'\1:***@', arg)
    arg = arg.replace('\n', '^M')

    # only for display; no use to construct command string for os.system()
    if not arg or ' ' in arg or '\\' in arg or '"' in arg:
        return '"%s"' % arg.replace('"', '\\"')
    else:
        return arg

def prettifycmdline(cmdline: List[str]) -> str:
    r"""Build pretty command-line string for display

    >>> prettifycmdline(['log', 'foo\\bar', '', 'foo bar', 'foo"bar'])
    'log "foo\\bar" "" "foo bar" "foo\\"bar"'
    >>> prettifycmdline(['log', '--template', '{node}\n'])
    'log --template {node}^M'

    mask password in url-like string:

    >>> prettifycmdline(['push', 'http://foo123:bar456@example.org/'])
    'push http://foo123:***@example.org/'
    >>> prettifycmdline(['clone', 'svn+http://:bar@example.org:8080/trunk/'])
    'clone svn+http://:***@example.org:8080/trunk/'
    """
    return ' '.join(_reprcmdarg(e) for e in cmdline)

def parsecmdline(cmdline: str, cwd: str) -> List[str]:
    r"""Split command line string to imitate a unix shell

    >>> origfuncs = glob.glob, os.path.expanduser, os.path.expandvars
    >>> glob.glob = lambda p: [p.replace('*', e) for e in ['foo', 'bar', 'baz']]
    >>> os.path.expanduser = lambda p: re.sub(r'^~', '/home/foo', p)
    >>> os.path.expandvars = lambda p: p.replace('$var', 'bar')

    emulates glob/variable expansion rule for simple cases:

    >>> parsecmdline('foo * "qux quux" "*"  "*"', '.')
    [u'foo', u'foo', u'bar', u'baz', u'qux quux', u'*', u'*']
    >>> parsecmdline('foo /*', '.')
    [u'foo', u'/foo', u'/bar', u'/baz']
    >>> parsecmdline('''foo ~/bar '~/bar' "~/bar"''', '.')
    [u'foo', u'/home/foo/bar', u'~/bar', u'~/bar']
    >>> parsecmdline('''foo $var '$var' "$var"''', '.')
    [u'foo', u'bar', u'$var', u'bar']

    but the following cases are unsupported:

    >>> parsecmdline('"foo"*"bar"', '.')  # '*' should be expanded
    [u'foo*bar']
    >>> parsecmdline(r'\*', '.')  # '*' should be a literal
    [u'foo', u'bar', u'baz']

    >>> glob.glob, os.path.expanduser, os.path.expandvars = origfuncs
    """
    _ = _gettext  # TODO: use unicode version globally
    # shlex can't process bytes on Python 3
    src = io.StringIO(cmdline)
    lex = shlex.shlex(src, posix=True)
    lex.whitespace_split = True
    lex.commenters = ''
    args = []
    while True:
        # peek first char of next token to guess its type. this isn't perfect
        # but can catch common cases.
        q = cmdline[src.tell():].lstrip(lex.whitespace)[:1]
        try:
            e = lex.get_token()
        except ValueError as err:
            raise ValueError(_('command parse error: %s') % err)
        if e == lex.eof:
            return args

        assert e is not None  # help pytype

        if q not in lex.quotes or q in lex.escapedquotes:
            e = os.path.expandvars(e)  # $var or "$var"
        if q not in lex.quotes:
            e = os.path.expanduser(e)  # ~user
        if q not in lex.quotes and any(c in e for c in '*?[]'):
            expanded = glob.glob(os.path.join(cwd, e))
            if not expanded:
                raise ValueError(_('no matches found: %s') % e)
            if os.path.isabs(e):
                args.extend(expanded)
            else:
                args.extend(p[len(cwd) + 1:] for p in expanded)
        else:
            args.append(e)


def createsnewhead(ctx, branchheads=None):
    branch = ctx.branch()
    if branchheads is None:
        branchheads = set(ctx.repo().branchheads(branch))
    return branchheads and not any(
        p.node() in branchheads and p.branch() == branch for p in ctx.parents()
    )


def dirstate_changing_files(repo: localrepo.localrepository):
    try:
        changing_files = repo.dirstate.changing_files
    except AttributeError:
        # hg<6.4 (3550e4a88ccd)
        return util.nullcontextmanager()
    return changing_files(repo)
