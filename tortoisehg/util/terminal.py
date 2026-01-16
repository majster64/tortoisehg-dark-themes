from __future__ import annotations

import os, sys, typing
from mercurial import util
from mercurial.utils import procutil
from tortoisehg.util import hglib

if typing.TYPE_CHECKING:
    from typing import (
        List,
        Optional,
        Tuple,
    )
    from mercurial import (
        ui as uimod,
    )


def defaultshell() -> Optional[bytes]:
    if sys.platform == 'darwin':
        shell = None # Terminal.App does not support open-to-folder
    elif os.name == 'nt':
        shell = b'cmd.exe /K title %(reponame)s'
    else:
        shell = b'xterm -T "%(reponame)s"'
    return shell

_defaultshell = defaultshell()

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
             default: bytes = b"") -> bytes:
    val = ui.config(b"terminal-tools", tool + b"." + part, default)
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

def _findterminal(ui: uimod.ui) -> Tuple[Optional[bytes], Optional[bytes]]:
    '''returns tuple of terminal name and terminal path.

    tools matched by pattern are returned as (name, toolpath)
    tools detected by search are returned as (name, toolpath)
    tortoisehg.shell is returned as  (None, tortoisehg.shell)

    So first return value is an [terminal-tool] name or None and
    second return value is a toolpath or user configured command line
    '''

    # first check for tool specified in terminal-tools
    tools = {}
    for k, v in ui.configitems(b"terminal-tools"):
        t = k.split(b'.')[0]
        if t not in tools:
            try:
                priority = int(_toolstr(ui, t, b"priority", b"0"))
            except ValueError as e:
                priority = -100
            tools[t] = priority
    names = list(tools.keys())
    tools = sorted([(-p, t) for t, p in tools.items()])
    terminal = ui.config(b'tortoisehg', b'shell')
    if terminal:
        if terminal not in names:
            # if tortoisehg.terminal does not match an terminal-tools entry, take
            # the value directly
            return None, terminal
        # else select this terminal as highest priority (may still use another if
        # it is not found on this machine)
        tools.insert(0, (0, terminal))
    for p, t in tools:
        toolpath = _findtool(ui, t)
        if toolpath:
            return t, procutil.shellquote(toolpath)

    # fallback to the default shell
    global _defaultshell
    return None, _defaultshell

def detectterminal(ui_: uimod.ui) -> Tuple[Optional[bytes], Optional[bytes]]:
    'returns tuple of terminal tool path and arguments'
    if ui_ is None:
        ui_ = hglib.loadui()
    name, pathorconfig = _findterminal(ui_)
    if name is None:
        return pathorconfig, None
    else:
        args = _toolstr(ui_, name, b"args")
        return pathorconfig, args

def findterminals(ui: uimod.ui) -> List[bytes]:
    seen = set()
    for key, value in ui.configitems(b'terminal-tools'):
        t = key.split(b'.')[0]
        seen.add(t)
    return [t for t in seen if _findtool(ui, t)]
