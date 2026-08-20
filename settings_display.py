"""virtualnote's interactive Settings screen (issue #43): the editor for
config_store.py's (#41) keybind remaps and per-note color overrides,
reachable as its own menu entry (menu_display.MENU_ITEMS) at the same tier
as any tool -- shell.py special-cases the "settings" selection instead of
sending it through main.run_session(), since it never touches audio.

Menu chrome for this screen only, per #37/#39's grilling: `blessed` for
field-to-field navigation and "press a key to capture this remap" input
handling -- a small, pure-Python helper over the same ANSI/terminfo
primitives this project already writes by hand elsewhere, not a wholesale
framework adoption. Every other screen in the unified shell stays raw ANSI.

Edits take effect through config_store.ConfigStore's existing mtime-checked
hot-reload -- set_keybind()/set_note_hue_override() write straight to the
TOML file, and every other tool's next store.keybind()/note_hue_override()
call picks the change up with no restart, same live-edit UX as M/P today.

Per this repo's test convention, only the pure field-navigation/formatting/
parsing logic below is unit-tested (see tests/test_settings_display.py);
the interactive blessed-driven run loop (run_settings_screen and its
render/edit helpers) is smoke-tested manually, same as every run_terminal_*
loop in main.py.
"""

import config
from color_map import NOTE_NAMES, note_to_hsl, hsl_to_rgb255
from config_store import store

KEYBIND_ACTIONS = [
    "source_toggle",
    "chord_mode_toggle",
    "notehead_style_toggle",
    "legend_toggle",
    "freeze_toggle",
]

_KEYBIND_LABELS = {
    "source_toggle": "Audio source (mic/loopback)",
    "chord_mode_toggle": "Chord mode",
    "notehead_style_toggle": "Notehead style (tab view)",
    "legend_toggle": "Staff legend (tab view)",
    "freeze_toggle": "Freeze view (tab view)",
}

# One row per keybind action, then one row per note (sharp spelling,
# pitch-class order 0..11, matching color_map.NOTE_NAMES) -- fixed at
# import time since neither list's length changes at runtime.
FIELDS = [("keybind", action) for action in KEYBIND_ACTIONS] + \
    [("color", pitch_class) for pitch_class in range(len(NOTE_NAMES))]


def _format_key(key):
    """Same 'space' spelling as main.py's _key_hint -- a literal space
    character is invisible in a rendered value column."""
    return "space" if key == " " else key


def keybind_label(action):
    return _KEYBIND_LABELS[action]


def keybind_value(action):
    return _format_key(store.keybind(action))


def color_label(pitch_class):
    return NOTE_NAMES[pitch_class]


def color_value(pitch_class):
    """'default' or 'NNN°' -- mirrors the config file's own degrees unit."""
    hue = store.note_hue_override(pitch_class)
    return "default" if hue is None else f"{hue:.0f}°"


def color_swatch_rgb(pitch_class):
    """RGB the field's preview dot should render in -- octave fixed at 4
    (this screen has no octave concept; the fixed mid-range value just
    picks a representative lightness) so the preview uses exactly the same
    hue/sat/lightness math every real view does, override included."""
    hue, sat, light = note_to_hsl(
        pitch_class, 4, scheme=config.DEFAULT_COLOR_SCHEME,
        hue_override=store.note_hue_override(pitch_class),
    )
    return hsl_to_rgb255(hue, sat, light)


def field_label(index):
    kind, value = FIELDS[index]
    return keybind_label(value) if kind == "keybind" else color_label(value)


def field_value(index):
    kind, value = FIELDS[index]
    return keybind_value(value) if kind == "keybind" else color_value(value)


def move(selected, delta):
    """Wraps both ends, same convention as menu_display.MenuDisplay.move."""
    return (selected + delta) % len(FIELDS)


# '|' (back-to-menu) and 'h'/'H' (help-legend toggle) are global keys every
# run_terminal_* loop checks unconditionally, ahead of or independent from
# any store.keybind() lookup (main.py's _handle_back_to_menu_key/
# _handle_help_legend_key) -- remapping an action onto either would make
# that key double-fire (the action, then instantly back out to the menu or
# flip the legend) every time it's pressed, not a usable remap.
_RESERVED_KEYS = {"|", "h"}


def is_valid_remap_key(ch):
    """A remap must be exactly one printable character (space included --
    that's freeze_toggle's own default), and not one of the reserved global
    keys above -- anything else (an empty read, a control character, '|',
    'h'/'H') is rejected and the capture prompt just stays open, the field
    left unchanged."""
    if not (isinstance(ch, str) and len(ch) == 1 and (ch == " " or ch.isprintable())):
        return False
    return ch.lower() not in _RESERVED_KEYS


def parse_hue_input(text):
    """Parses a color field's typed digit buffer into a hue in [0, 360), or
    None if `text` is empty (the field's "clear the override" case, same
    end state Backspace/Delete outside edit mode reaches directly). Raises
    ValueError on anything non-numeric -- the caller decides how to surface
    that (this screen just re-prompts with the buffer reset)."""
    text = text.strip()
    if text == "":
        return None
    return float(int(text) % 360)


def apply_field_edit(index, new_value):
    """Persists `new_value` for FIELDS[index] via the config store --
    `new_value` is a single remap character for a keybind row, or an
    Optional[float] hue (None clears the override) for a color row."""
    kind, value = FIELDS[index]
    if kind == "keybind":
        store.set_keybind(value, new_value)
    else:
        store.set_note_hue_override(value, new_value)


def clear_field(index):
    """Backspace/Delete outside edit mode on a color row resets it straight
    to 'default' with no digit entry needed; a no-op on keybind rows --
    there's no 'unset' state for a keybind, every action always has some
    key bound, so 'clear' isn't a meaningful action there."""
    kind, value = FIELDS[index]
    if kind == "color":
        store.set_note_hue_override(value, None)


def _highlight(term, text, is_selected):
    return term.reverse(text) if is_selected else text


def _render(term, selected, message):
    lines = [
        term.bold("note-color settings"),
        "Up/Down move  Enter edit  Backspace/Del clear (colors)  |/Esc back to menu",
        "",
        term.underline("Keybinds"),
    ]
    for i, action in enumerate(KEYBIND_ACTIONS):
        row = f"  {keybind_label(action):<32s} {keybind_value(action)}"
        lines.append(_highlight(term, row, i == selected))
    lines.append("")
    lines.append(term.underline("Note colors (hue override, degrees)"))
    for offset in range(len(NOTE_NAMES)):
        pitch_class = offset
        index = len(KEYBIND_ACTIONS) + offset
        dot = term.color_rgb(*color_swatch_rgb(pitch_class))("●") if term.does_styling else "●"
        row = f"  {dot} {color_label(pitch_class):<4s} {color_value(pitch_class)}"
        lines.append(_highlight(term, row, index == selected))
    lines.append("")
    lines.append(message)
    print(term.home + term.clear + "\r\n".join(lines))


def _capture_keybind(term, index):
    _, action = FIELDS[index]
    _render(term, index, f"Press a key to bind to '{keybind_label(action)}'... (Esc cancels)")
    key = term.inkey()
    if key.name == "KEY_ESCAPE":
        return "cancelled"
    ch = str(key)
    if not is_valid_remap_key(ch):
        return "reserved or unusable key -- remap unchanged"
    apply_field_edit(index, ch)
    return f"'{keybind_label(action)}' now bound to '{_format_key(ch)}'"


def _capture_hue(term, index):
    _, pitch_class = FIELDS[index]
    buffer = ""
    while True:
        prompt = (f"Hue for {color_label(pitch_class)} (0-360, Enter to confirm, "
                  f"Esc cancels, empty Enter clears): {buffer}")
        _render(term, index, prompt)
        key = term.inkey()
        if key.name == "KEY_ESCAPE":
            return "cancelled"
        if key.name == "KEY_ENTER" or str(key) in ("\r", "\n"):
            try:
                hue = parse_hue_input(buffer)
            except ValueError:
                buffer = ""
                continue
            apply_field_edit(index, hue)
            return f"{color_label(pitch_class)} -> {'default' if hue is None else f'{hue:.0f}°'}"
        if key.name in ("KEY_BACKSPACE", "KEY_DELETE"):
            buffer = buffer[:-1]
        elif str(key).isdigit():
            buffer += str(key)


def _edit_field(term, index):
    kind, _ = FIELDS[index]
    return _capture_keybind(term, index) if kind == "keybind" else _capture_hue(term, index)


def run_settings_screen(term=None):
    """Interactive editor: Up/Down move, Enter edits the highlighted field
    (captures the very next keypress for a keybind row; opens an inline
    digit buffer for a color row), Backspace/Delete clears a color row
    straight back to 'default', '|' or Esc returns to the menu -- the same
    always-live back-to-menu convention every other tool uses (#40), kept
    here for consistency even though this screen sits outside the
    audio-pipeline views that convention was originally defined for.
    Always returns to the menu; there's no separate quit-vs-menu distinction
    the way run_terminal_* tools have, since there's no audio session to
    tear down or preserve here."""
    import blessed
    term = term or blessed.Terminal()
    selected = 0
    message = ""

    with term.fullscreen(), term.hidden_cursor(), term.cbreak():
        while True:
            _render(term, selected, message)
            key = term.inkey()
            if str(key) == "|" or key.name == "KEY_ESCAPE":
                return
            if key.name == "KEY_UP":
                selected = move(selected, -1)
                message = ""
            elif key.name == "KEY_DOWN":
                selected = move(selected, 1)
                message = ""
            elif key.name in ("KEY_BACKSPACE", "KEY_DELETE"):
                clear_field(selected)
                message = f"{field_label(selected)} -> default"
            elif key.name == "KEY_ENTER" or str(key) in ("\r", "\n"):
                message = _edit_field(term, selected)
