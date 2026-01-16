# hgconfig.py - unicode wrapper for Mercurial's ui object
#
# Copyright 2019 Yuya Nishihara <yuya@tcha.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import typing

from mercurial import (
    pycompat,
    ui as uimod,
)

from ..util import (
    hglib,
)

if typing.TYPE_CHECKING:
    from typing import (
        List,
        Text,
        Tuple,
    )

UNSET_DEFAULT = uimod._unset

class HgConfig:
    """Unicode wrapper for Mercurial's ui object

    This provides Qt-like API on top of the ui object. Almost all methods
    are proxied through RepoAgent. Use these methods unless necessary.

    All config*() getter functions never return None, nor take None as
    default value. That's because Qt C++ API is strict about data types
    in general. Use hasConfig() to test if the config value is set.
    """

    # Design notes:
    # - It's probably better to fetch bytes data from ui at once, and cache
    #   the unicode representation in this object. We'll have to be careful
    #   to keep the data sync with the underlying ui object.
    # - No setter functions are provided right now because we can't propagate
    #   new values to the command process.

    def __init__(self, ui: uimod.ui) -> None:
        self._ui = ui

    def rawUi(self) -> uimod.ui:
        return self._ui

    def configBool(self,
                   section: str,
                   name: str,
                   default: bool = UNSET_DEFAULT) -> bool:
        data = self._ui.configbool(hglib.fromunicode(section), hglib.fromunicode(name),
                                   default=default)
        return bool(data)

    def configInt(self,
                  section: str,
                  name: str,
                  default: int = UNSET_DEFAULT) -> int:
        data = self._ui.configint(hglib.fromunicode(section), hglib.fromunicode(name),
                                  default=default)
        return int(data)

    def configString(self,
                     section: str,
                     name: str,
                     default: str = UNSET_DEFAULT) -> str:
        if default is not UNSET_DEFAULT:
            default = hglib.fromunicode(default)
        data = self._ui.config(hglib.fromunicode(section), hglib.fromunicode(name),
                               default=default)
        if data is None:
            return ''
        return hglib.tounicode(data)

    def configStringList(self,
                         section: str,
                         name: str,
                         default: List[str] = UNSET_DEFAULT) -> List[str]:
        if default is not UNSET_DEFAULT:
            default = pycompat.maplist(hglib.fromunicode, default)
        data = self._ui.configlist(hglib.fromunicode(section), hglib.fromunicode(name),
                                   default=default)
        return hglib.to_unicode_list(data)

    def configStringItems(self, section: str) -> List[Tuple[str, str]]:
        """Returns a list of string (key, value) pairs under the specified
        section"""
        items = self._ui.configitems(hglib.fromunicode(section))
        return [(hglib.tounicode(k), hglib.tounicode(v)) for k, v in items]

    def hasConfig(self, section: str, name: str) -> bool:
        return self._ui.hasconfig(hglib.fromunicode(section), hglib.fromunicode(name))
