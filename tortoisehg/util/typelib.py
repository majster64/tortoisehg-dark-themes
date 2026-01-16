# typelib.py - A collection of type hint helpers
#
# Copyright 2020 Matt Harbison <mharbison72@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.


from __future__ import annotations

import typing

if typing.TYPE_CHECKING:
    from typing import (
        Dict,
        List,
        Optional,
        Tuple,
        TypeVar,
        Union,
    )

    from mercurial import (
        config as config_mod,
        context,
    )

    from . import (
        patchctx,
        wconfig,
    )

    from ..hgqt.qtcore import (
        Qt,
    )

    # Tuple of the executable path (if found), list of `.diffargs` and
    # list of `.diff3args` for a diff tool.
    DiffTool = Tuple[Optional[bytes], List[bytes], List[bytes]]

    # Map of diff tool name to its configuration
    DiffTools = Dict[bytes, DiffTool]

    # The contexts returned from ``scmutil.revsymbol()`` and friends, or the
    # corresponding typed methods in ``hglib``.  The actual type will be a
    # dynamic subclass, as defined in ``thgrepo``.
    HgContext = Union[context.changectx, context.workingctx]

    IniConfig = TypeVar('IniConfig', wconfig._wconfig, config_mod.config)

    try:
        Qt_ItemFlags = Qt.ItemFlag
    except ImportError:
        Qt_ItemFlags = Qt.ItemFlags

    # The contexts returned from ``localrepository.__getitem()__``, including
    # regular ``HgContext``, as well as ``patchctx.patchctx`` for MQ related
    # objects.
    ThgContext = Union[context.changectx, context.workingctx, patchctx.patchctx]
