# topic.py - Topic dialog for TortoiseHg
#
# Copyright 2010 Michal De Wildt <michael.dewildt@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

from .qtcore import (
    Qt,
    pyqtSlot,
)
from .qtgui import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFontMetrics,
    QFormLayout,
    QFrame,
    QLabel,
    QLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..util import hglib
from ..util.obsoleteutil import first_known_successors
from ..util.i18n import _
from . import (
    cmdcore,
    qtlib,
)

class TopicDialog(QDialog):

    def __init__(self, repoagent, rev, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() &
                            ~Qt.WindowType.WindowContextHelpButtonHint)
        self._repoagent = repoagent
        repo = repoagent.rawRepo()
        self._cmdsession = cmdcore.nullCmdSession()
        self.rev = rev

        # base layout box
        base = QVBoxLayout()
        base.setSpacing(0)
        base.setContentsMargins(*(0,)*4)
        base.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self.setLayout(base)

        # main layout grid
        formwidget = QWidget(self)
        formwidget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        form = QFormLayout(fieldGrowthPolicy=QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        formwidget.setLayout(form)
        base.addWidget(formwidget)

        self.revLabel = QLabel()  # text is set in self.revUpdated()
        form.addRow(_('Revision:'), self.revLabel)

        # topic combo
        self.topicsCombo = QComboBox()
        self.topicsCombo.setEditable(True)
        self.topicsCombo.setMinimumContentsLength(30)  # cut long name
        self.topicsCombo.currentIndexChanged.connect(self.topicTextChanged)
        self.topicsCombo.editTextChanged.connect(self.topicTextChanged)
        qtlib.allowCaseChangingInput(self.topicsCombo)
        form.addRow(_('Topic:'), self.topicsCombo)

        # bottom buttons
        bbox = QDialogButtonBox()
        self.setBtn = bbox.addButton(_('&Set'), QDialogButtonBox.ButtonRole.ActionRole)
        self.clearBtn = bbox.addButton(_('&Clear'), QDialogButtonBox.ButtonRole.ActionRole)
        bbox.addButton(QDialogButtonBox.StandardButton.Close)
        bbox.rejected.connect(self.reject)
        form.addRow(bbox)

        self.setBtn.clicked.connect(self.set_topic)
        self.clearBtn.clicked.connect(self.clear_topic)

        # horizontal separator
        self.sep = QFrame()
        self.sep.setFrameShadow(QFrame.Shadow.Sunken)
        self.sep.setFrameShape(QFrame.Shape.HLine)
        self.layout().addWidget(self.sep)

        # status line
        self.status = qtlib.StatusLabel()
        self.status.setContentsMargins(4, 2, 4, 4)
        self.layout().addWidget(self.status)
        self._finishmsg = None

        # dialog setting
        self.setWindowTitle(_('Topic - %s') % repoagent.displayName())
        self.setWindowIcon(qtlib.geticon('hg-topics'))

        # prepare to show
        self.clear_status()
        self.revUpdated()
        self.refresh()
        self._repoagent.repositoryChanged.connect(self.refresh)
        self.topicsCombo.setFocus()
        self.topicTextChanged()

    def revUpdated(self):
        if self.rev is None:
            hasunicodestar = QFontMetrics(self.font()).inFont('\u2605')
            if hasunicodestar:
                # The Unicode symbol is a black star:
                revText = '\u2605 ' + _('Working Directory') + ' \u2605'
            else:
                revText = '*** ' + _('Working Directory') + ' ***'
        else:
            revText = '%d (%s)' % (self.rev, self.repo[self.rev])
        self.revLabel.setText(revText)
        self.topicsCombo.setEditText(self._current_topic)

    @property
    def repo(self):
        return self._repoagent.rawRepo()

    @pyqtSlot()
    def refresh(self):
        """Update drop-down list if repo changed."""
        cur = self.topicsCombo.currentText()
        self.topicsCombo.clear()
        self.topicsCombo.addItems(sorted(map(hglib.tounicode,
                                             self.repo.topics)))
        self.topicsCombo.setEditText(cur)

    @pyqtSlot()
    def topicTextChanged(self):
        topic = self.topicsCombo.currentText()
        self.setBtn.setEnabled(bool(topic))

    @property
    def _current_topic(self):
        return hglib.tounicode(self.repo[self.rev].topic())

    def setTopicName(self, name):
        self.topicsCombo.setEditText(name)

    def set_status(self, text, icon=None):
        self.status.setVisible(True)
        self.sep.setVisible(True)
        self.status.set_status(text, icon)

    def clear_status(self):
        self.status.setHidden(True)
        self.sep.setHidden(True)

    def _runTopic(self, *args, **opts):
        self._finishmsg = opts.pop('finishmsg')
        cmdline = hglib.buildcmdargs('topic', *args, **opts)
        self._cmdsession = sess = self._repoagent.runCommand(cmdline, self)
        sess.commandFinished.connect(self._onTopicFinished)

    @pyqtSlot(int)
    def _onTopicFinished(self, ret):
        if ret == 0:
            self.set_status(self._finishmsg, True)
        else:
            self.set_status(self._cmdsession.errorString(), False)
        repo = self.repo.unfiltered()
        ctx = repo[self.rev]
        if ctx.extinct():
            changes = [x for x in first_known_successors(ctx)]
            if changes:
                self.rev = changes[0].rev()
        self.revUpdated()

    @pyqtSlot()
    def set_topic(self):
        topic = self.topicsCombo.currentText()
        finishmsg = _("Set topic to '%s'") % topic
        self._runTopic(topic, rev=self.rev, finishmsg=finishmsg)

    @pyqtSlot()
    def clear_topic(self):
        finishmsg = _("Cleared current topic '%s'") % self._current_topic
        self._runTopic(rev=self.rev, clear=True, finishmsg=finishmsg)
