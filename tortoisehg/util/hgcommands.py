# hgcommands.py - miscellaneous Mercurial commands for TortoiseHg
#
# Copyright 2013, 2014 Yuya Nishihara <yuya@tcha.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import hashlib
import os
import socket

from mercurial import (
    error,
    extensions,
    node as nodemod,
    pycompat,
    registrar,
    sslutil,
    util,
)
from mercurial.utils import (
    urlutil,
)

from tortoisehg.util import (
    hgversion,
    version,
)
from tortoisehg.util.i18n import agettext as _

cmdtable = {}
_mqcmdtable = {}
command = registrar.command(cmdtable)
mqcommand = registrar.command(_mqcmdtable)

configtable = {}
configitem = registrar.configitem(configtable)

testedwith = hgversion.testedwith
getversion = version.version_bytes

configitem(b'tortoisehg', b'initskel')

@command(b'debuggethostfingerprint',
    [(b'', b'insecure', None,
      _('do not verify server certificate (ignoring web.cacerts config)')),
     ],
    _('[--insecure] [SOURCE]'),
    optionalrepo=True)
def debuggethostfingerprint(ui, repo, source=b'default', **opts):
    """retrieve a fingerprint of the server certificate

    Specify --insecure to disable SSL verification.
    """
    # we have no idea if the source url is for pull or push, so let's assume
    # 'pull' is more basic operation like other hg debug* commands.
    source, _branches = urlutil.get_unique_pull_path(
        b'debuggethostfingerprint', repo, ui, source)
    u = urlutil.url(source)
    scheme = (u.scheme or b'').split(b'+')[-1]
    host = u.host
    port = urlutil.getport(u.port or scheme or b'-1')
    if scheme != b'https' or not host or not (0 <= port <= 65535):
        raise error.Abort(_('unsupported URL: %s') % source)

    sock = socket.socket()
    try:
        sock.connect((host, port))
        sock = sslutil.wrapsocket(sock, None, None, ui, serverhostname=host)
        peercert = sock.getpeercert(True)
        if not peercert:
            raise error.Abort(_('%s certificate error: no certificate received')
                              % host)
    finally:
        sock.close()

    s = nodemod.hex(hashlib.sha256(peercert).digest())
    ui.write(b'sha256:', b':'.join([s[x:x + 2] for x
                                    in pycompat.xrange(0, len(s), 2)]),
             b'\n')

def postinitskel(ui, repo, hooktype, result, pats, **kwargs):
    """create common files in new repository"""
    assert hooktype == b'post-init', hooktype
    if result:
        return
    _src, dest, _branch = urlutil.get_clone_path(ui, pats and pats[0] or b'.')
    skel = ui.config(b'tortoisehg', b'initskel')
    if skel:
        # copy working tree from user-defined path if any
        skel = util.expandpath(skel)
        for name in os.listdir(skel):
            if name == b'.hg':
                continue
            util.copyfiles(os.path.join(skel, name),
                           os.path.join(dest, name),
                           hardlink=False)
        return
    # create .hg* files, mainly to workaround Explorer's problem in creating
    # files with a name beginning with a dot
    open(os.path.join(dest, b'.hgignore'), 'a').close()

def _applymovemqpatches(q, after, patches):
    fullindexes = {q.guard_re.split(rpn, 1)[0]: i
                       for i, rpn in enumerate(q.fullseries)}
    fullmap = {}  # patch: line in series file
    for i, n in sorted([(fullindexes[n], n) for n in patches], reverse=True):
        fullmap[n] = q.fullseries.pop(i)
    del fullindexes  # invalid

    if after is None:
        fullat = 0
    else:
        for i, rpn in enumerate(q.fullseries):
            if q.guard_re.split(rpn, 1)[0] == after:
                fullat = i + 1
                break
        else:
            fullat = len(q.fullseries)  # last ditch (should not happen)
    q.fullseries[fullat:fullat] = (fullmap[n] for n in patches)
    q.parseseries()
    q.seriesdirty = True

@mqcommand(b'qreorder',
    [(b'', b'after', b'', _('move after the specified patch'))],
    _('[--after PATCH] PATCH...'))
def qreorder(ui, repo, *patches, **opts):
    """move patches to the beginning or after the specified patch"""
    after = opts['after'] or None
    q = repo.mq
    if any(n not in q.series for n in patches):
        raise error.Abort(_('unknown patch to move specified'))
    if after in patches:
        raise error.Abort(_('invalid patch position specified'))
    if any(q.isapplied(n) for n in patches):
        raise error.Abort(_('cannot move applied patches'))

    if after is None:
        at = 0
    else:
        try:
            at = q.series.index(after) + 1
        except ValueError:
            raise error.Abort(_('patch %s not in series') % after)
    if at < q.seriesend(True):
        raise error.Abort(_('cannot move into applied patches'))

    wlock = repo.wlock()
    try:
        _applymovemqpatches(q, after, patches)
        q.savedirty()
    finally:
        wlock.release()

def uisetup(ui):
    try:
        extensions.find(b'mq')
        cmdtable.update(_mqcmdtable)
    except KeyError:
        pass
