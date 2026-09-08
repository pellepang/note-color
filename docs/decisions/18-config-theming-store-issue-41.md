# Config/theming store (issue #41)

Map #37 (unified tool shell) settled format/location/layering by grilling
in #39; this ticket only had to fill in the parts #39 left open: exact
schema and the inventory of what's covered beyond keybinds/colors.

**Schema, scoped down on purpose.** `[keybinds]` covers exactly the five
existing single-key terminal hotkeys (`source_toggle`/`chord_mode_toggle`/
`notehead_style_toggle`/`legend_toggle`/`freeze_toggle`) — `Up`/`Down`
sensitivity and `Ctrl+C` quit were left out of the remap surface, since
they're not single printable characters in the same sense (arrow-key
semantics, and quit-via-signal isn't really a "binding"). `[colors]`
overrides a note's *hue* only, by name (`C`, `F#`, etc., either sharp or
flat spelling on read — canonical write-back is always sharp), leaving
saturation and octave-driven lightness computed as normal; a full
HSL/RGB override was considered and rejected for v1 as scope creep beyond
what #37 actually asked for ("per-note color overrides", read as "the hue
identity of a note", not "arbitrary color replacement"). `[preferences]`
is a deliberately generic bucket for anything else (e.g. #40's future
global `H` keybind-legend on/off state, once #40 exists to read/write it)
— #41 only owns the load/persist mechanics, not any particular
preference's UI or behavior.

**Hot-reload via mtime-stat, not a file watcher.** Every accessor
(`keybind()`/`note_hue_override()`/`preference()`) calls `os.stat()` and
only re-parses the TOML if the mtime changed since last read — cheap
enough to call on every hop/frame (same design constraint that already
justified running chord mode's pipeline unconditionally every hop, and
the same "zero shared state, plain read every frame" shape as `P`/`M`/`N`/
`L`). No `inotify`/`watchdog` dependency, no background thread, no
explicit "reload" call the render loop has to remember to make.

**Absent/malformed file reproduces today's behavior exactly**, per #41's
destination text: `ConfigStore._refresh()` catches `OSError` (file
missing) and `tomllib.TOMLDecodeError`/`UnicodeDecodeError` (malformed)
alike, falling back to an empty `{}` overlay in both cases — every
accessor then falls through to `config.py`'s existing default. Verified
in `tests/test_config_store.py` (missing file, empty file, and garbage
content all resolve to unmodified defaults).

**Wired into the existing views now, not deferred to #40's shell.** #41
is independent of #40 per #37's dependency ordering, and nothing about
remapping `main.py`'s existing hotkeys or overriding `color_map.
note_to_hsl()`'s hue requires the future `virtualnote` entry point to
exist first — so `fill`/`wheel`/`tab` all read live through
`config_store.store` today, including the status-line hint text (e.g.
`src=mic (m)`), which resolves the *actual* bound key rather than a
hardcoded literal, so a remap doesn't leave a stale/wrong hotkey hint on
screen.

**Write path only serializes the shapes this app's schema needs** — three
known top-level tables, string-keyed leaves that are bool/int/float/str —
rather than pulling in a third-party TOML writer (`tomllib` is read-only;
stdlib has no `tomllib`-equivalent writer). `set_preference()` is the only
caller today, and nothing in this app invokes it yet (no settings-screen
UI exists until #43); it exists now so #43 has something to call against
without also needing to design the persistence layer at the same time.
