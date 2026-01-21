# filedata.py - generate displayable file data
#
# Copyright 2011 Steve Borho <steve@borho.org>
# Copyright (C) 2026 Peter Demcak <majster64@gmail.com> (dark theme)
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import os, posixpath
import typing

from typing import (
    List,
    Optional,
    Text,
    Tuple,
    TypeVar,
    Union,
    cast,
)

from mercurial import (
    commands,
    copies,
    error,
    patch,
    pycompat,
    scmutil,
    subrepo,
    util,
)

from mercurial.utils import (
    dateutil,
)
from mercurial.node import nullrev

from tortoisehg.util import hglib, patchctx
from tortoisehg.util.i18n import _
from tortoisehg.hgqt import fileencoding

_TSubrepoData = TypeVar('_TSubrepoData', bound='SubrepoData')
_T_AbstractFileData = TypeVar('_T_AbstractFileData', bound='_AbstractFileData')

if typing.TYPE_CHECKING:
    from mercurial import (
        context,
        localrepo,
    )
    from ..util.typelib import (
        HgContext,
        ThgContext,
    )

from .theme import THEME

forcedisplaymsg = _('Display the file anyway')

class _BadContent(Exception):
    """Failed to display file because it is binary or size limit exceeded"""

def _exceedsMaxLineLength(data: bytes, maxlength: int = 100000) -> bool:
    if len(data) < maxlength:
        return False
    for line in data.splitlines():
        if len(line) > maxlength:
            return True
    return False

def _checkdifferror(data: bytes, maxdiff: int) -> Optional[str]:
    p = _('Diff not displayed: ')
    size = len(data)
    if size > maxdiff:
        return p + _('File is larger than the specified max size.\n'
                     'maxdiff = %s KB') % (maxdiff // 1024)
    elif b'\0' in data:
        return p + _('File is binary')
    elif _exceedsMaxLineLength(data):
        # it's incredibly slow to render long line by QScintilla
        return p + _('File may be binary (maximum line length exceeded)')

def _trimdiffheader(diff: str) -> str:
    # trim first three lines, for example:
    # diff -r f6bfc41af6d7 -r c1b18806486d tortoisehg/hgqt/mq.py
    # --- a/tortoisehg/hgqt/mq.py
    # +++ b/tortoisehg/hgqt/mq.py
    out = diff.split('\n', 3)
    if len(out) == 4:
        return out[3]
    else:
        # there was an error or rename without diffs
        return diff


class _AbstractFileData:

    def __init__(
        self,
        ctx: HgContext,
        ctx2: Optional[HgContext],
        wfile: bytes,
        status: Optional[str] = None,
        rpath: Optional[bytes] = None,
    ) -> None:
        self._ctx = ctx
        self._pctx: Optional[HgContext] = ctx2
        self._wfile = wfile
        self._status: Optional[str] = status
        self._rpath: bytes = rpath or b''
        self.contents: Optional[bytes] = None
        self.ucontents: Optional[str] = None
        self.error: Optional[str] = None
        self.olddata: Optional[bytes] = None
        self.diff: Optional[bytes] = None
        self.flabel: str = ''
        self.elabel: str = ''
        self.changes: Optional[patch.header] = None

        self._textencoding = fileencoding.contentencoding(ctx.repo().ui)

    def createRebased(
        self: _T_AbstractFileData,
        pctx: HgContext,
    ) -> _T_AbstractFileData:
        # new status is not known
        return self.__class__(self._ctx, pctx, self._wfile, rpath=self._rpath)

    def load(self, changeselect: bool = False, force: bool = False) -> None:
        # Note: changeselect may be set to True even if the underlying data
        # isn't chunk-selectable
        raise NotImplementedError

    def __eq__(self, other: object) -> bool:
        # unlike changectx, this also compares hash in case it was stripped and
        # recreated.  FileData may live longer than changectx in Mercurial.
        return (isinstance(other, self.__class__)
                and self._ctx == other._ctx
                and self._ctx.node() == other._ctx.node()
                and self._pctx == other._pctx
                and (self._pctx is None
                     or self._pctx.node() == other._pctx.node())
                and self._wfile == other._wfile
                and self._rpath == other._rpath)

    def __ne__(self, other: object) -> bool:
        return not self == other

    def __hash__(self) -> int:
        return hash((self._ctx, self._ctx.node(),
                     self._pctx, self._pctx is None or self._pctx.node(),
                     self._wfile, self._rpath))

    def __repr__(self) -> str:
        return '<%s %s@%s>' % (self.__class__.__name__,
                               hglib.tounicode(
                                   posixpath.join(self._rpath, self._wfile)
                               ),
                               self._ctx)

    def isLoaded(self) -> bool:
        loadables = [self.contents, self.ucontents, self.error, self.diff]
        return any(e is not None for e in loadables)

    def isNull(self) -> bool:
        return self._ctx.rev() == nullrev and not self._wfile

    def isValid(self) -> bool:
        return self.error is None and not self.isNull()

    def rev(self) -> Optional[int]:
        return self._ctx.rev()

    def baseRev(self) -> Optional[int]:  # Optional allows reverse diff from wdir
        return self._pctx.rev()

    def parentRevs(self) -> List[int]:
        # may contain nullrev, which allows "nullrev in parentRevs()"
        return [p.rev() for p in self._ctx.parents()]

    def rawContext(self) -> HgContext:
        return self._ctx

    def rawBaseContext(self) -> Optional[HgContext]:
        return self._pctx

    # absoluteFilePath    : "C:\\Documents\\repo\\foo\\subrepo\\bar\\baz"
    # absoluteRepoRootPath: "C:\\Documents\\repo\\foo\\subrepo"
    # canonicalFilePath: "bar/baz"
    # filePath         : "foo/subrepo/bar/baz"
    # repoRootPath     : "foo/subrepo"

    def absoluteFilePath(self) -> str:
        """Absolute file-system path of this file"""
        repo = self._ctx.repo()
        return hglib.tounicode(os.path.normpath(repo.wjoin(self._wfile)))

    def absoluteRepoRootPath(self) -> str:
        """Absolute file-system path to the root of the container repository"""
        # repo.root should be normalized
        repo = self._ctx.repo()
        return hglib.tounicode(repo.root)

    def canonicalFilePath(self) -> str:
        """Path relative to the repository root which contains this file"""
        return hglib.tounicode(self._wfile)

    def filePath(self) -> str:
        """Path relative to the top repository root in the current context"""
        return hglib.tounicode(posixpath.join(self._rpath, self._wfile))

    def repoRootPath(self) -> str:
        """Root path of the container repository relative to the top repository
        in the current context; '' for top repository"""
        return hglib.tounicode(self._rpath)

    def fileStatus(self) -> Optional[str]:
        return self._status

    def isDir(self) -> bool:
        return not self._wfile

    def mergeStatus(self) -> Optional[str]:
        pass

    def subrepoType(self) -> Optional[str]:
        pass

    def textEncoding(self) -> str:
        return self._textencoding

    def setTextEncoding(self, name: str) -> None:
        self._textencoding = fileencoding.canonname(name)

    def detectTextEncoding(self) -> None:
        ui = self._ctx.repo().ui
        # use file content for better guess; diff may be mixed encoding or
        # have immature multi-byte sequence
        data = self.contents or self.diff or b''
        fallbackenc = self._textencoding
        self._textencoding = fileencoding.guessencoding(ui, data, fallbackenc)

    def _textToUnicode(self, s: bytes) -> str:
        return s.decode(self._textencoding, 'replace')

    def diffText(self) -> str:
        return self._textToUnicode(self.diff or b'')

    def fileText(self) -> str:
        return self._textToUnicode(self.contents or b'')


class FileData(_AbstractFileData):

    def __init__(
        self,
        ctx: HgContext,
        pctx: Optional[HgContext],
        path: bytes,
        status: Optional[str] = None,
        rpath: Optional[bytes] = None,
        mstatus: Optional[str] = None,
    ) -> None:
        super().__init__(ctx, pctx, path, status, rpath)
        self._mstatus: Optional[str] = mstatus

    def load(self, changeselect: bool = False, force: bool = False) -> None:
        if self.rev() == nullrev:
            return
        ctx = self._ctx
        ctx2 = self._pctx
        wfile = self._wfile
        status = self._status
        errorprefix = _('File or diffs not displayed: ')
        try:
            self._readStatus(ctx, ctx2, wfile, status, changeselect, force)
        except _BadContent as e:
            self.error = errorprefix + e.args[0] + '\n\n' + forcedisplaymsg
        except (OSError, error.LookupError, error.Abort) as e:
            self.error = errorprefix + hglib.exception_str(e)

    def _checkMaxDiff(
        self,
        ctx: HgContext,
        wfile: bytes,
        maxdiff: int,
        force: bool
    ) -> Tuple[context.basefilectx, bytes]:
        self.error = None
        fctx = ctx.filectx(wfile)
        size = hglib.getestimatedsize(fctx)
        if not force and size > maxdiff:
            raise _BadContent(_('File is larger than the specified max size.\n'
                                'maxdiff = %s KB') % (maxdiff // 1024))

        data = fctx.data()
        if not force:
            # ctx is really thgrepo.thgchangectx (i.e. a ThgContext without
            # patchctx, with additional methods).
            if b'\0' in data or ctx.isStandin(wfile):  # pytype: disable=attribute-error
                raise _BadContent(_('File is binary'))
            elif _exceedsMaxLineLength(data):
                # it's incredibly slow to render long line by QScintilla
                raise _BadContent(_('File may be binary (maximum line length '
                                    'exceeded)'))
        return fctx, data

    def _checkRenamed(
        self,
        repo: localrepo.localrepository,
        ctx: HgContext,
        pctx: Optional[HgContext],
        wfile: bytes
    ) -> Optional[bytes]:
        m = scmutil.matchfiles(repo, [wfile])
        copy = copies.pathcopies(pctx, ctx, match=m)
        oldname = copy.get(wfile)
        if not oldname:
            self.flabel += _(' <i>(was added)</i>')
            return
        fr = hglib.tounicode(oldname)
        if oldname in ctx:
            self.flabel += _(' <i>(copied from %s)</i>') % fr
        else:
            self.flabel += _(' <i>(renamed from %s)</i>') % fr
        return oldname

    def _readStatus(
        self,
        ctx: HgContext,
        ctx2: Optional[HgContext],
        wfile: bytes,
        status: str,
        changeselect: bool,
        force: bool,
    ) -> None:
        def getstatus(
            repo: localrepo.localrepository,
            n1: Optional[bytes],
            n2: Optional[bytes],
            wfile: bytes,
        ) -> Optional[str]:
            m = scmutil.matchfiles(repo, [wfile])
            st = repo.status(n1, n2, match=m)
            if wfile in st.modified:
                return 'M'
            if wfile in st.added:
                return 'A'
            if wfile in st.removed:
                return 'R'
            if wfile in ctx:
                return 'C'
            return None

        isbfile = False
        repo = ctx.repo()
        maxdiff = repo.maxdiff
        self.flabel = '<b>%s</b>' % self.filePath()

        if ctx2:
            # If a revision to compare to was provided, we must put it in
            # the context of the subrepo as well
            if ctx2.repo().root != ctx.repo().root:
                wsub2, wfileinsub2, sctx2 = \
                    hglib.getDeepestSubrepoContainingFile(wfile, ctx2)
                if wsub2:
                    ctx2 = sctx2

        absfile = repo.wjoin(wfile)
        if (wfile in ctx and b'l' in ctx.flags(wfile)) or \
           os.path.islink(absfile):
            if wfile in ctx:
                data = ctx[wfile].data()
            else:
                data = os.readlink(absfile)
            self.contents = data
            self.flabel += _(' <i>(is a symlink)</i>')
            return

        if ctx2 is None:
            ctx2 = ctx.p1()
        assert ctx2 is not None  # help pytype

        if status is None:
            status = getstatus(repo, ctx2.node(), ctx.node(), wfile)

        mde = _('File or diffs not displayed: '
                'File is larger than the specified max size.\n'
                'maxdiff = %s KB') % (maxdiff // 1024)

        if status in ('R', '!'):
            if wfile in ctx.p1():
                fctx = ctx.p1()[wfile]
                if hglib.getestimatedsize(fctx) > maxdiff:
                    self.error = mde
                else:
                    olddata = fctx.data()
                    if b'\0' in olddata:
                        self.error = 'binary file'
                    else:
                        self.contents = olddata
                self.flabel += _(' <i>(was deleted)</i>')
            elif hasattr(ctx.p1(), 'hasStandin') and ctx.p1().hasStandin(wfile):
                self.error = 'binary file'
                self.flabel += _(' <i>(was deleted)</i>')
            else:
                self.flabel += _(' <i>(was added, now missing)</i>')
            return

        if status in ('I', '?'):
            assert ctx.rev() is None
            self.flabel += _(' <i>(is unversioned)</i>')
            if os.path.getsize(absfile) > maxdiff:
                self.error = mde
                return
            with util.posixfile(absfile, b'rb') as fp:
                data = cast(bytes, fp.read())
            if not force and b'\0' in data:
                self.error = 'binary file'
            else:
                self.contents = data
            return

        if status in ('M', 'A', 'C'):
            # pytype: disable=attribute-error
            # The actual type is thgrepo.thgcontext, which has these methods
            # TODO: make these module functions
            if ctx.hasStandin(wfile):
                wfile = ctx.findStandin(wfile)
                # pytype: enable=attribute-error
                isbfile = True
            try:
                fctx, newdata = self._checkMaxDiff(ctx, wfile, maxdiff, force)
            except _BadContent:
                if status == 'A':
                    self._checkRenamed(repo, ctx, ctx2, wfile)
                raise
            self.contents = newdata
            if status == 'C':
                # no further comparison is necessary
                return
            for pctx in ctx.parents():
                if b'x' in fctx.flags() and b'x' not in pctx.flags(wfile):
                    if THEME.enabled:
                        self.elabel = _("exec mode has been <span style='color:%s'>set</span>") % THEME.ui_info.name()
                    else:
                        self.elabel = _("exec mode has been "
                                    "<font color='red'>set</font>")
                elif b'x' not in fctx.flags() and b'x' in pctx.flags(wfile):
                    if THEME.enabled:
                        self.elabel = _("exec mode has been <span style='color:%s'>unset</span>") % THEME.ui_info.name()
                    else:
                        self.elabel = _("exec mode has been "
                                    "<font color='red'>unset</font>")

        if status == 'A':
            oldname = self._checkRenamed(repo, ctx, ctx2, wfile)
            if not oldname:
                return
            olddata = ctx2[oldname].data()
        elif status == 'M':
            if wfile not in ctx2:
                # merge situation where file was added in other branch
                self.flabel += _(' <i>(was added)</i>')
                return
            oldname = wfile
            olddata = ctx2[wfile].data()
        else:
            return

        self.olddata = olddata
        if changeselect:
            diffopts = patch.difffeatureopts(repo.ui)
            diffopts.git = True
            m = scmutil.matchfiles(repo, [wfile])
            fp = util.bytesio()

            copy = {}
            if oldname != wfile:
                copy[wfile] = oldname
            patches = patch.diff(repo, ctx.node(), None, match=m,
                                 opts=diffopts, copy=copy)

            for c in patches:
                fp.write(c)
            fp.seek(0)

            # feed diffs through parsepatch() for more fine grained
            # chunk selection
            filediffs = patch.parsepatch(fp)
            if filediffs and filediffs[0].hunks:
                self.changes = filediffs[0]
            else:
                self.diff = b''
                return
            self.changes.excludecount = 0
            values = []
            lines = 0
            for chunk in self.changes.hunks:
                buf = util.bytesio()
                chunk.write(buf)
                chunk.excluded = False
                val = buf.getvalue()
                values.append(val)
                chunk.lineno = lines
                chunk.linecount = len(val.splitlines())
                lines += chunk.linecount
            self.diff = b''.join(values)
        else:
            diffopts = patch.diffopts(repo.ui)
            diffopts.git = False
            newdate = dateutil.datestr(ctx.date())
            olddate = dateutil.datestr(ctx2.date())
            if isbfile:
                olddata += b'\0'
                newdata += b'\0'
            difftext = hglib.unidifftext(olddata, olddate, newdata, newdate,
                                         oldname, wfile, opts=diffopts)
            if difftext:
                self.diff = (b'diff -r %s -r %s %s\n' % (ctx, ctx2, oldname)
                             + difftext)
            else:
                self.diff = b''

    def mergeStatus(self) -> Optional[str]:
        return self._mstatus

    def diffText(self) -> str:
        udiff = self._textToUnicode(self.diff or b'')
        if self.changes:
            return udiff
        return _trimdiffheader(udiff)

    def setChunkExcluded(self, chunk: patch.recordhunk, exclude: bool) -> None:
        # The `excluded` field is set in the hunk above when `self.changes` is
        # initialized, thus the assertion that it is in the changes list.
        # Therefore, the pytype warning about the missing field can be disabled.
        # Additionally, the `excludecount` field is added to `self.changes` in
        # the same area, so the missing field error can be disabled for it too.
        assert chunk in self.changes.hunks, repr(chunk)
        if chunk.excluded == exclude:  # pytype: disable=attribute-error
            return
        if exclude:
            chunk.excluded = True
            self.changes.excludecount += 1  # pytype: disable=attribute-error
        else:
            chunk.excluded = False
            self.changes.excludecount -= 1  # pytype: disable=attribute-error


class DirData(_AbstractFileData):

    def load(self, changeselect: bool = False, force: bool = False) -> None:
        self.error = None
        self.flabel = '<b>%s</b>' % self.filePath()

        # TODO: enforce maxdiff before generating diff?
        ctx = self._ctx
        pctx = self._pctx
        try:
            m = ctx.match([b'path:%s' % self._wfile])
            self.diff = b''.join(ctx.diff(pctx, m))
        except (OSError, error.Abort) as e:
            self.error = hglib.exception_str(e)
            return

        if not force:
            self.error = _checkdifferror(self.diff, ctx.repo().maxdiff)
            if self.error:
                self.error += '\n\n' + forcedisplaymsg

    def isDir(self) -> bool:
        return True


class PatchFileData(_AbstractFileData):

    def load(self, changeselect: bool = False, force: bool = False) -> None:
        ctx = cast(patchctx.patchctx, self._ctx)
        wfile = self._wfile
        maxdiff = ctx.repo().maxdiff

        self.error = None
        self.flabel = '<b>%s</b>' % self.filePath()

        try:
            self.diff = ctx.thgmqpatchdata(wfile)
            flags = ctx.flags(wfile)
        except OSError as e:
            self.error = hglib.exception_str(e)
            return

        if flags == b'x':
            if THEME.enabled:
                self.elabel = _("exec mode has been <span style='color:%s'>set</span>") % THEME.ui_info.name()
            else:
                self.elabel = _("exec mode has been "
                            "<font color='red'>set</font>")
        elif flags == b'-':
            if THEME.enabled:
                self.elabel = _("exec mode has been <span style='color:%s'>unset</span>") % THEME.ui_info.name()
            else:
                self.elabel = _("exec mode has been "
                            "<font color='red'>unset</font>")
        elif flags == b'l':
            self.flabel += _(' <i>(is a symlink)</i>')

        # Do not show patches that are too big or may be binary
        if not force:
            self.error = _checkdifferror(self.diff, maxdiff)
            if self.error:
                self.error += '\n\n' + forcedisplaymsg

    def rev(self) -> int:
        # avoid mixing integer and localstr
        return nullrev

    def baseRev(self) -> int:
        # patch has no comparison base
        return nullrev

    def diffText(self) -> str:
        return _trimdiffheader(self._textToUnicode(self.diff or b''))


class PatchDirData(_AbstractFileData):

    def load(self, changeselect: bool = False, force: bool = False) -> None:
        self.error = None
        self.flabel = '<b>%s</b>' % self.filePath()

        ctx = cast(patchctx.patchctx, self._ctx)
        try:
            self.diff = b''.join([ctx.thgmqpatchdata(f) for f in ctx.files()
                                  if f.startswith(self._wfile + b'/')])
        except OSError as e:
            self.error = hglib.exception_str(e)
            return

        if not force:
            self.error = _checkdifferror(self.diff, ctx.repo().maxdiff)
            if self.error:
                self.error += '\n\n' + forcedisplaymsg

    def rev(self) -> int:
        # avoid mixing integer and localstr
        return nullrev

    def baseRev(self) -> int:
        # patch has no comparison base
        return nullrev

    def isDir(self) -> bool:
        return True


class SubrepoData(_AbstractFileData):

    def __init__(
        self,
        ctx: HgContext,
        pctx: Optional[HgContext],
        path: bytes,
        status: Optional[str],
        rpath: Optional[bytes],
        subkind: str,
    ) -> None:
        super().__init__(ctx, pctx, path, status, rpath)
        self._subkind = subkind

    def createRebased(
        self: _TSubrepoData,
        pctx: Optional[HgContext],
    ) -> _TSubrepoData:
        # new status should be unknown, but currently it is 'S'
        # TODO: replace 'S' by subrepo's status
        assert self._status == 'S', self._status
        return self.__class__(self._ctx, pctx, self._wfile, status=self._status,
                              rpath=self._rpath, subkind=self._subkind)

    def load(self, changeselect: bool = False, force: bool = False) -> None:
        ctx = self._ctx
        ctx2 = self._pctx
        if ctx2 is None:
            ctx2 = ctx.p1()
        assert ctx2 is not None  # help pytype
        wfile = self._wfile

        self.error = None
        self.flabel = '<b>%s</b>' % self.filePath()

        try:
            def genSubrepoRevChangedDescription(subrelpath: bytes, sfrom: str, sto: str,
                                                repo: localrepo.localrepository) -> Tuple[List[str], str]:
                """Generate a subrepository revision change description"""
                out = []
                def getLog(_ui, srepo, opts):
                    if srepo is None:
                        return _('changeset: %s') % hglib.tounicode(opts['rev'][0][:12])
                    _ui.pushbuffer()
                    logOutput = b''
                    try:
                        commands.log(_ui, srepo, **opts)
                        logOutput = _ui.popbuffer()
                        if not logOutput:
                            return _('Initial revision') + '\n'
                    except error.ParseError as e:
                        # Some mercurial versions have a bug that results in
                        # saving a subrepo node id in the .hgsubstate file
                        # which ends with a "+" character. If that is the
                        # case, add a warning to the output, but try to
                        # get the revision information anyway
                        for n, rev in enumerate(opts['rev']):
                            if rev.endswith(b'+'):
                                logOutput += hglib.fromunicode(
                                    _('[WARNING] Invalid subrepo '
                                      'revision ID:\n\t%s\n\n')
                                    % hglib.tounicode(rev))
                                opts['rev'][n] = rev[:-1]
                        commands.log(_ui, srepo, **opts)
                        logOutput += _ui.popbuffer()
                    return hglib.tounicode(logOutput)

                opts = {'date':None, 'rev':[hglib.fromunicode(sfrom)]}
                subabspath = os.path.join(repo.root, subrelpath)
                missingsub = srepo is None or not os.path.isdir(subabspath)
                sfromlog = ''
                def isinitialrevision(rev):
                    return all([el == '0' for el in rev])
                if isinitialrevision(sfrom):
                    sfrom = ''
                if isinitialrevision(sto):
                    sto = ''
                header = ''
                if not sfrom and not sto:
                    sstatedesc = 'new'
                    out.append(_('Subrepo created and set to initial '
                                 'revision.') + '\n\n')
                    return out, sstatedesc
                elif not sfrom:
                    sstatedesc = 'new'
                    header = _('Subrepo initialized to revision:') + '\n\n'
                elif not sto:
                    sstatedesc = 'removed'
                    out.append(_('Subrepo removed from repository.')
                               + '\n\n')
                    out.append(_('Previously the subrepository was '
                                 'at the following revision:') + '\n\n')
                    subinfo = getLog(_ui, srepo,
                                     {'rev': [hglib.fromunicode(sfrom)]})
                    slog = hglib.tounicode(subinfo)
                    out.append(slog)
                    return out, sstatedesc
                elif sfrom == sto:
                    sstatedesc = 'unchanged'
                    header = _('Subrepo was not changed.')
                    slog = _('changeset: %s') % sfrom[:12] + '\n'
                    if missingsub:
                        header = _('[WARNING] Missing subrepo. '
                               'Update to this revision to clone it.') \
                             + '\n\n' + header
                    else:
                        try:
                            slog = getLog(_ui, srepo, opts)
                        except error.RepoError:
                            header = _('[WARNING] Incomplete subrepo. '
                               'Update to this revision to pull it.') \
                             + '\n\n' + header
                    out.append(header + ' ')
                    out.append(_('Subrepo state is:') + '\n\n' + slog)
                    return out, sstatedesc
                else:
                    sstatedesc = 'changed'

                    header = _('Revision has changed to:') + '\n\n'
                    sfromlog = _('changeset: %s') % sfrom[:12] + '\n\n'
                    if not missingsub:
                        try:
                            sfromlog = getLog(_ui, srepo, opts)
                        except error.RepoError:
                            sfromlog = _('changeset: %s '
                                         '(not found on subrepository)') \
                                            % sfrom[:12] + '\n\n'
                    sfromlog = _('From:') + '\n' + sfromlog

                stolog = ''
                if missingsub:
                    header = _(
                        '[WARNING] Missing changed subrepository. '
                        'Update to this revision to clone it.') \
                        + '\n\n' + header
                    stolog = _('changeset: %s') % sto[:12] + '\n\n'
                    sfromlog += _(
                        'Subrepository not found in the working '
                        'directory.') + '\n'
                else:
                    try:
                        opts['rev'] = [hglib.fromunicode(sto)]
                        stolog = getLog(_ui, srepo, opts)
                    except error.RepoError:
                        header = _(
                            '[WARNING] Incomplete changed subrepository. '
                            'Update to this revision to pull it.') \
                             + '\n\n' + header
                        stolog = _('changeset: %s '
                                   '(not found on subrepository)') \
                                 % sto[:12] + '\n\n'
                out.append(header)
                out.append(stolog)

                if sfromlog:
                    out.append(sfromlog)

                return out, sstatedesc

            srev = hglib.tounicode(
                ctx.substate.get(wfile, hglib.nullsubrepostate)[1]
            )
            srepo = None
            subabspath = os.path.join(ctx.repo().root, wfile)
            sactual = ''
            if os.path.isdir(subabspath):
                try:
                    sub = ctx.sub(wfile)
                    if isinstance(sub, subrepo.hgsubrepo):
                        srepo = sub._repo
                        if srepo is not None:
                            sactual = hglib.tounicode(srepo[b'.'].hex())
                    else:
                        self.error = _('Not a Mercurial subrepo, not '
                                       'previewable')
                        return
                except error.Abort as e:
                    self.error = (_('Error previewing subrepo: %s')
                                  % hglib.exception_str(e)) + '\n\n'
                    self.error += _('Subrepo may be damaged or '
                                    'inaccessible.')
                    return
                except KeyError as e:
                    # Missing, incomplete or removed subrepo.
                    # Will be handled later as such below
                    pass
            out = []
            # TODO: should be copied from the baseui
            _ui = hglib.loadui()
            _ui.setconfig(b'ui', b'paginate', b'off', b'subrepodata')

            if srepo is None or ctx.rev() is not None:
                data = []
            else:
                _ui.pushbuffer()
                commands.status(_ui, srepo, modified=True, added=True,
                                removed=True, deleted=True)
                data = _ui.popbuffer()
                if data:
                    out.append(_('The subrepository is dirty.') + ' '
                               + _('File Status:') + '\n')
                    out.append(hglib.tounicode(data))
                    out.append('\n')

            sstatedesc = 'changed'
            if ctx.rev() is not None:
                sparent = hglib.tounicode(
                    ctx2.substate.get(wfile, hglib.nullsubrepostate)[1]
                )
                subrepochange, sstatedesc = \
                    genSubrepoRevChangedDescription(wfile,
                        sparent, srev, ctx.repo())
                out += subrepochange
            else:
                sstatedesc = 'dirty'
                if srev != sactual:
                    subrepochange, sstatedesc = \
                        genSubrepoRevChangedDescription(wfile,
                            srev, sactual, ctx.repo())
                    out += subrepochange
                    if data:
                        sstatedesc += ' and dirty'
                elif srev and not sactual:
                    sstatedesc = 'removed'
            self.ucontents = ''.join(out).strip()

            lbl = {
                'changed':   _('(is a changed sub-repository)'),
                'unchanged':   _('(is an unchanged sub-repository)'),
                'dirty':   _('(is a dirty sub-repository)'),
                'new':   _('(is a new sub-repository)'),
                'removed':   _('(is a removed sub-repository)'),
                'changed and dirty': _('(is a changed and dirty '
                                       'sub-repository)'),
                'new and dirty':   _('(is a new and dirty sub-repository)'),
                'removed and dirty':   _('(is a removed sub-repository)')
            }[sstatedesc]
            self.flabel += ' <i>' + lbl + '</i>'
            if sactual:
                lbl = ' <a href="repo:%%s">%s</a>' % _('open...')
                self.flabel += lbl % hglib.tounicode(srepo.root)
        except (OSError, error.RepoError, error.Abort) as e:
            self.error = _('Error previewing subrepo: %s') % \
                    hglib.exception_str(e)

    def isDir(self) -> bool:
        return True

    def subrepoType(self) -> str:
        return self._subkind


def createFileData(
    ctx: ThgContext,
    ctx2: Optional[HgContext],
    wfile: bytes,
    status: Optional[str] = None,
    rpath: Optional[bytes] = None,
    mstatus: Optional[str] = None,
) -> Union[FileData, PatchFileData]:
    if isinstance(ctx, patchctx.patchctx):
        if mstatus:
            raise ValueError('invalid merge status for patch: %r' % mstatus)

        # `patchctx.patchctx` is ducktype compatible with changectx/workingctx
        # as far as the abstract class methods are concerned, so ignore the type
        # mismatch in order to avoid applying patchctx to the other data types
        # where it never applies.

        # pytype: disable=wrong-arg-types
        return PatchFileData(ctx, ctx2, wfile, status, rpath)
        # pytype: enable=wrong-arg-types

    return FileData(ctx, ctx2, wfile, status, rpath, mstatus)

def createDirData(
    ctx: ThgContext,
    pctx: Optional[HgContext],
    path: bytes,
    rpath: Optional[bytes] = None,
) -> Union[DirData, PatchDirData]:
    if isinstance(ctx, patchctx.patchctx):

        # `patchctx.patchctx` is ducktype compatible with changectx/workingctx
        # as far as the abstract class methods are concerned, so ignore the type
        # mismatch in order to avoid applying patchctx to the other data types
        # where it never applies.

        # pytype: disable=wrong-arg-types
        return PatchDirData(ctx, pctx, path, rpath=rpath)
        # pytype: enable=wrong-arg-types

    return DirData(ctx, pctx, path, rpath=rpath)

def createSubrepoData(
    ctx: HgContext,
    pctx: Optional[HgContext],
    path: bytes,
    status: Optional[str] = None,
    rpath: Optional[bytes] = None,
    subkind: Optional[str] = None,
) -> SubrepoData:
    if not subkind:
        subkind = pycompat.sysstr(
            ctx.substate.get(path, hglib.nullsubrepostate)[2]
        )
    # TODO: replace 'S' by subrepo's status
    return SubrepoData(ctx, pctx, path, 'S', rpath, subkind)

def createNullData(repo: localrepo.localrepository) -> FileData:
    ctx = repo[nullrev]
    fd = FileData(ctx, ctx.p1(), b'', 'C')
    return fd
