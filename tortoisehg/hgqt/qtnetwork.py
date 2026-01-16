# qtnetwork.py - PyQt5/6 compatibility wrapper
#
# Copyright 2015 Yuya Nishihara <yuya@tcha.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

"""Thin compatibility wrapper for QtNetwork"""

from __future__ import annotations

from .qtcore import QT_API

if QT_API == 'PyQt6':
    from PyQt6.QtNetwork import *  # pytype: disable=import-error
elif QT_API == 'PyQt5':
    from PyQt5.QtNetwork import *  # pytype: disable=import-error
else:
    raise RuntimeError('unsupported Qt API: %s' % QT_API)
