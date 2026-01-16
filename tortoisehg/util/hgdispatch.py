# hgdispatch.py - Mercurial command wrapper for TortoiseHg
#
# Copyright 2007, 2009 Steve Borho <steve@borho.org>
# Copyright 2010 Yuki KODAMA <endflow.net@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import ssl

from mercurial import encoding, error, extensions, subrepo, util
from mercurial import dispatch as dispatchmod

from tortoisehg.util import (
    hgversion,
    version,
)
from tortoisehg.util.i18n import agettext as _

testedwith = hgversion.testedwith
getversion = version.version_bytes

# exception handling different from _runcatch()
def _dispatch(orig, req):
    ui = req.ui
    try:
        return orig(req)
    except subrepo.SubrepoAbort as e:
        errormsg = bytes(e)
        label = b'ui.error'
        if e.subrepo:
            label += b' subrepo=%s' % util.urlreq.quote(e.subrepo)
        ui.write_err(_('abort: ') + errormsg + b'\n', label=label)
        if e.hint:
            ui.write_err(_('hint: ') + bytes(e.hint) + b'\n', label=label)
    except error.Abort as e:
        ui.write_err(_('abort: ') + bytes(e) + b'\n', label=b'ui.error')
        if e.hint:
            ui.write_err(_('hint: ') + bytes(e.hint) + b'\n', label=b'ui.error')
    except error.RepoError as e:
        ui.write_err(bytes(e) + b'\n', label=b'ui.error')
    except util.urlerr.httperror as e:
        err = _('HTTP Error: %d (%s)') % (e.code, encoding.strtolocal(e.msg))
        ui.write_err(err + b'\n', label=b'ui.error')
    except util.urlerr.urlerror as e:
        err = _('URLError: %s') % encoding.strtolocal(str(e.reason))
        if isinstance(e.args[0], ssl.SSLError):
            parts = encoding.strtolocal(e.args[0].strerror).split(b':')
            if len(parts) == 7:
                file, line, level, _errno, lib, func, reason = parts
                if func == b'SSL3_GET_SERVER_CERTIFICATE':
                    err = _('SSL: Server certificate verify failed')
                elif _errno == b'00000000':
                    err = _('SSL: unknown error %s:%s') % (file, line)
                else:
                    err = _('SSL error: %s') % reason
        ui.write_err(err + b'\n', label=b'ui.error')

    return -1

def uisetup(ui):
    # uisetup() is called after the initial dispatch(), so this only makes an
    # effect on command server
    extensions.wrapfunction(dispatchmod, '_dispatch', _dispatch)
