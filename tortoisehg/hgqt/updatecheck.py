# updatecheck.py - Check website for TortoiseHg updates
#
# Copyright 2007 TK Soh <teekaysoh@gmail.com>
# Copyright 2007 Steve Borho <steve@borho.org>
# Copyright 2010 Yuki KODAMA <endflow.net@gmail.com>
# Copyright 2010 Johan Samyn <johan.samyn@gmail.com>
# Copyright 2020 Matt Harbison <mharbison72@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import platform
import re
import sys

from .qtcore import (
    QObject,
    QUrl,
    pyqtSignal,
    pyqtSlot,
)
from .qtnetwork import (
    QNetworkAccessManager,
    QNetworkRequest,
)

from mercurial import (
    pycompat,
)

from ..util import (
    version,
)


class Checker(QObject):
    # Provides the new version string and download URL when an update is
    # available.
    updateAvailable = pyqtSignal(str, str)

    # Update check completed without an available update
    updateUnavailable = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._newverreply = None

    @pyqtSlot()
    def query(self):
        verurl = 'https://www.mercurial-scm.org/release/tortoisehg/latest.dat'
        # If we use QNetworkAccessManager elsewhere, it should be shared
        # through the application.
        self._netmanager = QNetworkAccessManager(self)
        self._newverreply = self._netmanager.get(QNetworkRequest(QUrl(verurl)))
        self._newverreply.finished.connect(self._uFinished)

    @pyqtSlot()
    def _uFinished(self):
        newver = (0,0,0)

        reply = self._newverreply
        assert reply is not None

        try:
            data = reply.readAll().data()
        finally:
            reply.close()
            self._newverreply = None

        # Simulate a User-Agent string for platforms with an entry in the file.
        # https://developers.whatismybrowser.com/useragents/explore/operating_system_name
        useragent = "unknown"
        if sys.platform == 'win32':
            from win32process import (  # pytype: disable=import-error
                IsWow64Process as IsX64
            )
            if IsX64():
                useragent = "Windows WOW64"
            else:
                import struct
                width = struct.calcsize("P") * 8
                useragent = width == 64 and 'Windows x64' or 'Windows'
        elif sys.platform == 'darwin':
            # Mac packages will be universal binaries, but try to include the
            # architecture so that older architectures can eventually be
            # dropped from the current installer, and the last of the older
            # architecture offered independently.
            useragent = "Macintosh"
            arch = platform.processor()
            if arch == 'i386':
                useragent += "; Intel"

        candidates = {}

        # Extra check to ensure the regex returned from the server is safe
        # before using it.
        def _check_regex(p):
            # Clear any simple groups.  Example: ``.*Windows.*(WOW|x)64.*``
            p = re.sub(r"\([0-9A-Za-z|]+\)", "", p)

            # Now the regex should be a simple alphanumeric or wildcards
            return re.match("^[A-Za-z0-9.*]+$", p)

        # https://www.mercurial-scm.org/wiki/Packaging#Version_information_protocol
        # priority \t version \t user-agent-regex \t download url \t desc
        for line in pycompat.sysstr(data).splitlines():
            try:
                parts = line.strip().split("\t")
                pattern = parts[2]

                if not _check_regex(pattern):
                    print("Ignoring upgrade pattern %s" % pattern)
                    continue

                if not re.match(pattern, useragent):
                    continue

                prio = int(parts[0].strip())
                candidates[prio] = (parts[1].strip(), parts[3].strip())
            except (IndexError, ValueError):
                pass

        if not candidates:
            self.updateUnavailable.emit()
            return

        prio = [p for p in sorted(candidates.keys())][0]
        newverstr, upgradeurl = candidates[prio]

        try:
            # Convert to a version string that accounts for rc builds.
            pkg_version = version._build_package_version('stable', newverstr)
            newver = tuple([int(p) for p in pkg_version.split('.')])
            thgv = version.package_version()
            curver = tuple([int(p) for p in thgv.split('.')])
        except ValueError:
            curver = (0, 0, 0)

        if newver > curver:
            self.updateAvailable.emit(newverstr, upgradeurl)
        else:
            self.updateUnavailable.emit()

    def close(self):
        if self._newverreply:
            self._newverreply.abort()
