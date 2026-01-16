# htmlui.py - mercurial.ui.ui class which emits HTML/Rich Text
#
# Copyright 2010 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import time

from mercurial import (
    ui as ui_mod,
    url as url_mod,
)
from tortoisehg.hgqt import qtlib
from tortoisehg.util import hglib

BEGINTAG = b'\033' + hglib.fromunicode(str(time.time()))
ENDTAG = b'\032' + hglib.fromunicode(str(time.time()))

class htmlui(ui_mod.ui):
    def __init__(self, src=None):
        super().__init__(src)
        self.setconfig(b'ui', b'interactive', b'off')
        self.setconfig(b'progress', b'disable', b'True')
        self.output, self.error = [], []

    def write(self, *args, **opts):
        if self._buffers:
            return super().write(*args, **opts)
        label = opts.get('label', b'')
        self.output.extend(self.smartlabel(b''.join(args), label))

    def write_err(self, *args, **opts):
        if self._buffers:
            return super().write_err(*args, **opts)
        label = opts.get('label', b'ui.error')
        self.error.extend(self.smartlabel(b''.join(args), label))

    def label(self, msg, label):
        '''
        Called by Mercurial to apply styling (formatting) to a piece of
        text.  Our implementation wraps tags around the data so we can
        find it later when it is passed to smartlabel through ui.write()
        '''
        if self._buffers:
            return super().label(msg, label)
        return BEGINTAG + self.style(msg, label) + ENDTAG

    def style(self, msg, label):
        '''htmlui specific method.
        Escape message as safe HTML with style from specified label.
        '''
        msg = url_mod.escape(msg).replace(b'\n', b'<br />')
        style = hglib.fromunicode(qtlib.geteffect(hglib.tounicode(label)))
        return b'<span style="%s">%s</span>' % (style, msg)

    def smartlabel(self, text, label):
        '''htmlui specific method.
        Escape and apply style from label on text, excluding any text between
        BEGINTAG and ENDTAG which already must have been escaped and styled.
        '''
        parts = []
        try:
            while True:
                b = text.index(BEGINTAG)
                e = text.index(ENDTAG)
                if e > b:
                    if b:
                        parts.append(self.style(text[:b], label))
                    parts.append(text[b + len(BEGINTAG):e])
                    text = text[e + len(ENDTAG):]
                else:
                    # invalid range, assume ENDTAG and BEGINTAG
                    # are naturually occuring.  Style, append, and
                    # consume up to the BEGINTAG and repeat.
                    parts.append(self.style(text[:b], label))
                    text = text[b:]
        except ValueError:
            pass
        if text:
            parts.append(self.style(text, label))
        return parts

    def plain(self, feature=None):
        '''Always pretend HGPLAIN when using htmlui'''
        return True

    def getdata(self):
        '''htmlui specific method.
        Retrieve buffered htmlui output and error as unicode html.
        '''
        d, e = b''.join(self.output), b''.join(self.error)
        self.output, self.error = [], []
        return d, e

if __name__ == "__main__":
    from mercurial import hg
    u = htmlui.load()
    repo = hg.repository(u)
    repo.status()
    print(u.getdata()[0])
