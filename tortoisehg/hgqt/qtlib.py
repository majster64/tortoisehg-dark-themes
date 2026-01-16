# qtlib.py - Qt utility code
#
# Copyright 2010 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import atexit
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import weakref

from .qtcore import (
    PYQT_VERSION,
    QByteArray,
    QDir,
    QEvent,
    QFile,
    QObject,
    QPoint,
    QProcess,
    QSettings,
    QSignalMapper,
    QSize,
    QUrl,
    QT_API,
    QT_VERSION,
    Qt,
    pyqtBoundSignal,
    pyqtSignal,
    pyqtSlot,
)
from .qtgui import (
    QAction,
    QApplication,
    QComboBox,
    QCommonStyle,
    QColor,
    QDesktopServices,
    QDialog,
    QDropEvent,
    QFont,
    QFrame,
    QHBoxLayout,
    QIcon,
    QInputDialog,
    QKeyEvent,
    QKeySequence,
    QLabel,
    QLineEdit,
    QMessageBox,
    QMouseEvent,
    QPainter,
    QPalette,
    QPixmap,
    QPushButton,
    QShortcut,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QVBoxLayout,
    QWidget,
)

if QT_API == "PyQt6":
    import PyQt6.sip as sip  # pytype: disable=import-error
else:
    import sip  # pytype: disable=import-error

from typing import (
    Any,
    AnyStr,
    Callable,
    Dict,
    Hashable,
    Iterable,
    List,
    Optional,
    Text,
    Tuple,
    TypeVar,
    Union,
    cast,
)

from mercurial import (
    color,
    encoding,
    extensions,
    localrepo,
    pycompat,
    ui as uimod,
    util,
)
from mercurial.utils import (
    procutil,
    stringutil,
)

from ..util import (
    editor,
    hglib,
    paths,
    terminal,
)
from ..util.i18n import _

try:
    import win32con  # pytype: disable=import-error
    openflags: int = win32con.CREATE_NO_WINDOW
except ImportError:
    openflags = 0

_W = TypeVar('_W', bound=QWidget)

# def htmlescape(s: Text, quote: bool = True) -> Text:
from html import escape as htmlescape

# largest allowed size for widget, defined in <src/gui/kernel/qwidget.h>
QWIDGETSIZE_MAX = (1 << 24) - 1

tmproot: Optional[bytes] = None
def gettempdir() -> bytes:
    """Return the byte string path of a temporary directory, static for the
    application lifetime, removed recursively atexit."""
    global tmproot
    def cleanup():
        try:
            os.chmod(tmproot, os.stat(tmproot).st_mode | stat.S_IWUSR)
            for top, dirs, files in os.walk(tmproot):
                for name in dirs + files:
                    fullname = os.path.join(top, name)
                    os.chmod(fullname, os.stat(fullname).st_mode | stat.S_IWUSR)
            shutil.rmtree(tmproot)
        except OSError:
            pass
    if not tmproot:
        tmproot = tempfile.mkdtemp(prefix=b'thg.')
        atexit.register(cleanup)
    return tmproot

def openhelpcontents(url: str) -> None:
    'Open online help, use local CHM file if available'
    if not url.startswith('http'):
        fullurl = 'https://tortoisehg.readthedocs.org/en/latest/' + url
        # Use local CHM file if it can be found
        if os.name == 'nt' and paths.bin_path:
            chm = os.path.join(paths.bin_path, 'doc', 'TortoiseHg.chm')
            if os.path.exists(chm):
                fullurl = (r'mk:@MSITStore:%s::/' % chm) + url
                openlocalurl(fullurl)
                return
        QDesktopServices.openUrl(QUrl(fullurl))

def openlocalurl(path: AnyStr) -> bool:
    """open the given path with the Operating System's default application for
    this file type.

    Path may be a directory or a file.

    takes bytes or unicode as argument
    returns True if open was successfull
    """

    if isinstance(path, bytes):
        path = hglib.tounicode(path)
    if os.name == 'nt' and path.startswith('\\\\'):
        # network share, special handling because of qt bug 13359
        # see https://bugreports.qt.io/browse/QTBUG-13359
        qurl = QUrl()
        qurl.setUrl(QDir.toNativeSeparators(path))
    else:
        qurl = QUrl.fromLocalFile(path)
    return QDesktopServices.openUrl(qurl)

def editfiles(repo: localrepo.localrepository,
              files: List[bytes],
              lineno: Optional[int] = None,
              search: Optional[bytes] = None,
              parent: Optional[QWidget] = None) -> None:
    """open the given file with TortoiseHg's configured visual editor."""
    if len(files) == 1:
        # if editing a single file, open in cwd context of that file
        filename = files[0].strip()
        if not filename:
            return
        path = repo.wjoin(filename)
        cwd = os.path.dirname(path)
        files = [os.path.basename(path)]
    else:
        # else edit in cwd context of repo root
        cwd = repo.root

    toolpath, args, argsln, argssearch = editor.detecteditor(repo, files)
    if os.path.basename(toolpath) in (b'vi', b'vim', b'hgeditor'):
        QMessageBox.critical(parent, _('No visual editor configured'),
                             _('Please configure a visual editor.'))
        from tortoisehg.hgqt.settings import SettingsDialog
        dlg = SettingsDialog(False, focus='tortoisehg.editor')
        dlg.exec()
        return

    files = [procutil.shellquote(util.localpath(f)) for f in files]
    assert len(files) == 1 or lineno is None, (files, lineno)

    cmdline = None
    if search:
        assert lineno is not None
        if argssearch:
            cmdline = b' '.join([toolpath, argssearch])
            cmdline = cmdline.replace(b'$LINENUM', b'%d' % lineno)
            cmdline = cmdline.replace(b'$SEARCH', search)
        elif argsln:
            cmdline = b' '.join([toolpath, argsln])
            cmdline = cmdline.replace(b'$LINENUM', b'%d' % lineno)
        elif args:
            cmdline = b' '.join([toolpath, args])
    elif lineno is not None:
        if argsln:
            cmdline = b' '.join([toolpath, argsln])
            cmdline = cmdline.replace(b'$LINENUM', b'%d' % lineno)
        elif args:
            cmdline = b' '.join([toolpath, args])
    else:
        if args:
            cmdline = b' '.join([toolpath, args])

    if cmdline is None:
        # editor was not specified by editor-tools configuration, fall
        # back to older tortoisehg.editor OpenAtLine parsing
        cmdline = b' '.join([toolpath] + files) # default
        try:
            regexp = re.compile(rb'\[([^\]]*)\]')
            expanded = []
            pos = 0
            for m in regexp.finditer(toolpath):
                expanded.append(toolpath[pos:m.start()-1])
                phrase = toolpath[m.start()+1:m.end()-1]
                pos = m.end()+1
                if b'$LINENUM' in phrase:
                    if lineno is None:
                        # throw away phrase
                        continue
                    phrase = phrase.replace(b'$LINENUM', b'%d' % lineno)
                elif b'$SEARCH' in phrase:
                    if search is None:
                        # throw away phrase
                        continue
                    phrase = phrase.replace(b'$SEARCH', search)
                if b'$FILE' in phrase:
                    phrase = phrase.replace(b'$FILE', files[0])
                    files = []
                expanded.append(phrase)
            expanded.append(toolpath[pos:])
            cmdline = b' '.join(expanded + files)
        except ValueError:
            # '[' or ']' not found
            pass
        except TypeError:
            # variable expansion failed
            pass

    shell = not (len(cwd) >= 2 and cwd[0:2] == br'\\')
    try:
        if b'$FILES' in cmdline:
            cmdline = cmdline.replace(b'$FILES', b' '.join(files))
            subprocess.Popen(procutil.tonativestr(cmdline), shell=shell,
                             creationflags=openflags,
                             stderr=None, stdout=None, stdin=None,
                             cwd=procutil.tonativestr(cwd))
        elif b'$FILE' in cmdline:
            for file in files:
                cmd = cmdline.replace(b'$FILE', file)
                subprocess.Popen(procutil.tonativestr(cmd), shell=shell,
                                 creationflags=openflags,
                                 stderr=None, stdout=None, stdin=None,
                                 cwd=procutil.tonativestr(cwd))
        else:
            # assume filenames were expanded already
            subprocess.Popen(procutil.tonativestr(cmdline), shell=shell,
                             creationflags=openflags,
                             stderr=None, stdout=None, stdin=None,
                             cwd=procutil.tonativestr(cwd))
    except OSError as e:
        QMessageBox.warning(parent,
                _('Editor launch failure'),
                '%s : %s' % (hglib.tounicode(cmdline),
                              hglib.exception_str(e)))

def openshell(
    root: bytes,
    reponame: bytes,
    ui: Optional[uimod.ui] = None,
) -> None:
    if not os.path.exists(root):
        WarningMsgBox(
            _('Failed to open path in terminal'),
            _('"%s" is not a valid directory') % hglib.tounicode(root))
        return
    shell, args = terminal.detectterminal(ui)
    if shell:
        if args:
            shell = shell + b' ' + util.expandpath(args)
        # check invalid expression in tortoisehg.shell.  we shouldn't apply
        # string formatting to untrusted value, but too late to change syntax.
        try:
            shell % {b'root': b'', b'reponame': b''}
        except (KeyError, TypeError, ValueError):
            # KeyError: "%(invalid)s", TypeError: "%(root)d", ValueError: "%"
            ErrorMsgBox(_('Failed to open path in terminal'),
                        _('Invalid configuration: %s')
                        % hglib.tounicode(shell))
            return
        shellcmd = shell % {b'root': root, b'reponame': reponame}

        cwd = os.getcwd()
        try:
            # Unix: QProcess.startDetached(program) cannot parse single-quoted
            # parameters built using procutil.shellquote().
            # Windows: subprocess.Popen(program, shell=True) cannot spawn
            # cmd.exe in new window, probably because the initial cmd.exe is
            # invoked with SW_HIDE.
            os.chdir(root)
            if os.name == 'nt':
                # can't parse shellcmd in POSIX way
                started = QProcess.startDetached(hglib.tounicode(shellcmd))
            else:
                fullargs = pycompat.maplist(hglib.tounicode,
                                            pycompat.shlexsplit(shellcmd))
                started = QProcess.startDetached(fullargs[0], fullargs[1:])
        finally:
            os.chdir(cwd)
        if not started:
            ErrorMsgBox(_('Failed to open path in terminal'),
                        _('Unable to start the following command:'),
                        hglib.tounicode(shellcmd))
    else:
        InfoMsgBox(_('No shell configured'),
                   _('A terminal shell must be configured'))


def qbytearray_or_bytes_to_bytes(val: Union[QByteArray, bytes]) -> bytes:
    if isinstance(val, bytes):
        return cast(bytes, val)
    else:
        return val.data()


def keyCombinationValue(combination) -> int:
    if isinstance(combination, int):
        return combination  # PyQt5
    else:
        return combination.toCombined()  # QKeyCombination


# 'type' argument of QSettings.value() can't be used because:
#  a) it appears to be broken before PyQt 4.11.x (#4882)
#  b) it may raise TypeError if a setting has a value of an unexpected type

def readBool(qs: QSettings, key: str, default: bool = False) -> bool:
    """Read the specified value from QSettings and coerce into bool"""
    v = qs.value(key, default)
    if hglib.isbasestring(v):
        # qvariant.cpp:qt_convertToBool()
        return not (v == '0' or v == 'false' or v == '')
    return bool(v)

def readByteArray(qs: QSettings, key: str, default: bytes = b'') -> QByteArray:
    """Read the specified value from QSettings and coerce into QByteArray"""
    v = qs.value(key, default)
    if v is None:
        return QByteArray(default)
    try:
        return QByteArray(v)
    except TypeError:
        return QByteArray(default)

def readInt(qs: QSettings, key: str, default: int = 0) -> int:
    """Read the specified value from QSettings and coerce into int"""
    v = qs.value(key, default)
    if v is None:
        return int(default)
    try:
        return int(v)
    except (TypeError, ValueError):
        return int(default)

def readString(qs: QSettings, key: str, default: str = '') -> str:
    """Read the specified value from QSettings and coerce into string"""
    v = qs.value(key, default)
    if v is None:
        return pycompat.unicode(default)
    try:
        return pycompat.unicode(v)
    except ValueError:
        return pycompat.unicode(default)

def readStringList(
    qs: QSettings,
    key: str,
    default: Iterable[str] = (),
) -> List[str]:
    """Read the specified value from QSettings and coerce into string list"""
    v = qs.value(key, default)
    if v is None:
        return list(default)
    if hglib.isbasestring(v):
        # qvariant.cpp:convert()
        return [v]
    try:
        return [pycompat.unicode(e) for e in v]
    except (TypeError, ValueError):
        return list(default)


def isDarkTheme(palette: Optional[QPalette] = None) -> bool:
    """True if white-on-black color scheme is preferable"""
    if not palette:
        palette = QApplication.palette()
    return palette.color(QPalette.ColorRole.Base).black() >= 0x80

# _styles maps from ui labels to effects
# _effects maps an effect to font style properties.  We define a limited
# set of _effects, since we convert color effect names to font style
# effect programatically.

# TODO: update ui._styles instead of color._defaultstyles
_styles: Dict[str, str] = pycompat.rapply(pycompat.sysstr, color._defaultstyles)

_effects = {
    'bold': 'font-weight: bold',
    'italic': 'font-style: italic',
    'underline': 'text-decoration: underline',
}

_thgstyles = {
    # Styles defined by TortoiseHg
    'log.branch': 'black #aaffaa_background',
    'log.patch': 'black #aaddff_background',
    'log.unapplied_patch': 'black #dddddd_background',
    'log.tag': 'black #ffffaa_background',
    'log.bookmark': 'blue #ffffaa_background',
    'log.curbookmark': 'black #ffdd77_background',
    'log.modified': 'black #ffddaa_background',
    'log.added': 'black #aaffaa_background',
    'log.removed': 'black #ffcccc_background',
    'log.warning': 'black #ffcccc_background',
    'status.deleted': 'red bold',
    'ui.error': 'red bold #ffcccc_background',
    'ui.warning': 'black bold #ffffaa_background',
    'control': 'black bold #dddddd_background',

    # Topic related styles
    'log.topic': 'black bold #2ecc71_background',
    'topic.active': 'black bold #2ecc71_background',
}

thgstylesheet = '* { white-space: pre; font-family: monospace;' \
                ' font-size: 9pt; }'
tbstylesheet = 'QToolBar { border: 0px }'

def configstyles(ui: uimod.ui) -> None:
    # extensions may provide more labels and default effects
    for name, ext in extensions.extensions():
        extstyle = getattr(ext, 'colortable', {})
        _styles.update(pycompat.rapply(pycompat.sysstr, extstyle))

    # tortoisehg defines a few labels and default effects
    _styles.update(_thgstyles)

    # allow the user to override
    for status, cfgeffects in ui.configitems(b'color'):  # type: Tuple[bytes, bytes]
        if b'.' not in status:
            continue
        cfgeffects = ui.configlist(b'color', status)
        _styles[pycompat.sysstr(status)] = pycompat.sysstr(b' '.join(cfgeffects))

    for status, cfgeffects in ui.configitems(b'thg-color'):  # type: Tuple[bytes, bytes]
        if b'.' not in status:
            continue
        cfgeffects = ui.configlist(b'thg-color', status)
        _styles[pycompat.sysstr(status)] = pycompat.sysstr(b' '.join(cfgeffects))

# See https://doc.qt.io/qt-4.8/richtext-html-subset.html
# and https://www.w3.org/TR/SVG/types.html#ColorKeywords

def geteffect(labels: str) -> str:
    'map labels like "log.date" to Qt font styles'
    effects = []
    # Multiple labels may be requested
    for l in labels.split():
        if not l:
            continue
        # Each label may request multiple effects
        es = _styles.get(l, '')
        for e in es.split():
            if e in _effects:
                effects.append(_effects[e])
            elif e.endswith('_background'):
                e = e[:-11]
                if e.startswith('#') or e in QColor.colorNames():
                    effects.append('background-color: ' + e)
            elif e.startswith('#') or e in QColor.colorNames():
                # Accept any valid QColor
                effects.append('color: ' + e)
    return ';'.join(effects)

def gettextcoloreffect(labels: str) -> QColor:
    """Map labels like "log.date" to foreground color if available"""
    for l in labels.split():
        if not l:
            continue
        for e in _styles.get(l, '').split():
            if e.startswith('#') or e in QColor.colorNames():
                return QColor(e)
    return QColor()

def getbgcoloreffect(labels: str) -> QColor:
    """Map labels like "log.date" to background color if available

    Returns QColor object. You may need to check validity by isValid().
    """
    for l in labels.split():
        if not l:
            continue
        for e in _styles.get(l, '').split():
            if e.endswith('_background'):
                return QColor(e[:-11])
    return QColor()

# TortoiseHg uses special names for the properties controlling the appearance of
# its interface elements.
#
# This dict maps internal style names to corresponding CSS property names.
NAME_MAP = {
    'fg': 'color',
    'bg': 'background-color',
    'family': 'font-family',
    'size': 'font-size',
    'weight': 'font-weight',
    'space': 'white-space',
    'style': 'font-style',
    'decoration': 'text-decoration',
}

def markup(msg: AnyStr, **styles: str) -> str:
    style = {'white-space': 'pre'}
    for name, value in styles.items():
        if not value:
            continue
        if name in NAME_MAP:
            name = NAME_MAP[name]
        style[name] = value
    style = ';'.join(['%s: %s' % t for t in style.items()])
    msg = hglib.tounicode(msg)  # TODO: enforce unicode arg?
    msg = htmlescape(msg, False)
    msg = msg.replace('\n', '<br />')
    return '<span style="%s">%s</span>' % (style, msg)

def descriptionhtmlizer(ui: uimod.ui) -> Callable[[AnyStr], str]:
    """Return a function to mark up ctx.description() as an HTML

    >>> from mercurial import ui
    >>> u = ui.ui()
    >>> htmlize = descriptionhtmlizer(u)
    >>> htmlize('foo <bar> \\n& <baz>')
    u'foo &lt;bar&gt; \\n&amp; &lt;baz&gt;'

    changeset hash link:
    >>> htmlize('foo af50a62e9c20 bar')
    u'foo <a href="cset:af50a62e9c20">af50a62e9c20</a> bar'
    >>> htmlize('af50a62e9c2040dcdaf61ba6a6400bb45ab56410') # doctest: +ELLIPSIS
    u'<a href="cset:af...10">af...10</a>'

    http/https links:
    >>> s = htmlize('foo http://example.com:8000/foo?bar=baz&bax#blah')
    >>> (s[:63], s[63:]) # doctest: +NORMALIZE_WHITESPACE
    (u'foo <a href="http://example.com:8000/foo?bar=baz&amp;bax#blah">',
     u'http://example.com:8000/foo?bar=baz&amp;bax#blah</a>')
    >>> htmlize('https://example/')
    u'<a href="https://example/">https://example/</a>'
    >>> htmlize('<https://example/>')
    u'&lt;<a href="https://example/">https://example/</a>&gt;'

    issue links:
    >>> u.setconfig(b'tortoisehg', b'issue.regex', br'#(\\d+)\\b')
    >>> u.setconfig(b'tortoisehg', b'issue.link', b'http://example/issue/{1}/')
    >>> htmlize = descriptionhtmlizer(u)
    >>> htmlize('foo #123')
    u'foo <a href="http://example/issue/123/">#123</a>'

    missing issue.link setting:
    >>> u.setconfig(b'tortoisehg', b'issue.link', b'')
    >>> htmlize = descriptionhtmlizer(u)
    >>> htmlize('foo #123')
    u'foo #123'

    too many replacements in issue.link:
    >>> u.setconfig(b'tortoisehg', b'issue.link', b'http://example/issue/{1}/{2}')
    >>> htmlize = descriptionhtmlizer(u)
    >>> htmlize('foo #123')
    u'foo #123'

    invalid regexp in issue.regex:
    >>> u.setconfig(b'tortoisehg', b'issue.regex', b'(')
    >>> htmlize = descriptionhtmlizer(u)
    >>> htmlize('foo #123')
    u'foo #123'
    >>> htmlize('http://example/')
    u'<a href="http://example/">http://example/</a>'
    """
    csmatch = r'(\b[0-9a-f]{12}(?:[0-9a-f]{28})?\b)'
    httpmatch = r'(\b(http|https)://([-A-Za-z0-9+&@#/%?=~_()|!:,.;]*' \
                r'[-A-Za-z0-9+&@#/%=~_()|]))'
    regexp = r'%s|%s' % (csmatch, httpmatch)
    bodyre = re.compile(regexp)

    issuematch = hglib.tounicode(ui.config(b'tortoisehg', b'issue.regex'))
    issuerepl = hglib.tounicode(ui.config(b'tortoisehg', b'issue.link'))
    if issuematch and issuerepl:
        regexp += '|(%s)' % issuematch
        try:
            bodyre = re.compile(regexp)
        except re.error:
            pass

    def htmlize(desc: AnyStr) -> str:
        """Mark up ctx.description() [localstr] as an HTML [unicode]"""
        desc = hglib.tounicode(desc)

        buf = ''
        pos = 0
        for m in bodyre.finditer(desc):
            a, b = m.span()
            if a >= pos:
                buf += htmlescape(desc[pos:a], False)
                pos = b
            groups = m.groups()
            if groups[0]:
                cslink = htmlescape(groups[0])
                buf += '<a href="cset:%s">%s</a>' % (cslink, cslink)
            if groups[1]:
                urllink = htmlescape(groups[1])
                buf += '<a href="%s">%s</a>' % (urllink, urllink)
            if len(groups) > 4 and groups[4]:
                issue = htmlescape(groups[4])
                issueparams = groups[4:]
                try:
                    link = re.sub(r'\{(\d+)\}',
                                  lambda m: issueparams[int(m.group(1))],
                                  issuerepl)
                    link = htmlescape(link)
                    buf += '<a href="%s">%s</a>' % (link, issue)
                except IndexError:
                    buf += issue

        if pos < len(desc):
            buf += htmlescape(desc[pos:], False)

        return buf

    return htmlize

_iconcache: Dict[str, QIcon] = {}

if getattr(sys, 'frozen', False) and os.name == 'nt':
    def iconpath(f: str, *insidef: str) -> str:
        return posixpath.join(':/icons', f, *insidef)
else:
    def iconpath(f: str, *insidef: str) -> str:
        return os.path.join(paths.get_icon_path(), f, *insidef)

if hasattr(QIcon, 'hasThemeIcon'):  # PyQt>=4.7
    def _findthemeicon(name: str) -> Optional[QIcon]:
        if QIcon.hasThemeIcon(name):
            return QIcon.fromTheme(name)
else:
    def _findthemeicon(name: str) -> Optional[QIcon]:
        pass

def _findcustomicon(name: str) -> Optional[QIcon]:
    # let a user set the icon of a custom tool button
    if os.path.isabs(name):
        path = name
        if QFile.exists(path):
            return QIcon(path)
    return None

# https://specifications.freedesktop.org/icon-theme-spec/icon-theme-spec-latest.html
_SCALABLE_ICON_PATHS = [(QSize(), 'scalable/actions', '.svg'),
                        (QSize(), 'scalable/apps', '.svg'),
                        (QSize(), 'scalable/status', '.svg'),
                        (QSize(16, 16), '16x16/actions', '.png'),
                        (QSize(16, 16), '16x16/apps', '.png'),
                        (QSize(16, 16), '16x16/mimetypes', '.png'),
                        (QSize(16, 16), '16x16/status', '.png'),
                        (QSize(22, 22), '22x22/actions', '.png'),
                        (QSize(32, 32), '32x32/actions', '.png'),
                        (QSize(32, 32), '32x32/status', '.png'),
                        (QSize(24, 24), '24x24/actions', '.png')]

def getallicons() -> List[str]:
    """Get a sorted, unique list of all available icons"""
    iconset = set()
    for size, subdir, sfx in _SCALABLE_ICON_PATHS:
        path = iconpath(subdir)
        d = QDir(path)
        d.setNameFilters(['*%s' % sfx])
        for iconname in d.entryList():
            iconset.add(pycompat.unicode(iconname).rsplit('.', 1)[0])
    return sorted(iconset)

def _findscalableicon(name: str) -> Optional[QIcon]:
    """Find icon from qrc by using freedesktop-like icon lookup"""
    o = QIcon()
    for size, subdir, sfx in _SCALABLE_ICON_PATHS:
        path = iconpath(subdir, name + sfx)
        if QFile.exists(path):
            for mode in (QIcon.Mode.Normal, QIcon.Mode.Active):
                o.addFile(path, size, mode)
    if not o.isNull():
        return o

def geticon(name: str) -> QIcon:
    """
    Return a QIcon for the specified name. (the given 'name' parameter
    must *not* provide the extension).

    This searches for the icon from theme, Qt resource or icons directory,
    named as 'name.(svg|png|ico)'.
    """
    try:
        return _iconcache[name]
    except KeyError:
        icon = (_findthemeicon(name)
                or _findscalableicon(name)
                or _findcustomicon(name)
                or QIcon())
        assert icon is not None  # help pytype
        _iconcache[name] = icon
        return _iconcache[name]


def getoverlaidicon(base: QIcon, overlay: QIcon) -> QIcon:
    """Generate an overlaid icon"""
    pixmap = base.pixmap(16, 16)
    painter = QPainter(pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    painter.drawPixmap(0, 0, overlay.pixmap(16, 16))
    del painter
    return QIcon(pixmap)


_pixmapcache: Dict[str, QPixmap] = {}

def getpixmap(name: str, width: int = 16, height: int = 16) -> QPixmap:
    key = '%s_%sx%s' % (name, width, height)
    try:
        return _pixmapcache[key]
    except KeyError:
        pixmap = geticon(name).pixmap(width, height)
    _pixmapcache[key] = pixmap
    return pixmap

def getcheckboxpixmap(
    state: QStyle.StateFlag,
    bgcolor: Union[QColor, Qt.GlobalColor],
    widget: QWidget
) -> QPixmap:
    # TODO: switch state to QStyle.State enum
    pix = QPixmap(16,16)
    painter = QPainter(pix)
    painter.fillRect(0, 0, 16, 16, bgcolor)
    option = QStyleOptionButton()
    style = QApplication.style()
    option.initFrom(widget)
    option.rect = style.subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, None)
    option.rect.moveTo(1, 1)
    option.state |= state
    style.drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorCheckBox, option, painter)
    return pix


# On machines with a retina display running OSX (i.e. "darwin"), most icons are
# too big because Qt4 does not support retina displays very well.
# To fix that we let users force tortoishg to use smaller icons by setting a
# THG_RETINA environment variable to True (or any value that mercurial parses
# as True.
# Whereas on Linux, Qt4 has no support for high dpi displays at all causing
# icons to be rendered unusably small. The workaround for that is to render
# the icons at double the normal size.
# TODO: Remove this hack after upgrading to Qt5.
IS_RETINA = stringutil.parsebool(encoding.environ.get(b'THG_RETINA', b'0'))

def _fixIconSizeForRetinaDisplay(s: int) -> int:
    if IS_RETINA:
        if sys.platform == 'darwin':
            if s > 1:
                s //= 2
        elif sys.platform == 'linux2':
            s *= 2
    return s

def smallIconSize() -> QSize:
    style = QApplication.style()
    s = style.pixelMetric(QStyle.PixelMetric.PM_SmallIconSize)
    s = _fixIconSizeForRetinaDisplay(s)
    return QSize(s, s)

def toolBarIconSize() -> QSize:
    if sys.platform == 'darwin':
        # most Mac users will have laptop-sized screens and prefer a smaller
        # toolbar to preserve vertical space.
        style = QCommonStyle()
    else:
        style = QApplication.style()
    s = style.pixelMetric(QStyle.PixelMetric.PM_ToolBarIconSize)
    s = _fixIconSizeForRetinaDisplay(s)
    return QSize(s, s)

def listviewRetinaIconSize() -> QSize:
    return QSize(16, 16)

def treeviewRetinaIconSize() -> QSize:
    return QSize(16, 16)

def barRetinaIconSize() -> QSize:
    return QSize(10, 10)

class ThgFont(QObject):
    changed = pyqtSignal(QFont)
    def __init__(self, name: str) -> None:
        QObject.__init__(self)
        self.myfont = QFont()
        self.myfont.fromString(name)
    def font(self) -> QFont:
        return self.myfont
    def setFont(self, f: QFont) -> None:
        self.myfont = f
        self.changed.emit(f)

_fontdefaults: Dict[str, str] = {
    'fontcomment': 'monospace,10',
    'fontdiff': 'monospace,10',
    'fonteditor': 'monospace,10',
    'fontlog': 'monospace,10',
    'fontoutputlog': 'sans,8'
}
if sys.platform == 'darwin':
    _fontdefaults['fontoutputlog'] = 'sans,10'
_fontcache: Dict[str, ThgFont] = {}

def initfontcache(ui: uimod.ui):
    for name in _fontdefaults:
        fname = ui.config(b'tortoisehg', pycompat.sysbytes(name),
                          pycompat.sysbytes(_fontdefaults[name]))
        _fontcache[name] = ThgFont(hglib.tounicode(fname))

def getfont(name: str) -> ThgFont:
    assert name in _fontdefaults, (name, _fontdefaults)
    return _fontcache[name]

def CommonMsgBox(
    icon: QMessageBox.Icon,
    title: str,
    main: str,
    text: str = '',
    buttons: Union[QMessageBox.StandardButtons, QMessageBox.StandardButton] = QMessageBox.StandardButton.Ok,
    labels: Optional[Iterable[Tuple[QMessageBox.StandardButton, str]]] = None,
    parent: Optional[QWidget] = None,
    defaultbutton: Optional[Union[QPushButton, QMessageBox.StandardButton]] = None,
) -> int:
    if labels is None:
        labels = []
    msg = QMessageBox(parent)
    msg.setIcon(icon)
    msg.setWindowTitle(title)
    msg.setStandardButtons(buttons)
    for button_id, label in labels:
        msg.button(button_id).setText(label)
    if defaultbutton:
        msg.setDefaultButton(defaultbutton)
    msg.setText('<b>%s</b>' % main)
    info = ''
    for line in text.split('\n'):
        info += '<nobr>%s</nobr><br />' % line
    msg.setInformativeText(info)
    return msg.exec()

def InfoMsgBox(*args: str, **kargs) -> int:
    return CommonMsgBox(QMessageBox.Icon.Information, *args, **kargs)

def WarningMsgBox(*args: str, **kargs) -> int:
    return CommonMsgBox(QMessageBox.Icon.Warning, *args, **kargs)

def ErrorMsgBox(*args: str, **kargs) -> int:
    return CommonMsgBox(QMessageBox.Icon.Critical, *args, **kargs)

def QuestionMsgBox(*args: str, **kargs) -> bool:
    btn = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    res = CommonMsgBox(QMessageBox.Icon.Question, buttons=btn, *args, **kargs)
    return res == QMessageBox.StandardButton.Yes

class CustomPrompt(QMessageBox):
    def __init__(
        self,
        title: AnyStr,
        message: AnyStr,
        parent: Optional[QWidget],
        choices: Iterable[str],
        default: Optional[int] = None,
        esc: Optional[int] = None,
        files: Optional[Iterable[Union[bytes, str]]] = None,
    ) -> None:
        # Note: `files` is typed as `Union[bytes, str]` instead of `AnyStr`
        #  because the latter is defined with TypeVar, and that would enforce
        #  the same type as `title` and `message` for a particular invocation.
        #  IOW, a str title would prevent a list of bytes for the files.
        QMessageBox.__init__(self, parent)

        # TODO: force args to Text
        self.setWindowTitle(hglib.tounicode(title))
        self.setText(hglib.tounicode(message))
        if files:
            self.setDetailedText('\n'.join(hglib.tounicode(f) for f in files))
        self.hotkeys = {}
        for i, s in enumerate(choices):
            btn = self.addButton(s, QMessageBox.ButtonRole.AcceptRole)
            try:
                char = s[s.index('&')+1].lower()
                self.hotkeys[char] = btn
            except (ValueError, IndexError):
                pass
            if default == i:
                self.setDefaultButton(btn)
            if esc == i:
                self.setEscapeButton(btn)

    def run(self) -> int:
        self.exec()
        clicked = self.clickedButton()
        if clicked is None:
            return -1
        return self.buttons().index(clicked)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        for k, btn in self.hotkeys.items():
            if event.text() == k:
                btn.clicked.emit(False)
        super().keyPressEvent(event)

class ChoicePrompt(QDialog):
    def __init__(self,
                 title: str,
                 message: str,
                 parent: QWidget,
                 choices: List[str],
                 default: Optional[str] = None) -> None:
        QDialog.__init__(self, parent)
        self.setWindowTitle(title)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self.box = QHBoxLayout()
        self.vbox = QVBoxLayout()
        self.vbox.setSpacing(8)

        self.message_lbl = QLabel()
        self.message_lbl.setText(message)
        self.vbox.addWidget(self.message_lbl)

        self.choice_combo = combo = QComboBox()
        self.choices = choices
        combo.addItems(choices)
        if default:
            try:
                combo.setCurrentIndex(choices.index(default))
            except:
                # Ignore a missing default value
                pass
        self.vbox.addWidget(combo)
        self.box.addLayout(self.vbox)
        vbox = QVBoxLayout()
        self.ok = QPushButton('&OK')
        self.ok.clicked.connect(self.accept)
        vbox.addWidget(self.ok)
        self.cancel = QPushButton('&Cancel')
        self.cancel.clicked.connect(self.reject)
        vbox.addWidget(self.cancel)
        vbox.addStretch()
        self.box.addLayout(vbox)
        self.setLayout(self.box)

    def run(self) -> Optional[str]:
        if self.exec():
            return self.choices[self.choice_combo.currentIndex()]
        return None

def allowCaseChangingInput(combo: QComboBox) -> None:
    """Allow case-changing input of known combobox item

    QComboBox performs case-insensitive inline completion by default. It's
    all right, but sadly it implies case-insensitive check for duplicates,
    i.e. you can no longer enter "Foo" if the combobox contains "foo".

    For details, read QComboBoxPrivate::_q_editingFinished() and matchFlags()
    of src/gui/widgets/qcombobox.cpp.
    """
    assert isinstance(combo, QComboBox) and combo.isEditable()
    combo.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseSensitive)

class BadCompletionBlocker(QObject):
    """Disable unexpected inline completion by enter key if selectAll()-ed

    If the selection state looks in the middle of the completion, QComboBox
    replaces the edit text by the current completion on enter key pressed.
    This is wrong in the following scenario:

    >>> from .qtgui import QKeyEvent
    >>> combo = QComboBox(editable=True)
    >>> combo.addItem('history value')
    >>> combo.setEditText('initial value')
    >>> combo.lineEdit().selectAll()
    >>> QApplication.sendEvent(
    ...     combo, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Enter, Qt.KeyboardModifier.NoModifier))
    True
    >>> combo.currentText()
    'history value'

    In this example, QLineControl picks the first item in the combo box
    because the completion prefix has not been set.

    BadCompletionBlocker is intended to work around this problem.

    >>> combo.installEventFilter(BadCompletionBlocker(combo))
    >>> combo.setEditText('initial value')
    >>> combo.lineEdit().selectAll()
    >>> QApplication.sendEvent(
    ...     combo, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Enter, Qt.KeyboardModifier.NoModifier))
    True
    >>> combo.currentText()
    'initial value'

    For details, read QLineControl::processKeyEvent() and complete() of
    src/gui/widgets/qlinecontrol.cpp.
    """

    def __init__(self, parent: QComboBox) -> None:
        super().__init__(parent)
        if not isinstance(parent, QComboBox):
            raise ValueError('invalid object to watch: %r' % parent)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is not self.parent():
            return super().eventFilter(watched, event)
        if (event.type() != QEvent.Type.KeyPress
            or cast(QKeyEvent, event).key() not in (Qt.Key.Key_Enter, Qt.Key.Key_Return)
            or not watched.isEditable()):
            return False
        # deselect without completion if all text selected
        le = watched.lineEdit()
        if le.selectedText() == le.text():
            le.deselect()
        return False

class ActionPushButton(QPushButton):
    """Button which properties are defined by QAction like QToolButton"""

    def __init__(self, action: QAction, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAutoDefault(False)  # action won't be used as dialog default
        self._defaultAction = action
        self.addAction(action)
        self.clicked.connect(action.trigger)
        self._copyActionProps()

    def actionEvent(self, event) -> None:
        if (event.type() == QEvent.Type.ActionChanged
            and event.action() is self._defaultAction):
            self._copyActionProps()
        super().actionEvent(event)

    def _copyActionProps(self) -> None:
        action = self._defaultAction
        self.setEnabled(action.isEnabled())
        self.setText(action.text())
        self.setToolTip(action.toolTip())

class PMButton(QPushButton):
    """Toggle button with plus/minus icon images"""

    def __init__(
        self,
        expanded: bool = True,
        parent: Optional[QWidget] = None
    ) -> None:
        QPushButton.__init__(self, parent)

        size = QSize(11, 11)
        self.setIconSize(size)
        self.setMaximumSize(size)
        self.setFlat(True)
        self.setAutoDefault(False)

        self.plus = QIcon(iconpath('expander-open.png'))
        self.minus = QIcon(iconpath('expander-close.png'))
        icon = expanded and self.minus or self.plus
        self.setIcon(icon)

        self.clicked.connect(self._toggle_icon)

    @pyqtSlot()
    def _toggle_icon(self) -> None:
        icon = self.is_expanded() and self.plus or self.minus
        self.setIcon(icon)

    def set_expanded(self, state: bool = True) -> None:
        icon = state and self.minus or self.plus
        self.setIcon(icon)

    def set_collapsed(self, state: bool = True) -> None:
        icon = state and self.plus or self.minus
        self.setIcon(icon)

    def is_expanded(self) -> bool:
        return self.icon().cacheKey() == self.minus.cacheKey()

    def is_collapsed(self) -> bool:
        return not self.is_expanded()

class ClickableLabel(QLabel):

    clicked = pyqtSignal()

    def __init__(
        self,
        label: str,
        parent: Optional[QWidget] = None,
    ):
        QLabel.__init__(self, parent)

        self.setText(label)

    def mouseReleaseEvent(self, event):
        self.clicked.emit()

class ExpanderLabel(QWidget):

    expanded = pyqtSignal(bool)

    def __init__(
        self,
        label: str,
        expanded: bool = True,
        stretch: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        QWidget.__init__(self, parent)

        box = QHBoxLayout()
        box.setSpacing(4)
        box.setContentsMargins(*(0,)*4)
        self.button = PMButton(expanded, self)
        self.button.clicked.connect(self.pm_clicked)
        box.addWidget(self.button)
        self.label = ClickableLabel(label, self)
        self.label.clicked.connect(self.button.click)
        box.addWidget(self.label)
        if not stretch:
            box.addStretch(0)

        self.setLayout(box)

    def pm_clicked(self) -> None:
        self.expanded.emit(self.button.is_expanded())

    def set_expanded(self, state: bool = True) -> None:
        if not self.button.is_expanded() == state:
            self.button.set_expanded(state)
            self.expanded.emit(state)

    def is_expanded(self) -> bool:
        return self.button.is_expanded()

class StatusLabel(QWidget):

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        QWidget.__init__(self, parent)
        # same policy as status bar of QMainWindow
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

        box = QHBoxLayout()
        box.setContentsMargins(*(0,)*4)
        self.status_icon = QLabel()
        self.status_icon.setMaximumSize(16, 16)
        self.status_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self.status_icon)
        self.status_text = QLabel()
        self.status_text.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        box.addWidget(self.status_text)
        box.addStretch(0)

        self.setLayout(box)

    def set_status(
        self,
        text: str,
        icon: Optional[Union[bool, str, QIcon]] = None,
    ) -> None:
        self.set_text(text)
        self.set_icon(icon)

    def clear_status(self) -> None:
        self.clear_text()
        self.clear_icon()

    def set_text(self, text: Optional[str] = '') -> None:
        if text is None:
            text = ''
        self.status_text.setText(text)

    def clear_text(self) -> None:
        self.set_text()

    def set_icon(self, icon: Optional[Union[bool, str, QIcon]] = None) -> None:
        if icon is None:
            self.clear_icon()
        else:
            if isinstance(icon, bool):
                icon = geticon(icon and 'thg-success' or 'thg-error')
            elif hglib.isbasestring(icon):
                icon = geticon(icon)
            elif not isinstance(icon, QIcon):
                raise TypeError('%s: bool, str or QIcon' % type(icon))
            self.status_icon.setVisible(True)
            self.status_icon.setPixmap(icon.pixmap(16, 16))

    def clear_icon(self) -> None:
        self.status_icon.setHidden(True)

class LabeledSeparator(QWidget):

    def __init__(
        self,
        label: Optional[Union[QLabel, str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        QWidget.__init__(self, parent)

        box = QHBoxLayout()
        box.setContentsMargins(*(0,)*4)

        if label:
            # TODO: It looks like all callers only pass Text
            if hglib.isbasestring(label):
                label = QLabel(label)
            box.addWidget(label)

        sep = QFrame()
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        sep.setFrameShape(QFrame.Shape.HLine)
        box.addWidget(sep, 1, Qt.AlignmentFlag.AlignVCenter)

        self.setLayout(box)

class WidgetGroups:
    """ Support for bulk-updating properties of Qt widgets """

    def __init__(self) -> None:
        object.__init__(self)

        # help pytype by forward declaring
        self.groups: Dict[str, List[QWidget]] = {}

        self.clear(all=True)

    ### Public Methods ###

    def add(self, widget: QWidget, group: str = 'default') -> None:
        if group not in self.groups:
            self.groups[group] = []
        widgets = self.groups[group]
        if widget not in widgets:
            widgets.append(widget)

    def remove(self, widget: QWidget, group: str = 'default') -> None:
        if group not in self.groups:
            return
        widgets = self.groups[group]
        if widget in widgets:
            widgets.remove(widget)

    def clear(self, group: str = 'default', all: bool = True) -> None:
        if all:
            self.groups = {}
        else:
            del self.groups[group]

    def set_prop(
        self,
        prop: str,
        value: object,
        group: str = 'default',
        cond: Optional[Callable[[QWidget], bool]] = None,
    ) -> None:
        if group not in self.groups:
            return
        widgets = self.groups[group]
        if callable(cond):
            widgets = [w for w in widgets if cond(w)]
        for widget in widgets:
            getattr(widget, prop)(value)

    def set_visible(self, *args, **kargs) -> None:
        self.set_prop('setVisible', *args, **kargs)

    def set_enable(self, *args, **kargs) -> None:
        self.set_prop('setEnabled', *args, **kargs)

class DialogKeeper(QObject):
    """Manage non-blocking dialogs identified by creation parameters

    Example "open single dialog per type":

    >>> mainwin = QWidget()
    >>> dialogs = DialogKeeper(lambda self, cls: cls(self), parent=mainwin)
    >>> dlg1 = dialogs.open(QDialog)
    >>> dlg1.parent() is mainwin
    True
    >>> dlg2 = dialogs.open(QDialog)
    >>> dlg1 is dlg2
    True
    >>> dialogs.count()
    1

    closed dialog will be deleted:

    >>> from .qtcore import QEventLoop, QTimer
    >>> def processDeferredDeletion():
    ...     loop = QEventLoop()
    ...     QTimer.singleShot(0, loop.quit)
    ...     loop.exec()

    >>> dlg1.reject()
    >>> processDeferredDeletion()
    >>> dialogs.count()
    0

    and recreates as necessary:

    >>> dlg3 = dialogs.open(QDialog)
    >>> dlg1 is dlg3
    False

    creates new dialog of the same type:

    >>> dlg4 = dialogs.openNew(QDialog)
    >>> dlg3 is dlg4
    False
    >>> dialogs.count()
    2

    and the last dialog is preferred:

    >>> dialogs.open(QDialog) is dlg4
    True
    >>> dlg4.reject()
    >>> processDeferredDeletion()
    >>> dialogs.count()
    1
    >>> dialogs.open(QDialog) is dlg3
    True

    The following example is not recommended because it creates reference
    cycles and makes hard to garbage-collect::

        self._dialogs = DialogKeeper(self._createDialog)
        self._dialogs = DialogKeeper(lambda *args: Foo(self))
    """

    def __init__(
        self,
        createdlg: Callable[..., _W],
        genkey: Optional[Callable[..., Hashable]] = None,
        parent: Optional[QObject] = None
    ) -> None:
        super().__init__(parent)
        self._createdlg: Callable[..., _W] = createdlg
        self._genkey = genkey or DialogKeeper._defaultgenkey
        self._keytodlgs = {}  # key: [dlg, ...]

    def open(self, *args, **kwargs) -> _W:
        """Create new dialog or reactivate existing dialog"""
        dlg = self._preparedlg(self._genkey(self.parent(), *args, **kwargs),
                               args, kwargs)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return dlg

    def openNew(self, *args, **kwargs) -> _W:
        """Create new dialog even if there exists the specified one"""
        dlg = self._populatedlg(self._genkey(self.parent(), *args, **kwargs),
                                args, kwargs)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return dlg

    def _preparedlg(self, key, args, kwargs) -> _W:
        if key in self._keytodlgs:
            assert len(self._keytodlgs[key]) > 0, key
            return self._keytodlgs[key][-1]  # prefer latest
        else:
            return self._populatedlg(key, args, kwargs)

    def _populatedlg(self, key, args, kwargs) -> _W:
        dlg = self._createdlg(self.parent(), *args, **kwargs)
        if key not in self._keytodlgs:
            self._keytodlgs[key] = []
        self._keytodlgs[key].append(dlg)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.destroyed.connect(self._cleanupdlgs)
        return dlg

    # "destroyed" is emitted soon after Python wrapper is deleted
    @pyqtSlot()
    def _cleanupdlgs(self) -> None:
        for key, dialogs in list(self._keytodlgs.items()):
            livedialogs = [dlg for dlg in dialogs if not sip.isdeleted(dlg)]
            if livedialogs:
                self._keytodlgs[key] = livedialogs
            else:
                del self._keytodlgs[key]

    def count(self) -> int:
        return sum(len(dlgs) for dlgs in self._keytodlgs.values())

    @staticmethod
    def _defaultgenkey(_parent, *args, **_kwargs):
        return args

class TaskWidget:
    def canswitch(self) -> bool:
        """Return True if the widget allows to switch away from it"""
        return True

    def canExit(self) -> bool:
        return True

    def reload(self) -> None:
        pass

class DemandWidget(QWidget):
    'Create a widget the first time it is shown'

    def __init__(
        self,
        createfuncname: str,
        createinst: object,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        # We store a reference to the create function name to avoid having a
        # hard reference to the bound function, which prevents it being
        # disposed. Weak references to bound functions don't work.
        self._createfuncname = createfuncname
        self._createinst = weakref.ref(createinst)
        self._widget: Optional[QWidget] = None
        vbox = QVBoxLayout()
        vbox.setContentsMargins(*(0,)*4)
        self.setLayout(vbox)

    def showEvent(self, event):
        """create the widget if necessary"""
        self.get()
        super().showEvent(event)

    def forward(self, funcname: str, *args, **opts) -> Optional[Any]:
        if self._widget:
            return getattr(self._widget, funcname)(*args, **opts)
        return None

    def get(self) -> QWidget:
        """Returns the stored widget"""
        if self._widget is None:
            func = getattr(self._createinst(), self._createfuncname, None)
            self._widget = func()
            self.layout().addWidget(self._widget)
        return self._widget

    def canswitch(self) -> bool:
        """Return True if the widget allows to switch away from it"""
        if self._widget is None:
            return True
        return self._widget.canswitch()

    def canExit(self) -> bool:
        if self._widget is None:
            return True
        return self._widget.canExit()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._widget, name)

class Spacer(QWidget):
    """Spacer to separate controls in a toolbar"""

    def __init__(
        self,
        width: int,
        height: int,
        parent: Optional[QWidget] = None,
    ) -> None:
        QWidget.__init__(self, parent)
        self.width = width
        self.height = height

    def sizeHint(self) -> QSize:
        return QSize(self.width, self.height)

def getCurrentUsername(widget: Optional[QWidget],
                       repo: localrepo.localrepository,
                       opts: Optional[Dict[str, str]] = None) -> Optional[str]:
    if opts:
        # 1. Override has highest priority
        user = opts.get('user')
        if user:
            return user

    # 2. Read from repository
    user = hglib.configuredusername(repo.ui)
    if user:
        return hglib.tounicode(user)

    # 3. Get a username from the user
    QMessageBox.information(widget, _('Please enter a username'),
                _('You must identify yourself to Mercurial'),
                QMessageBox.StandardButton.Ok)
    from tortoisehg.hgqt.settings import SettingsDialog
    dlg = SettingsDialog(False, focus='ui.username')
    dlg.exec()
    repo.invalidateui()
    return hglib.tounicode(hglib.configuredusername(repo.ui))

class _EncodingSafeInputDialog(QInputDialog):
    def accept(self) -> None:
        try:
            hglib.fromunicode(self.textValue())
            return super().accept()
        except UnicodeEncodeError:
            WarningMsgBox(_('Text Translation Failure'),
                          _('Unable to translate input to local encoding.'),
                          parent=self)

def getTextInput(
    parent: Optional[QWidget],
    title: str,
    label: str,
    mode: QLineEdit.EchoMode = QLineEdit.EchoMode.Normal,
    text: str = '',
    flags: Union[Qt.WindowType, Qt.WindowFlags] = Qt.WindowType.Widget,
) -> Tuple[str, bool]:
    flags |= (Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint
              | Qt.WindowType.WindowCloseButtonHint)
    dlg = _EncodingSafeInputDialog(parent, flags)
    dlg.setWindowTitle(title)
    dlg.setLabelText(label)
    dlg.setTextValue(text)
    dlg.setTextEchoMode(mode)

    r = dlg.exec()
    dlg.setParent(None)  # so that garbage collected
    return r and dlg.textValue() or '', bool(r)

def keysequence(
    o: Union[QKeySequence, QKeySequence.StandardKey, str, int],
) -> Union[QKeySequence, QKeySequence.StandardKey]:
    """Create QKeySequence from string or QKeySequence"""
    if isinstance(o, (QKeySequence, QKeySequence.StandardKey)):
        return o
    try:
        return getattr(QKeySequence, str(o))  # standard key
    except AttributeError:
        return QKeySequence(o)

def newshortcutsforstdkey(
    key: QKeySequence.StandardKey, *args, **kwargs,
) -> List[QShortcut]:
    """Create [QShortcut,...] for all key bindings of the given StandardKey"""
    return [QShortcut(keyseq, *args, **kwargs)
            for keyseq in QKeySequence.keyBindings(key)]

class PaletteSwitcher:
    """
    Class that can be used to enable a predefined, alterantive background color
    for a widget

    This is normally used to change the color of widgets when they display some
    "filtered" content which is a subset of the actual widget contents.

    The alternative background color is fixed, and depends on the original
    background color (dark and light backgrounds use a different alternative
    color).

    The alterenative color cannot be changed because the idea is to set a
    consistent "filter" style for all widgets.

    An instance of this class must be added as a property of the widget whose
    background we want to change. The constructor takes the "target widget" as
    its only parameter.

    In order to enable or disable the background change, simply call the
    enablefilterpalette() method.
    """
    def __init__(self, targetwidget: QWidget) -> None:
        self._targetwref = weakref.ref(targetwidget)  # avoid circular ref
        self._defaultpalette = targetwidget.palette()
        if not isDarkTheme(self._defaultpalette):
            filterbgcolor = QColor('#FFFFB7')
        else:
            filterbgcolor = QColor('darkgrey')
        self._filterpalette = QPalette()
        self._filterpalette.setColor(QPalette.ColorRole.Base, filterbgcolor)

    def enablefilterpalette(self, enabled: bool = False) -> None:
        targetwidget = self._targetwref()
        if not targetwidget:
            return
        if enabled:
            pl = self._filterpalette
        else:
            pl = self._defaultpalette
        targetwidget.setPalette(pl)

def setContextMenuShortcut(
    action: QAction,
    shortcut: Union[QKeySequence, str],
) -> None:
    """Set shortcut for a context menu action, making sure it's visible"""
    action.setShortcut(shortcut)
    if QT_VERSION >= 0x50a00 and PYQT_VERSION >= 0x50a00:
        action.setShortcutVisibleInContextMenu(True)

def setContextMenuShortcuts(action: QAction,
                            shortcuts: List[QKeySequence]) -> None:
    """Set shortcuts for a context menu action, making sure it's visible"""
    action.setShortcuts(shortcuts)
    if QT_VERSION >= 0x50a00 and PYQT_VERSION >= 0x50a00:
        action.setShortcutVisibleInContextMenu(True)

def getMappedStringSignal(signalMapper: QSignalMapper) -> pyqtBoundSignal:
    try:
        return signalMapper.mappedString[str]
    except AttributeError:
        # qt < 5.15
        return signalMapper.mapped[str]

def toCheckStateEnum(value: Union[int, Qt.CheckState]) -> Qt.CheckState:
    """Coerce a Qt.CheckState value to the corresponding enum value."""
    if PYQT_VERSION >= 0x060000:
        if isinstance(value, int):
            # pytype: disable=attribute-error
            return next(e for e in Qt.CheckState if e.value == value)
            # pytype: enable=attribute-error

    return cast(Qt.CheckState, value)


def qDropEventPosition(event: QDropEvent) -> QPoint:
    """Retrieve QPoint position for the drop event"""
    if QT_VERSION >= 0x060000:
        return event.position().toPoint()  # pytype: disable=attribute-error
    else:
        return event.pos()  # pytype: disable=attribute-error

def qMouseEventPosition(event: QMouseEvent) -> QPoint:
    """Retrieve QPoint position for the mouse event"""
    if QT_VERSION >= 0x060000:
        return event.position().toPoint()  # pytype: disable=attribute-error
    else:
        return event.pos()  # pytype: disable=attribute-error
