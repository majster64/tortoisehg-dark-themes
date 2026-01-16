# gpg.py - TortoiseHg GnuPG support
#
# Copyright 2013 Elson Wei <elson.wei@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import os
import typing

if typing.TYPE_CHECKING:
    from typing import (
        List,
        Text,
    )
    from mercurial import (
        ui as uimod,
    )

if os.name == 'nt':
    from mercurial.windows import winreg

    def findgpg(ui: uimod.ui) -> List[str]:
        path = []
        for key in (r"Software\GNU\GnuPG", r"Software\Wow6432Node\GNU\GnuPG"):
            try:
                # pytype: disable=module-attr
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as hkey:
                    pfx = winreg.QueryValueEx(hkey, 'Install Directory')[0]
                    # pytype: enable=module-attr

                for dirPath, dirNames, fileNames in os.walk(pfx):
                    for f in fileNames:
                        if f == 'gpg.exe':
                            path.append(os.path.join(dirPath, f))
            except OSError:
                pass

        return path

else:
    def findgpg(ui: uimod.ui) -> List[str]:
        return []
