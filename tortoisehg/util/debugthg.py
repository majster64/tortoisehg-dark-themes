# debugthg.py - debugging library for TortoiseHg shell extensions
#
# Copyright 2008 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

debugging = ''
try:
    from mercurial.windows import winreg
    try:
        # pytype: disable=module-attr
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\TortoiseHg", 0,
                            winreg.KEY_ALL_ACCESS) as hkey:
            val = winreg.QueryValueEx(hkey, 'OverlayDebug')[0]
            if val in ('1', 'True'):
                debugging += 'O'
            val = winreg.QueryValueEx(hkey, 'ContextMenuDebug')[0]
        # pytype: enable=module-attr

        if val in ('1', 'True'):
            debugging += 'M'
        if debugging:
            import win32traceutil  # pytype: disable=import-error
    except OSError:
        pass
except (AttributeError, ImportError):    # AttributeError for winreg.* on Unix
    import os
    debugging = os.environ.get("DEBUG_THG", "")
    if debugging.lower() in ("1", "true"):
        debugging = True

def debugf_No(str, args=None, level=''):
    pass

if debugging:
    def debug(level=''):
        return debugging == True or level in debugging
    def debugf(str, args=None, level=''):
        if not debug(level):
            return
        if args:
            print(str % args)
        elif debug('e') and isinstance(str, BaseException):
            import traceback
            traceback.print_exc()
        else:
            print(str)
else:
    def debug(level=''):
        return False
    debugf = debugf_No
