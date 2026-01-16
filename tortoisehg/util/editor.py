from __future__ import annotations

import os, sys, typing
from mercurial import (
    match,
    util,
)

from mercurial.utils import procutil

if typing.TYPE_CHECKING:
    from typing import (
        List,
        Optional,
        Tuple,
    )
    from mercurial import (
        localrepo,
        ui as uimod,
    )


def _getplatformexecutablekey() -> bytes:
    if sys.platform == 'darwin':
        key = b'executable-osx'
    elif os.name == 'nt':
        key = b'executable-win'
    else:
        key = b'executable-unix'
    return key

_platformexecutablekey = _getplatformexecutablekey()

def _toolstr(ui: uimod.ui,
             tool: bytes,
             part: bytes,
             default: Optional[bytes] = b"") -> bytes:
    val = ui.config(b"editor-tools", tool + b"." + part, default)
    assert val is not None  # help pytype
    return val

toolcache = {}
def _findtool(ui: uimod.ui, tool: bytes) -> Optional[bytes]:
    global toolcache
    if tool in toolcache:
        return toolcache[tool]
    for kn in (b"regkey", b"regkeyalt"):
        k = _toolstr(ui, tool, kn)
        if not k:
            continue
        p = util.lookupreg(k, _toolstr(ui, tool, b"regname"))
        if p:
            p = procutil.findexe(p + _toolstr(ui, tool, b"regappend"))
            if p:
                toolcache[tool] = p
                return p
    global _platformexecutablekey
    exe = _toolstr(ui, tool, _platformexecutablekey)
    if not exe:
        exe = _toolstr(ui, tool, b'executable', tool)
    path = procutil.findexe(util.expandpath(exe))
    if path:
        toolcache[tool] = path
        return path
    elif tool != exe:
        path = procutil.findexe(tool)
        toolcache[tool] = path
        return path
    toolcache[tool] = None
    return None

def _findeditor(repo: localrepo.localrepository,
                files: List[bytes]) -> Tuple[Optional[bytes], bytes]:
    '''returns tuple of editor name and editor path.

    tools matched by pattern are returned as (name, toolpath)
    tools detected by search are returned as (name, toolpath)
    tortoisehg.editor is returned as         (None, tortoisehg.editor)
    HGEDITOR or ui.editor are returned as    (None, ui.editor)

    So first return value is an [editor-tool] name or None and
    second return value is a toolpath or user configured command line
    '''
    ui = repo.ui

    # first check for tool specified by file patterns.  The first file pattern
    # which matches one of the files being edited selects the editor
    for pat, tool in ui.configitems(b"editor-patterns"):
        mf = match.match(repo.root, b'', [pat])
        toolpath = _findtool(ui, tool)
        if mf(files[0]) and toolpath:
            return tool, procutil.shellquote(toolpath)

    # then editor-tools
    tools = {}
    for k, v in ui.configitems(b"editor-tools"):
        t = k.split(b'.')[0]
        if t not in tools:
            try:
                priority = int(_toolstr(ui, t, b"priority", b"0"))
            except ValueError as e:
                priority = -100
            tools[t] = priority
    names = list(tools.keys())
    tools = sorted([(-p, t) for t, p in tools.items()])
    editor = ui.config(b'tortoisehg', b'editor')
    if editor:
        if editor not in names:
            # if tortoisehg.editor does not match an editor-tools entry, take
            # the value directly
            return None, editor
        # else select this editor as highest priority (may still use another if
        # it is not found on this machine)
        tools.insert(0, (0, editor))
    for p, t in tools:
        toolpath = _findtool(ui, t)
        if toolpath:
            return t, procutil.shellquote(toolpath)

    # fallback to potential CLI editor
    editor = ui.geteditor()
    return None, editor

def detecteditor(repo: localrepo.localrepository,
                 files: List[bytes]) -> Tuple[bytes, Optional[bytes], Optional[bytes], Optional[bytes]]:
    'returns tuple of editor tool path and arguments'
    name, pathorconfig = _findeditor(repo, files)
    if name is None:
        return pathorconfig, None, None, None
    else:
        args = _toolstr(repo.ui, name, b"args")
        argsln = _toolstr(repo.ui, name, b"argsln")
        argssearch = _toolstr(repo.ui, name, b"argssearch")
        return pathorconfig, args, argsln, argssearch

def findeditors(ui: uimod.ui) -> List[bytes]:
    seen = set()
    for key, value in ui.configitems(b'editor-tools'):
        t = key.split(b'.')[0]
        seen.add(t)
    return [t for t in seen if _findtool(ui, t)]
