# hgversion.py - Version information for Mercurial
#
# Copyright 2009 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import re
from typing import (
    List,
    Optional,
)

hgversion: Optional[bytes] = None

try:
    try:
        # post 1.1.2
        from mercurial import util
        hgversion = util.version()
    except AttributeError:
        # <= 1.1.2
        from mercurial import version  # pytype: disable=import-error
        hgversion = version.get_version()
except ImportError:
    pass

testedwith = b'6.3 6.4 6.5 6.6 6.7 6.8 6.9'

def _splitversion(v: bytes) -> Optional[List[bytes]]:
    """Extract (major, minor) version components as bytes, or None"""
    v = v.split(b'+')[0]
    if not v or v == b'unknown' or len(v) >= 12:
        # can't make any intelligent decisions about unknown or hashes
        return
    vers = re.split(br'\.|-|rc', v)[:2]
    if len(vers) < 2:
        return
    return vers

def checkhgversion(v: bytes) -> Optional[bytes]:
    """range check the Mercurial version"""
    reqvers = testedwith.split()
    vers = _splitversion(v)
    if not vers:
        return
    if b'.'.join(vers) in reqvers:
        return
    return (b'This version of TortoiseHg requires Mercurial version %s.n to '
            b'%s.n, but found %s') % (reqvers[0], reqvers[-1], v)

def checkminhgversion(v: bytes) -> Optional[bytes]:
    """Check if the given Mercurial version is not lower than the minimum
    supported version

    >>> checkminhgversion(b'deadbeef1234')
    >>> checkminhgversion(b'unknown')
    >>> checkminhgversion(b'nan.nan')
    >>> checkminhgversion(b'1.0.1') # doctest: +ELLIPSIS
    b'This version of TortoiseHg requires Mercurial version ...'
    >>> checkminhgversion(b'100.0')
    >>> checkminhgversion(testedwith.split()[0])
    >>> checkminhgversion(testedwith.split()[-1])
    """
    reqvers = testedwith.split()
    vers = _splitversion(v)
    if not vers:
        return
    try:
        vernums = tuple(map(int, vers))
    except ValueError:
        return
    if vernums >= tuple(map(int, _splitversion(reqvers[0]))):
        return
    return (b'This version of TortoiseHg requires Mercurial version %s or '
            b'later, but found %s') % (reqvers[0], v)
