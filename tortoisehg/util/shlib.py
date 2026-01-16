# shlib.py - TortoiseHg shell utilities
#
# Copyright 2007 TK Soh <teekaysoh@gmail.com>
# Copyright 2008 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import os
import time
import typing

from hgext.largefiles import (
    lfutil,
)

from mercurial import (
    hg,
)

from . import (
    hglib,
)

if typing.TYPE_CHECKING:
    from typing import (
        Dict,
        List,
    )

if os.name == 'nt':
    def shell_notify(paths: List[bytes], noassoc: bool = False) -> None:
        try:
            from win32com.shell import (  # pytype: disable=import-error
                shell, shellcon
            )
            import pywintypes  # pytype: disable=import-error
        except ImportError:
            return
        dirs = set()
        for path in paths:
            if path is None:
                continue
            abspath = os.path.abspath(path)
            if not os.path.isdir(abspath):
                abspath = os.path.dirname(abspath)
            dirs.add(abspath)
        # send notifications to deepest directories first
        for dir in sorted(dirs, key=len, reverse=True):
            try:
                pidl, ignore = shell.SHILCreateFromPath(
                    hglib.tounicode(dir), 0)
            except pywintypes.com_error:
                return
            if pidl is None:
                continue
            shell.SHChangeNotify(shellcon.SHCNE_UPDATEITEM,
                                 shellcon.SHCNF_IDLIST | shellcon.SHCNF_FLUSH,
                                 pidl, None)
        if not noassoc:
            shell.SHChangeNotify(shellcon.SHCNE_ASSOCCHANGED,
                                 shellcon.SHCNF_FLUSH,
                                 None, None)

    def update_thgstatus(ui, root, wait=False):
        '''Rewrite the file .hg/thgstatus

        Caches the information provided by repo.status() in the file
        .hg/thgstatus, which can then be read by the overlay shell extension
        to display overlay icons for directories.

        The file .hg/thgstatus contains one line for each directory that has
        removed, modified or added files (in that order of preference). Each
        line consists of one char for the status of the directory (r, m or a),
        followed by the relative path of the directory in the repo. If the
        file .hg/thgstatus is empty, then the repo's working directory is
        clean.

        Specify wait=True to wait until the system clock ticks to the next
        second before accessing Mercurial's dirstate. This is useful when
        Mercurial's .hg/dirstate contains unset entries (in output of
        "hg debugstate"). unset entries happen if .hg/dirstate was updated
        within the same second as Mercurial updated the respective file in
        the working tree. This happens with a high probability for example
        when cloning a repo. The overlay shell extension will display unset
        dirstate entries as (potentially false) modified. Specifying wait=True
        ensures that there are no unset entries left in .hg/dirstate when this
        function exits.
        '''
        if wait:
            tref = time.time()
            tdelta = float(int(tref)) + 1.0 - tref
            if tdelta > 0.0:
                time.sleep(tdelta)

        repo = hg.repository(ui, root) # a fresh repo object is needed
        with lfutil.lfstatus(repo):
            repostate = repo.status() # will update dirstate as a side effect

        dirstatus: Dict[bytes, bytes] = {}
        def dirname(f: bytes) -> bytes:
            return b'/'.join(f.split(b'/')[:-1])
        for fn in repostate.added:
            dirstatus[dirname(fn)] = b'a'
        for fn in repostate.modified:
            dirstatus[dirname(fn)] = b'm'
        for fn in repostate.removed + repostate.deleted:
            dirstatus[dirname(fn)] = b'r'

        update = False

        try:
            with repo.vfs(b'thgstatus', b'rb') as f:
                for dn in sorted(dirstatus):
                    s = dirstatus[dn]
                    e: bytes = f.readline()
                    if e.startswith(b'@@noicons'):
                        break
                    if e == b'' or e[0:1] != s or e[1:-1] != dn:
                        update = True
                        break
                if f.readline() != b'':
                    # extra line in f, needs update
                    update = True
        except OSError:
            update = True

        if update:
            with repo.vfs(b'thgstatus', b'wb', atomictemp=True) as f:
                for dn in sorted(dirstatus):
                    s = dirstatus[dn]
                    f.write(s + dn + b'\n')
                    ui.note(b"%s %s\n" % (s, dn))
        return update

else:
    def shell_notify(paths: List[bytes], noassoc: bool = False) -> None:
        if not paths:
            return
        notify = os.environ.get('THG_NOTIFY', '.tortoisehg/notify')
        if not os.path.isabs(notify):
            notify = os.path.join(os.path.expanduser('~'), notify)
            os.environ['THG_NOTIFY'] = notify
        if not os.path.isfile(notify):
            return
        try:
            f_notify = open(notify, 'wb')
        except OSError:
            return
        try:
            abspaths = [os.path.abspath(path) for path in paths if path]
            f_notify.write(b'\n'.join(abspaths))
        finally:
            f_notify.close()

    def update_thgstatus(*args, **kws):
        pass
