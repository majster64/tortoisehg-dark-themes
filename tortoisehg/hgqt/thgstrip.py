# thgstrip.py - MQ strip dialog for TortoiseHg
#
# Copyright 2009 Yuki KODAMA <endflow.net@gmail.com>
# Copyright 2010 David Wilhelm <dave@jumbledpile.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import typing

from .qtcore import (
    Qt,
    pyqtSlot,
)
from .qtgui import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
)

from mercurial import (
    error,
)

from ..util import hglib
from ..util.i18n import _, ngettext
from . import (
    cmdcore,
    cmdui,
    cslist,
    qtlib,
)

if typing.TYPE_CHECKING:
    from typing import (
        Any,
        Dict,
        Optional,
        Text,
    )
    from .qtgui import (
        QWidget,
    )
    from .thgrepo import (
        RepoAgent,
    )


class StripWidget(cmdui.AbstractCmdWidget):
    """Command widget to strip changesets"""

    def __init__(self,
                 repoagent: RepoAgent,
                 rev: Optional[str] = None,
                 parent: Optional[QWidget] = None,
                 opts: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(parent)
        if opts is None:
            opts = {}
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._repoagent = repoagent

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self.setLayout(grid)

        ### target revision combo
        self.rev_combo = combo = QComboBox()
        combo.setEditable(True)
        grid.addWidget(QLabel(_('Strip:')), 0, 0)
        grid.addWidget(combo, 0, 1)
        grid.addWidget(QLabel(_('Preview:')), 1, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.status = QLabel("")
        grid.addWidget(self.status, 1, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        if rev is None:
            rev = hglib.tounicode(self.repo.dirstate.branch())

        combo.addItem(rev)
        combo.setCurrentIndex(0)
        for name in hglib.namedbranches(self.repo):
            combo.addItem(hglib.tounicode(name))

        for tag in sorted(self.repo.tags(), reverse=True):
            combo.addItem(hglib.tounicode(tag))

        ### preview box, contained in scroll area, contains preview grid
        self.cslist = cslist.ChangesetList(self.repo)
        cslistrow = 2
        cslistcol = 1
        grid.addWidget(self.cslist, cslistrow, cslistcol)

        ### options
        optbox = QVBoxLayout()
        optbox.setSpacing(6)
        grid.addWidget(QLabel(_('Options:')), 3, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        grid.addLayout(optbox, 3, 1)

        self._optchks = {}
        for name, text in [
                ('force', _('Force removal of changesets, discard uncommitted '
                    'changes (-f/--force)')),
                ('nobackup', _('Do not save backup bundle (--nobackup)')),
                ('keep', _('Do not modify working directory during strip '
                           '(-k/--keep)')),
                ]:
            self._optchks[name] = w = QCheckBox(text)
            w.setChecked(bool(opts.get(name)))
            optbox.addWidget(w)

        grid.setRowStretch(cslistrow, 1)
        grid.setColumnStretch(cslistcol, 1)

        # signal handlers
        self.rev_combo.editTextChanged.connect(self.preview)

        # prepare to show
        self.rev_combo.lineEdit().selectAll()
        self.cslist.setHidden(False)
        self.preview()

    ### Private Methods ###

    @property
    def repo(self):
        return self._repoagent.rawRepo()

    def get_rev(self) -> Optional[int]:
        """Return the integer revision number of the input or None"""
        revstr = hglib.fromunicode(self.rev_combo.currentText())
        if not revstr:
            return None
        try:
            rev = hglib.revsymbol(self.repo, revstr).rev()
        except (error.RepoError, error.LookupError):
            return None
        return rev

    def updatecslist(self, uselimit: bool = True) -> bool:
        """Update the cs list and return the success status as a bool"""
        rev = self.get_rev()
        if rev is None:
            return False
        striprevs = list(self.repo.changelog.descendants([rev]))
        striprevs.append(rev)
        striprevs.sort()
        self.cslist.updateItems(striprevs)
        return True

    @pyqtSlot()
    def preview(self) -> None:
        if self.updatecslist():
            striprevs = self.cslist.curitems
            cstext = ngettext(
                "<b>%d changeset</b> will be stripped",
                "<b>%d changesets</b> will be stripped",
                len(striprevs)) % len(striprevs)
            self.status.setText(cstext)
        else:
            self.cslist.clear()
            self.cslist.updatestatus()
            cstext = qtlib.markup(_('Unknown revision!'), fg='red',
                                  weight='bold')
            self.status.setText(cstext)
        self.commandChanged.emit()

    def canRunCommand(self) -> bool:
        return self.get_rev() is not None

    def runCommand(self) -> cmdcore.CmdSession:
        opts = {'verbose': True}
        opts.update((n, w.isChecked()) for n, w in self._optchks.items())

        wc = self.repo[None]
        wcparents = wc.parents()
        wcp1rev = wcparents[0].rev()
        wcp2rev = None
        if len(wcparents) > 1:
            wcp2rev = wcparents[1].rev()
        # TODO: use cmdutil.checkunfinished()+cmdutil.bailifchanged()?
        if not opts['force'] and not opts['keep'] and \
                (wcp1rev in self.cslist.curitems or
                         wcp2rev in self.cslist.curitems) and \
                (wc.modified() or wc.added() or wc.removed()):
            main = _("Detected uncommitted local changes.")
            text = _("Do you want to keep them or discard them?")
            choices = (_('&Keep (--keep)'),
                      _('&Discard (--force)'),
                      _('&Cancel'),
            )
            resp = qtlib.CustomPrompt(_('Confirm Strip'),
                '<b>%s</b><p>%s' % (main, text),
                self, choices, default=0, esc=2).run()
            if resp == 0:
                opts['keep'] = True
            elif resp == 1:
                opts['force'] = True
            else:
                return cmdcore.nullCmdSession()

        rev = self.rev_combo.currentText()
        cmdline = hglib.buildcmdargs('strip', rev, **opts)
        return self._repoagent.runCommand(cmdline, self)


def createStripDialog(repoagent: RepoAgent,
                      rev: Optional[str] = None,
                      parent: Optional[QWidget] = None,
                      opts: Optional[Dict[str, Any]] = None) -> cmdui.CmdControlDialog:
    if opts is None:
        opts = {}
    dlg = cmdui.CmdControlDialog(parent)
    dlg.setWindowIcon(qtlib.geticon('hg-strip'))
    dlg.setWindowTitle(_('Strip - %s') % repoagent.displayName())
    dlg.setObjectName('strip')
    dlg.setRunButtonText(_('&Strip'))
    dlg.setCommandWidget(StripWidget(repoagent, rev, dlg, opts))
    return dlg
