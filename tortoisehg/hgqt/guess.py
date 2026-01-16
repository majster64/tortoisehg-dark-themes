# guess.py - TortoiseHg's dialogs for detecting copies and renames
#
# Copyright 2010 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import os

from .qtcore import (
    QAbstractTableModel,
    QModelIndex,
    QSettings,
    QThread,
    QTimer,
    Qt,
    pyqtSignal,
)
from .qtgui import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSplitter,
    QTextBrowser,
    QToolButton,
    QTreeView,
    QVBoxLayout,
)

from hgext.largefiles import (
    lfutil,
)

from mercurial import (
    hg,
    patch,
    pycompat,
    similar,
)

from ..util import (
    hglib,
    thread2,
)
from ..util.i18n import _
from . import (
    cmdui,
    htmlui,
    qtlib,
)

# Techincal debt
# Try to cut down on the jitter when findRenames is pressed.  May
# require a splitter.

class DetectRenameDialog(QDialog):
    'Detect renames after they occur'
    matchAccepted = pyqtSignal()

    def __init__(self, repoagent, parent, *pats):
        QDialog.__init__(self, parent)

        self._repoagent = repoagent
        self.pats = pats
        self.thread = None

        self.setWindowTitle(_('Detect Copies/Renames in %s')
                            % repoagent.displayName())
        self.setWindowIcon(qtlib.geticon('thg-guess'))
        self.setWindowFlags(Qt.WindowType.Window)

        layout = QVBoxLayout()
        layout.setContentsMargins(*(2,)*4)
        self.setLayout(layout)

        # vsplit for top & diff
        vsplit = QSplitter(Qt.Orientation.Horizontal)
        utframe = QFrame(vsplit)
        matchframe = QFrame(vsplit)

        utvbox = QVBoxLayout()
        utvbox.setContentsMargins(*(2,)*4)
        utframe.setLayout(utvbox)
        matchvbox = QVBoxLayout()
        matchvbox.setContentsMargins(*(2,)*4)
        matchframe.setLayout(matchvbox)

        hsplit = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(hsplit)
        hsplit.addWidget(vsplit)
        utheader = QHBoxLayout()
        utvbox.addLayout(utheader)

        utlbl = QLabel(_('<b>Unrevisioned Files</b>'))
        utheader.addWidget(utlbl)

        self.refreshBtn = tb = QToolButton()
        tb.setToolTip(_('Refresh file list'))
        tb.setIcon(qtlib.geticon('view-refresh'))
        tb.clicked.connect(self.refresh)
        utheader.addWidget(tb)

        self.unrevlist = QListWidget()
        self.unrevlist.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.unrevlist.doubleClicked.connect(self.onUnrevDoubleClicked)
        utvbox.addWidget(self.unrevlist)

        simhbox = QHBoxLayout()
        utvbox.addLayout(simhbox)
        lbl = QLabel()
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setTickInterval(10)
        slider.setPageStep(10)
        slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        slider.changefunc = lambda v: lbl.setText(
                            _('Min Similarity: %d%%') % v)
        slider.valueChanged.connect(slider.changefunc)
        self.simslider = slider
        lbl.setBuddy(slider)
        simhbox.addWidget(lbl)
        simhbox.addWidget(slider, 1)

        buthbox = QHBoxLayout()
        utvbox.addLayout(buthbox)
        copycheck = QCheckBox(_('Only consider deleted files'))
        copycheck.setToolTip(_('Uncheck to consider all revisioned files '
                               'for copy sources'))
        copycheck.setChecked(True)
        findrenames = QPushButton(_('Find Renames'))
        findrenames.setToolTip(_('Find copy and/or rename sources'))
        findrenames.setEnabled(False)
        findrenames.clicked.connect(self.findRenames)
        buthbox.addWidget(copycheck)
        buthbox.addStretch(1)
        buthbox.addWidget(findrenames)
        self.findbtn, self.copycheck = findrenames, copycheck

        matchlbl = QLabel(_('<b>Candidate Matches</b>'))
        matchvbox.addWidget(matchlbl)
        matchtv = QTreeView()
        matchtv.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        matchtv.setItemsExpandable(False)
        matchtv.setRootIsDecorated(False)
        matchtv.setModel(MatchModel())
        matchtv.setSortingEnabled(True)
        matchtv.selectionModel().selectionChanged.connect(self.showDiff)
        buthbox = QHBoxLayout()
        matchbtn = QPushButton(_('Accept All Matches'))
        matchbtn.clicked.connect(self.acceptMatch)
        matchbtn.setEnabled(False)
        buthbox.addStretch(1)
        buthbox.addWidget(matchbtn)
        matchvbox.addWidget(matchtv)
        matchvbox.addLayout(buthbox)
        self.matchtv, self.matchbtn = matchtv, matchbtn
        def matchselect(s, d):
            count = len(matchtv.selectedIndexes())
            if count:
                self.matchbtn.setText(_('Accept Selected Matches'))
            else:
                self.matchbtn.setText(_('Accept All Matches'))
        selmodel = matchtv.selectionModel()
        selmodel.selectionChanged.connect(matchselect)

        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        sp.setHorizontalStretch(1)
        matchframe.setSizePolicy(sp)

        diffframe = QFrame(hsplit)
        diffvbox = QVBoxLayout()
        diffvbox.setContentsMargins(*(2,)*4)
        diffframe.setLayout(diffvbox)

        difflabel = QLabel(_('<b>Differences from Source to Dest</b>'))
        diffvbox.addWidget(difflabel)
        difftb = QTextBrowser()
        difftb.document().setDefaultStyleSheet(qtlib.thgstylesheet)
        diffvbox.addWidget(difftb)
        self.difftb = difftb

        self.stbar = cmdui.ThgStatusBar()
        layout.addWidget(self.stbar)

        s = QSettings()
        self.restoreGeometry(qtlib.readByteArray(s, 'guess/geom'))
        hsplit.restoreState(qtlib.readByteArray(s, 'guess/hsplit-state'))
        vsplit.restoreState(qtlib.readByteArray(s, 'guess/vsplit-state'))
        slider.setValue(qtlib.readInt(s, 'guess/simslider') or 50)
        self.vsplit, self.hsplit = vsplit, hsplit
        QTimer.singleShot(0, self.refresh)

    @property
    def repo(self):
        return self._repoagent.rawRepo()

    def refresh(self):
        self.repo.thginvalidate()
        with lfutil.lfstatus(self.repo):
            wctx = self.repo[None]
            ws = wctx.status(listunknown=True)
        self.unrevlist.clear()
        dests = []
        for u in ws.unknown:
            dests.append(u)
        for a in ws.added:
            if not wctx[a].renamed():
                dests.append(a)
        for x in dests:
            item = QListWidgetItem(hglib.tounicode(x))
            item.orig = x
            self.unrevlist.addItem(item)
            item.setSelected(x in self.pats)
        if dests:
            self.findbtn.setEnabled(True)
        else:
            self.findbtn.setEnabled(False)
        self.difftb.clear()
        self.pats = []
        model = self.matchtv.model()
        assert model is not None
        self.matchbtn.setEnabled(bool(model.rows))

    def findRenames(self):
        'User pressed "find renames" button'
        if self.thread and self.thread.isRunning():
            QMessageBox.information(self, _('Search already in progress'),
                                    _('Cannot start a new search'))
            return

        # TODO: better to use data(role) instead
        ulist = [it.orig  # pytype: disable=attribute-error
                 for it in self.unrevlist.selectedItems()]  # pytype: disable=attribute-error
        if not ulist:
            # When no files are selected, look for all files
            ulist = [self.unrevlist.item(n).orig  # pytype: disable=attribute-error
                     for n in range(self.unrevlist.count())]  # pytype: disable=attribute-error

        if not ulist:
            QMessageBox.information(self, _('No files to find'),
                _('There are no files that may have been renamed'))
            return

        pct = self.simslider.value() / 100.0
        copies = not self.copycheck.isChecked()
        self.findbtn.setEnabled(False)

        model = self.matchtv.model()
        assert model is not None
        model.clear()
        self.thread = RenameSearchThread(self.repo, ulist, pct, copies)
        self.thread.match.connect(self.rowReceived)
        self.thread.progress.connect(self.stbar.progress)
        self.thread.showMessage.connect(self.stbar.showMessage)
        self.thread.finished.connect(self.searchfinished)
        self.thread.start()

    def searchfinished(self):
        self.stbar.clearProgress()
        for col in pycompat.xrange(3):
            self.matchtv.resizeColumnToContents(col)
        self.findbtn.setEnabled(bool(self.unrevlist.count()))
        model = self.matchtv.model()
        assert model is not None
        self.matchbtn.setEnabled(bool(model.rows))

    def rowReceived(self, args):
        model = self.matchtv.model()
        assert model is not None
        model.appendRow(*args)

    def acceptMatch(self):
        'User pressed "accept match" button'
        remdests = {}
        wctx = self.repo[None]
        model = self.matchtv.model()
        assert model is not None

        # If no rows are selected, ask the user if he'd like to accept all renames
        if self.matchtv.selectionModel().hasSelection():
            itemList = [model.getRow(index) \
                for index in self.matchtv.selectionModel().selectedRows()]
        else:
            itemList = model.rows

        for item in itemList:
            src, dest, percent = item
            if dest in remdests:
                udest = hglib.tounicode(dest)
                QMessageBox.warning(self, _('Multiple sources chosen'),
                    _('You have multiple renames selected for '
                      'destination file:\n%s. Aborting!') % udest)
                return
            remdests[dest] = src

        with self.repo.wlock(), hglib.dirstate_changing_files(self.repo):
            for dest, src in remdests.items():
                if not os.path.exists(self.repo.wjoin(src)):
                    wctx.forget([src]) # !->R
                wctx.copy(src, dest)
                model.remove(dest)
        self.matchAccepted.emit()
        self.refresh()

    def showDiff(self, index):
        'User selected a row in the candidate tree'
        indexes = index.indexes()
        if not indexes:
            return
        index = indexes[0]
        ctx = self.repo[b'.']
        hu = htmlui.htmlui(self.repo.ui)
        model = self.matchtv.model()
        assert model is not None
        row = model.getRow(index)
        src, dest, percent = model.getRow(index)
        aa = self.repo.wread(dest)
        rr = ctx.filectx(src).data()
        date = hglib.displaytime(ctx.date())
        difftext = hglib.unidifftext(rr, date, aa, date, src, dest)
        if not difftext:
            t = _('%s and %s have identical contents\n\n') % \
                    (hglib.tounicode(src), hglib.tounicode(dest))
            hu.write(hglib.fromunicode(t), label=b'ui.error')
        else:
            for t, l in patch.difflabel(difftext.splitlines, True):
                hu.write(t, label=l)
        self.difftb.setHtml(hglib.tounicode(hu.getdata()[0]))

    def onUnrevDoubleClicked(self, index):
        model = self.unrevlist.model()
        assert model is not None
        file = hglib.fromunicode(model.data(index))
        qtlib.editfiles(self.repo, [file])

    def accept(self):
        s = QSettings()
        s.setValue('guess/geom', self.saveGeometry())
        s.setValue('guess/vsplit-state', self.vsplit.saveState())
        s.setValue('guess/hsplit-state', self.hsplit.saveState())
        s.setValue('guess/simslider', self.simslider.value())
        QDialog.accept(self)

    def reject(self):
        if self.thread and self.thread.isRunning():
            self.thread.cancel()
            if self.thread.wait(2000):
                self.thread = None
        else:
            s = QSettings()
            s.setValue('guess/geom', self.saveGeometry())
            s.setValue('guess/vsplit-state', self.vsplit.saveState())
            s.setValue('guess/hsplit-state', self.hsplit.saveState())
            s.setValue('guess/simslider', self.simslider.value())
            QDialog.reject(self)


def _aspercent(s):
    # i18n: percent format
    return _('%d%%') % (s * 100)

class MatchModel(QAbstractTableModel):
    def __init__(self, parent=None):
        QAbstractTableModel.__init__(self, parent)
        self.rows = []
        self.headers = (_('Source'), _('Dest'), _('% Match'))
        self.displayformats = (hglib.tounicode, hglib.tounicode, _aspercent)

    def rowCount(self, parent):
        return len(self.rows)

    def columnCount(self, parent):
        return len(self.headers)

    def data(self, index, role):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            s = self.rows[index.row()][index.column()]
            f = self.displayformats[index.column()]
            return f(s)
        '''
        elif role == Qt.ItemDataRole.ForegroundRole:
            src, dst, pct = self.rows[index.row()]
            if pct == 1.0:
                return QColor('green')
            else:
                return QColor('black')
        elif role == Qt.ItemDataRole.ToolTipRole:
            # explain what row means?
        '''
        return None

    def headerData(self, col, orientation, role):
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        else:
            return self.headers[col]

    def flags(self, index):
        return Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled

    # Custom methods

    def getRow(self, index):
        assert index.isValid()
        return self.rows[index.row()]

    def appendRow(self, *args):
        self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows))
        self.rows.append(args)
        self.endInsertRows()
        self.layoutChanged.emit()

    def clear(self):
        self.beginRemoveRows(QModelIndex(), 0, len(self.rows)-1)
        self.rows = []
        self.endRemoveRows()
        self.layoutChanged.emit()

    def remove(self, dest):
        i = 0
        while i < len(self.rows):
            if self.rows[i][1] == dest:
                self.beginRemoveRows(QModelIndex(), i, i)
                self.rows.pop(i)
                self.endRemoveRows()
            else:
                i += 1
        self.layoutChanged.emit()

    def sort(self, col, order):
        self.beginResetModel()
        self.layoutAboutToBeChanged.emit()
        self.rows.sort(key=lambda x: x[col],
                       reverse=(order == Qt.SortOrder.DescendingOrder))
        self.layoutChanged.emit()
        self.endResetModel()

    def isEmpty(self):
        return not bool(self.rows)

class RenameSearchThread(QThread):
    '''Background thread for searching repository history'''
    match = pyqtSignal(object)
    progress = pyqtSignal(str, object, str, str, object)
    showMessage = pyqtSignal(str)

    def __init__(self, repo, ufiles, minpct, copies):
        super().__init__()
        self.repo = hg.repository(hglib.loadui(), repo.root)
        self.ufiles = ufiles
        self.minpct = minpct
        self.copies = copies
        self.threadid = None

    def run(self):
        def emit(topic, pos, item='', unit='', total=None):
            topic = hglib.tounicode(topic or '')
            item = hglib.tounicode(item or '')
            unit = hglib.tounicode(unit or '')
            self.progress.emit(topic, pos, item, unit, total)
        self.repo.ui.progress = emit
        self.threadid = int(self.currentThreadId())
        try:
            self.search(self.repo)
        except KeyboardInterrupt:
            pass
        except Exception as e:
            self.showMessage.emit(hglib.exception_str(e))
        finally:
            self.threadid = None

    def cancel(self):
        tid = self.threadid
        if tid is None:
            return
        try:
            thread2._async_raise(tid, KeyboardInterrupt)
        except ValueError:
            pass

    def search(self, repo):
        wctx = repo[None]
        pctx = repo[b'.']
        if self.copies:
            ws = wctx.status(listclean=True)
            srcs = ws.removed + ws.deleted
            srcs += ws.modified + ws.clean
        else:
            ws = wctx.status()
            srcs = ws.removed + ws.deleted
        added = [wctx[a] for a in sorted(self.ufiles)]
        removed = [pctx[a] for a in sorted(srcs) if a in pctx]
        # do not consider files of zero length
        added = [fctx for fctx in added if fctx.size() > 0]
        removed = [fctx for fctx in removed if fctx.size() > 0]
        exacts = []
        gen = similar._findexactmatches(repo, added, removed)
        for o, n in gen:
            old, new = o.path(), n.path()
            exacts.append(old)
            self.match.emit([old, new, 1.0])
        if self.minpct == 1.0:
            return
        removed = [r for r in removed if r.path() not in exacts]
        gen = similar._findsimilarmatches(repo, added, removed, self.minpct)
        for o, n, s in gen:
            old, new, sim = o.path(), n.path(), s
            self.match.emit([old, new, sim])
