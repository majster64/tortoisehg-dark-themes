# thgstatus.py - update TortoiseHg status cache
#
# Copyright 2009 Adrian Buehlmann
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

'''update TortoiseHg status cache'''

from __future__ import annotations

from mercurial import hg
from tortoisehg.util import hglib, paths, shlib
import os

def cachefilepath(repo):
    return repo.vfs.join(b"thgstatus")

def run(_ui, *pats, **opts):

    if opts.get('all'):
        roots = set()
        base: bytes = hglib.getcwdb()
        for f in os.listdir(base):
            r = paths.find_root_bytes(os.path.join(base, f))
            if r is not None:
                roots.add(r)
        for r in roots:
            _ui.note(b"%s\n" % r)
            shlib.update_thgstatus(_ui, r, wait=False)
            shlib.shell_notify([r])
        return

    root = paths.find_root_bytes()
    if opts.get('repository'):
        root = opts.get('repository')
    if root is None:
        _ui.status(b"no repository\n")
        return

    repo = hg.repository(_ui, root)

    if opts.get('remove'):
        try:
            os.remove(cachefilepath(repo))
        except OSError:
            pass
        return

    if opts.get('show'):
        try:
            with open(cachefilepath(repo), 'rb') as f:
                for e in f:
                    _ui.status(b"%s %s\n" % (e[0:1], e[1:-1]))
        except OSError:
            _ui.status(b"*no status*\n")
        return

    wait = opts.get('delay') is not None
    shlib.update_thgstatus(_ui, root, wait=wait)

    if opts.get('notify'):
        shlib.shell_notify(opts.get('notify'))
    _ui.note(b"thgstatus updated\n")
