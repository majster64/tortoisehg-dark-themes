# i18n.py - TortoiseHg internationalization code
#
# Copyright 2009 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

import gettext
import locale
import os
from typing import (
    Dict,
    List,
    Optional,
)

from mercurial import pycompat

from . import paths

_localeenvs = ('LANGUAGE', 'LC_ALL', 'LC_MESSAGES', 'LANG')
def _defaultlanguage() -> Optional[str]:
    if os.name != 'nt' or any(e in os.environ for e in _localeenvs):
        return  # honor posix-style env var

    # On Windows, UI language can be determined by GetUserDefaultUILanguage(),
    # but gettext doesn't take it into account.
    # Note that locale.getdefaultlocale() uses GetLocaleInfo(), which may be
    # different from UI language.
    #
    # For details, please read "User Interface Language Management":
    # http://msdn.microsoft.com/en-us/library/dd374098(v=VS.85).aspx
    try:
        from ctypes import windll  # pytype: disable=import-error
        langid = windll.kernel32.GetUserDefaultUILanguage()
        return locale.windows_locale[langid]
    except (ImportError, AttributeError, KeyError):
        pass

def setlanguage(lang: Optional[str] = None) -> None:
    """Change translation catalog to the specified language"""
    global t, language
    if not lang:
        lang = _defaultlanguage()
    opts = {}
    if lang:
        opts['languages'] = (lang,)
    t = gettext.translation('tortoisehg', paths.get_locale_path(),
                            fallback=True, **opts)
    if not lang:
        try:
            lang = locale.getdefaultlocale(_localeenvs)[0]
        except ValueError:  # 'unknown locale: %s'
            lang = None
    language = lang

setlanguage()

def availablelanguages() -> List[str]:
    """List up language code of which message catalog is available"""
    basedir = paths.get_locale_path()
    def mopath(lang):
        return os.path.join(basedir, lang, 'LC_MESSAGES', 'tortoisehg.mo')
    if os.path.exists(basedir): # locale/ is an install option
        langs = [e for e in os.listdir(basedir) if os.path.exists(mopath(e))]
    else:
        langs = []
    langs.append('en')  # means null translation
    return sorted(langs)

def _(message: str, context: str = '') -> str:
    if context:
        sep = '\004'
        tmsg = t.gettext(context + sep + message)
        if sep not in tmsg:
            return tmsg
    return t.gettext(message)

def ngettext(singular: str, plural: str, n: int) -> str:
    return t.ngettext(singular, plural, n)

def agettext(message: str, context: str = '') -> bytes:
    """Translate message and convert to local encoding
    such as 'ascii' before being returned.

    Only use this if you need to output translated messages
    to command-line interface (ie: Windows Command Prompt).
    """
    try:
        from tortoisehg.util import hglib
        u = _(message, context)
        return hglib.fromunicode(u)
    except (LookupError, UnicodeEncodeError):
        return pycompat.sysbytes(message)

class keepgettext:
    def _(self, message: str, context: str = '') -> Dict[str, str]:
        return {'id': message, 'str': _(message, context)}
