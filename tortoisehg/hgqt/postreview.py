# postreview.py - post review dialog for TortoiseHg
#
# Copyright 2011 Michael De Wildt <michael.dewildt@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

"""A dialog to allow users to post a review to reviewboard

https://www.reviewboard.org

This dialog requires a fork of the review board mercurial plugin, maintained
by mdelagra, that can be downloaded from:

https://foss.heptapod.net/mercurial/tortoisehg/thg-build-deps/mercurial-reviewboard

More information can be found at http://www.mikeyd.com.au/tortoisehg-reviewboard
"""

from __future__ import annotations

import typing

from .qtcore import (
    QSettings,
    QThread,
    QUrl,
    Qt,
    pyqtSlot,
)
from .qtgui import (
    QDesktopServices,
    QDialog,
    QKeySequence,
    QLineEdit,
    QShortcut,
)

from mercurial import (
    extensions,
    pycompat,
    scmutil,
)

from ..util import hglib
from ..util.i18n import _
from . import (
    cmdcore,
    qtlib,
)
from .hgemail import _ChangesetsModel
from .postreview_ui import Ui_PostReviewDialog

if typing.TYPE_CHECKING:
    from typing import (
        Dict,
        List,
        Optional,
        Sequence,
        Text,
        Union,
    )
    from mercurial import (
        ui as uimod,
    )
    from .qtgui import (
        QCloseEvent,
        QWidget,
    )
    from .thgrepo import (
        RepoAgent,
    )


class LoadReviewDataThread(QThread):
    def __init__ (self, dialog: PostReviewDialog) -> None:
        super().__init__(dialog)
        self.dialog = dialog

    def run(self) -> None:
        msg = None
        if not self.dialog.server:
            msg = _("Invalid Settings - The ReviewBoard server is not setup")
        elif not self.dialog.user:
            msg = _("Invalid Settings - Please provide your ReviewBoard username")
        else:
            rb = extensions.find(b"reviewboard")
            try:
                pwd = self.dialog.password
                #if we don't have a password send something here to skip
                #the cli getpass in the extension. We will set the password
                #later
                if not pwd:
                    pwd = b"None"

                self.reviewboard = rb.make_rbclient(self.dialog.server,
                                                    self.dialog.user,
                                                    pwd)
                self.loadCombos()

            except rb.ReviewBoardError as e:
                msg = e.msg
            except TypeError:
                msg = _("Invalid reviewboard plugin. Please download the "
                        "Mercurial reviewboard plugin version 3.5 or higher "
                        "from the website below.\n\n %s") % \
                        'https://foss.heptapod.net/mercurial/tortoisehg/thg-build-deps/mercurial-reviewboard'

        self.dialog.error_message = msg

    def loadCombos(self) -> None:
        #Get the index of a users previously selected repo id
        index = 0
        count = 0

        self.dialog.qui.progress_label.setText("Loading repositories...")
        for r in self.reviewboard.repositories():
            if r.id == self.dialog.repo_id:
                index = count
            self.dialog.qui.repo_id_combo.addItem(str(r.id) + ": " + r.name)
            count += 1

        if self.dialog.qui.repo_id_combo.count():
            self.dialog.qui.repo_id_combo.setCurrentIndex(index)

        self.dialog.qui.progress_label.setText("Loading existing reviews...")
        for r in self.reviewboard.pending_user_requests():
            summary = str(r.id) + ": " + r.summary[0:100]
            self.dialog.qui.review_id_combo.addItem(summary)

        if self.dialog.qui.review_id_combo.count():
            self.dialog.qui.review_id_combo.setCurrentIndex(0)

class PostReviewDialog(QDialog):
    """Dialog for sending patches to reviewboard"""
    def __init__(self,
                 ui: uimod.ui,
                 repoagent: RepoAgent,
                 revs: Sequence[Union[bytes, int]],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.ui = ui
        self._repoagent = repoagent
        self._cmdsession = cmdcore.nullCmdSession()
        self._cmdoutputs = []
        self.error_message = None

        self.qui = Ui_PostReviewDialog()
        self.qui.setupUi(self)

        self.initChangesets(revs)
        self.readSettings()

        self.review_thread = LoadReviewDataThread(self)
        self.review_thread.finished.connect(self.errorPrompt)
        self.review_thread.start()
        QShortcut(QKeySequence('Ctrl+Return'), self, self.accept)
        QShortcut(QKeySequence('Ctrl+Enter'), self, self.accept)

    @property
    def repo(self):
        return self._repoagent.rawRepo()

    @pyqtSlot()
    def passwordPrompt(self) -> bool:
        pwd, ok = qtlib.getTextInput(self,
                                     _('Review Board'),
                                     _('Password:'),
                                     mode=QLineEdit.EchoMode.Password)
        if ok and pwd:
            self.password = hglib.fromunicode(pwd)
            return True
        else:
            self.password = None
            return False

    @pyqtSlot()
    def errorPrompt(self) -> None:
        self.qui.progress_bar.hide()
        self.qui.progress_label.hide()

        if self.error_message:
            qtlib.ErrorMsgBox(_('Review Board'),
                              _('Error'), self.error_message)
            self.close()
        elif self.isValid():
            self.qui.post_review_button.setEnabled(True)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._cmdsession.isFinished():
            self._cmdsession.abort()
            event.ignore()
            return

        # Dispose of the review data thread
        self.review_thread.terminate()
        self.review_thread.wait()

        self.writeSettings()
        super().closeEvent(event)

    def readSettings(self) -> None:
        s = QSettings()

        self.restoreGeometry(qtlib.readByteArray(s, 'reviewboard/geom'))

        self.qui.publish_immediately_check.setChecked(
            qtlib.readBool(s, 'reviewboard/publish_immediately_check'))
        self.qui.outgoing_changes_check.setChecked(
            qtlib.readBool(s, 'reviewboard/outgoing_changes_check'))
        self.qui.branch_check.setChecked(
            qtlib.readBool(s, 'reviewboard/branch_check'))
        self.qui.update_fields.setChecked(
            qtlib.readBool(s, 'reviewboard/update_fields'))
        self.qui.summary_edit.addItems(
            qtlib.readStringList(s, 'reviewboard/summary_edit_history'))

        try:
            self.repo_id = int(self.repo.ui.config(b'reviewboard', b'repoid'))
        except Exception:
            self.repo_id = None

        if not self.repo_id:
            self.repo_id = qtlib.readInt(s, 'reviewboard/repo_id')

        self.server = self.repo.ui.config(b'reviewboard', b'server')
        self.user = self.repo.ui.config(b'reviewboard', b'user')
        self.password = self.repo.ui.config(b'reviewboard', b'password')
        self.browser = self.repo.ui.config(b'reviewboard', b'browser')

    def writeSettings(self) -> None:
        s = QSettings()
        s.setValue('reviewboard/geom', self.saveGeometry())
        s.setValue('reviewboard/publish_immediately_check',
                   self.qui.publish_immediately_check.isChecked())
        s.setValue('reviewboard/branch_check',
                   self.qui.branch_check.isChecked())
        s.setValue('reviewboard/outgoing_changes_check',
                   self.qui.outgoing_changes_check.isChecked())
        s.setValue('reviewboard/update_fields',
                   self.qui.update_fields.isChecked())
        s.setValue('reviewboard/repo_id', self.getRepoId())

        def itercombo(w):
            if w.currentText():
                yield w.currentText()
            for i in pycompat.xrange(w.count()):
                if w.itemText(i) != w.currentText():
                    yield w.itemText(i)

        s.setValue('reviewboard/summary_edit_history',
                   list(itercombo(self.qui.summary_edit))[:10])

    def initChangesets(self,
                       revs: Sequence[Union[bytes, int]],
                       selected_revs: Optional[Sequence[int]] = None) -> None:
        def purerevs(revs: Sequence[Union[bytes, int]]) -> Sequence[int]:
            return [r for r in scmutil.revrange(self.repo, revs)]
        if selected_revs:
            selectedrevs = purerevs(selected_revs)
        else:
            selectedrevs = purerevs(revs)

        self._changesets = _ChangesetsModel(self.repo,
                                            # TODO: [':'] is inefficient
                                            revs=purerevs(revs or [b':']),
                                            selectedrevs=selectedrevs,
                                            parent=self)

        self.qui.changesets_view.setModel(self._changesets)

    @property
    def selectedRevs(self) -> List[int]:
        """Returns list of revisions to be sent"""
        return self._changesets.selectedrevs

    @property
    def allRevs(self) -> List[int]:
        """Returns list of revisions to be sent"""
        return self._changesets.revs

    def getRepoId(self) -> str:
        comboText = self.qui.repo_id_combo.currentText().split(":")
        return comboText[0]

    def getReviewId(self) -> str:
        comboText = self.qui.review_id_combo.currentText().split(":")
        return comboText[0]

    def getSummary(self) -> str:
        comboText = self.qui.review_id_combo.currentText().split(":")
        return comboText[1]

    def postReviewOpts(self, **opts) -> Dict[str, Union[bool, str]]:
        """Generate opts for reviewboard by form values"""
        opts['outgoingchanges'] = self.qui.outgoing_changes_check.isChecked()
        opts['branch'] = self.qui.branch_check.isChecked()
        opts['publish'] = self.qui.publish_immediately_check.isChecked()

        if self.qui.tab_widget.currentIndex() == 1:
            opts["existing"] = self.getReviewId()
            opts['update'] = self.qui.update_fields.isChecked()
            opts['summary'] = self.getSummary()
        else:
            opts['repoid'] = self.getRepoId()
            opts['summary'] = self.qui.summary_edit.currentText()

        if len(self.selectedRevs) > 1:
            #Set the parent to the revision below the last one on the list
            #so all checked revisions are included in the request
            ctx = self.repo[self.selectedRevs[0]]
            opts['parent'] = str(ctx.p1().rev())

        # Always use the upstream repo to determine the parent diff base
        # without the diff uploaded to review board dies
        opts['outgoing'] = True

        #Set the password just in  case the user has opted to not save it
        opts['password'] = hglib.tounicode(self.password)

        return opts

    def isValid(self) -> bool:
        """Filled all required values?"""
        if not self.qui.repo_id_combo.currentText():
            return False

        if self.qui.tab_widget.currentIndex() == 1:
            if not self.qui.review_id_combo.currentText():
                return False

        if not self.allRevs:
            return False

        return True

    @pyqtSlot()
    def tabChanged(self) -> None:
        self.qui.post_review_button.setEnabled(self.isValid())

    @pyqtSlot()
    def branchCheckToggle(self) -> None:
        if self.qui.branch_check.isChecked():
            self.qui.outgoing_changes_check.setChecked(False)

        self.toggleOutgoingChangesets()

    @pyqtSlot()
    def outgoingChangesCheckToggle(self) -> None:
        if self.qui.outgoing_changes_check.isChecked():
            self.qui.branch_check.setChecked(False)

        self.toggleOutgoingChangesets()

    def toggleOutgoingChangesets(self) -> None:
        branch = self.qui.branch_check.isChecked()
        outgoing = self.qui.outgoing_changes_check.isChecked()
        if branch or outgoing:
            self.initChangesets(self.allRevs, [self.selectedRevs.pop()])
            self.qui.changesets_view.setEnabled(False)
        else:
            self.initChangesets(self.allRevs, self.allRevs)
            self.qui.changesets_view.setEnabled(True)

    def close(self) -> None:
        super().close()

    def accept(self) -> None:
        if not self.isValid():
            return
        if not self.password and not self.passwordPrompt():
            return

        self.qui.progress_bar.show()
        self.qui.progress_label.setText("Posting Review...")
        self.qui.progress_label.show()

        def cmdargs(opts: Dict[str, Union[bool, str]]) -> List[str]:
            args = []
            for k, v in opts.items():
                if isinstance(v, bool):
                    if v:
                        args.append('--%s' % k.replace('_', '-'))
                else:
                    for e in hglib.isbasestring(v) and [v] or v:
                        args += ['--%s' % k.replace('_', '-'), e]

            return args

        opts = self.postReviewOpts()

        revstr = str(self.selectedRevs.pop())

        self.qui.post_review_button.setEnabled(False)
        self.qui.close_button.setEnabled(False)

        cmdline = ['postreview'] + cmdargs(opts) + [revstr]
        self._cmdsession = sess = self._repoagent.runCommand(cmdline, self)
        del self._cmdoutputs[:]
        sess.commandFinished.connect(self.onCompletion)
        sess.outputReceived.connect(self._captureOutput)

    @pyqtSlot()
    def onCompletion(self) -> None:
        self.qui.progress_bar.hide()
        self.qui.progress_label.hide()

        output = hglib.fromunicode(''.join(self._cmdoutputs), 'replace')

        saved = b'saved:' in output
        published = b'published:' in output
        if saved or published:
            if saved:
                url = hglib.tounicode(output.split(b'saved: ').pop().strip())
                msg = _('Review draft posted to %s\n') % url
            else:
                url = output.split(b'published: ').pop().strip()
                url = hglib.tounicode(url)
                msg = _('Review published to %s\n') % url

            QDesktopServices.openUrl(QUrl(url))

            qtlib.InfoMsgBox(_('Review Board'), _('Success'),
                               msg, parent=self)
        else:
            error = output.split(b'abort: ').pop().strip()
            if error[:29] == b"HTTP Error: basic auth failed":
                if self.passwordPrompt():
                    self.accept()
                else:
                    self.qui.post_review_button.setEnabled(True)
                    self.qui.close_button.setEnabled(True)
                    return
            else:
                qtlib.ErrorMsgBox(_('Review Board'),
                                  _('Error'), hglib.tounicode(error))

        self.writeSettings()
        super().accept()

    @pyqtSlot(str, str)
    def _captureOutput(self, msg: str, label: str) -> None:
        if label != 'control':
            self._cmdoutputs.append(msg)

    @pyqtSlot()
    def onSettingsButtonClicked(self) -> None:
        from tortoisehg.hgqt import settings
        if settings.SettingsDialog(parent=self, focus='reviewboard.server').exec():
            # not use repo.configChanged because it can clobber user input
            # accidentally.
            self.repo.invalidateui()  # force reloading config immediately
            self.readSettings()
