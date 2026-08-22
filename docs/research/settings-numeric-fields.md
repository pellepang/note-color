# Research: adding numeric settings fields to the Settings screen

## Question

How should three new generic numeric settings — a rhythm-reanalysis time
window, a quality/time-budget knob, and a `tab`-view scrollback window
length — be added to the interactive Settings screen (`settings_display.py`)
and its backing TOML overlay (`config_store.py`), following this codebase's
existing "additive, hot-reloadable, mtime-checked" config-store convention?

Findings below are cited to the actual source as of this writing
(`settings_display.py`, `config_store.py`); every numbered claim from the
originating research request was checked against the file and is confirmed
accurate except where noted.

## 1. How the existing hue digit-entry field works end to end

- `FIELDS` (`settings_display.py:48-49`) is a flat list of `(kind, value)`
  tuples built once at import: one `("keybind", action)` row per
  `KEYBIND_ACTIONS`, then one `("color", pitch_class)` row per
  `range(len(NOTE_NAMES))`.
- Display: `color_value(pitch_class)` (`settings_display.py:70-73`) reads
  `store.note_hue_override(pitch_class)` (`None` or a float degree value)
  and formats it as `"default"` or `f"{hue:.0f}°"`. `color_swatch_rgb`
  (`settings_display.py:76-85`) renders a live preview dot by calling
  `note_to_hsl(..., hue_override=store.note_hue_override(pitch_class))`.
- Input capture: `_capture_hue(term, index)` (`settings_display.py:195-216`)
  is a `while True` loop calling `term.inkey()` and building a string
  `buffer` of typed characters. `KEY_ESCAPE` cancels; Enter parses `buffer`
  via `parse_hue_input`; `KEY_BACKSPACE`/`KEY_DELETE` trims the buffer one
  character; any other digit character is appended.
- Validation/parsing: `parse_hue_input(text)` (`settings_display.py:123-132`)
  strips the input; an empty string returns `None` (the "clear the
  override" case); otherwise it returns `float(int(text) % 360)` — this
  **wraps via modulo, does not clamp**, and raises `ValueError` on
  non-numeric input, which the caller catches at
  `settings_display.py:206-210` by resetting `buffer` to `""` and
  re-prompting.
- Persistence: `apply_field_edit(index, new_value)`
  (`settings_display.py:135-143`) calls
  `store.set_note_hue_override(pitch_class, hue)`, which writes into the
  TOML `[colors]` table and calls `self._write()`
  (`config_store.py:128-142`, `_write()` at `config_store.py:144-150`) — a
  full-file rewrite.
- Clear shortcut: `clear_field(index)` (`settings_display.py:146-153`) —
  Backspace/Delete pressed *outside* edit mode (in the main navigation
  loop, `settings_display.py:252-254`) calls
  `store.set_note_hue_override(value, None)` directly, no digit entry
  needed.
- Render: `_render()` (`settings_display.py:160-179`) draws two hardcoded
  sections ("Keybinds" at line 165, "Note colors" at line 171) by
  iterating `KEYBIND_ACTIONS` (line 167) and `range(len(NOTE_NAMES))`
  (line 172) directly — **not** by iterating `FIELDS` generically. `FIELDS`
  is consulted elsewhere (`move()`'s wraparound count at
  `settings_display.py:98-100`, `field_label`/`field_value`'s lookups at
  `settings_display.py:88-95`), but `_render()` has its own parallel
  hardcoded loop structure with no shared iteration path.

**Verified accurate**, line numbers matched the claim's approximate ranges
exactly.

## 2. Is `FIELDS` / the dispatch logic already generic for a third "kind"?

No. Every consumer function branches with a strict binary `if/else` on
`kind`, not a lookup table:

- `field_label`/`field_value` (`settings_display.py:88-95`):
  `keybind_value(value) if kind == "keybind" else color_value(value)`.
- `apply_field_edit` (`settings_display.py:139-143`):
  `if kind == "keybind": store.set_keybind(...) else: store.set_note_hue_override(...)`.
- `clear_field` (`settings_display.py:151-153`):
  `if kind == "color": store.set_note_hue_override(value, None)` — no
  `else`, so a keybind row is silently a no-op.
- `_edit_field` (`settings_display.py:219-221`):
  `_capture_keybind(...) if kind == "keybind" else _capture_hue(...)`.
- `_render()` (`settings_display.py:160-179`): two separate hardcoded
  loops, doesn't consult `FIELDS` generically for rendering at all.

Adding a third kind requires widening every one of these binary branches to
a three-way branch (or refactoring to a `kind -> handlers` registry dict),
plus adding a third rendering loop/section in `_render()`. `FIELDS`'s
*shape* (a list of `(kind, value)` tuples) is extensible enough to append a
third kind's rows with no structural change, but the surrounding code is
hardcoded for exactly two kinds throughout, not written generically.

**Verified accurate.**

## 3. `config_store.py`'s generic preference path

- `preference(name, default)` (`config_store.py:110-112`) and
  `set_preference(name, value)` (`config_store.py:114-117`) are already
  fully generic:
  `self._data.get("preferences", {}).get(name, default)` /
  `self._data.setdefault("preferences", {})[name] = value; self._write()`.
  No per-field bespoke logic, no sharp/flat name mapping, no dedicated
  per-key default table the way `keybind()`/`note_hue_override()` have.
- This generic path is already load-bearing in production:
  `menu_display.py:131` reads
  `store.preference("menu_perf_mode", "auto")` directly — confirmed by
  grepping the actual call site, matching CLAUDE.md's documentation of
  `menu_display._resolve_perf_mode()`. `_dump_toml`/`_dump_value`
  (`config_store.py:153-176`) already serialize `int`/`float`/`bool`/`str`
  generically (`_dump_value` at lines 170-176 special-cases only `bool`
  vs. numeric vs. string), so numeric preferences already round-trip
  end-to-end today with zero new `config_store.py` code.
- Contrast: `keybind()`/`note_hue_override()`/`set_keybind()`/
  `set_note_hue_override()` (`config_store.py:91-93`, `95-108`, `119-126`,
  `128-142`) are field-specific wrappers — dedicated defaults from
  `config.DEFAULT_KEYBINDS`, `NOTE_NAME_TO_PITCH_CLASS` sharp/flat mapping,
  hardcoded table names — not the pattern to imitate for new numeric
  fields; they exist because color/keybind semantics don't fit the
  generic key/value shape as cleanly, not because `preference()`/
  `set_preference()` didn't exist.
- There is no existing `clear_preference()`/delete-key method on
  `ConfigStore` — confirmed, no such method appears anywhere in the file.
  "Clearing" a preference today would mean writing its default value back
  explicitly (leaves a line in the TOML) rather than removing the key
  entirely, unlike `set_note_hue_override(None)`
  (`config_store.py:136-141`, which pops any existing entry for the
  pitch class under either spelling and only re-adds one if `hue is not
  None`) which does remove the `[colors]`-table entry outright.

**Verified accurate.**

## 4. Recommendation — least new code, following the existing convention

- **`config_store.py`: zero new code required.** All three new settings
  (e.g. preference keys `"rhythm_reanalysis_window_s"`,
  `"quality_time_budget"`, `"tab_scrollback_columns"`) can be read/written
  purely through the existing generic `preference()`/`set_preference(name,
  value)` calls (`config_store.py:110-117`) — the same path
  `menu_perf_mode` already proves out in production. Optional (~5 lines,
  not required): a `clear_preference(name)` method that pops the key from
  `self._data["preferences"]` and calls `self._write()`, mirroring
  `set_note_hue_override(None)`'s delete-not-just-default semantics — only
  worth adding if "reset to default" should remove the TOML line rather
  than explicitly write the default value back.

- **`settings_display.py`: genuinely new code needed:**
  a. A `NUMERIC_FIELDS` list of small records (preference key, label, min,
     max, step, default, int-vs-float) — analogous in role to
     `KEYBIND_ACTIONS` (`settings_display.py:29-35`).
  b. Extend `FIELDS` (`settings_display.py:48-49`) to append
     `[("numeric", spec) for spec in NUMERIC_FIELDS]`.
  c. A generic `numeric_value(spec)`/`numeric_label(spec)` pair mirroring
     `color_value`/`color_label` (`settings_display.py:66-73`), backed by
     `store.preference(spec.key, spec.default)`.
  d. Widen the four binary branches (`field_label`/`field_value`
     `settings_display.py:88-95`; `apply_field_edit`
     `settings_display.py:139-143`; `clear_field`
     `settings_display.py:151-153`; `_edit_field`
     `settings_display.py:219-221`) to three-way, adding the numeric case
     — `apply_field_edit`'s numeric branch is a one-line
     `store.set_preference(spec.key, new_value)`; `clear_field`'s numeric
     branch is `store.set_preference(spec.key, spec.default)` (or the
     optional `clear_preference()` above).
  e. One genuinely new capture function, `_capture_numeric(term, index,
     spec)`, modeled directly on `_capture_hue`'s loop shape
     (`settings_display.py:195-216` — same buffer/Enter/Esc/Backspace
     mechanics) but generalized: parse via a new
     `parse_numeric_input(text, min_val, max_val, step)` that **clamps**
     into `[min, max]` rather than wrapping mod 360 the way
     `parse_hue_input` does (`settings_display.py:123-132`) — hue's
     circular wraparound is the wrong semantics for a time-window/
     quality-dial/scrollback-length field — and supports int or float per
     spec.
  f. Add a third rendering section/loop in `_render()`
     (`settings_display.py:160-179`, mirroring the existing "Keybinds"/
     "Note colors" hardcoded loops) iterating `NUMERIC_FIELDS`.

**Net assessment:** this is a small, mechanical generalization — one new
parameterized capture/parse function pair, one new per-field spec list,
and widening ~5 existing binary branches to three-way — not a
rearchitecture. The store side needs no new mechanism at all; the generic
`[preferences]` table + `preference()`/`set_preference()` this project
already built for `menu_perf_mode` is exactly the reuse point.
