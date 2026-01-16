# archive.py - TortoiseHg's dialog for archiving a repo revision
#
# Copyright 2009 Emmanuel Rosa <goaway1000@gmail.com>
# Copyright 2010 Johan Samyn <johan.samyn@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import os
import typing

from .qtcore import (
    pyqtSlot,
)
from .qtgui import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mercurial import (
    error,
    pycompat,
    scmutil,
)

from ..util import hglib
from ..util.i18n import _
from . import (
    cmdcore,
    cmdui,
    qtlib,
)

if typing.TYPE_CHECKING:
    from typing import (
        Dict,
        List,
        Optional,
        Text,
    )

    from . import (
        thgrepo,
    )


WD_PARENT = _('= Working Directory Parent =')

_ARCHIVE_TYPES = [
    {'type': 'files', 'ext': '', 'label': _('Directory of files'),
     'desc': _('Directory of files')},
    {'type': 'tar', 'ext': '.tar', 'label': _('Tar archives'),
     'desc': _('Uncompressed tar archive')},
    {'type': 'tbz2', 'ext': '.tar.bz2', 'label': _('Bzip2 tar archives'),
     'desc': _('Tar archive compressed using bzip2')},
    {'type': 'tgz', 'ext': '.tar.gz', 'label': _('Gzip tar archives'),
     'desc': _('Tar archive compressed using gzip')},
    {'type': 'uzip', 'ext': '.zip', 'label': _('Zip archives'),
     'desc': _('Uncompressed zip archive')},
    {'type': 'zip', 'ext': '.zip', 'label': _('Zip archives'),
     'desc': _('Zip archive compressed using deflate')},
    ]

class ArchiveWidget(cmdui.AbstractCmdWidget):
    """Command widget to archive a particular Mercurial revision"""

    _archive_content_all_files = 0
    _archive_content_touched_files = 1
    _archive_content_touched_since = 2

    def __init__(self,
                 repoagent: thgrepo.RepoAgent,
                 rev: Optional[str],
                 parent: Optional[QWidget] = None,
                 minrev: Optional[str] = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._repoagent = repoagent
        if not minrev:
            minrev = rev
            archive_since = False
        else:
            archive_since = True

        possibleroots = []
        if minrev:
            parents = hglib.revsymbol(self.repo,
                                      hglib.fromunicode(minrev)).parents()
            if parents:
                for p in parents:
                    possibleroots.append('%d' % p.rev())
            else:
                possibleroots.append('null')

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.setLayout(form)

        ### content selection
        ## revision selection
        self.rev_combo = QComboBox()
        self.rev_combo.setEditable(True)
        self.rev_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        form.addRow(_('Revision:'), self.rev_combo)

        ### content type
        ## Selection of the content mode
        self.content_mode = QButtonGroup(self)
        # All files
        box = QVBoxLayout()
        w = QRadioButton(_('All files in this revision'), self)
        self.content_mode.addButton(w, self._archive_content_all_files)
        box.addWidget(w)
        # Touched in this revision
        w = QRadioButton(_('Only files modified/created in this revision'), self)
        self.content_mode.addButton(w, self._archive_content_touched_files)
        box.addWidget(w)
        # Touched since
        sincebox = QHBoxLayout()
        self.rootrev_combo = QComboBox(self)
        self.rootrev_combo.setEditable(True)
        self.rootrev_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        if minrev:
            for text in possibleroots:
                self.rootrev_combo.addItem(text)
            self.rootrev_combo.setCurrentIndex(0)
        w = QRadioButton(_('Only files modified/created since:'), self)
        self.content_mode.addButton(w, self._archive_content_touched_since)
        sincebox.addWidget(w)
        sincebox.addWidget(self.rootrev_combo)
        box.addLayout(sincebox)
        if archive_since:
            # default to "touched since X" if the input is a range
            self.content_mode.button(self._archive_content_touched_since).setChecked(True)
        else:
            self.content_mode.button(self._archive_content_all_files).setChecked(True)
        form.addRow(_('Archive Content:'), box)

        # subrepository option
        self.subrepos_chk = QCheckBox(_('Recurse into subrepositories'))
        form.addRow('', self.subrepos_chk)

        # selecting a destination
        self.dest_edit = QLineEdit()
        self.dest_edit.setMinimumWidth(300)
        self.dest_btn = QPushButton(_('Browse...'))
        self.dest_btn.setAutoDefault(False)
        box = QHBoxLayout()
        box.addWidget(self.dest_edit)
        box.addWidget(self.dest_btn)
        form.addRow(_('Destination path:'), box)

        # archive type selection
        self._typesradios = QButtonGroup(self)
        box = QVBoxLayout()
        for i, spec in enumerate(_ARCHIVE_TYPES):
            w = QRadioButton(spec['desc'], self)
            self._typesradios.addButton(w, i)
            box.addWidget(w)
        form.addRow(_('Archive types:'), box)

        # some extras
        self.hgcmd_txt = QLineEdit()
        self.hgcmd_txt.setReadOnly(True)
        form.addRow(_('Hg command:'), self.hgcmd_txt)

        # set default values
        self.prevtarget = None
        self.rev_combo.addItem(WD_PARENT)
        self.rev_combo.addItems(pycompat.maplist(hglib.tounicode,
                                    hglib.namedbranches(self.repo)))
        for t in sorted(self.repo.tags(), reverse=True):
            self.rev_combo.addItem(hglib.tounicode(t))
        if rev:
            text = rev
            selectindex = self.rev_combo.findText(text)
            if selectindex >= 0:
                self.rev_combo.setCurrentIndex(selectindex)
            else:
                self.rev_combo.insertItem(0, text)
                self.rev_combo.setCurrentIndex(0)
        self.rev_combo.setMaxVisibleItems(self.rev_combo.count())
        self.subrepos_chk.setChecked(self.get_subrepos_present())
        self.dest_edit.setText(hglib.tounicode(self.repo.root))
        self._typesradios.button(0).setChecked(True)
        self.update_path()

        # connecting slots
        self.dest_edit.textEdited.connect(self.compose_command)
        self.rev_combo.editTextChanged.connect(self.rev_combo_changed)
        self.dest_btn.clicked.connect(self.browse_clicked)
        self.content_mode.buttonClicked.connect(self.compose_command)
        self.rootrev_combo.editTextChanged.connect(self.compose_command)
        self.subrepos_chk.toggled.connect(self.compose_command)
        self._typesradios.buttonClicked.connect(self.update_path)

    @property
    def repo(self):
        return self._repoagent.rawRepo()

    def rev_combo_changed(self) -> None:
        self.subrepos_chk.setChecked(self.get_subrepos_present())
        self.update_path()

    def browse_clicked(self) -> None:
        """Select the destination directory or file"""
        dest = self.dest_edit.text()
        if not os.path.exists(dest):
            dest = os.path.dirname(dest)
        select = self.get_selected_archive_type()
        FD = QFileDialog
        if select['type'] == 'files':
            caption = _('Select Destination Folder')
            filter = ''
        else:
            caption = _('Select Destination File')
            ext = '*' + select['ext']
            filter = ';;'.join(['%s (%s)' % (select['label'], ext),
                                _('All files (*)')])
        response, _filter = FD.getSaveFileName(
            self, caption, dest, filter, '', QFileDialog.Option.ReadOnly)
        if response:
            self.dest_edit.setText(response)
            self.update_path()

    def get_subrepos_present(self) -> bool:
        rev = self.get_selected_rev()
        try:
            ctx = hglib.revsymbol(self.repo, rev)
        except (error.LookupError, error.RepoLookupError):
            return False
        return b'.hgsubstate' in ctx

    def get_selected_rev(self) -> bytes:
        rev = self.rev_combo.currentText()
        if rev == WD_PARENT:
            rev = b'.'
        else:
            rev = hglib.fromunicode(rev)
        return rev

    def get_selected_rootrev(self) -> bytes:
        rev = self.rootrev_combo.currentText()
        return hglib.fromunicode(rev)

    def get_selected_archive_type(self) -> Dict[str, str]:
        """Return a dictionary describing the selected archive type"""
        return _ARCHIVE_TYPES[self._typesradios.checkedId()]

    def update_path(self) -> None:

        # pytype: disable=redundant-function-type-comment
        def remove_ext(path: str) -> str:
            for ext in ('.tar', '.tar.bz2', '.tar.gz', '.zip'):
                if path.endswith(ext):
                    return path.replace(ext, '')
            return path
        def remove_rev(path: str) -> str:
            l = ''
            for i in pycompat.xrange(self.rev_combo.count() - 1):
                l += self.rev_combo.itemText(i)
            revs = [rev[0] for rev in l]
            revs.append(wdrev)
            if not self.prevtarget is None:
                revs.append(self.prevtarget)
            for rev in ['_' + rev for rev in revs]:
                if path.endswith(rev):
                    return path.replace(rev, '')
            return path
        def add_rev(path: str, rev: str) -> str:
            return '%s_%s' % (path, rev)
        def add_ext(path: str) -> str:
            select = self.get_selected_archive_type()
            if select['type'] != 'files':
                path += select['ext']
            return path
        # pytype: enable=redundant-function-type-comment

        text = self.rev_combo.currentText()
        if len(text) == 0:
            self.commandChanged.emit()
            return
        wdrev = str(self.repo[b'.'].rev())
        if text == WD_PARENT:
            text = wdrev
        else:
            try:
                hglib.revsymbol(self.repo, hglib.fromunicode(text))
            except (error.RepoError, error.LookupError):
                self.commandChanged.emit()
                return
        path = self.dest_edit.text()
        path = remove_ext(path)
        path = remove_rev(path)
        path = add_rev(path, text)
        path = add_ext(path)
        self.dest_edit.setText(path)
        self.prevtarget = text
        self.compose_command()

    @pyqtSlot()
    def compose_command(self) -> List[str]:
        content = self.content_mode.checkedId()
        targetrev = hglib.tounicode(self.get_selected_rev())
        if content == self._archive_content_all_files:
            incl = None
        elif content == self._archive_content_touched_files:
            incl = 'set:added() or modified()'
        elif content == self._archive_content_touched_since:
            expr = 'set:status(%s, %s, added() or modified())'
            rootrev = hglib.tounicode(self.get_selected_rootrev())
            incl = hglib.formatfilespec(expr, rootrev, targetrev)
        else:
            assert False
        cmdline = hglib.buildcmdargs('archive', self.dest_edit.text(),
                                     r=targetrev,
                                     S=self.subrepos_chk.isChecked(), I=incl,
                                     t=self.get_selected_archive_type()['type'])
        self.hgcmd_txt.setText('hg ' + hglib.prettifycmdline(cmdline))
        self.commandChanged.emit()
        return cmdline

    def canRunCommand(self) -> bool:
        rev = self.get_selected_rev()
        if not rev or not self.dest_edit.text():
            return False
        try:
            return scmutil.isrevsymbol(self.repo, rev)
        except error.LookupError:
            # ambiguous changeid
            return False

    def runCommand(self) -> cmdcore.CmdSession:
        # verify input
        type = self.get_selected_archive_type()['type']
        dest = self.dest_edit.text()
        if os.path.exists(dest):
            if type == 'files':
                if os.path.isfile(dest):
                    qtlib.WarningMsgBox(_('Duplicate Name'),
                            _('The destination "%s" already exists as '
                              'a file!') % dest)
                    return cmdcore.nullCmdSession()
                elif os.listdir(dest):
                    if not qtlib.QuestionMsgBox(_('Confirm Overwrite'),
                                 _('The directory "%s" is not empty!\n\n'
                                   'Do you want to overwrite it?') % dest,
                                 parent=self):
                        return cmdcore.nullCmdSession()
            else:
                if os.path.isfile(dest):
                    if not qtlib.QuestionMsgBox(_('Confirm Overwrite'),
                                 _('The file "%s" already exists!\n\n'
                                   'Do you want to overwrite it?') % dest,
                                 parent=self):
                        return cmdcore.nullCmdSession()
                else:
                    qtlib.WarningMsgBox(_('Duplicate Name'),
                          _('The destination "%s" already exists as '
                            'a folder!') % dest)
                    return cmdcore.nullCmdSession()

        cmdline = self.compose_command()
        return self._repoagent.runCommand(cmdline, self)


def createArchiveDialog(repoagent: thgrepo.RepoAgent,
                        rev: Optional[str] = None,
                        parent: Optional[QWidget] = None,
                        minrev: Optional[str] = None) -> cmdui.CmdControlDialog:
    dlg = cmdui.CmdControlDialog(parent)
    dlg.setWindowTitle(_('Archive - %s') % repoagent.displayName())
    dlg.setWindowIcon(qtlib.geticon('hg-archive'))
    dlg.setObjectName('archive')
    dlg.setRunButtonText(_('&Archive'))
    dlg.setCommandWidget(ArchiveWidget(repoagent, rev, dlg, minrev))
    return dlg
