# chunks.py - TortoiseHg patch/diff browser and editor
#
# Copyright 2010 Steve Borho <steve@borho.org>
# Copyright (C) 2026 Peter Demcak <majster64@gmail.com> (dark theme)
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import os
import re
import sys

from . import qsci as Qsci
from .qtcore import (
    QPoint,
    QTimer,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from .qtgui import (
    QAction,
    QColor,
    QDialog,
    QFontMetrics,
    QFrame,
    QHBoxLayout,
    QKeySequence,
    QLabel,
    QMenu,
    QPainter,
    QPixmap,
    QPen,
    QSplitter,
    QStyle,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mercurial import (
    commands,
    patch,
    pycompat,
    scmutil,
    util,
)

from ..util import hglib
from ..util.patchctx import patchctx
from ..util.i18n import _
from . import (
    blockmatcher,
    filedata,
    filelistview,
    lexers,
    manifestmodel,
    qscilib,
    qtlib,
    rejects,
    revert,
    visdiff,
)

from .theme import THEME

# TODO
# Add support for tools like TortoiseMerge that help resolve rejected chunks

qsci = Qsci.QsciScintilla

class ChunksWidget(QWidget):

    linkActivated = pyqtSignal(str)
    showMessage = pyqtSignal(str)
    chunksSelected = pyqtSignal(bool)
    fileSelected = pyqtSignal(bool)
    fileModelEmpty = pyqtSignal(bool)
    fileModified = pyqtSignal()

    contextmenu = None

    def __init__(self, repoagent, parent):
        QWidget.__init__(self, parent)

        self._repoagent = repoagent
        self.currentFile = None

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(2, 2, 2, 2)
        self.setLayout(layout)

        self.splitter = QSplitter(self)
        self.splitter.setOrientation(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.layout().addWidget(self.splitter)

        repo = self._repoagent.rawRepo()
        self.filelist = filelistview.HgFileListView(self)
        model = manifestmodel.ManifestModel(
            repoagent, self, statusfilter='MAR', flat=True)
        self.filelist.setModel(model)
        self.filelist.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.filelist.customContextMenuRequested.connect(self.menuRequest)
        self.filelist.doubleClicked.connect(self.vdiff)

        self.fileListFrame = QFrame(self.splitter)
        self.fileListFrame.setFrameShape(QFrame.Shape.NoFrame)
        vbox = QVBoxLayout()
        vbox.setSpacing(0)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.addWidget(self.filelist)
        self.fileListFrame.setLayout(vbox)

        self.diffbrowse = DiffBrowser(self.splitter)
        self.diffbrowse.showMessage.connect(self.showMessage)
        self.diffbrowse.linkActivated.connect(self.linkActivated)
        self.diffbrowse.chunksSelected.connect(self.chunksSelected)

        self.filelist.fileSelected.connect(self.displayFile)
        self.filelist.clearDisplay.connect(self.diffbrowse.clearDisplay)

        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 3)
        self.timerevent = self.startTimer(500)

        self._actions = {}
        for name, desc, icon, key, tip, cb in [
            ('diff', _('Visual Diff'), 'visualdiff', 'Ctrl+D',
              _('View file changes in external diff tool'), self.vdiff),
            ('edit', _('Edit Local'), 'edit-file', 'Shift+Ctrl+L',
              _('Edit current file in working copy'), self.editCurrentFile),
            ('revert', _('Revert to Revision'), 'hg-revert', 'Shift+Ctrl+R',
              _('Revert file(s) to contents at this revision'),
              self.revertfile),
            ]:
            act = QAction(desc, self)
            if icon:
                act.setIcon(qtlib.geticon(icon))
            if key:
                qtlib.setContextMenuShortcut(act, key)
            if tip:
                act.setStatusTip(tip)
            if cb:
                act.triggered.connect(cb)
            self._actions[name] = act
            self.addAction(act)

    @property
    def repo(self):
        return self._repoagent.rawRepo()

    @pyqtSlot(QPoint)
    def menuRequest(self, point):
        actionlist = ['diff', 'edit', 'revert']
        if not self.contextmenu:
            menu = QMenu(self)
            for act in actionlist:
                menu.addAction(self._actions[act])
            self.contextmenu = menu
        self.contextmenu.exec(self.filelist.viewport().mapToGlobal(point))

    def vdiff(self):
        filenames = self.getSelectedFiles()
        if len(filenames) == 0:
            return
        opts = {'change':self.ctx.rev()}
        dlg = visdiff.visualdiff(self.repo.ui, self.repo, filenames, opts)
        if dlg:
            dlg.exec()

    def revertfile(self):
        filenames = [hglib.tounicode(f) for f in self.getSelectedFiles()]
        if len(filenames) == 0:
            return
        rev = self.ctx.rev()
        if rev is None:
            rev = self.ctx.p1().rev()
        dlg = revert.RevertDialog(self._repoagent, filenames, rev, self)
        dlg.exec()
        dlg.deleteLater()

    def timerEvent(self, event):
        'Periodic poll of currently displayed patch or working file'
        if not hasattr(self, 'filelist'):
            return
        ctx = self.ctx
        if ctx is None:
            return
        if isinstance(ctx, patchctx):
            path = ctx._path
            mtime = ctx._mtime
        elif self.currentFile:
            path = self.repo.wjoin(self.currentFile)
            mtime = self.mtime
        else:
            return
        try:
            if os.path.exists(path):
                newmtime = os.path.getmtime(path)
                if mtime != newmtime:
                    self.mtime = newmtime
                    self.refresh()
        except OSError:
            pass

    def runPatcher(self, fp, wfile, updatestate):
        # don't repo.ui.copy(), which is protected to clone baseui since hg 2.9
        ui = self.repo.ui
        class warncapt(ui.__class__):
            def warn(self, msg, *args, **opts):
                self.write(msg)
        ui = warncapt(ui)

        ok = True
        repo = self.repo
        ui.pushbuffer()
        try:
            eolmode = ui.config(b'patch', b'eol')
            if eolmode.lower() not in patch.eolmodes:
                eolmode = b'strict'
            else:
                eolmode = eolmode.lower()
            # 'updatestate' flag has no effect since hg 1.9
            try:
                ret = patch.internalpatch(ui, repo, fp, 1, files=None,
                                          eolmode=eolmode, similarity=0)
            except ValueError:
                ret = -1
            if ret < 0:
                ok = False
                self.showMessage.emit(_('Patch failed to apply'))
        except (patch.PatchError, OSError) as err:
            ok = False
            self.showMessage.emit(hglib.exception_str(err))
        rejfilere = re.compile(br'\b%s\.rej\b' % re.escape(wfile))
        for line in ui.popbuffer().splitlines():
            if rejfilere.search(line):
                if qtlib.QuestionMsgBox(_('Manually resolve rejected chunks?'),
                                        hglib.tounicode(line) + '<br><br>' +
                                        _('Edit patched file and rejects?'),
                                       parent=self):
                    dlg = rejects.RejectsDialog(repo.ui, repo.wjoin(wfile),
                                                self)
                    if dlg.exec() == QDialog.DialogCode.Accepted:
                        ok = True
                    break
        return ok

    def editCurrentFile(self):
        ctx = self.ctx
        if isinstance(ctx, patchctx):
            paths = [ctx._path]
        else:
            paths = self.getSelectedFiles()
        qtlib.editfiles(self.repo, paths, parent=self)

    def getSelectedFileAndChunks(self):
        chunks = self.diffbrowse.curchunks
        if chunks:
            dchunks = [c for c in chunks[1:] if c.selected]
            return self.currentFile, [chunks[0]] + dchunks
        else:
            return self.currentFile, []

    def getSelectedFiles(self):
        return self.filelist.getSelectedFiles()

    def deleteSelectedChunks(self):
        'delete currently selected chunks'
        repo = self.repo
        chunks = self.diffbrowse.curchunks
        dchunks = [c for c in chunks[1:] if c.selected]
        if not dchunks:
            self.showMessage.emit(_('No deletable chunks'))
            return
        ctx = self.ctx
        kchunks = [c for c in chunks[1:] if not c.selected]
        revertall = False
        if not kchunks:
            if isinstance(ctx, patchctx):
                revertmsg = _('Completely remove file from patch?')
            else:
                revertmsg = _('Revert all file changes?')
            revertall = qtlib.QuestionMsgBox(_('No chunks remain'), revertmsg)
        if isinstance(ctx, patchctx):
            repo.thgbackup(ctx._path)
            fp = util.atomictempfile(ctx._path, b'wb')
            buf = util.bytesio()
            try:
                if ctx._ph.comments:
                    buf.write(b'\n'.join(ctx._ph.comments))
                    buf.write(b'\n\n')
                needsnewline = False
                for wfile in ctx._fileorder:
                    if wfile == self.currentFile:
                        if revertall:
                            continue
                        chunks[0].write(buf)
                        for chunk in kchunks:
                            chunk.write(buf)
                    else:
                        if buf.tell() and not buf.getvalue().endswith(b'\n'):
                            buf.write(b'\n')
                        for chunk in ctx._files[wfile]:
                            chunk.write(buf)
                fp.write(buf.getvalue())
                fp.close()
            finally:
                del fp
            ctx.invalidate()
            self.fileModified.emit()
        else:
            path = repo.wjoin(self.currentFile)
            if not os.path.exists(path):
                self.showMessage.emit(_('file has been deleted, refresh'))
                return
            if self.mtime != os.path.getmtime(path):
                self.showMessage.emit(_('file has been modified, refresh'))
                return
            repo.thgbackup(path)
            if revertall:
                commands.revert(repo.ui, repo, path, no_backup=True)
            else:
                wlock = repo.wlock()
                try:
                    # atomictemp can preserve file permission
                    wf = repo.wvfs(self.currentFile, b'wb', atomictemp=True)
                    wf.write(self.diffbrowse.origcontents)
                    wf.close()
                    fp = util.bytesio()
                    chunks[0].write(fp)
                    for c in kchunks:
                        c.write(fp)
                    fp.seek(0)
                    self.runPatcher(fp, self.currentFile, False)
                finally:
                    wlock.release()
            self.fileModified.emit()

    def mergeChunks(self, wfile, chunks):
        def isAorR(header):
            for line in header:
                if line.startswith(b'--- /dev/null'):
                    return True
                if line.startswith(b'+++ /dev/null'):
                    return True
            return False
        repo = self.repo
        ctx = self.ctx
        if isinstance(ctx, patchctx):
            if wfile in ctx._files:
                patchchunks = ctx._files[wfile]
                if isAorR(chunks[0].header) or isAorR(patchchunks[0].header):
                    qtlib.InfoMsgBox(_('Unable to merge chunks'),
                                    _('Add or remove patches must be merged '
                                      'in the working directory'))
                    return False
                # merge new chunks into existing chunks, sorting on start line
                newchunks = [chunks[0]]
                pidx = nidx = 1
                while pidx < len(patchchunks) or nidx < len(chunks):
                    if pidx == len(patchchunks):
                        newchunks.append(chunks[nidx])
                        nidx += 1
                    elif nidx == len(chunks):
                        newchunks.append(patchchunks[pidx])
                        pidx += 1
                    elif chunks[nidx].fromline < patchchunks[pidx].fromline:
                        newchunks.append(chunks[nidx])
                        nidx += 1
                    else:
                        newchunks.append(patchchunks[pidx])
                        pidx += 1
                ctx._files[wfile] = newchunks
            else:
                # add file to patch
                ctx._files[wfile] = chunks
                ctx._fileorder.append(wfile)
            repo.thgbackup(ctx._path)
            fp = util.atomictempfile(ctx._path, b'wb')
            try:
                if ctx._ph.comments:
                    fp.write(b'\n'.join(ctx._ph.comments))
                    fp.write(b'\n\n')
                for file in ctx._fileorder:
                    for chunk in ctx._files[file]:
                        chunk.write(fp)
                fp.close()
                ctx.invalidate()
                self.fileModified.emit()
                return True
            finally:
                del fp
        else:
            # Apply chunks to wfile
            repo.thgbackup(repo.wjoin(wfile))
            fp = util.bytesio()
            for c in chunks:
                c.write(fp)
            fp.seek(0)
            wlock = repo.wlock()
            try:
                return self.runPatcher(fp, wfile, True)
            finally:
                wlock.release()

    def getFileList(self):
        return self.ctx.files()

    def removeFile(self, wfile):
        repo = self.repo
        ctx = self.ctx
        if isinstance(ctx, patchctx):
            repo.thgbackup(ctx._path)
            fp = util.atomictempfile(ctx._path, b'wb')
            try:
                if ctx._ph.comments:
                    fp.write(b'\n'.join(ctx._ph.comments))
                    fp.write(b'\n\n')
                for file in ctx._fileorder:
                    if file == wfile:
                        continue
                    for chunk in ctx._files[file]:
                        chunk.write(fp)
                fp.close()
            finally:
                del fp
            ctx.invalidate()
        else:
            fullpath = repo.wjoin(wfile)
            repo.thgbackup(fullpath)
            wasadded = wfile in repo[None].added()
            try:
                commands.revert(repo.ui, repo, fullpath, rev=b'.',
                                no_backup=True)
                if wasadded and os.path.exists(fullpath):
                    os.unlink(fullpath)
            except OSError:
                qtlib.InfoMsgBox(_("Unable to remove"),
                                 _("Unable to remove file %s,\n"
                                   "permission denied") %
                                    hglib.tounicode(wfile))
        self.fileModified.emit()

    def getChunksForFile(self, wfile):
        repo = self.repo
        ctx = self.ctx
        if isinstance(ctx, patchctx):
            if wfile in ctx._files:
                return ctx._files[wfile]
            else:
                return []
        else:
            buf = util.bytesio()
            diffopts = patch.diffopts(repo.ui, {b'git':True})
            m = scmutil.matchfiles(repo, [wfile])
            for p in patch.diff(repo, ctx.p1().node(), None, match=m,
                                opts=diffopts):
                buf.write(p)
            buf.seek(0)
            chunks = patch.parsepatch(buf)
            if chunks:
                header = chunks[0]
                return [header] + header.hunks
            else:
                return []

    @pyqtSlot(str, str)
    def displayFile(self, file, status):
        if isinstance(file, pycompat.unicode):
            file = hglib.fromunicode(file)
        if not isinstance(file, pycompat.unicode):
            status = hglib.tounicode(status)
        if file:
            self.currentFile = file
            path = self.repo.wjoin(file)
            if os.path.exists(path):
                self.mtime = os.path.getmtime(path)
            else:
                self.mtime = None
            self.diffbrowse.displayFile(file, status)
            self.fileSelected.emit(True)
        else:
            self.currentFile = None
            self.diffbrowse.clearDisplay()
            self.diffbrowse.clearChunks()
            self.fileSelected.emit(False)

    def setContext(self, ctx):
        self.diffbrowse.setContext(ctx)
        model = self.filelist.model()
        assert isinstance(model, manifestmodel.ManifestModel)
        model.setRawContext(ctx)
        empty = len(ctx.files()) == 0
        self.fileModelEmpty.emit(empty)
        self.fileSelected.emit(not empty)
        if empty:
            self.currentFile = None
            self.diffbrowse.clearDisplay()
            self.diffbrowse.clearChunks()
        self.diffbrowse.updateSummary()
        self.ctx = ctx
        for act in ['diff', 'revert']:
            self._actions[act].setEnabled(ctx.rev() is None)

    def refresh(self):
        ctx = self.ctx
        if isinstance(ctx, patchctx):
            # if patch mtime has not changed, it could return the same ctx
            ctx = self.repo[ctx._path]
        else:
            self.repo.thginvalidate()
            ctx = self.repo[ctx.node()]
        self.setContext(ctx)

    def loadSettings(self, qs, prefix):
        self.diffbrowse.loadSettings(qs, prefix)

    def saveSettings(self, qs, prefix):
        self.diffbrowse.saveSettings(qs, prefix)


# DO NOT USE.  Sadly, this does not work.
class ElideLabel(QLabel):
    def __init__(self, text='', parent=None):
        QLabel.__init__(self, text, parent)

    def sizeHint(self):
        return super().sizeHint()

    def paintEvent(self, event):
        p = QPainter()
        fm = QFontMetrics(self.font())
        if fm.horizontalAdvance(self.text()): # > self.contentsRect().width():
            elided = fm.elidedText(self.text(), Qt.TextElideMode.ElideLeft,
                                   self.rect().width(), 0)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight |
                       Qt.TextFlag.TextSingleLine, elided)
        else:
            super().paintEvent(event)

class DiffBrowser(QFrame):
    """diff browser"""

    linkActivated = pyqtSignal(str)
    showMessage = pyqtSignal(str)
    chunksSelected = pyqtSignal(bool)

    def __init__(self, parent):
        QFrame.__init__(self, parent)

        self.curchunks = []
        self.countselected = 0
        self._ctx = None
        self._lastfile = None
        self._status = None

        vbox = QVBoxLayout()
        vbox.setContentsMargins(0,0,0,0)
        vbox.setSpacing(0)
        self.setLayout(vbox)

        self.labelhbox = hbox = QHBoxLayout()
        hbox.setContentsMargins(0,0,0,0)
        hbox.setSpacing(2)
        self.layout().addLayout(hbox)
        self.filenamelabel = w = QLabel()
        self.filenamelabel.hide()
        hbox.addWidget(w)
        w.setWordWrap(True)
        f = w.textInteractionFlags()
        w.setTextInteractionFlags(f | Qt.TextInteractionFlag.TextSelectableByMouse)
        w.linkActivated.connect(self.linkActivated)

        self.searchbar = qscilib.SearchToolBar()
        self.searchbar.hide()
        self.searchbar.searchRequested.connect(self.find)
        self.searchbar.conditionChanged.connect(self.highlightText)
        self.addActions(self.searchbar.editorActions())

        self.sumlabel = QLabel()
        self.allbutton = QToolButton()
        self.allbutton.setText(_('All', 'files'))
        self.allbutton.setShortcut(QKeySequence.StandardKey.SelectAll)
        self.allbutton.clicked.connect(self.selectAll)
        self.nonebutton = QToolButton()
        self.nonebutton.setText(_('None', 'files'))
        self.nonebutton.setShortcut(QKeySequence.StandardKey.New)
        self.nonebutton.clicked.connect(self.selectNone)
        self.actionFind = self.searchbar.toggleViewAction()
        self.actionFind.setIcon(qtlib.geticon('edit-find'))
        self.actionFind.setToolTip(_('Toggle display of text search bar'))
        qtlib.newshortcutsforstdkey(QKeySequence.StandardKey.Find, self, self.searchbar.show)
        self.diffToolbar = QToolBar(_('Diff Toolbar'))
        self.diffToolbar.setIconSize(qtlib.smallIconSize())
        self.diffToolbar.setStyleSheet(qtlib.tbstylesheet)
        self.diffToolbar.addAction(self.actionFind)
        hbox.addWidget(self.diffToolbar)
        hbox.addStretch(1)
        hbox.addWidget(self.sumlabel)
        hbox.addWidget(self.allbutton)
        hbox.addWidget(self.nonebutton)

        self.extralabel = w = QLabel()
        w.setWordWrap(True)
        w.linkActivated.connect(self.linkActivated)
        self.layout().addWidget(w)
        self.layout().addSpacing(2)
        w.hide()

        self._forceviewindicator = None
        self.sci = qscilib.Scintilla(self)
        self.sci.setReadOnly(True)
        self.sci.setUtf8(True)
        self.sci.installEventFilter(qscilib.KeyPressInterceptor(self))
        self.sci.setCaretLineVisible(False)
        self.sci.setFont(qtlib.getfont('fontdiff').font())

        self.sci.setMarginType(1, qsci.MarginType.SymbolMargin)
        self.sci.setMarginLineNumbers(1, False)
        self.sci.setMarginWidth(1, QFontMetrics(self.font()).horizontalAdvance('XX'))
        self.sci.setMarginSensitivity(1, True)
        self.sci.marginClicked.connect(self.marginClicked)

        if THEME.enabled:
            # Dark theme colors for margin (line number / markers area)
            self.sci.setMarginsBackgroundColor(THEME.backgroundLighter)
            self.sci.setMarginsForegroundColor(THEME.control_text)

        if THEME.enabled and sys.platform == 'win32':
            # Draw custom checkboxes for Windows: the margin is 12px wide on this platform
            # Not needed for Linux, as the margin is 16px wide
            self._checkedpix = self._create_checkbox_pixmap(True)
            self._uncheckedpix = self._create_checkbox_pixmap(False)
        else:
            self._checkedpix = qtlib.getcheckboxpixmap(QStyle.StateFlag.State_On,
                                                   Qt.GlobalColor.gray, self)
            self._uncheckedpix = qtlib.getcheckboxpixmap(QStyle.StateFlag.State_Off,
                                                    Qt.GlobalColor.gray, self)


        self.selected = self.sci.markerDefine(self._checkedpix, -1)
 
        self.unselected = self.sci.markerDefine(self._uncheckedpix, -1)

        self.vertical = self.sci.markerDefine(qsci.MarkerSymbol.VerticalLine, -1)
        
        if THEME.enabled:
            self.sci.setMarkerBackgroundColor(THEME.chunks_vertical_line, self.vertical)

        self.divider = self.sci.markerDefine(qsci.MarkerSymbol.Background, -1)
        self.selcolor = self.sci.markerDefine(qsci.MarkerSymbol.Background, -1)
        self.sci.setMarkerBackgroundColor(QColor('#BBFFFF'), self.selcolor)
        self.sci.setMarkerBackgroundColor(QColor('#AAAAAA'), self.divider)
        mask = (1 << self.selected) | (1 << self.unselected) | \
               (1 << self.vertical) | (1 << self.selcolor) | (1 << self.divider)
        self.sci.setMarginMarkerMask(1, mask)

        self.blksearch = blockmatcher.BlockList(self)
        self.blksearch.linkScrollBar(self.sci.verticalScrollBar())
        self.blksearch.setVisible(False)

        hbox = QHBoxLayout()
        hbox.addWidget(self.sci)
        hbox.addWidget(self.blksearch)

        lexer = lexers.difflexer(self)
        self.sci.setLexer(lexer)

        self.layout().addLayout(hbox)
        self.layout().addWidget(self.searchbar)

        self.clearDisplay()

    def _create_checkbox_pixmap(self, checked):
        """Create a small checkbox pixmap (12x16) with optional checkmark"""
        pm = QPixmap(12, 16)
        pm.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(pm)
        painter.setRenderHint(qtlib.QtPainterRenderHint.Antialiasing, False)
        
        # Draw rectangle for checkbox
        color = THEME.diff_text
        painter.setPen(QPen(color, 1))
        painter.drawRect(0, 1, 11, 14)
        
        # Draw checkmark if checked
        if checked:
            painter.setPen(QPen(color, 2))
            painter.setRenderHint(qtlib.QtPainterRenderHint.Antialiasing, True)
            painter.drawLine(3, 8, 5, 12)
            painter.drawLine(5, 12, 9, 4)
            painter.setRenderHint(qtlib.QtPainterRenderHint.Antialiasing, False)
        
        painter.end()
        return pm



    def loadSettings(self, qs, prefix):
        self.sci.loadSettings(qs, prefix)

    def saveSettings(self, qs, prefix):
        self.sci.saveSettings(qs, prefix)

    def updateSummary(self):
        self.sumlabel.setText(_('Chunks selected: %d / %d') % (
            self.countselected, len(self.curchunks[1:])))
        self.chunksSelected.emit(self.countselected > 0)

    @pyqtSlot()
    def selectAll(self):
        for chunk in self.curchunks[1:]:
            if not chunk.selected:
                self.sci.markerDelete(chunk.mline, -1)
                self.sci.markerAdd(chunk.mline, self.selected)
                chunk.selected = True
                self.countselected += 1
                for i in pycompat.xrange(*chunk.lrange):
                    self.sci.markerAdd(i, self.selcolor)
        self.updateSummary()

    @pyqtSlot()
    def selectNone(self):
        for chunk in self.curchunks[1:]:
            if chunk.selected:
                self.sci.markerDelete(chunk.mline, -1)
                self.sci.markerAdd(chunk.mline, self.unselected)
                chunk.selected = False
                self.countselected -= 1
                for i in pycompat.xrange(*chunk.lrange):
                    self.sci.markerDelete(i, self.selcolor)
        self.updateSummary()

    #@pyqtSlot(int, int, Qt.KeyboardModifier)
    def marginClicked(self, margin, line, modifiers):
        for chunk in self.curchunks[1:]:
            if line >= chunk.lrange[0] and line < chunk.lrange[1]:
                self.toggleChunk(chunk)
                self.updateSummary()
                return

    def toggleChunk(self, chunk):
        self.sci.markerDelete(chunk.mline, -1)
        if chunk.selected:
            self.sci.markerAdd(chunk.mline, self.unselected)
            chunk.selected = False
            self.countselected -= 1
            for i in pycompat.xrange(*chunk.lrange):
                self.sci.markerDelete(i, self.selcolor)
        else:
            self.sci.markerAdd(chunk.mline, self.selected)
            chunk.selected = True
            self.countselected += 1
            for i in pycompat.xrange(*chunk.lrange):
                self.sci.markerAdd(i, self.selcolor)

    def setContext(self, ctx):
        self._ctx = ctx
        self.sci.setTabWidth(ctx.repo().tabwidth)

    def clearDisplay(self):
        self.sci.clear()
        self.filenamelabel.setText(' ')
        self.extralabel.hide()
        self.blksearch.clear()

    def clearChunks(self):
        self.curchunks = []
        self.countselected = 0
        self.updateSummary()

    def _setupForceViewIndicator(self):
        if not self._forceviewindicator:
            self._forceviewindicator = self.sci.indicatorDefine(self.sci.IndicatorStyle.PlainIndicator)
            self.sci.setIndicatorDrawUnder(True, self._forceviewindicator)
            self.sci.setIndicatorForegroundColor(
                QColor('blue'), self._forceviewindicator)
            # delay until next event-loop in order to complete mouse release
            self.sci.SCN_INDICATORRELEASE.connect(self.forceDisplayFile,
                                                  Qt.ConnectionType.QueuedConnection)

    def forceDisplayFile(self):
        if self.curchunks:
            return
        self.sci.setText(_('Please wait while the file is opened ...'))
        QTimer.singleShot(10,
            lambda: self.displayFile(self._lastfile, self._status, force=True))

    def displayFile(self, filename, status, force=False):
        self._status = status
        self.clearDisplay()
        if filename == self._lastfile:
            reenable = [(c.fromline, len(c.before)) for c in self.curchunks[1:]\
                        if c.selected]
        else:
            reenable = []
        self._lastfile = filename
        self.clearChunks()

        fd = filedata.createFileData(self._ctx, None, filename, status)
        fd.load(force=force)
        fd.detectTextEncoding()

        if fd.elabel:
            self.extralabel.setText(fd.elabel)
            self.extralabel.show()
        else:
            self.extralabel.hide()
        self.filenamelabel.setText(fd.flabel)

        if not fd.isValid() or not fd.diff:
            if fd.error is None:
                self.sci.clear()
                return
            self.sci.setText(fd.error)
            forcedisplaymsg = filedata.forcedisplaymsg
            linkstart = fd.error.find(forcedisplaymsg)
            if linkstart >= 0:
                # add the link to force to view the data anyway
                self._setupForceViewIndicator()
                self.sci.fillIndicatorRange(
                    0, linkstart, 0, linkstart+len(forcedisplaymsg),
                    self._forceviewindicator)
            return
        elif isinstance(self._ctx.rev(), str):
            chunks = self._ctx._files[filename]
        else:
            header = patch.parsepatch(util.bytesio(fd.diff))[0]
            chunks = [header] + header.hunks

        utext = []
        for chunk in chunks[1:]:
            buf = util.bytesio()
            chunk.selected = False
            chunk.write(buf)
            chunk.lines = buf.getvalue().splitlines()
            utext.append(buf.getvalue().decode(fd.textEncoding(), 'replace'))
        self.sci.setText('\n'.join(utext))

        start = 0
        self.sci.markerDeleteAll(-1)
        for chunk in chunks[1:]:
            chunk.lrange = (start, start+len(chunk.lines))
            chunk.mline = start
            if start:
                self.sci.markerAdd(start-1, self.divider)
            for i in pycompat.xrange(0,len(chunk.lines)):
                if start + i == chunk.mline:
                    self.sci.markerAdd(chunk.mline, self.unselected)
                else:
                    self.sci.markerAdd(start+i, self.vertical)
            start += len(chunk.lines) + 1
        self.origcontents = fd.olddata
        self.countselected = 0
        self.curchunks = chunks
        for c in chunks[1:]:
            if (c.fromline, len(c.before)) in reenable:
                self.toggleChunk(c)
        self.updateSummary()

        if THEME.enabled:
            # Reapply dark theme colors because setLexer() resets marker colors to white

            # Divider between margin and editor text
            self.sci.setMarkerBackgroundColor(THEME.backgroundLighter, self.divider)

            # Selected chunk background
            self.sci.setMarkerBackgroundColor(THEME.diff_selected, self.selcolor)


    @pyqtSlot(str, bool, bool, bool)
    def find(self, exp, icase=True, wrap=False, forward=True):
        self.sci.find(exp, icase, wrap, forward)

    @pyqtSlot(str, bool)
    def highlightText(self, match, icase=False):
        self._lastSearch = match, icase
        self.sci.highlightText(match, icase)
        blk = self.blksearch
        blk.clear()
        blk.setUpdatesEnabled(False)
        blk.clear()
        for l in self.sci.highlightLines:
            blk.addBlock('s', l, l + 1)
        blk.setVisible(bool(match))
        blk.setUpdatesEnabled(True)
