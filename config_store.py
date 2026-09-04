"""Centralized config/theming store (issue #41): an additive TOML overlay
over config.py's defaults, loaded from
`$XDG_CONFIG_HOME/note-color/config.toml` (falling back to
`~/.config/note-color/config.toml`). An absent, empty, or malformed file
reproduces today's exact behavior -- config.py stays the default/fallback
source of truth; this file only overrides keys a user actually set.

Hot-reload for keybind remaps and per-note color overrides: every accessor
re-stats the file and only re-parses it if its mtime changed -- the same
"cheap, checked every hop" spirit as the existing P/M/N/L hotkeys' plain
attribute-access state, no watcher thread or explicit reload call needed.

Schema (all tables optional):

    [keybinds]
    source_toggle = "m"
    chord_mode_toggle = "p"
    notehead_style_toggle = "n"
    legend_toggle = "l"
    freeze_toggle = " "
    # Score editor (issue #98, `virtualnote edit <path>`) -- thirteen more
    # remappable actions across the main editor view and the Chord builder
    # screen; see config.DEFAULT_KEYBINDS for the full set/defaults and
    # score_editor_display.py's module docstring for what each does.
    # undo ("u") / redo ("U") are matched case-sensitively by
    # main.run_score_editor() (unlike every other keybind here, matched
    # case-insensitively) specifically so the two share a letter but stay
    # two distinct actions -- see docs/DECISIONS.md. transpose_up/
    # transpose_down and score_properties_exit are deliberately *not*
    # here -- a post-#98 hands-on-feedback follow-up made transpose a
    # hardcoded Shift+Up/Shift+Down (not remappable, same tier as Left/
    # Right/Up/Down/Enter) and retired the separate Score properties
    # screen score_properties_exit used to close (folded into an inline
    # header editor within the main view instead) -- see
    # docs/DECISIONS.md.

    [colors]
    # Note name (sharp or flat spelling both accepted on read; write-back
    # always uses sharp spelling) -> hue override in degrees, 0-360.
    # Overrides hue only -- saturation/lightness still come from the
    # active color scheme/octave as usual, same as every other note.
    C = 200
    "F#" = 45

    [preferences]
    # Free-form booleans/numbers/strings for quality-of-life settings.
    # menu_perf_mode has no dedicated editor screen (hand-edit only); the
    # numeric fields below are editable live from the Settings
    # screen's generic NUMERIC_FIELDS (issue #43 follow-up), same
    # preference()/set_preference() path, no bespoke accessor needed:
    menu_perf_mode = "auto"
    # "auto" (default) / "full" / "perf" -- issue #51's menu-donut
    # render-mode override; see menu_display._resolve_perf_mode().
    rhythm_reanalysis_window_seconds = 60.0
    # How many seconds of recent audio/data the tab view's 'R' offline-
    # style rhythm re-analysis reaches back over; see
    # config.RHYTHM_REANALYSIS_WINDOW_SECONDS.
    tab_scrollback_seconds = 300.0
    # How far back the tab view's freeze-mode Left/Right scrollback can
    # browse; see config.TAB_SCROLLBACK_SECONDS.
    polyphony_standalone = 40
    polyphony_with_detection = 24
    # Sound-engine hard voice caps (map #99, decision #105) -- two
    # separate budgets because prototype #100 measured the safe figure
    # differing ~2x depending on whether this app's live detection is
    # running in the same process; see config.POLYPHONY_STANDALONE /
    # config.POLYPHONY_WITH_DETECTION and sound_engine.polyphony_for().
    # The rest of the table stays reserved for future settings this
    # ticket only owns the load/persist mechanics for, not any particular
    # one's UI (e.g. #40's still-unwired global 'H' keybind-legend on/off
    # state, under key "keybind_legend_on").
"""

import os
import tomllib

import config

NOTE_NAME_TO_PITCH_CLASS = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5,
    "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10,
    "B": 11,
}
_SHARP_NAME_BY_PITCH_CLASS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "note-color", "config.toml")


class ConfigStore:
    """One instance per process (see the module-level `store` singleton
    below). Reads (`keybind`/`note_hue_override`/`preference`) are what
    every terminal view's hotkey handling and the analysis loop's color
    lookups hit every hop -- cheap by design, per the module docstring.
    `set_preference()` (writes) backs the settings-screen editor's (#43)
    generic numeric fields, alongside the earlier hand-edit-only
    menu_perf_mode preference."""

    def __init__(self, path=None):
        self.path = path or config_path()
        self._mtime = None
        self._data = {}
        self._refresh()

    def _refresh(self):
        try:
            mtime = os.stat(self.path).st_mtime
        except OSError:
            if self._mtime is not None:
                self._mtime, self._data = None, {}
            return
        if mtime == self._mtime:
            return
        try:
            with open(self.path, "rb") as f:
                self._data = tomllib.load(f)
        except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError):
            self._data = {}
        self._mtime = mtime

    def keybind(self, action):
        self._refresh()
        return self._data.get("keybinds", {}).get(action, config.DEFAULT_KEYBINDS[action])

    def note_hue_override(self, pitch_class):
        """Hue in degrees for `pitch_class` if the user's [colors] table
        overrides it (checked under both sharp and flat spelling), else
        None -- the caller (color_map.note_to_hsl) falls back to its
        normal scheme-derived hue in that case."""
        self._refresh()
        colors = self._data.get("colors", {})
        for name, pc in NOTE_NAME_TO_PITCH_CLASS.items():
            if pc != pitch_class:
                continue
            value = colors.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value) % 360
        return None

    def preference(self, name, default):
        self._refresh()
        return self._data.get("preferences", {}).get(name, default)

    def set_preference(self, name, value):
        self._refresh()
        self._data.setdefault("preferences", {})[name] = value
        self._write()

    def set_keybind(self, action, key):
        """Persists a remap for `action` (one of config.DEFAULT_KEYBINDS'
        keys) -- the interactive editor this exists for is #43's settings
        screen; `keybind()` picks the new value up on its next call via the
        usual mtime-checked hot-reload, no restart needed."""
        self._refresh()
        self._data.setdefault("keybinds", {})[action] = key
        self._write()

    def set_note_hue_override(self, pitch_class, hue):
        """Sets (or, with `hue=None`, clears) `pitch_class`'s hue override.
        Always writes back under sharp spelling (matching the module
        docstring's read/write-spelling split) and drops any existing entry
        for the same pitch class under *either* spelling first, so a user
        who originally set 'Db' in the file by hand doesn't end up with both
        'Db' and 'C#' present after an edit here."""
        self._refresh()
        colors = self._data.setdefault("colors", {})
        for name, pc in NOTE_NAME_TO_PITCH_CLASS.items():
            if pc == pitch_class:
                colors.pop(name, None)
        if hue is not None:
            colors[_SHARP_NAME_BY_PITCH_CLASS[pitch_class]] = float(hue) % 360
        self._write()

    def _write(self):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(_dump_toml(self._data))
        self._mtime = os.stat(self.path).st_mtime


def _dump_toml(data):
    lines = []
    for table_name in ("keybinds", "colors", "preferences"):
        table = data.get(table_name)
        if not table:
            continue
        lines.append(f"[{table_name}]")
        for key, value in table.items():
            lines.append(f"{_dump_key(key)} = {_dump_value(value)}")
        lines.append("")
    return "\n".join(lines)


def _dump_key(key):
    return _dump_value(str(key))


def _dump_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


store = ConfigStore()
