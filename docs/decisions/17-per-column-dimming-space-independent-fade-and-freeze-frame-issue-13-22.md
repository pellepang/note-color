# Per-column dimming (`Space`-independent fade) and freeze-frame (issue #13/#22/#23)

Both landed directly against the shipped app (not left in a throwaway
prototype) based on a finished reference implementation the user already
iterated to convergence in `prototype_notehead_toggle.py` on
`prototype/notehead-toggle-in-context` (throwaway, never merged) —
porting that algorithm into `terminal_tab_display.py`/`main.py`'s real
threaded architecture rather than re-deriving it.

**Dimming curve (#22).** Two rounds of live reaction rejected the first
cut's approach entirely: round 1 dimmed instantly (no fade) and pushed the
*newest* column toward `config.BASE_LIGHTNESS_RANGE`'s high end — borrowed
from `terminal_wheel_display.py`'s active-note *pulse* convention, which
reads as a washed-out highlight rather than tab's normal, always-fully-
saturated note color. Final shape: the newest visible column renders at
plain `config.TAB_NOTE_LIGHTNESS` (tab's ordinary note lightness, nothing
special); every older column fades *down* from there, linearly, to
`config.DIM_LIGHTNESS` over `config.FADE_COLUMNS` columns of age, held at
that floor beyond it. `FADE_COLUMNS` was doubled twice via live reaction
(4 → 8 → 16) — 16 is the shipped value, not a placeholder to revisit
without new feedback. Only lightness moves; hue and saturation are
untouched, using the exact same `note_to_hsl(..., scheme="fifths")` +
`hsl_to_rgb255()` pipeline the unfaded code already used, just with
`config.TAB_NOTE_LIGHTNESS` replaced by an age-derived value
(`terminal_tab_display._aged_lightness()`).

**Shared `DIM_LIGHTNESS` (#22).** Rather than let `tab` define its own
copy of `terminal_wheel_display.py`'s `DIM_LIGHTNESS = 0.16` (its inactive-
wedge floor), the constant was promoted to `config.DIM_LIGHTNESS` and both
modules import it from there — `terminal_wheel_display.py` keeps its own
`DIM_LIGHTNESS` name as a same-value alias for any external callers/tests
that reference it directly, but the literal `0.16` now exists exactly
once. Same rationale as the existing `NOTE_NAMES_FIFTHS`/`diatonic_step()`
shared-source fix: two independently-hand-copied constants for the same
visual convention are one accidental edit away from silently drifting
apart between the two views.

**Age computation.** Baking a note's color in at push time (as `push()`/
`push_notes()` did before this) can't work once color depends on age,
because a column's age changes every single frame as newer columns scroll
in from the right — the same column is age 0 the instant it's pushed and
age 1 the moment a newer one supersedes it. `TabDisplay.render()` now
recomputes every visible note's color fresh each call from its raw
`pitch_class` (already stored, from issue #21's notehead-restyling work)
and `age = last_index - index` within the *visible* window (0 = newest
visible column, not "newest ever pushed" — an off-screen column scrolled
out of the visible range doesn't matter). The `rgb` field `TabEntry.notes`
still carries from push time is now read only by `dump_ansi()`, which
keeps rendering at fixed brightness (untouched, per the map's standing
decision).

**Freeze-frame (#23).** `Space` is a third render-thread-local flag in
`main.py`'s `run_terminal_tab`, same pattern as `notehead_style`/
`legend_on` — no `TabDisplay`-side state. Two effects, both driven from
one boolean: (1) while frozen, `run_terminal_tab` skips
`result_queue.get_nowait()` entirely, so no new column is ever pushed and
the status line's note/freq/confidence/rms fields hold their last value —
the analysis thread keeps running and overwriting the single-slot
`result_queue` in the background the whole time, per this app's existing
drop-oldest/overwrite architecture, so there's no backlog to build up and
nothing extra to tear down or restart (unlike `M`'s `AudioCapture.
restart()`); (2) `TabDisplay.render(frozen=True)` forces every visible
column's `age` to 0 regardless of its real position, which resolves
through the exact same `_aged_lightness()` formula to full
`TAB_NOTE_LIGHTNESS` — no separate freeze-specific lightness branch was
needed, `render()` just "lies" about age. Un-freezing resumes live
immediately (the very next queue read picks up whatever's current) with
no catch-up/replay of anything that happened while frozen, matching how
`M`'s source-switch and every other toggle in this app already behaves.
