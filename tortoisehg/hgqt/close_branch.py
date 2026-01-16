# close_branch.py - Close branch dialog for TortoiseHg
#
# Copyright 2020 Bram Belpaire <belpairebram@hotmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import typing

from .qtgui import (
    QSizePolicy,
    QLineEdit,
    QFormLayout,
    QLabel
)

from ..util import (
    hglib,
    i18n,
)
from ..util.i18n import _
from . import (
    cmdui,
)

if typing.TYPE_CHECKING:
    from typing import (
        List,
        Optional,
        Text,
    )
    from .qtgui import (
        QWidget,
    )
    from .cmdcore import (
        CmdSession,
    )
    from .thgrepo import (
        RepoAgent,
    )


class CloseWidget(cmdui.AbstractCmdWidget):
    def __init__(self,
                 repoagent: RepoAgent,
                 rev: int,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._repoagent = repoagent
        self._repo = repoagent.rawRepo()
        self._rev = rev
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        # simple widget with only an editable commit message textbox
        self.setLayout(form)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # add revision information about selected revision
        form.addRow(_('Revision:'), QLabel('%d (%s)' % (rev, self._repo[rev])))
        # commit message
        self.hg_commit = QLineEdit()
        # automatic message
        msgset = i18n.keepgettext()._('Close %s branch')
        str_msg = msgset['str']
        self.hg_commit.setText(str_msg %
                               hglib.tounicode(self._repo[self._rev].branch()))
        form.addRow(_('Commit message:'), self.hg_commit)

    def compose_command(self) -> List[str]:
        rev = '%d' % self._rev
        cmdline = hglib.buildcmdargs('close-head', m=self.hg_commit.text(),
                                     r=rev)
        return cmdline

    def runCommand(self) -> CmdSession:
        cmdline = self.compose_command()
        return self._repoagent.runCommand(cmdline, self)

    def canRunCommand(self) -> bool:
        return True

def createCloseBranchDialog(repoagent: RepoAgent,
                            rev: int,
                            parent: Optional[QWidget]) -> cmdui.CmdControlDialog:
    dlg = cmdui.CmdControlDialog(parent)
    dlg.setWindowTitle(_('Close Branch - %s') % repoagent.displayName())
    dlg.setRunButtonText(_('&Close Branch'))
    dlg.setCommandWidget(CloseWidget(repoagent, rev, dlg))
    return dlg
