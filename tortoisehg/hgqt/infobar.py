# infobar.py - widget for non-modal message
#
# Copyright 2011 Yuya Nishihara <yuya@tcha.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import re

from .qtcore import (
    QTimer,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from .qtgui import (
    QColor,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPalette,
    QPushButton,
    QSizePolicy,
    QStyle,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from mercurial.i18n import _ as hggettext

from mercurial import (
    util,
)

from ..util import hglib
from ..util.i18n import _
from . import qtlib

# Strings and regexes used to convert hashes and subrepo paths into links
_hashregex = re.compile(r'\b[0-9a-fA-F]{12,}')
# Currently converting subrepo paths into links only works in English
_subrepoindicatorpattern = hglib.tounicode(hggettext(b'(in subrepo %s)')
                                           + b'\n')

def _linkifyHash(message, subrepo=''):
    if subrepo:
        p = 'repo:%s?' % subrepo
    else:
        p = 'cset:'
    replaceexpr = lambda m: '<a href="%s">%s</a>' % (p + m.group(0), m.group(0))
    return _hashregex.sub(replaceexpr, message)

def _linkifySubrepoRef(message, subrepo, hash=''):
    if hash:
        hash = '?' + hash
    subrepolink = '<a href="repo:%s%s">%s</a>' % (subrepo, hash, subrepo)
    subrepoindicator = _subrepoindicatorpattern % subrepo
    linkifiedsubrepoindicator = _subrepoindicatorpattern % subrepolink
    message = message.replace(subrepoindicator, linkifiedsubrepoindicator)
    return message

def linkifyMessage(message: str, subrepo=None):
    r"""Convert revision id hashes and subrepo paths in messages into links

    >>> linkifyMessage('abort: 0123456789ab!\nhint: foo\n')
    u'abort: <a href="cset:0123456789ab">0123456789ab</a>!<br>hint: foo<br>'
    >>> linkifyMessage('abort: foo (in subrepo bar)\n', subrepo='bar')
    u'abort: foo (in subrepo <a href="repo:bar">bar</a>)<br>'
    >>> linkifyMessage('abort: 0123456789ab! (in subrepo bar)\nhint: foo\n',
    ...                subrepo='bar') #doctest: +NORMALIZE_WHITESPACE
    u'abort: <a href="repo:bar?0123456789ab">0123456789ab</a>!
    (in subrepo <a href="repo:bar?0123456789ab">bar</a>)<br>hint: foo<br>'

    subrepo name containing regexp backreference, \g:

    >>> linkifyMessage('abort: 0123456789ab! (in subrepo foo\\goo)\n',
    ...                subrepo='foo\\goo') #doctest: +NORMALIZE_WHITESPACE
    u'abort: <a href="repo:foo\\goo?0123456789ab">0123456789ab</a>!
    (in subrepo <a href="repo:foo\\goo?0123456789ab">foo\\goo</a>)<br>'
    """
    message = _linkifyHash(message, subrepo)
    if subrepo:
        hash = ''
        m = _hashregex.search(message)
        if m:
            hash = m.group(0)
        message = _linkifySubrepoRef(message, subrepo, hash)
    return message.replace('\n', '<br>')


# type of InfoBar (the number denotes its priority)
INFO = 1
ERROR = 2
CONFIRM = 3


class InfoBar(QFrame):
    """Non-modal confirmation/alert (like web flash or Chrome's InfoBar)

    Layout::

        |widgets ...                |right widgets ...|x|
    """

    finished = pyqtSignal(int)  # mimic QDialog
    linkActivated = pyqtSignal(str)

    infobartype = INFO

    _colormap = {
        INFO: '#e7f9e0',
        ERROR: '#f9d8d8',
        CONFIRM: '#fae9b3',
        }

    def __init__(self, parent=None):
        super().__init__(parent, frameShape=QFrame.Shape.StyledPanel,
                                      frameShadow=QFrame.Shadow.Plain)
        self.setAutoFillBackground(True)
        p = self.palette()
        p.setColor(QPalette.ColorRole.Window, QColor(self._colormap[self.infobartype]))
        p.setColor(QPalette.ColorRole.WindowText, QColor("black"))
        self.setPalette(p)

        self.setLayout(QHBoxLayout())
        self.layout().setContentsMargins(2, 2, 2, 2)

        self.layout().addStretch()
        self._closebutton = QPushButton(self, flat=True, autoDefault=False,
            icon=self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton))
        if qtlib.IS_RETINA:
            self._closebutton.setIconSize(qtlib.barRetinaIconSize())
        self._closebutton.clicked.connect(self.close)
        self.layout().addWidget(self._closebutton)

    def addWidget(self, w, stretch=0):
        self.layout().insertWidget(self.layout().count() - 2, w, stretch)

    def addRightWidget(self, w):
        self.layout().insertWidget(self.layout().count() - 1, w)

    def closeEvent(self, event):
        if self.isVisible():
            self.finished.emit(0)
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        super().keyPressEvent(event)

    def heightForWidth(self, width):
        # loosely based on the internal strategy of QBoxLayout
        if self.layout().hasHeightForWidth():
            return super().heightForWidth(width)
        else:
            return self.sizeHint().height()


class StatusInfoBar(InfoBar):
    """Show status message"""

    def __init__(self, message, parent=None):
        super().__init__(parent)
        self._msglabel = QLabel(message, self,
                                wordWrap=True,
                                textInteractionFlags=Qt.TextInteractionFlag.TextSelectableByMouse \
                                | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self._msglabel.linkActivated.connect(self.linkActivated)
        self.addWidget(self._msglabel, stretch=1)


class CommandErrorInfoBar(InfoBar):
    """Show command execution failure (with link to open log window)"""

    infobartype = ERROR

    def __init__(self, message, parent=None):
        super().__init__(parent)

        self._msglabel = QLabel(message, self,
                                wordWrap=True,
                                textInteractionFlags=Qt.TextInteractionFlag.TextSelectableByMouse \
                                | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self._msglabel.linkActivated.connect(self.linkActivated)
        self.addWidget(self._msglabel, stretch=1)

        self._loglabel = QLabel('<a href="log:">%s</a>' % _('Show Log'))
        self._loglabel.linkActivated.connect(self.linkActivated)
        self.addRightWidget(self._loglabel)


class ConfirmInfoBar(InfoBar):
    """Show confirmation message with accept/reject buttons"""

    accepted = pyqtSignal()
    rejected = pyqtSignal()
    infobartype = CONFIRM

    def __init__(self, message, parent=None):
        super().__init__(parent)

        # no wordWrap=True and stretch=1, which inserts unwanted space
        # between _msglabel and _buttons.
        self._msglabel = QLabel(message, self,
                                textInteractionFlags=Qt.TextInteractionFlag.TextSelectableByMouse \
                                | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self._msglabel.linkActivated.connect(self.linkActivated)
        self.addWidget(self._msglabel)

        self._buttons = QDialogButtonBox(self)
        self._buttons.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.acceptButton = self._buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        self.rejectButton = self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self._accept)
        self._buttons.rejected.connect(self._reject)
        self.addWidget(self._buttons)

        # so that acceptButton gets focus by default
        self.setFocusProxy(self._buttons)

    def closeEvent(self, event):
        if self.isVisible():
            self.finished.emit(1)
            self.rejected.emit()
            self.hide()  # avoid double emission of finished signal
        super().closeEvent(event)

    @pyqtSlot()
    def _accept(self):
        self.finished.emit(0)
        self.accepted.emit()
        self.hide()
        self.close()

    @pyqtSlot()
    def _reject(self):
        self.finished.emit(1)
        self.rejected.emit()
        self.hide()
        self.close()


class InfoBarPlaceholder(QWidget):
    """Manage geometry of view according to visibility of InfoBar"""

    linkActivated = pyqtSignal(str)

    def __init__(self, repoagent, parent=None):
        super().__init__(parent)
        self._repoagent = repoagent
        lay = QVBoxLayout()
        lay.setSpacing(0)
        lay.setContentsMargins(0, 0, 0, 0)
        self.setLayout(lay)
        self._view = None
        self._activeInfoBar = None
        self._infoLifetime = QTimer(self, singleShot=True)
        self._infoLifetime.timeout.connect(self._clearStaleInfo)
        repoagent.busyChanged.connect(self._clearStaleInfo)

    def setView(self, view):
        assert isinstance(view, QTableView), repr(view)
        lay = self.layout()
        if self._view:
            lay.removeWidget(self._view)
        self._view = view
        lay.addWidget(view)

    def activeInfoBar(self):
        return self._activeInfoBar

    def setInfoBar(self, cls, *args, **kwargs):
        """Show the given infobar at top of the widget

        If the priority of the current infobar is higher than new one,
        the request is silently ignored.
        """
        cleared = self.clearInfoBar(priority=cls.infobartype)
        if not cleared:
            return
        w = cls(*args, **kwargs)
        w.setParent(self)
        w.finished.connect(self._freeInfoBar)
        w.linkActivated.connect(self.linkActivated)
        self._activeInfoBar = w
        self._updateInfoBarGeometry()
        w.show()
        if w.infobartype > INFO:
            w.setFocus()  # to handle key press by InfoBar
        else:
            self._infoLifetime.start(2000)
        return w

    def clearInfoBar(self, priority=None):
        """Close current infobar if available; return True if got empty"""
        if not self._activeInfoBar:
            return True
        if priority is None or self._activeInfoBar.infobartype <= priority:
            self._activeInfoBar.finished.disconnect(self._freeInfoBar)
            self._activeInfoBar.close()
            self._freeInfoBar()  # call directly in case of event delay
            return True
        else:
            return False

    def discardInfoBar(self):
        """Remove current infobar silently with no signal"""
        if self._activeInfoBar:
            self._activeInfoBar.hide()
            self._freeInfoBar()

    @pyqtSlot()
    def _freeInfoBar(self):
        """Disown closed infobar"""
        if not self._activeInfoBar:
            return
        self._activeInfoBar.setParent(None)
        self._activeInfoBar = None
        self._infoLifetime.stop()

        # clear margin for overlay
        self.layout().setContentsMargins(0, 0, 0, 0)

    @pyqtSlot()
    def _clearStaleInfo(self):
        # do not clear message while command is running because it doubles
        # as busy indicator
        if self._repoagent.isBusy() or self._infoLifetime.isActive():
            return
        self.clearInfoBar(INFO)

    def _updateInfoBarGeometry(self):
        if not self._activeInfoBar:
            return
        w = self._activeInfoBar
        f = self
        w.setGeometry(0, 0, f.width(), w.heightForWidth(f.width()))

        # give margin to make header or first row accessible. without header,
        # column width cannot be changed while confirmation is presented.
        #
        #                  CONFIRM         ERROR           INFO
        #   ____________   ....            ....            ____
        #    :                  cmy        ____ cmy        ---- h.y
        #    : w.height    ____            ---- h.y        ____ h.height
        #   _:__________   ---- h.y        ____ h.height
        #                  ____ h.height
        #
        h = self._view.horizontalHeader()
        if w.infobartype >= CONFIRM:
            cmy = w.height() - h.y()
        elif w.infobartype >= ERROR:
            cmy = w.height() - h.y() - h.height()
        else:
            cmy = 0
        self.layout().setContentsMargins(0, max(cmy, 0), 0, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._updateInfoBarGeometry()

    @pyqtSlot(str)
    def showMessage(self, msg):
        if msg:
            self.setInfoBar(StatusInfoBar, msg)
        else:
            self.clearInfoBar(priority=StatusInfoBar.infobartype)

    @pyqtSlot(str, str)
    def showOutput(self, msg: str, label: str, maxlines=2, maxwidth=140):
        labelslist = label.split()
        if 'ui.error' in labelslist:
            # Check if a subrepo is set in the label list
            subrepo = None
            subrepolabel = 'subrepo='
            for label in labelslist:
                if label.startswith(subrepolabel):
                    # The subrepo "label" is encoded ascii
                    subrepo = hglib.tounicode(util.urlreq.unquote(
                        hglib.fromunicode(label)[len(subrepolabel):]))
                    break
            # Limit the text shown on the info bar to maxlines lines of up to
            # maxwidth chars
            msglines = msg.strip().splitlines()
            infolines = []
            for line in msglines[0:maxlines]:
                if len(line) > maxwidth:
                    line = line[0:maxwidth] + ' ...'
                infolines.append(line)
            if len(msglines) > maxlines and not infolines[-1].endswith('...'):
                infolines[-1] += ' ...'
            infomsg = linkifyMessage('\n'.join(infolines), subrepo=subrepo)
            self.setInfoBar(CommandErrorInfoBar, infomsg)
