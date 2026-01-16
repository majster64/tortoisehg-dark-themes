# pipeui.py - append parsable label to output, prompt and progress
#
# Copyright 2014 Yuya Nishihara <yuya@tcha.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

"""append parsable label to output, prompt and progress

This extension is intended to be used with the command server, so the packed
message provides no reliable length field.

Message structure::

    without label:
    |msg...|

    with label:
    |'\1'|label...|'\n'|msg...|

    progress:
    |'\1'|'ui.progress'|'\n'|topic|'\0'|pos|'\0'|item|'\0'|unit|'\0'|total|

    prompt:
    |'\1'|'ui.prompt'|'\n'|msg|'\0'|default|

Labels:

ui.getpass (with ui.prompt)
    denotes message for password prompt
ui.progress
    contains packed progress data (not for display)
ui.promptchoice (with ui.prompt)
    denotes message and choices for prompt
"""

from __future__ import annotations

import time

from mercurial import (
    error,
    scmutil,
)

from tortoisehg.util import (
    hgversion,
    version,
)
from tortoisehg.util.i18n import agettext as _

testedwith = hgversion.testedwith
getversion = version.version_bytes

class _labeledstr(bytes):
    r"""
    >>> a = _labeledstr(b'foo', b'ui.warning')
    >>> a.packed()
    b'\x01ui.warning\nfoo'
    >>> _labeledstr(a, b'ui.error').packed()
    b'\x01ui.warning ui.error\nfoo'
    >>> _labeledstr(b'foo', b'').packed()
    b'foo'
    >>> _labeledstr(b'\1foo', b'').packed()
    b'\x01\n\x01foo'
    >>> _labeledstr(a, b'') is a  # fast path
    True
    """

    def __new__(cls, s, l):
        if isinstance(s, cls):
            if not l:
                return s
            if s._label:
                l = s._label + b' ' + l
        t = bytes.__new__(cls, s)
        t._label = l
        return t

    def packed(self):
        if not self._label and not self.startswith(b'\1'):
            return bytes(self)
        return b'\1%s\n%s' % (self._label, self)

def _packmsgs(msgs, label):
    r"""
    >>> _packmsgs([b'foo'], b'')
    [b'foo']
    >>> _packmsgs([b'foo ', b'bar'], b'')
    [b'foo bar']
    >>> _packmsgs([b'foo ', b'bar'], b'ui.status')
    [b'\x01ui.status\nfoo bar']
    >>> _packmsgs([b'foo ', _labeledstr(b'bar', b'log.branch')], b'')
    [b'foo ', b'\x01log.branch\nbar']
    """
    if not any(isinstance(e, _labeledstr) for e in msgs):
        # pack into single message to avoid overhead of label header and
        # channel protocol; also it's convenient for command-server client
        # to receive the whole message at once.
        if len(msgs) > 1:
            msgs = [b''.join(msgs)]
        if not label:
            # fast path
            return msgs
    return [_labeledstr(e, label).packed() for e in msgs]

def splitmsgs(data):
    r"""Split data to list of packed messages assuming that original messages
    contain no '\1' character

    >>> splitmsgs(b'')
    []
    >>> splitmsgs(b'\x01ui.warning\nfoo\x01\nbar')
    [b'\x01ui.warning\nfoo', b'\x01\nbar']
    >>> splitmsgs(b'foo\x01ui.warning\nbar')
    [b'foo', b'\x01ui.warning\nbar']
    """
    msgs = data.split(b'\1')
    if msgs[0]:
        return msgs[:1] + [b'\1' + e for e in msgs[1:]]
    else:
        return [b'\1' + e for e in msgs[1:]]

def unpackmsg(data):
    r"""Try to unpack data to original message and label

    >>> unpackmsg(b'foo')
    (b'foo', b'')
    >>> unpackmsg(b'\x01ui.warning\nfoo')
    (b'foo', b'ui.warning')
    >>> unpackmsg(b'\x01ui.warning')  # immature end
    (b'', b'ui.warning')
    """
    if not data.startswith(b'\1'):
        return data, b''
    try:
        label, msg = data[1:].split(b'\n', 1)
        return msg, label
    except ValueError:
        return b'', data[1:]

def _packprompt(msg, default):
    r"""
    >>> _packprompt(b'foo', None)
    b'foo\x00'
    >>> _packprompt(_labeledstr(b'$$ &Yes', b'ui.promptchoice'), b'y').packed()
    b'\x01ui.promptchoice\n$$ &Yes\x00y'
    """
    s = b'\0'.join((msg, default or b''))
    if not isinstance(msg, _labeledstr):
        return s
    return _labeledstr(s, msg._label)

def unpackprompt(msg):
    """Try to unpack prompt message to original message and default value"""
    args = msg.split(b'\0', 1)
    if len(args) == 1:
        return msg, b''
    else:
        return args

def _fromint(n):
    if n is None:
        return b''
    return b'%d' % n

def _toint(s):
    if not s:
        return None
    return int(s)

def _packprogress(topic, pos, item, unit, total):
    r"""
    >>> _packprogress(b'updating', 1, b'foo', b'files', 5)
    b'updating\x001\x00foo\x00files\x005'
    >>> _packprogress(b'updating', None, b'', b'', None)
    b'updating\x00\x00\x00\x00'
    """
    return b'\0'.join((topic, _fromint(pos), item, unit, _fromint(total)))

def unpackprogress(msg):
    r"""Try to unpack progress message to tuple of parameters

    >>> unpackprogress(b'updating\x001\x00foo\x00files\x005')
    (b'updating', 1, b'foo', b'files', 5)
    >>> unpackprogress(b'updating\x00\x00\x00\x00')
    (b'updating', None, b'', b'', None)
    >>> unpackprogress(b'updating\x001\x00foo\x00files')  # immature end
    (b'updating', None, b'', b'', None)
    >>> unpackprogress(b'')  # no separator
    (b'', None, b'', b'', None)
    >>> unpackprogress(b'updating\x00a\x00foo\x00files\x005')  # invalid pos
    (b'updating', None, b'', b'', None)
    """
    try:
        topic, pos, item, unit, total = msg.split(b'\0')
        return topic, _toint(pos), item, unit, _toint(total)
    except ValueError:
        # fall back to termination
        topic = msg.split(b'\0', 1)[0]
        return topic, None, b'', b'', None

_progressrefresh = 0.1  # [sec]

def _extenduiclass(parcls):
    class pipeui(parcls):
        _lastprogresstopic = None
        _lastprogresstime = 0

        def _writenobuf(self, dest, *args, **opts):
            label = opts.get('label', b'')
            super()._writenobuf(dest, *_packmsgs(args, label),
                                            **opts)

        def prompt(self, msg, default=b'y'):
            fullmsg = _packprompt(msg, default)
            return super().prompt(fullmsg, default)

        # write raw prompt value with choices
        def promptchoice(self, prompt, default=0):
            _msg, choices = self.extractchoices(prompt)
            resps = [r for r, _t in choices]
            prompt = self.label(prompt, b'ui.promptchoice')
            r = self.prompt(prompt, resps[default])
            try:
                return resps.index(r.lower())
            except ValueError:
                raise error.Abort(_('unrecognized response: %s') % r)

        def getpass(self, prompt=None, default=None):
            prompt = self.label(prompt or _('password: '), b'ui.getpass')
            return super().getpass(prompt, default)

        # TODO: progress handler can be extracted to new class, and pass its
        # instance to scmutil.progress() per makeprogress() call.
        def progress(self, topic, pos, item=b'', unit=b'', total=None):
            now = time.time()
            if (topic == self._lastprogresstopic and pos is not None
                and now - self._lastprogresstime < _progressrefresh):
                # skip busy increment of the same topic
                return
            if pos is None:
                # the topic is about to be closed
                self._lastprogresstopic = None
            else:
                self._lastprogresstopic = topic
            self._lastprogresstime = now
            msg = _packprogress(topic, pos, item, unit, total)
            self.write_err(msg, label=b'ui.progress')

        def makeprogress(self, topic, unit=b'', total=None):
            return scmutil.progress(self, self.progress, topic, unit, total)

        def label(self, msg, label):
            return _labeledstr(msg, label)

    return pipeui

def uisetup(ui):
    ui.__class__ = _extenduiclass(ui.__class__)
