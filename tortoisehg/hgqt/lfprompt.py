# lfprompt.py - prompt to add large files
#
# Copyright 2011 Fog Creek Software
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import os
import typing

from mercurial import error, match
from tortoisehg.hgqt import qtlib
from tortoisehg.util import hglib
from tortoisehg.util.i18n import _

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
    from .qtgui import (
        QWidget,
    )


def _createPrompt(parent: Optional[QWidget],
                  files: List[bytes]) -> qtlib.CustomPrompt:
    return qtlib.CustomPrompt(
        _('Confirm Add'),
        _('Some of the files that you have selected are of a size '
          'over 10 MB.  You may make more efficient use of disk space '
          'by adding these files as largefiles, which will store only the '
          'most recent revision of each file in your local repository, '
          'with older revisions available on the server.  Do you wish '
          'to add these files as largefiles?'), parent,
        (_('Add as &Largefiles'), _('Add as &Normal Files'), _('Cancel')),
        0, 2, files)

def promptForLfiles(parent: Optional[QWidget],
                    ui: uimod.ui,
                    repo: localrepo.localrepository,
                    files: List[bytes]) -> Optional[Tuple[List[bytes], List[bytes]]]:
    lfiles = []
    section = b'largefiles'
    try:
        minsize = float(ui.config(section, b'minsize', default=10))
    except ValueError:
        minsize = 10
    patterns = ui.config(section, b'patterns')
    if patterns:
        patterns = patterns.split(b' ')
        try:
            matcher = match.match(repo.root, b'', list(patterns))
        except error.Abort as e:
            qtlib.WarningMsgBox(_('Invalid Patterns'),
                                _('Failed to process largefiles.patterns.'),
                                hglib.exception_str(e),
                                parent=parent)
            return None
    else:
        matcher = None
    for wfile in files:
        if matcher and matcher(wfile):
            # patterns have always precedence over size
            lfiles.append(wfile)
        else:
            # check for minimal size
            try:
                filesize = os.path.getsize(repo.wjoin(wfile))
                if filesize > minsize * 1024 * 1024:
                    lfiles.append(wfile)
            except OSError:
                pass  # file not exist or inaccessible
    if lfiles:
        ret = _createPrompt(parent, files).run()
        if ret == 0:
            # add as largefiles/bfiles
            for lfile in lfiles:
                files.remove(lfile)
        elif ret == 1:
            # add as normal files
            lfiles = []
        elif ret == 2:
            return None
    return files, lfiles
