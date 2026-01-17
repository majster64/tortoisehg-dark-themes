# theme.py - Optional theme support for TortoiseHg
#
# Copyright (C) 2026 Peter Demcak <majster64@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.
#
# Default theme behavior is unchanged.
# Non-default themes provide opt-in color overrides only.
#
# Color formats supported when loading themes from .ini configuration.
# These formats apply only to user-defined themes.
#
# Supported formats:
#   - #RRGGBB
#   - rgb(r, g, b)
#
# Built-in themes define colors using QColor objects directly.
#
# Theme changes require application restart.

import re
from typing import Optional

from PyQt5.QtCore import QSettings
from PyQt5.QtGui import QColor

from tortoisehg.util import hglib
from mercurial import pycompat


# ----------------------------------------------------------------------
# Built-in themes (single source of truth).
# The first theme is the base and defines all required color keys.
# Other themes are partial overlays on top of it.
# ----------------------------------------------------------------------

BUILTIN_THEMES = {

    'dark': {
        'colors': {

            # --- Core UI and text ---
            'background': QColor('#1E1E1E'),
            'backgroundLighter': QColor('#252525'),
            'text': QColor('#A0AA82'),
            'text_disabled': QColor('#787878'),
            'text_margin': QColor('#96966E'),
            'text_author': QColor('#999999'),
            'text_description': QColor('#A0AA82'),     # for revision Description
            'text_selection': QColor('#d4d4d4'),
            'selection_background': QColor('#264F78'), # for Author/Age/Tags/Phase
            'selection_text': QColor('#d4d4d4'),
            'caret_foreground': QColor('#dcdcdc'),

            # --- Diff and file status ---
            'diff_text': QColor('#A0AA82'),
            'diff_start': QColor('#9B2991'),
            'diff_added': QColor('#58B62D'),
            'diff_removed': QColor('#C23A28'),
            'diff_excluded': QColor('#2C2C2C'),
            'file_modified': QColor('#9B2991'),
            'file_added': QColor('#58B62D'),
            'file_removed': QColor('#C23A28'),
            'file_deleted': QColor('#C23A28'),
            'file_missing': QColor('#C23A28'),
            'file_unknown': QColor('#6392ac'),
            'file_ignored': QColor('#96966E'),
            'file_clean': QColor('#A0AA82'),

            # --- Controls and UI feedback ---
            'control_background': QColor('#2b2b2b'),
            'control_hover': QColor('#656565'),
            'control_pressed': QColor('#5e81ac'),
            'control_border': QColor('#3c3c3c'),
            'control_text': QColor('#d4d4d4'),
            'header_background': QColor('#252526'),
            'header_text': QColor('#DCC896'),
            'ui_error': QColor('#3C2828'),
            'ui_warning': QColor('#373723'),
            'ui_control': QColor('#806464'),
            'ui_info': QColor('#6392ac'),
            'error_text': QColor('#f48771'),
            'warning_text': QColor('#C23A28'),
            'success_text': QColor('#769e76'),
            'success_background': QColor('#2f4f2f'),
            'error_background': QColor('#4f2f2f'),
            'warning_background': QColor('#1E1E1E'),

            # --- Special and window elements ---
            'chip_text': QColor('#dddbdb'),
            'chip_branch_background': QColor('#3c723c'),
            'chip_tag_background': QColor('#8a7b29'),
            'chip_bookmark_background': QColor('#68683d'),
            'chip_curbookmark_background': QColor('#7c6627'),
            'chip_topic_background': QColor('#25794f'),
            'brace_match_bg': QColor('#50501E'),
            'brace_match_fg': QColor('#F0F0B4'),
            'brace_bad_bg': QColor('#3C1414'),
            'brace_bad_fg': QColor('#FF7878'),
            'chunks_vertical_line': QColor('#76467C'), # shelve tool / chunk separator
            'config_scrollbar': QColor('#4c566a'),     # mercurial.ini editor
            'titlebar_background': QColor('#252526'), # Windows 11 title bar
            'titlebar_text': QColor('#d4d4d4'),
        },
    },

    'dark_vscode': {
        'colors': {

            # --- Core UI and text ---
            'background': QColor('#1F1F1F'),
            'backgroundLighter': QColor('#181818'),
            'text': QColor('#CCCCCC'),
            'text_disabled': QColor('#DCDCAA'),
            'text_margin': QColor('#6E7681'),
            'text_author': QColor('#9D9D9D'),
            'text_description': QColor('#DCDCAA'),
            'text_selection': QColor('#CCCCCC'),
            'selection_background': QColor('#2B445F'),
            'selection_text': QColor('#d4d4d4'),
            'caret_foreground': QColor('#dcdcdc'),

            # --- Diff and file status ---
            'diff_text': QColor('#CCCCCC'),
            'diff_start': QColor('#BB2BAF'),
            'diff_removed': QColor('#F85149'),
            'diff_added': QColor('#4EC9B0'),
            'diff_excluded': QColor('#2C2C2C'),
            'file_modified': QColor('#BB2BAF'),
            'file_added': QColor('#4EC9B0'),
            'file_removed': QColor('#F85149'),
            'file_deleted': QColor('#F85149'),
            'file_missing': QColor('#F85149'),
            'file_unknown': QColor('#9CDCFE'),
            'file_ignored': QColor('#6E7681'),
            'file_clean': QColor('#CCCCCC'),

            # --- Controls and UI feedback ---
            'control_background': QColor('#181818'),
            'control_hover': QColor('#454545'),
            'control_pressed': QColor('#666666'),
            'control_border': QColor('#3c3c3c'),
            'control_text': QColor('#d4d4d4'),
            'header_text': QColor('#DCDCAA'),
            'header_background': QColor('#252526'),
            'ui_error': QColor('#3C2828'),
            'ui_warning': QColor('#373723'),
            'ui_control': QColor('#806464'),
            'error_text': QColor('#f48771'),
            'warning_text': QColor('#C23A28'),
            'success_text': QColor('#9ecb9e'),
            'success_background': QColor('#2f4f2f'),

            # --- Special and window elements ---
            'chip_text': QColor("#dddbdb"),
            'chip_branch_background': QColor("#3c723c"),
            'chip_tag_background': QColor("#8a7b29"),
            'chip_bookmark_background': QColor("#68683d"),
            'chip_curbookmark_background': QColor("#7c6627"),
            'chip_topic_background': QColor("#25794f"),
            'brace_match_bg': QColor('#50501E'),
            'brace_match_fg': QColor('#F1D70B'),
            'brace_bad_bg': QColor('#3C1414'),
            'brace_bad_fg': QColor('#F85149'),
            'chunks_vertical_line': QColor('#AC7ED7'),
            'config_scrollbar': QColor('#4c566a'),
            'titlebar_background': QColor('#252526'),
            'titlebar_text': QColor('#d4d4d4'),
        },
    },

    'dark_dracula': {
        'colors': {

            # --- Core UI and text ---
            'background': QColor('#282A36'),
            'backgroundLighter': QColor('#343746'),
            'text': QColor('#F8F8F2'),
            'text_disabled': QColor('#6272A4'),
            'text_margin': QColor('#6272A4'),
            'text_author': QColor('#6272A4'),
            'text_description': QColor('#F8F8F2'),
            'text_selection': QColor('#F8F8F2'),
            'selection_background': QColor('#44475A'),
            'selection_text': QColor('#F8F8F2'),
            'caret_foreground': QColor('#F8F8F2'),

            # --- Diff and file status ---
            'diff_text': QColor('#F8F8F2'),
            'diff_start': QColor('#BD93F9'),
            'diff_added': QColor('#50FA7B'),
            'diff_removed': QColor('#FF5555'),
            'diff_excluded': QColor('#2C2C2C'),
            'file_modified': QColor('#BD93F9'),
            'file_added': QColor('#50FA7B'),
            'file_removed': QColor('#FF5555'),
            'file_deleted': QColor('#FF5555'),
            'file_missing': QColor('#FF5555'),
            'file_unknown': QColor('#8BE9FD'),
            'file_ignored': QColor('#6272A4'),
            'file_clean': QColor('#F8F8F2'),

            # --- Controls and UI feedback ---
            'control_background': QColor('#343746'),
            'control_hover': QColor('#44475A'),
            'control_pressed': QColor('#6272A4'),
            'control_border': QColor('#44475A'),
            'control_text': QColor('#F8F8F2'),
            'header_background': QColor('#343746'),
            'header_text': QColor('#F8F8F2'),
            'ui_error': QColor('#3C2828'),
            'ui_warning': QColor('#373723'),
            'ui_control': QColor('#806464'),
            'error_text': QColor('#FF5555'),
            'warning_text': QColor('#FFB86C'),
            'success_text': QColor('#50FA7B'),
            'success_background': QColor('#2f4f2f'),

            # --- Special and window elements ---
            'chip_text': QColor('#E6E6D8'),
            'chip_tag_background': QColor("#9C7521"),
            'chip_bookmark_background': QColor('#50FA7B'),
            'chip_curbookmark_background': QColor('#BD93F9'),
            'chip_topic_background': QColor('#8BE9FD'),
            'brace_match_bg': QColor('#44475A'),
            'brace_match_fg': QColor('#F1FA8C'),
            'brace_bad_bg': QColor('#3C1414'),
            'brace_bad_fg': QColor('#FF5555'),
            'chunks_vertical_line': QColor('#6272A4'),
            'config_scrollbar': QColor('#4c566a'),
            'titlebar_background': QColor('#282A36'),
            'titlebar_text': QColor('#F8F8F2'),
        },
    },

    'dark_nord': {
        'colors': {

            # --- Core UI and text ---
            'background': QColor('#2E3440'),
            'backgroundLighter': QColor('#3B4252'),
            'text': QColor('#D8DEE9'),
            'text_disabled': QColor('#616E88'),
            'text_margin': QColor('#4C566A'),
            'text_author': QColor('#4C566A'),
            'text_description': QColor('#D8DEE9'),
            'text_selection': QColor('#D8DEE9'),
            'selection_background': QColor('#434C5E'),
            'selection_text': QColor('#ECEFF4'),
            'caret_foreground': QColor('#ECEFF4'),

            # --- Diff and file status ---
            'diff_text': QColor('#D8DEE9'),
            'diff_start': QColor('#81A1C1'),
            'diff_added': QColor('#A3BE8C'),
            'diff_removed': QColor('#BF616A'),
            'diff_excluded': QColor('#2C2C2C'),
            'file_modified': QColor('#81A1C1'),
            'file_added': QColor('#A3BE8C'),
            'file_removed': QColor('#BF616A'),
            'file_deleted': QColor('#BF616A'),
            'file_missing': QColor('#BF616A'),
            'file_unknown': QColor('#88C0D0'),
            'file_ignored': QColor('#4C566A'),
            'file_clean': QColor('#D8DEE9'),

            # --- Controls and UI feedback ---
            'control_background': QColor('#3B4252'),
            'control_hover': QColor('#4C566A'),
            'control_pressed': QColor('#5E81AC'),
            'control_border': QColor('#4C566A'),
            'control_text': QColor('#ECEFF4'),
            'header_background': QColor('#3B4252'),
            'header_text': QColor('#ECEFF4'),
            'ui_error': QColor('#3C2828'),
            'ui_warning': QColor('#373723'),
            'ui_control': QColor('#806464'),
            'error_text': QColor('#BF616A'),
            'warning_text': QColor('#EBCB8B'),
            'success_text': QColor('#A3BE8C'),
            'success_background': QColor('#2f4f2f'),

            # --- Special and window elements ---
            'chip_text': QColor('#EBDBB2'),
            'chip_branch_background': QColor("#415080"),
            'chip_tag_background': QColor("#725E38"),
            'chip_bookmark_background': QColor('#A3BE8C'),
            'chip_curbookmark_background': QColor('#88C0D0'),
            'chip_topic_background': QColor('#B48EAD'),
            'brace_match_bg': QColor('#434C5E'),
            'brace_match_fg': QColor('#EBCB8B'),
            'brace_bad_bg': QColor('#3C1414'),
            'brace_bad_fg': QColor('#BF616A'),
            'chunks_vertical_line': QColor('#4C566A'),
            'config_scrollbar': QColor('#4c566a'),
            'titlebar_background': QColor('#2E3440'),
            'titlebar_text': QColor('#ECEFF4'),
        },
    },

    'dark_gruvbox': {
        'colors': {

            # --- Core UI and text ---
            'background': QColor('#282828'),
            'backgroundLighter': QColor('#32302F'),
            'text': QColor('#EBDBB2'),
            'text_disabled': QColor('#7C6F64'),
            'text_margin': QColor('#928374'),
            'text_author': QColor('#928374'),
            'text_description': QColor('#EBDBB2'),
            'text_selection': QColor('#EBDBB2'),
            'selection_background': QColor('#3C3836'),
            'selection_text': QColor('#EBDBB2'),
            'caret_foreground': QColor('#EBDBB2'),

            # --- Diff and file status ---
            'diff_text': QColor('#EBDBB2'),
            'diff_start': QColor('#D3869B'),
            'diff_added': QColor('#B8BB26'),
            'diff_removed': QColor('#FB4934'),
            'diff_excluded': QColor('#2C2C2C'),
            'file_modified': QColor('#D3869B'),
            'file_added': QColor('#B8BB26'),
            'file_removed': QColor('#FB4934'),
            'file_deleted': QColor('#FB4934'),
            'file_missing': QColor('#FB4934'),
            'file_unknown': QColor('#83A598'),
            'file_ignored': QColor('#928374'),
            'file_clean': QColor('#EBDBB2'),

            # --- Controls and UI feedback ---
            'control_background': QColor('#32302F'),
            'control_hover': QColor('#3C3836'),
            'control_pressed': QColor('#504945'),
            'control_border': QColor('#504945'),
            'control_text': QColor('#EBDBB2'),
            'header_background': QColor('#32302F'),
            'header_text': QColor('#EBDBB2'),
            'ui_error': QColor('#3C2828'),
            'ui_warning': QColor('#373723'),
            'ui_control': QColor('#806464'),
            'error_text': QColor('#FB4934'),
            'warning_text': QColor('#FABD2F'),
            'success_text': QColor('#B8BB26'),
            'success_background': QColor('#2f4f2f'),

            # --- Special and window elements ---
            'chip_text': QColor('#EBDBB2'),
            'chip_branch_background': QColor('#665C30'),
            'chip_tag_background': QColor("#B19038"),
            'chip_bookmark_background': QColor('#B8BB26'),
            'chip_curbookmark_background': QColor('#D3869B'),
            'chip_topic_background': QColor('#8EC07C'),
            'brace_match_bg': QColor('#3C3836'),
            'brace_match_fg': QColor('#FABD2F'),
            'brace_bad_bg': QColor('#3C1414'),
            'brace_bad_fg': QColor('#FB4934'),
            'chunks_vertical_line': QColor('#928374'),
            'config_scrollbar': QColor('#4c566a'),
            'titlebar_background': QColor('#282828'),
            'titlebar_text': QColor('#EBDBB2'),
        },
    },

    'dark_onedark': {
        'colors': {

            # --- Core UI and text ---
            'background': QColor('#282C34'),
            'backgroundLighter': QColor('#2C313C'),
            'text': QColor('#ABB2BF'),
            'text_disabled': QColor('#5C6370'),
            'text_margin': QColor('#5C6370'),
            'text_author': QColor('#5C6370'),
            'text_description': QColor('#ABB2BF'),
            'text_selection': QColor('#ABB2BF'),
            'selection_background': QColor('#3E4451'),
            'selection_text': QColor('#ABB2BF'),
            'caret_foreground': QColor('#ABB2BF'),

            # --- Diff and file status ---
            'diff_text': QColor('#ABB2BF'),
            'diff_start': QColor('#C678DD'),
            'diff_added': QColor('#98C379'),
            'diff_removed': QColor('#E06C75'),
            'diff_excluded': QColor('#2C2C2C'),
            'file_modified': QColor('#C678DD'),
            'file_added': QColor('#98C379'),
            'file_removed': QColor('#E06C75'),
            'file_deleted': QColor('#E06C75'),
            'file_missing': QColor('#E06C75'),
            'file_unknown': QColor('#56B6C2'),
            'file_ignored': QColor('#5C6370'),
            'file_clean': QColor('#ABB2BF'),

            # --- Controls and UI feedback ---
            'control_background': QColor('#2C313C'),
            'control_hover': QColor('#3E4451'),
            'control_pressed': QColor('#528BFF'),
            'control_border': QColor('#3E4451'),
            'control_text': QColor('#ABB2BF'),
            'header_background': QColor('#2C313C'),
            'header_text': QColor('#ABB2BF'),
            'ui_error': QColor('#3C2828'),
            'ui_warning': QColor('#373723'),
            'ui_control': QColor('#806464'),
            'error_text': QColor('#E06C75'),
            'warning_text': QColor('#D19A66'),
            'success_text': QColor('#98C379'),
            'success_background': QColor('#2f4f2f'),

            # --- Special and window elements ---
            'chip_text': QColor('#E5E9F0'),
            'chip_branch_background': QColor('#5A4E2D'),
            'chip_tag_background': QColor("#9B7450"),
            'chip_bookmark_background': QColor('#98C379'),
            'chip_curbookmark_background': QColor('#C678DD'),
            'chip_topic_background': QColor('#56B6C2'),
            'brace_match_bg': QColor('#3E4451'),
            'brace_match_fg': QColor('#E5C07B'),
            'brace_bad_bg': QColor('#3C1414'),
            'brace_bad_fg': QColor('#E06C75'),
            'chunks_vertical_line': QColor('#5C6370'),
            'config_scrollbar': QColor('#4c566a'),
            'titlebar_background': QColor('#282C34'),
            'titlebar_text': QColor('#ABB2BF'),
        },
    },
}



# ----------------------------------------------------------------------
# Derived constants
# ----------------------------------------------------------------------

THEME_KEYS = tuple(
    next(iter(BUILTIN_THEMES.values()))['colors'].keys()
)

_THEME_NAME_RE = re.compile(r'^[a-z0-9_]+$')


# ----------------------------------------------------------------------
# ThemeColors container
# ----------------------------------------------------------------------

class ThemeColors:
    """
    Lightweight container for optional theme color overrides.
    """
    __slots__ = ('enabled', 'saturation') + THEME_KEYS

    def __init__(self, enabled: bool = False):
        self.enabled = enabled
        self.saturation = 1.0

        for key in THEME_KEYS:
            setattr(self, key, None)


# ----------------------------------------------------------------------
# Color parsing (no alpha support)
# ----------------------------------------------------------------------

def _parse_color(value: str) -> Optional[QColor]:
    if not value:
        return None

    v = value.strip().lower()

    # Hex: #RRGGBB only
    if v.startswith('#'):
        if len(v) != 7:
            return None
        c = QColor(v)
        return c if c.isValid() else None

    # rgb(r, g, b)
    if v.startswith('rgb(') and v.endswith(')'):
        try:
            parts = [int(x.strip()) for x in v[4:-1].split(',')]
        except ValueError:
            return None

        if len(parts) != 3:
            return None

        if not all(0 <= x <= 255 for x in parts):
            return None

        return QColor(*parts)

    return None


# ----------------------------------------------------------------------
# Public helpers
# ----------------------------------------------------------------------

def available_themes():
    settings = QSettings()
    themes = set(BUILTIN_THEMES.keys())

    for group in settings.childGroups():
        if not group.startswith('theme.'):
            continue

        name = group[6:].lower()

        if len(name) < 2:
            continue

        if not _THEME_NAME_RE.match(name):
            continue

        themes.add(name)

    return sorted(themes)


# ----------------------------------------------------------------------
# Theme loading
# ----------------------------------------------------------------------

def load_theme_colors() -> ThemeColors:
    ui = hglib.loadui()

    name = pycompat.sysstr(
        ui.config(b'ui', b'theme', b'default')
    ).lower()

    # Special value: disable theming
    if name == 'default':
        return ThemeColors(enabled=False)

    # Always start from the first built-in theme as base
    base = next(iter(BUILTIN_THEMES.values()))

    # First theme must define a complete color set.
    # If it does not, disable theming to avoid runtime errors.
    if 'colors' not in base or not base['colors']:
        return ThemeColors(enabled=False)
    
    overlay = BUILTIN_THEMES.get(name)

    theme = ThemeColors(enabled=True)

    # Base saturation
    theme.saturation = float(base.get('saturation', 1.0))

    # Overlay saturation if present
    if overlay and 'saturation' in overlay:
        theme.saturation = float(overlay.get('saturation', theme.saturation))

    # Start with full dark palette
    colors = base['colors'].copy()

    # Overlay selected theme colors (partial allowed)
    if overlay:
        colors.update(overlay.get('colors', {}))

    # Load overrides from .ini
    section = b'theme.' + pycompat.sysbytes(name)
    for k, v in (ui.configitems(section) or []):
        key = pycompat.sysstr(k)
        val = pycompat.sysstr(v)

        if key == 'saturation':
            try:
                s = float(val)
            except ValueError:
                continue

            if 0.0 <= s <= 1.0:
                theme.saturation = s
            continue

        if key not in THEME_KEYS:
            continue

        color = _parse_color(val)
        if color:
            colors[key] = color

    for key, color in colors.items():
        setattr(theme, key, color)

    return theme


# ----------------------------------------------------------------------
# Singleton access
# ----------------------------------------------------------------------

_THEME_INSTANCE = None

def get_theme() -> ThemeColors:
    global _THEME_INSTANCE
    if _THEME_INSTANCE is None:
        _THEME_INSTANCE = load_theme_colors()
    return _THEME_INSTANCE


# This will be called on module import
THEME = get_theme()
