# run.py - front-end script for TortoiseHg dialogs
#
# Copyright 2008 Steve Borho <steve@borho.org>
# Copyright 2008 TK Soh <teekaysoh@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import getopt
import os
import pdb
import sys
import subprocess

from typing import (
    Optional,
)

from mercurial import (
    cmdutil,
    encoding,
    error,
    extensions,
    fancyopts,
    pathutil,
    pycompat,
    registrar,
    scmutil,
    ui as uimod,
    util,
)
from mercurial.utils import (
    procutil,
    stringutil,
    urlutil,
)

from ..util import (
    hglib,
    hgversion,
    i18n,
    paths,
    version as thgversion,
)
from ..util.i18n import agettext as _
from . import (
    bugreport,
    cmdui,
    hgconfig,
    qtapp,
    qtlib,
    quickop,
    thgrepo,
)

try:
    from tortoisehg.util.config import nofork as config_nofork  # pytype: disable=import-error
except ImportError:
    config_nofork = None

shortlicense = b'''
Copyright (C) 2008-2025 Steve Borho <steve@borho.org> and others.
This is free software; see the source for copying conditions.  There is NO
warranty; not even for MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
'''

console_commands = b'help thgstatus version'
nonrepo_commands = b'''userconfig shellconfig clone init debugblockmatcher
debugbugreport about help version thgstatus serve rejects log'''

def run():
    argv = pycompat.sysargv[1:]
    # Remove the -psn argument supplied by launchd (if present - it's not
    # on 10.9)
    if 'THG_OSX_APP' in os.environ and argv and argv[0].startswith(b'-psn'):
        argv = argv[1:]

    # Verify we have an acceptable version of Mercurial
    errmsg = hgversion.checkminhgversion(hgversion.hgversion)
    if errmsg:
        opts = {
            'cmd': b' '.join(argv),
            'error': b'\n' + errmsg + b'\n',
            'nofork': True,
        }
        qtrun(bugreport.run, uimod.ui(), **opts)
        sys.exit(1)

    if ('THGDEBUG' in os.environ or b'--profile' in argv
        or getattr(sys, 'frozen', None) != 'windows_exe'):
        sys.exit(dispatch(argv))
    else:
        origstderr = sys.stderr

        # TODO: Figure out if this hack for py2exe is still needed, and possibly
        #       wrap this to handle unicode on py3
        if not pycompat.ispy3:
            mystderr = util.bytesio()
            sys.stderr = mystderr
            sys.__stdout__ = sys.stdout
            sys.__stderr__ = sys.stderr
            procutil.stderr = sys.stderr
        ret = 0
        try:
            ret = dispatch(argv)
            if not pycompat.ispy3:
                sys.stderr = origstderr
                stderrout = mystderr.getvalue()
            else:
                stderrout = ''
            errors = ('Traceback', 'TypeError', 'NameError', 'AttributeError',
                      'NotImplementedError')
            for l in stderrout.splitlines():
                if l.startswith(errors):
                    opts = {
                        'cmd': b' '.join(argv),
                        'error': 'Recoverable error (stderr):\n' + stderrout,
                        'nofork': True,
                    }
                    qtrun(bugreport.run, uimod.ui(), **opts)
                    break
            sys.exit(ret)
        except KeyboardInterrupt:
            sys.exit(-1)
        except SystemExit:
            raise
        except:
            procutil.stderr = sys.__stderr__ = sys.stderr = origstderr
            raise

def dispatch(args, u=None):
    """run the command specified in args"""
    try:
        if u is None:
            u = hglib.loadui()
        if b'--traceback' in args:
            u.setconfig(b'tortoisehg', b'traceback', b'on')
            u.setconfig(b'ui', b'traceback', b'on')
        if b'--debugger' in args:
            pdb.set_trace()
        return _runcatch(u, args)
    except error.ConfigError as e:
        qtapp.earlyExceptionMsgBox(e)
    except SystemExit as e:
        return e.code
    except Exception as e:
        if b'--debugger' in args:
            pdb.post_mortem(sys.exc_info()[2])
        if u and u.configbool(b'tortoisehg', b'traceback'):
            raise
        qtapp.earlyBugReport(e)

        if not u:
            raise

        return -1
    except KeyboardInterrupt:
        print(hglib.tounicode(_('\nCaught keyboard interrupt, aborting.\n')))
        return -1

def portable_fork(ui, opts):
    if 'THG_GUI_SPAWN' in os.environ or (
        not opts.get(b'fork') and opts.get(b'nofork')):
        os.environ['THG_GUI_SPAWN'] = '1'
        return
    elif 'THG_OSX_APP' in os.environ:
        # guifork seems to break Mac app bundles
        return
    elif ui.configbool(b'tortoisehg', b'guifork') is not None:
        if not ui.configbool(b'tortoisehg', b'guifork'):
            return
    elif config_nofork:
        return
    os.environ['THG_GUI_SPAWN'] = '1'
    try:
        _forkbg(ui)
    except OSError as inst:
        ui.warn(_('failed to fork GUI process: %s\n')
                % hglib.fromunicode(inst.strerror))

# native window API can't be used after fork() on Mac OS X
if os.name == 'posix' and sys.platform != 'darwin':
    def _forkbg(ui):
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
        # disables interaction with tty, keeping logs sent to stdout/stderr
        nullfd = os.open(os.devnull, os.O_RDONLY)
        os.dup2(nullfd, ui.fin.fileno())
        os.close(nullfd)

else:
    _origwdir = os.getcwd()

    def _forkbg(ui):
        # Spawn background process and exit
        cmdline = list(paths.get_thg_command())
        cmdline.extend(sys.argv[1:])
        os.chdir(_origwdir)
        # redirect stdout/stderr to "nul" device; otherwise EBADF could occur
        # on cmd.exe (#4837). for stdin, create a pipe since "nul" is a tty
        # on Windows! (#4469)
        nullfd = os.open(os.devnull, os.O_RDWR)
        p = subprocess.Popen(cmdline, stdin=subprocess.PIPE,
                             stdout=nullfd, stderr=nullfd,
                             creationflags=qtlib.openflags)
        p.stdin.close()
        os.close(nullfd)
        sys.exit(0)

# Windows and Nautilus shellext execute
# "thg subcmd --listfile TMPFILE" or "thg subcmd --listfileutf8 TMPFILE"(planning) .
# Extensions written in .hg/hgrc is enabled after calling
# extensions.loadall(lui)
#
# 1. win32mbcs extension
#     Japanese shift_jis and Chinese big5 include '0x5c'(backslash) in filename.
#     Mercurial resolves this problem with win32mbcs extension.
#     So, thg must parse path after loading win32mbcs extension.
#
# 2. fixutf8 extension
#     fixutf8 extension requires paths encoding utf-8.
#     So, thg need to convert to utf-8.
#

_lines     = []
_linesutf8 = []

def get_lines_from_listfile(filename, isutf8):
    global _lines
    global _linesutf8
    try:
        if filename == b'-':
            lines = [x.replace(b"\n", b"") for x in procutil.stdin.readlines()]
        else:
            fd = open(filename, "rb")
            lines = [ x.replace(b"\n", b"") for x in fd.readlines() ]
            fd.close()
            os.unlink(filename)
        if isutf8:
            _linesutf8 = lines
        else:
            _lines = lines
    except OSError:
        sys.stderr.write('can not read file %r. Ignored.\n' % filename)

def get_files_from_listfile():
    global _lines
    global _linesutf8
    lines = []
    need_to_utf8 = False
    if os.name == 'nt':
        try:
            fixutf8 = extensions.find(b"fixutf8")
            if fixutf8:
                need_to_utf8 = True
        except KeyError:
            pass

    if need_to_utf8:
        lines += _linesutf8
        for l in _lines:
            lines.append(hglib.toutf(l))
    else:
        lines += _lines
        for l in _linesutf8:
            lines.append(hglib.fromutf(l))

    # Convert absolute file paths to repo/cwd canonical
    cwd = encoding.getcwd()
    root = paths.find_root_bytes(cwd)
    if not root:
        return lines
    if cwd == root:
        cwd_rel = b''
    else:
        cwd_rel = cwd[len(root + pycompat.ossep):] + pycompat.ossep
    files = []
    for f in lines:
        try:
            cpath = pathutil.canonpath(root, cwd, f)
            # canonpath will abort on .hg/ paths
        except error.Abort:
            continue
        if cpath.startswith(cwd_rel):
            cpath = cpath[len(cwd_rel):]
            files.append(cpath)
        else:
            files.append(f)
    return files

def _parse(ui, args):
    options = {}
    cmdoptions = {}

    try:
        args = fancyopts.fancyopts(args, globalopts, options)
    except getopt.GetoptError as inst:
        raise error.CommandError(None, stringutil.forcebytestr(inst))

    if args:
        alias, args = args[0], args[1:]
    elif options[b'help']:
        help_(ui, None)
        sys.exit()
    else:
        alias, args = b'workbench', []

    aliases, i = cmdutil.findcmd(alias, table, ui.config(b"ui", b"strict"))
    for a in aliases:
        if a.startswith(alias):
            alias = a
            break
    cmd = aliases[0]
    c = list(i[1])

    # combine global options into local
    for o in globalopts:
        c.append((o[0], o[1], options[o[1]], o[3]))

    try:
        args = fancyopts.fancyopts(args, c, cmdoptions, True)
    except getopt.GetoptError as inst:
        raise error.CommandError(cmd, stringutil.forcebytestr(inst))

    # separate global options back out
    for o in globalopts:
        n = o[1]
        options[n] = cmdoptions[n]
        del cmdoptions[n]

    listfile = options.get(b'listfile')
    if listfile:
        del options[b'listfile']
        get_lines_from_listfile(listfile, False)
    listfileutf8 = options.get(b'listfileutf8')
    if listfileutf8:
        del options[b'listfileutf8']
        get_lines_from_listfile(listfileutf8, True)

    return cmd, cmd and i[0] or None, args, options, cmdoptions, alias

def _runcatch(ui, args):
    try:
        # read --config before doing anything else like Mercurial
        hglib.parseconfigopts(ui, args)
        # register config items specific to TortoiseHg GUI
        ui.setconfig(b'extensions', b'tortoisehg.util.configitems', b'', b'run')
        try:
            return runcommand(ui, args)
        finally:
            ui.flush()
    except error.AmbiguousCommand as inst:
        ui.warn(_("thg: command '%s' is ambiguous:\n    %s\n") %
                (inst.prefix, b" ".join(inst.matches)))
    except error.CommandError as inst:
        if inst.command:
            ui.warn(_("thg %s: %s\n") % (inst.command, inst.message))
            help_(ui, inst.command)
        else:
            ui.warn(_("thg: %s\n") % inst.message)
            help_(ui, b'shortlist')
    except error.UnknownCommand as inst:
        ui.warn(_("thg: unknown command '%s'\n") % inst.command)
        help_(ui, b'shortlist')
    except error.RepoError as inst:
        ui.warn(_("abort: %s!\n") % inst)
    except error.Abort as inst:
        ui.warn(_("abort: %s\n") % inst)
        if inst.hint:
            ui.warn(_("(%s)\n") % inst.hint)

    return -1

def runcommand(ui, args):
    cmd, func, args, options, cmdoptions, alias = _parse(ui, args)

    if options[b'config']:
        raise error.Abort(_('option --config may not be abbreviated!'))

    cmdoptions[b'alias'] = alias
    config = hgconfig.HgConfig(ui)
    ui.setconfig(b"ui", b"verbose", pycompat.bytestr(bool(options[b"verbose"])))
    ui.setconfig(b'ui', b'debug',
                 pycompat.bytestr(bool(options[b'debug']
                                       or 'THGDEBUG' in os.environ)))
    i18n.setlanguage(config.configString('tortoisehg', 'ui.language'))

    if options[b'help']:
        return help_(ui, cmd)

    path = options[b'repository']
    if path:
        if path.startswith(b'bundle:'):
            s = path[7:].split(b'+', 1)
            if len(s) == 1:
                path, bundle = os.getcwd(), s[0]
            else:
                path, bundle = s
            cmdoptions[b'bundle'] = os.path.abspath(bundle)
        path = urlutil.get_clone_path(ui, path)[0]
        # TODO: replace by abspath() if chdir() isn't necessary
        try:
            os.chdir(path)
            path = encoding.getcwd()
        except OSError:
            pass
    if options[b'profile']:
        options[b'nofork'] = True
    path = paths.find_root_bytes(path)
    if path:
        try:
            lui = ui.copy()
            lui.readconfig(os.path.join(path, b".hg", b"hgrc"))
        except OSError:
            pass
    else:
        lui = ui

    hglib.loadextensions(lui)

    args += get_files_from_listfile()

    if options[b'quiet']:
        ui.quiet = True

    # repository existence will be tested in qtrun()
    if cmd not in nonrepo_commands.split():
        cmdoptions[b'repository'] = path or options[b'repository'] or b'.'

    cmdoptions[b'mainapp'] = True
    checkedfunc = util.checksignature(func)
    if cmd in console_commands.split():
        d = lambda: checkedfunc(ui, *args, **pycompat.strkwargs(cmdoptions))
    else:
        # disables interaction with tty as it would block GUI events
        ui.setconfig(b'ui', b'interactive', b'off', b'qtrun')
        ui.setconfig(b'ui', b'paginate', b'off', b'qtrun')
        # ANSI color output is useless in GUI
        ui.setconfig(b'ui', b'color', b'off', b'qtrun')
        portable_fork(ui, options)
        d = lambda: qtrun(checkedfunc, ui, *args,
                          **pycompat.strkwargs(cmdoptions))
    return _runcommand(lui, options, cmd, d)

def _runcommand(ui, options, cmd, cmdfunc):
    def checkargs():
        try:
            return cmdfunc()
        except error.SignatureError:
            raise error.CommandError(cmd, _("invalid arguments"))

    if options[b'profile']:
        format = ui.config(b'profiling', b'format')
        field = ui.config(b'profiling', b'sort')

        if format not in [b'text', b'kcachegrind']:
            ui.warn(_("unrecognized profiling format '%s'"
                        " - Ignored\n") % format)
            format = b'text'

        output = ui.config(b'profiling', b'output')

        if output:
            path = util.expandpath(output)
            ostream = open(path, 'wb')
        else:
            ostream = procutil.stderr

        try:
            from mercurial import lsprof
        except ImportError:
            raise error.Abort(_(
                'lsprof not available - install from '
                'http://codespeak.net/svn/user/arigo/hack/misc/lsprof/'))
        p = lsprof.Profiler()
        p.enable(subcalls=True)
        try:
            return checkargs()
        finally:
            p.disable()

            if format == b'kcachegrind':
                from mercurial import lsprofcalltree
                calltree = lsprofcalltree.KCacheGrind(p)
                calltree.output(ostream)
            else:
                # format == b'text'
                stats = lsprof.Stats(p.getstats())
                stats.sort(pycompat.sysstr(field))
                stats.pprint(top=10, file=ostream, climit=5)

            if output:
                ostream.close()
    else:
        return checkargs()

qtrun = qtapp.QtRunner()

table = {}
command = registrar.command(table)

# common command options

globalopts = [
    (b'R', b'repository', b'',
     _('repository root directory or symbolic path name')),
    (b'v', b'verbose', None, _('enable additional output')),
    (b'q', b'quiet', None, _('suppress output')),
    (b'h', b'help', None, _('display help and exit')),
    (b'', b'config', [],
     _("set/override config option (use 'section.name=value')")),
    (b'', b'debug', None, _('enable debugging output')),
    (b'', b'debugger', None, _('start debugger')),
    (b'', b'traceback', None,
     _("show verbose tracebacks without using bug reporter dialog")),
    (b'', b'profile', None, _('print command execution profile')),
    (b'', b'nofork', None, _('do not fork GUI process')),
    (b'', b'fork', None, _('always fork GUI process')),
    (b'', b'listfile', b'', _('read file list from file')),
    (b'', b'listfileutf8', b'', _('read file list from file encoding utf-8')),
]

# common command functions

def _formatfilerevset(pats):
    q = [b"file('path:%s')" % f for f in hglib.canonpaths(pats)]
    return b' or '.join(q)

def _workbench(ui, *pats, **opts):
    root = opts.get('root') or paths.find_root_bytes()

    # TODO: unclear that _workbench() is called inside qtrun(). maybe this
    # function should receive factory object instead of using global qtrun.
    w = qtrun.createWorkbench()
    if root:
        root = hglib.tounicode(root)
        bundle = opts.get('bundle')
        if bundle:
            w.openRepo(root, False, bundle=hglib.tounicode(bundle))
        else:
            w.showRepo(root)
        rev = opts.get('rev')
        if rev:
            w.goto(hglib.fromunicode(root), rev)

        q = opts.get('query') or _formatfilerevset(pats)
        if q:
            w.setRevsetFilter(root, hglib.tounicode(q))
    if not w.currentRepoRootPath():
        w.reporegistry.setVisible(True)
    return w

# commands start here, listed alphabetically

@command(b'about', [], _('thg about'))
def about(ui, *pats, **opts):
    """about dialog"""
    from tortoisehg.hgqt import about as aboutmod
    return aboutmod.AboutDialog()

@command(b'add', [], _('thg add [FILE]...'))
def add(ui, repoagent, *pats, **opts):
    """add files"""
    return quickop.run(ui, repoagent, *pats, **opts)

@command(b'annotate|blame',
    [(b'r', b'rev', b'', _('revision to annotate')),
     (b'n', b'line', b'', _('open to line')),
     (b'p', b'pattern', b'', _('initial search pattern'))],
    _('thg annotate'),
    helpbasic=True)
def annotate(ui, repoagent, *pats, **opts):
    """annotate dialog"""
    from tortoisehg.hgqt import fileview
    dlg = filelog(ui, repoagent, *pats, **opts)
    dlg.setFileViewMode(fileview.AnnMode)
    if opts.get('line'):
        try:
            lineno = int(opts['line'])
        except ValueError:
            raise error.Abort(_('invalid line number: %s') % opts['line'])
        dlg.showLine(lineno)
    if opts.get('pattern'):
        dlg.setSearchPattern(hglib.tounicode(opts['pattern']))
    return dlg

@command(b'archive',
    [(b'r', b'rev', b'', _('revision to archive'))],
    _('thg archive'))
def archive(ui, repoagent, *pats, **opts):
    """archive dialog"""
    from tortoisehg.hgqt import archive as archivemod
    rev = hglib.tounicode(opts.get('rev'))
    return archivemod.createArchiveDialog(repoagent, rev)

@command(b'backout',
    [(b'', b'merge', None, _('merge with old dirstate parent after backout')),
     (b'', b'parent', b'', _('parent to choose when backing out merge')),
     (b'r', b'rev', b'', _('revision to backout'))],
    _('thg backout [OPTION]... [[-r] REV]'),
    helpbasic=True)
def backout(ui, repoagent, *pats, **opts):
    """backout tool"""
    from tortoisehg.hgqt import backout as backoutmod
    if opts.get('rev'):
        rev = opts.get('rev')
    elif len(pats) == 1:
        rev = pats[0]
    else:
        rev = b'tip'
    repo = repoagent.rawRepo()
    rev = scmutil.revsingle(repo, rev).rev()
    msg = backoutmod.checkrev(repo, rev)
    if msg:
        raise error.Abort(hglib.fromunicode(msg))
    return backoutmod.BackoutDialog(repoagent, rev)

@command(b'bisect', [], _('thg bisect'),
         helpbasic=True)
def bisect(ui, repoagent, *pats, **opts):
    """bisect dialog"""
    from tortoisehg.hgqt import bisect as bisectmod
    return bisectmod.BisectDialog(repoagent)

@command(b'bookmarks|bookmark',
    [(b'r', b'rev', b'', _('revision'))],
    _('thg bookmarks [-r REV] [NAME]'))
def bookmark(ui, repoagent, *names, **opts):
    """add or remove a movable marker"""
    from tortoisehg.hgqt import bookmark as bookmarkmod
    repo = repoagent.rawRepo()
    rev = scmutil.revsingle(repo, opts.get('rev')).rev()
    if len(names) > 1:
        raise error.Abort(_('only one new bookmark name allowed'))
    dlg = bookmarkmod.BookmarkDialog(repoagent, rev)
    if names:
        dlg.setBookmarkName(hglib.tounicode(names[0]))
    return dlg

@command(b'clone',
    [(b'U', b'noupdate', None, _('the clone will include an empty working copy '
                                 '(only a repository)')),
     (b'u', b'updaterev', b'', _('revision, tag or branch to check out')),
     (b'r', b'rev', b'', _('include the specified changeset')),
     (b'b', b'branch', [], _('clone only the specified branch')),
     (b'', b'pull', None, _('use pull protocol to copy metadata')),
     (b'', b'uncompressed', None, _('an alias to --stream (DEPRECATED)')),
     (b'', b'stream', None, _('clone with minimal data processing'))],
    _('thg clone [OPTION]... [SOURCE] [DEST]'),
    helpbasic=True)
def clone(ui, *pats, **opts):
    """clone tool"""
    from tortoisehg.hgqt import clone as clonemod
    opts['stream'] = opts['stream'] or opts['uncompressed']
    dlg = clonemod.CloneDialog(ui, hgconfig.HgConfig(ui))
    dlg.clonedRepository.connect(qtrun.openRepoInWorkbench)
    if len(pats) >= 1:
        dlg.setSource(hglib.tounicode(pats[0]))
    if len(pats) >= 2:
        dlg.setDestination(hglib.tounicode(pats[1]))
    dlg.setRevSymbol(hglib.tounicode(opts.get('rev') or b''))
    for k in ['noupdate', 'pull', 'stream']:
        dlg.setOption(k, bool(opts[k]))
    return dlg

@command(b'commit|ci',
    [(b'u', b'user', b'', _('record user as committer')),
     (b'd', b'date', b'', _('record datecode as commit date'))],
    _('thg commit [OPTIONS] [FILE]...'),
    helpbasic=True)
def commit(ui, repoagent, *pats, **opts):
    """commit tool"""
    from tortoisehg.hgqt import commit as commitmod
    repo = repoagent.rawRepo()
    pats = hglib.canonpaths(pats)
    os.chdir(repo.root)
    return commitmod.CommitDialog(repoagent, pats, opts)

@command(b'debugblockmatcher', [], _('thg debugblockmatcher'))
def debugblockmatcher(ui, *pats, **opts):
    """show blockmatcher widget"""
    from tortoisehg.hgqt import blockmatcher
    return blockmatcher.createTestWidget(ui)

@command(b'debugbugreport', [], _('thg debugbugreport [TEXT]'))
def debugbugreport(ui, *pats, **opts):
    """open bugreport dialog by exception"""
    raise Exception(' '.join(map(hglib.tounicode, pats)))

@command(b'debugconsole', [], _('thg debugconsole'))
def debugconsole(ui, repoagent, *pats, **opts):
    """open console window"""
    from tortoisehg.hgqt import docklog
    dlg = docklog.ConsoleWidget(repoagent)
    dlg.closeRequested.connect(dlg.close)
    dlg.resize(700, 400)
    return dlg

@command(b'debuglighthg', [], _('thg debuglighthg'))
def debuglighthg(ui, repoagent, *pats, **opts):
    from tortoisehg.hgqt import repowidget
    return repowidget.LightRepoWindow(qtrun.actionRegistry(), repoagent)

@command(b'debugruncommand', [],
    _('thg debugruncommand -- COMMAND [ARGUMENT]...'))
def debugruncommand(ui, repoagent, *cmdline, **opts):
    """run hg command in dialog"""
    if not cmdline:
        raise error.Abort(_('no command specified'))
    dlg = cmdui.CmdSessionDialog()
    dlg.setLogVisible(ui.verbose)
    sess = repoagent.runCommand(pycompat.maplist(hglib.tounicode, cmdline),
                                dlg)
    dlg.setSession(sess)
    return dlg

@command(b'drag_copy', [], _('thg drag_copy SOURCE... DEST'))
def drag_copy(ui, repoagent, *pats, **opts):
    """copy the selected files to the desired directory"""
    # TODO: drop the disable comment after updating to pytype 2023.03.02+
    opts.update(alias=b'copy', headless=True)  # pytype: disable=wrong-arg-types
    return quickop.run(ui, repoagent, *pats, **opts)

@command(b'drag_move', [], _('thg drag_move SOURCE... DEST'))
def drag_move(ui, repoagent, *pats, **opts):
    """move the selected files to the desired directory"""
    # TODO: drop the disable comment after updating to pytype 2023.03.02+
    opts.update(alias=b'move', headless=True)  # pytype: disable=wrong-arg-types
    return quickop.run(ui, repoagent, *pats, **opts)

@command(b'email',
    [(b'r', b'rev', [], _('a revision to send'))],
    _('thg email [REVS]'),
    helpbasic=True)
def email(ui, repoagent, *revs, **opts):
    """send changesets by email"""
    from tortoisehg.hgqt import hgemail
    # TODO: same options as patchbomb
    if opts.get('rev'):
        if revs:
            raise error.Abort(_('use only one form to specify the revision'))
        revs = opts.get('rev')

    repo = repoagent.rawRepo()
    revs = scmutil.revrange(repo, revs)
    return hgemail.EmailDialog(repoagent, revs)

@command(b'filelog',
    [(b'r', b'rev', b'', _('select the specified revision')),
     (b'', b'compare', False, _('side-by-side comparison of revisions'))],
    _('thg filelog [OPTION]... FILE'),
    helpbasic=True)
def filelog(ui, repoagent, *pats, **opts):
    """show history of the specified file"""
    from tortoisehg.hgqt import filedialogs
    if len(pats) != 1:
        raise error.Abort(_('requires a single filename'))
    repo = repoagent.rawRepo()
    rev = scmutil.revsingle(repo, opts.get('rev')).rev()
    filename = hglib.canonpaths(pats)[0]
    if opts.get('compare'):
        dlg = filedialogs.FileDiffDialog(repoagent, filename)
    else:
        dlg = filedialogs.FileLogDialog(repoagent, filename)
    dlg.goto(rev)
    return dlg

@command(b'forget', [], _('thg forget [FILE]...'))
def forget(ui, repoagent, *pats, **opts):
    """forget selected files"""
    return quickop.run(ui, repoagent, *pats, **opts)

@command(b'graft',
    [(b'r', b'rev', [], _('revisions to graft'))],
    _('thg graft [-r] REV...'))
def graft(ui, repoagent, *revs, **opts):
    """graft dialog"""
    from tortoisehg.hgqt import graft as graftmod
    repo = repoagent.rawRepo()
    revs = list(revs)
    revs.extend(opts['rev'])

    if not hglib.graft_in_progress(repo) and not revs:
        raise error.Abort(_('You must provide revisions to graft'))
    return graftmod.GraftDialog(repoagent, None,
        source=[hglib.tounicode(rev) for rev in revs])

@command(b'grep|search',
    [(b'i', b'ignorecase', False, _('ignore case during search'))],
    _('thg grep'),
    helpbasic=True)
def grep(ui, repoagent, *pats, **opts):
    """grep/search dialog"""
    from tortoisehg.hgqt import grep as grepmod
    upats = [hglib.tounicode(p) for p in pats]
    return grepmod.SearchDialog(repoagent, upats, **opts)

@command(b'guess', [], _('thg guess'),
         helpbasic=True)
def guess(ui, repoagent, *pats, **opts):
    """guess previous renames or copies"""
    from tortoisehg.hgqt import guess as guessmod
    return guessmod.DetectRenameDialog(repoagent, None, *pats)

### help management, adapted from mercurial.commands.help_()
@command(b'help', [], _('thg help [COMMAND]'))
def help_(ui, name: Optional[bytes]=None, with_version=False, **opts):
    """show help for a command, extension, or list of commands

    With no arguments, print a list of commands and short help.

    Given a command name, print help for that command.

    Given an extension name, print help for that extension, and the
    commands it provides."""
    option_lists = []
    textwidth = ui.termwidth() - 2

    def addglobalopts(aliases):
        if ui.verbose:
            option_lists.append((_("global options:"), globalopts))
            if name == b'shortlist':
                option_lists.append((_('use "thg help" for the full list '
                                       'of commands'), ()))
        else:
            if name == b'shortlist':
                msg = _('use "thg help" for the full list of commands '
                        'or "thg -v" for details')
            elif aliases:
                msg = _('use "thg -v help%s" to show aliases and '
                        'global options') % (name and b" " + name or b"")
            else:
                msg = _('use "thg -v help %s" to show global options') % name
            option_lists.append((msg, ()))

    def helpcmd(name: bytes) -> None:
        if with_version:
            version(ui)
            ui.write(b'\n')

        try:
            aliases, i = cmdutil.findcmd(name, table, False)
        except error.AmbiguousCommand as inst:
            prefix = inst.prefix
            select = lambda c: c.startswith(prefix)
            helplist(_('list of commands:\n\n'), select)
            return

        # synopsis
        ui.write(b"%s\n" % i[2])

        # aliases
        if not ui.quiet and len(aliases) > 1:
            ui.write(_("\naliases: %s\n") % b', '.join(aliases[1:]))

        # description
        doc = i[0].__doc__
        if doc:
            doc = hglib.fromunicode(doc)
            if ui.quiet:
                doc = doc.splitlines(False)[0]
        else:
            doc = _("(no help text available)")
        ui.write(b"\n%s\n" % doc.rstrip())

        if not ui.quiet:
            # options
            if i[1]:
                option_lists.append((_("options:\n"), i[1]))

            addglobalopts(False)

    def helplist(header, select=None):
        h = {}
        cmds = {}
        for c, e in table.items():
            f = c.split(b"|", 1)[0]
            if select and not select(f):
                continue
            if (not select and name != b'shortlist' and
                e[0].__module__ != __name__):
                continue
            if name == b"shortlist":
                continue
            if not ui.debugflag and f.startswith(b"debug"):
                continue
            doc = e[0].__doc__
            if doc and 'DEPRECATED' in doc and not ui.verbose:
                continue
            #doc = gettext(doc)
            if not doc:
                h[f] = _("(no help text available)")
            else:
                h[f] = hglib.fromunicode(doc.splitlines()[0].rstrip())
            cmds[f] = c

        if not h:
            ui.status(_('no commands defined\n'))
            return

        ui.status(header)
        fns = sorted(h)
        m = max(map(len, fns))
        for f in fns:
            if ui.verbose:
                commands = cmds[f].replace(b"|", b", ")
                ui.write(b" %s:\n      %s\n" % (commands, h[f]))
            else:
                ui.write(b'%s\n'
                         % (stringutil.wrap(h[f], textwidth,
                                            initindent=b' %-*s   ' % (m, f),
                                            hangindent=b' ' * (m + 4))))

        if not ui.quiet:
            addglobalopts(True)

    def helptopic(name: bytes) -> None:
        from mercurial import help
        for topic in help.helptable:
            names, header, doc = topic[0:3]
            if name in names:
                break
        else:
            raise error.UnknownCommand(name)

        # description
        if not doc:
            doc = _("(no help text available)")
        if hasattr(doc, '__call__'):
            doc = doc(ui)

        ui.write(b"%s\n" % header)
        ui.write(b"%s\n" % doc.rstrip())

    if name and name != b'shortlist':
        i = None
        for f in (helpcmd, helptopic):
            try:
                f(name)
                i = None
                break
            except error.UnknownCommand as inst:
                i = inst
        if i:
            raise i

    else:
        # program name
        if ui.verbose or with_version:
            version(ui)
        else:
            ui.status(_("Thg - TortoiseHg's GUI tools for Mercurial SCM (Hg)\n"))
        ui.status(b'\n')

        # list of commands
        if name == b"shortlist":
            header = _('basic commands:\n\n')
        else:
            header = _('list of commands:\n\n')

        helplist(header)

    # list all option lists
    opt_output = []
    for title, options in option_lists:
        opt_output.append((b"\n%s" % title, None))
        for shortopt, longopt, default, desc in options:
            if b"DEPRECATED" in desc and not ui.verbose:
                continue
            opt_output.append((b"%2s%s" % (shortopt and b"-%s" % shortopt,
                                           longopt and b" --%s" % longopt),
                               b"%s%s" % (desc,
                                          default
                                          and _(" (default: %s)") % default
                                          or b"")))

    if opt_output:
        opts_len = max([len(line[0]) for line in opt_output if line[1]] or [0])
        for first, second in opt_output:
            if second:
                initindent = b' %-*s  ' % (opts_len, first)
                hangindent = b' ' * (opts_len + 3)
                ui.write(b'%s\n' % (stringutil.wrap(second, textwidth,
                                                    initindent=initindent,
                                                    hangindent=hangindent)))
            else:
                ui.write(b"%s\n" % first)

@command(b'hgignore|ignore|filter', [], _('thg hgignore [FILE]'),
         helpbasic=True)
def hgignore(ui, repoagent, *pats, **opts):
    """ignore filter editor"""
    from tortoisehg.hgqt import hgignore as hgignoremod
    if pats and pats[0].endswith(b'.hgignore'):
        pats = []
    return hgignoremod.HgignoreDialog(repoagent, None, *pats)

@command(b'import',
    [(b'', b'mq', False, _('import to the patch queue (MQ)'))],
    _('thg import [OPTION] [SOURCE]...'))
def import_(ui, repoagent, *pats, **opts):
    """import an ordered set of patches"""
    from tortoisehg.hgqt import thgimport
    dlg = thgimport.ImportDialog(repoagent, None, **opts)
    dlg.setfilepaths(pats)
    return dlg

@command(b'init', [], _('thg init [DEST]'),
         helpbasic=True)
def init(ui, dest='.', **opts):
    """init dialog"""
    from tortoisehg.hgqt import hginit
    dlg = hginit.InitDialog(ui, hglib.tounicode(dest))
    dlg.newRepository.connect(qtrun.openRepoInWorkbench)
    return dlg

@command(b'lock|unlock', [], _('thg lock'),
         helpbasic=True)
def lock(ui, repoagent, **opts):
    """lock dialog"""
    from tortoisehg.hgqt import locktool
    return locktool.LockDialog(repoagent)

@command(b'log|history|explorer|workbench',
    [(b'k', b'query', b'', _('search for a given text or revset')),
     (b'r', b'rev', b'', _('select the specified revision')),
     (b'l', b'limit', b'', _('(DEPRECATED)')),
     (b'', b'newworkbench', None, _('open a new workbench window'))],
    _('thg log [OPTIONS] [FILE]'),
    helpbasic=True)
def log(ui, *pats, **opts):
    """workbench application"""
    if opts.get('query') and pats:
        # 'filelog' does not support -k, and multiple filenames are packed
        # into revset query that may conflict with user-supplied one.
        raise error.Abort(_('cannot specify both -k/--query and filenames'))

    root = opts.get('root') or paths.find_root_bytes()
    if root and len(pats) == 1 and os.path.isfile(pats[0]):
        # TODO: do not instantiate repo here
        repo = thgrepo.repository(ui, root)
        repoagent = repo._pyqtobj
        return filelog(ui, repoagent, *pats, **opts)

    # Before starting the workbench, we must check if we must try to reuse an
    # existing workbench window (we don't by default)
    # Note that if the "single workbench mode" is enabled, and there is no
    # existing workbench window, we must tell the Workbench object to create
    # the workbench server
    singleworkbenchmode = ui.configbool(b'tortoisehg', b'workbench.single')
    mustcreateserver = False
    if singleworkbenchmode:
        newworkbench = opts.get('newworkbench')
        if root and not newworkbench:
            # TODO: send -rREV to server
            q = opts.get('query') or _formatfilerevset(pats)
            if qtapp.connectToExistingWorkbench(root, q):
                # The were able to connect to an existing workbench server, and
                # it confirmed that it has opened the selected repo for us
                sys.exit(0)
            # there is no pre-existing workbench server
            serverexists = False
        else:
            serverexists = qtapp.connectToExistingWorkbench(b'[echo]')
        # When in " single workbench mode", we must create a server if there
        # is not one already
        mustcreateserver = not serverexists

    w = _workbench(ui, *pats, **opts)
    if mustcreateserver:
        qtrun.createWorkbenchServer()
    return w

@command(b'manifest',
    [(b'r', b'rev', b'', _('revision to display')),
     (b'n', b'line', b'', _('open to line')),
     (b'p', b'pattern', b'', _('initial search pattern'))],
    _('thg manifest [-r REV] [FILE]'))
def manifest(ui, repoagent, *pats, **opts):
    """display the current or given revision of the project manifest"""
    from tortoisehg.hgqt import revdetails as revdetailsmod
    repo = repoagent.rawRepo()
    rev = scmutil.revsingle(repo, opts.get('rev')).rev()
    dlg = revdetailsmod.createManifestDialog(repoagent, rev)
    if pats:
        path = hglib.canonpaths(pats)[0]
        dlg.setFilePath(hglib.tounicode(path))
        if opts.get('line'):
            try:
                lineno = int(opts['line'])
            except ValueError:
                raise error.Abort(_('invalid line number: %s') % opts['line'])
            dlg.showLine(lineno)
    if opts.get('pattern'):
        dlg.setSearchPattern(hglib.tounicode(opts['pattern']))
    return dlg

@command(b'merge',
    [(b'r', b'rev', b'', _('revision to merge'))],
    _('thg merge [[-r] REV]'),
    helpbasic=True)
def merge(ui, repoagent, *pats, **opts):
    """merge wizard"""
    from tortoisehg.hgqt import merge as mergemod
    rev = opts.get('rev') or None
    if not rev and len(pats):
        rev = pats[0]
    if not rev:
        raise error.Abort(_('Merge revision not specified or not found'))
    repo = repoagent.rawRepo()
    rev = scmutil.revsingle(repo, rev).rev()
    return mergemod.MergeDialog(repoagent, rev)

@command(b'postreview',
    [(b'r', b'rev', [], _('a revision to post'))],
    _('thg postreview [-r] REV...'))
def postreview(ui, repoagent, *pats, **opts):
    """post changesets to reviewboard"""
    from tortoisehg.hgqt import postreview as postreviewmod
    repo = repoagent.rawRepo()
    if b'reviewboard' not in repo.extensions():
        url = b'https://www.mercurial-scm.org/wiki/ReviewboardExtension'
        raise error.Abort(_('reviewboard extension not enabled'),
                          hint=(_('see <a href="%(url)s">%(url)s</a>')
                                % {b'url': url}))
    revs = opts.get('rev') or None
    if not revs and len(pats):
        revs = pats
    if not revs:
        raise error.Abort(_('no revisions specified'))
    return postreviewmod.PostReviewDialog(repo.ui, repoagent, revs)

@command(b'prune|obsolete',
    [(b'r', b'rev', [], _('revisions to prune'))],
    _('thg prune [-r] REV...'),
    helpbasic=True)
def prune(ui, repoagent, *revs, **opts):
    """hide changesets by marking them obsolete"""
    from tortoisehg.hgqt import prune as prunemod
    revs = list(revs)
    revs.extend(opts.get('rev'))
    revs = pycompat.maplist(hglib.tounicode, revs)

    if len(revs) < 2:
        revspec = ''.join(revs)
    else:
        revspec = hglib.formatrevspec('%lr', revs)
    return prunemod.createPruneDialog(repoagent, revspec)

@command(b'purge', [], _('thg purge'),
         helpbasic=True)
def purge(ui, repoagent, *pats, **opts):
    """purge unknown and/or ignore files from repository"""
    from tortoisehg.hgqt import purge as purgemod
    return purgemod.PurgeDialog(repoagent)

@command(b'rebase',
    [(b'', b'keep', False, _('keep original changesets')),
     (b'', b'keepbranches', False, _('keep original branch names')),
     (b'', b'detach', False, _('(DEPRECATED)')),
     (b's', b'source', b'', _('rebase from the specified changeset')),
     (b'd', b'dest', b'', _('rebase onto the specified changeset'))],
    _('thg rebase -s REV -d REV [--keep]'),
    helpbasic=True)
def rebase(ui, repoagent, *pats, **opts):
    """rebase dialog"""
    from tortoisehg.hgqt import rebase as rebasemod
    repo = repoagent.rawRepo()
    if hglib.rebase_in_progress(repo):
        # TODO: move info dialog into RebaseDialog if possible
        qtlib.InfoMsgBox(hglib.tounicode(_('Rebase already in progress')),
                         hglib.tounicode(_('Resuming rebase already in '
                                           'progress')))
    elif not opts['source'] or not opts['dest']:
        raise error.Abort(_('You must provide source and dest arguments'))

    for name in ('source', 'dest'):
        revspec = opts[name]
        if revspec:
            revs = scmutil.revrange(repo, [revspec])

            if not revs:
                raise error.Abort(
                    _("empty revision range in --%s") % hglib.fromunicode(name)
                )

            if len(revs) > 1:
                raise error.Abort(
                    _("multiple revisions cannot be given to --%s")
                    % hglib.fromunicode(name)
                )

            opts[name] = revs.first()

    return rebasemod.RebaseDialog(repoagent, None, **opts)

@command(b'rejects', [], _('thg rejects [FILE]'))
def rejects(ui, *pats, **opts):
    """manually resolve rejected patch chunks"""
    from tortoisehg.hgqt import rejects as rejectsmod
    if len(pats) != 1:
        raise error.Abort(_('You must provide the path to a file'))
    path = pats[0]
    if path.endswith(b'.rej'):
        path = path[:-4]
    return rejectsmod.RejectsDialog(ui, path)

@command(b'remove|rm', [], _('thg remove [FILE]...'))
def remove(ui, repoagent, *pats, **opts):
    """remove selected files"""
    return quickop.run(ui, repoagent, *pats, **opts)

@command(b'rename|mv|copy', [], _('thg rename [SOURCE] [DEST]'))
def rename(ui, repoagent, source=None, dest=None, **opts):
    """rename dialog"""
    from tortoisehg.hgqt import rename as renamemod
    repo = repoagent.rawRepo()
    cwd = repo.getcwd()
    if source:
        source = hglib.tounicode(pathutil.canonpath(repo.root, cwd, source))
    if dest:
        dest = hglib.tounicode(pathutil.canonpath(repo.root, cwd, dest))
    iscopy = (opts.get('alias') == b'copy')
    return renamemod.RenameDialog(repoagent, None, source, dest, iscopy)

@command(b'repoconfig',
    [(b'', b'focus', b'', _('field to give initial focus'))],
    _('thg repoconfig'),
    helpbasic=True)
def repoconfig(ui, repoagent, *pats, **opts):
    """repository configuration editor"""
    from tortoisehg.hgqt import settings
    return settings.SettingsDialog(True, focus=opts.get('focus'))

@command(b'resolve', [], _('thg resolve'))
def resolve(ui, repoagent, *pats, **opts):
    """resolve dialog"""
    from tortoisehg.hgqt import resolve as resolvemod
    return resolvemod.ResolveDialog(repoagent)

@command(b'revdetails',
    [(b'r', b'rev', b'', _('the revision to show'))],
    _('thg revdetails [-r REV]'),
    helpbasic=True)
def revdetails(ui, repoagent, *pats, **opts):
    """revision details tool"""
    from tortoisehg.hgqt import revdetails as revdetailsmod
    repo = repoagent.rawRepo()
    os.chdir(repo.root)
    rev = scmutil.revsingle(repo, opts.get('rev')).rev()
    return revdetailsmod.RevDetailsDialog(repoagent, rev=rev)

@command(b'revert', [], _('thg revert [FILE]...'))
def revert(ui, repoagent, *pats, **opts):
    """revert selected files"""
    return quickop.run(ui, repoagent, *pats, **opts)

@command(b'serve',
    [(b'', b'web-conf', b'', _('name of the hgweb config file (serve more than '
                               'one repository)')),
     (b'', b'webdir-conf', b'',
      _('name of the hgweb config file (DEPRECATED)'))],
    _('thg serve [--web-conf FILE]'),
    helpbasic=True)
def serve(ui, *pats, **opts):
    """start stand-alone webserver"""
    from tortoisehg.hgqt import serve as servemod
    return servemod.run(ui, *pats, **opts)

if os.name == 'nt':
    # TODO: extra detection to determine if shell extension is installed
    @command(b'shellconfig', [], _('thg shellconfig'))
    def shellconfig(ui, *pats, **opts):
        """explorer extension configuration editor"""
        from tortoisehg.hgqt import shellconf
        return shellconf.ShellConfigWindow()

@command(b'shelve|unshelve', [], _('thg shelve'))
def shelve(ui, repoagent, *pats, **opts):
    """move changes between working directory and patches"""
    from tortoisehg.hgqt import shelve as shelvemod
    return shelvemod.ShelveDialog(repoagent)

@command(b'sign',
    [(b'f', b'force', None, _('sign even if the sigfile is modified')),
     (b'l', b'local', None, _('make the signature local')),
     (b'k', b'key', b'', _('the key id to sign with')),
     (b'', b'no-commit', None, _('do not commit the sigfile after signing')),
     (b'm', b'message', b'', _('use <text> as commit message'))],
    _('thg sign [-f] [-l] [-k KEY] [-m TEXT] [REV]'),
    helpbasic=True)
def sign(ui, repoagent, *pats, **opts):
    """sign tool"""
    from tortoisehg.hgqt import sign as signmod
    repo = repoagent.rawRepo()
    if b'gpg' not in repo.extensions():
        raise error.Abort(_('Please enable the Gpg extension first.'))
    kargs = {}
    rev = len(pats) > 0 and pats[0] or None
    if rev:
        kargs['rev'] = rev
    return signmod.SignDialog(repoagent, opts=opts, **kargs)

@command(b'status|st',
    [(b'c', b'clean', False, _('show files without changes')),
     (b'i', b'ignored', False, _('show ignored files'))],
    _('thg status [OPTIONS] [FILE]'),
    helpbasic=True)
def status(ui, repoagent, *pats, **opts):
    """browse working copy status"""
    from tortoisehg.hgqt import status as statusmod
    repo = repoagent.rawRepo()
    pats = hglib.canonpaths(pats)
    os.chdir(repo.root)
    return statusmod.StatusDialog(repoagent, pats, opts)

@command(b'strip',
    [(b'f', b'force', None, _('discard uncommitted changes (no backup)')),
     (b'n', b'nobackup', None, _('do not back up stripped revisions')),
     (b'k', b'keep', None, _('do not modify working copy during strip')),
     (b'r', b'rev', b'', _('revision to strip'))],
    _('thg strip [-k] [-f] [-n] [[-r] REV]'),
    helpbasic=True)
def strip(ui, repoagent, *pats, **opts):
    """strip dialog"""
    from tortoisehg.hgqt import thgstrip
    rev = None
    if opts.get('rev'):
        rev = opts.get('rev')
    elif len(pats) == 1:
        rev = pats[0]
    return thgstrip.createStripDialog(repoagent, rev=hglib.tounicode(rev),
                                      opts=opts)

@command(b'sync|synchronize',
    [(b'B', b'bookmarks', False, _('open the bookmark sync window'))],
    _('thg sync [OPTION]... [PEER]'),
    helpbasic=True)
def sync(ui, repoagent, url=None, **opts):
    """synchronize with other repositories"""
    from tortoisehg.hgqt import bookmark as bookmarkmod, repowidget
    url = hglib.tounicode(url)
    if opts.get('bookmarks'):
        return bookmarkmod.SyncBookmarkDialog(repoagent, url)

    repo = repoagent.rawRepo()
    repo.ui.setconfig(b'tortoisehg', b'defaultwidget', b'sync')
    w = repowidget.LightRepoWindow(qtrun.actionRegistry(), repoagent)
    if url:
        w.setSyncUrl(url)
    return w

@command(b'tag',
    [(b'f', b'force', None, _('replace existing tag')),
     (b'l', b'local', None, _('make the tag local')),
     (b'r', b'rev', b'', _('revision to tag')),
     (b'', b'remove', None, _('remove a tag')),
     (b'm', b'message', b'', _('use <text> as commit message'))],
    _('thg tag [-f] [-l] [-m TEXT] [-r REV] [NAME]'),
    helpbasic=True)
def tag(ui, repoagent, *pats, **opts):
    """tag tool"""
    from tortoisehg.hgqt import tag as tagmod
    kargs = {}
    tag = len(pats) > 0 and pats[0] or None
    if tag:
        kargs['tag'] = tag
    rev = opts.get('rev')
    if rev:
        kargs['rev'] = hglib.tounicode(rev)
    return tagmod.TagDialog(repoagent, opts=opts, **kargs)

@command(b'thgstatus',
    [(b'',  b'delay', None, _('wait until the second ticks over')),
     (b'n', b'notify', [], _('notify the shell for paths given')),
     (b'',  b'remove', None, _('remove the status cache')),
     (b's', b'show', None, _('show the contents of the status cache '
                             '(no update)')),
     (b'',  b'all', None, _('update all repos in current dir'))],
    _('thg thgstatus [OPTION]'))
def thgstatus(ui, *pats, **opts):
    """update TortoiseHg status cache"""
    from tortoisehg.util import thgstatus as thgstatusmod
    thgstatusmod.run(ui, *pats, **opts)

@command(b'topics',
    [(b'r', b'rev', b'', _('revision'))],
    _('thg topics [-r REV] [NAME]'))
def topics(ui, repoagent, *names, **opts):
    """set or clear a topic"""
    repo = repoagent.rawRepo()
    if b'topic' not in repo.extensions():
        raise error.Abort(_('Please enable the Topic extension first.'))
    from tortoisehg.hgqt import topic as topicmod
    rev = scmutil.revsingle(repo, opts.get('rev'), default=None).rev()
    if len(names) > 1:
        raise error.Abort(_('only one new topic name allowed'))
    dlg = topicmod.TopicDialog(repoagent, rev)
    if names:
        dlg.setTopicName(hglib.tounicode(names[0]))
    return dlg

@command(b'update|checkout|co',
    [(b'C', b'clean', None, _('discard uncommitted changes (no backup)')),
     (b'r', b'rev', b'', _('revision to update')),],
    _('thg update [-C] [[-r] REV]'),
    helpbasic=True)
def update(ui, repoagent, *pats, **opts):
    """update/checkout tool"""
    from tortoisehg.hgqt import update as updatemod
    rev = None
    if opts.get('rev'):
        rev = hglib.tounicode(opts.get('rev'))
    elif len(pats) == 1:
        rev = hglib.tounicode(pats[0])
    return updatemod.UpdateDialog(repoagent, rev, None, opts)

@command(b'userconfig',
    [(b'', b'focus', b'', _('field to give initial focus'))],
    _('thg userconfig'),
    helpbasic=True)
def userconfig(ui, *pats, **opts):
    """user configuration editor"""
    from tortoisehg.hgqt import settings
    return settings.SettingsDialog(False, focus=opts.get('focus'))

@command(b'vdiff',
    [(b'c', b'change', b'', _('changeset to view in diff tool')),
     (b'r', b'rev', [], _('revisions to view in diff tool')),
     (b'b', b'bundle', b'', _('bundle file to preview'))],
    _('launch visual diff tool'),
    helpbasic=True)
def vdiff(ui, repoagent, *pats, **opts):
    """launch configured visual diff tool"""
    from tortoisehg.hgqt import visdiff
    repo = repoagent.rawRepo()
    if opts.get('bundle'):
        repo = thgrepo.repository(ui, opts.get('bundle'))
    pats = hglib.canonpaths(pats)

    rev = opts.get('rev')
    if rev:
        opts['rev'] = [hglib.tounicode(r) for r in rev]

    rev = opts.get('change')
    if rev:
        opts['change'] = hglib.tounicode(rev)

    return visdiff.visualdiff(ui, repo, pats, opts)

@command(b'version',
    [(b'v', b'verbose', None, _('print license'))],
    _('thg version [OPTION]'),
    helpbasic=True)
def version(ui, **opts):
    """output version and copyright information"""
    ui.write(_('TortoiseHg Dialogs (version %s), '
               'Mercurial (version %s)\n') %
             (pycompat.sysbytes(thgversion.version()),
              pycompat.sysbytes(hglib.hgversion)))
    if not ui.quiet:
        ui.write(shortlicense)
