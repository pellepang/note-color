"""Score editor (issue #98, revised by a follow-up after #98's own
hands-on session -- see docs/DECISIONS.md): the score-level properties --
time signature, key signature, tempo -- editable via `score_properties`
('t'). Originally a second, separate reel-based screen (own render loop,
its own `b`-to-exit keybind), the same shape as the Chord builder; direct
user feedback after hands-on use found leaving the main editor view for
this unwanted, so the screen is gone -- `main.run_score_editor()` now
edits these three fields inline, in the main view's own status line (see
that function's `_property_field_texts()`/`_handle_property_key()`/
`_parse_property_input()`).

This module keeps only the pure logic those inline helpers reuse:
`PROPERTY_SLOTS` (field order), `spin_time_signature()`/`spin_key_fifths()`/
`spin_tempo()` (Up/Down's per-field stepping, exactly as before -- only the
screen/mode plumbing around them changed), `key_fifths_label()` (the
status-line key-signature label), and the three fields' fixed ranges/step
sizes (`TIME_SIGNATURE_OPTIONS`, `KEY_FIFTHS_MIN`/`MAX`,
`TEMPO_MIN_BPM`/`MAX_BPM`/`STEP_BPM`).

Per this repo's test convention: every function below is pure and
unit-tested (tests/test_score_properties_display.py).
"""

PROPERTY_SLOTS = ["time_signature", "key_signature", "tempo"]

# A small fixed set of common signatures (per #98's spec's own suggested
# list) -- not free-form N/D entry, matching the "reel steps through a
# fixed set" mechanic the other two properties reels use.
TIME_SIGNATURE_OPTIONS = [(4, 4), (3, 4), (2, 4), (6, 8), (3, 8), (5, 4), (7, 8)]

# Matches music21.key.KeySignature's own valid range, which
# score_editor_state.save_score() writes directly.
KEY_FIFTHS_MIN, KEY_FIFTHS_MAX = -7, 7

TEMPO_MIN_BPM, TEMPO_MAX_BPM = 20.0, 300.0
TEMPO_STEP_BPM = 5.0


def move_slot(slot, delta):
    return (slot + delta) % len(PROPERTY_SLOTS)


def spin_time_signature(current, delta):
    """`current`: an (numerator, denominator) tuple, snapped to the
    nearest TIME_SIGNATURE_OPTIONS entry first (index 0 if it isn't in
    the fixed set at all -- e.g. a loaded score's own unusual signature)
    so there's always a sane starting position to step from."""
    try:
        idx = TIME_SIGNATURE_OPTIONS.index(tuple(current))
    except ValueError:
        idx = 0
    return TIME_SIGNATURE_OPTIONS[(idx + delta) % len(TIME_SIGNATURE_OPTIONS)]


def spin_key_fifths(current, delta):
    """+/-1 around the circle of fifths, clamped (not wrapped) at
    +/-7."""
    return max(KEY_FIFTHS_MIN, min(KEY_FIFTHS_MAX, current + delta))


def spin_tempo(current, delta):
    """+/- TEMPO_STEP_BPM, clamped into [TEMPO_MIN_BPM, TEMPO_MAX_BPM] --
    a bounded real-world quantity, same clamp-not-wrap convention
    settings_display.parse_numeric_input() already uses for
    rhythm_reanalysis_window_seconds/tab_scrollback_seconds."""
    return max(TEMPO_MIN_BPM, min(TEMPO_MAX_BPM, current + delta * TEMPO_STEP_BPM))


def key_fifths_label(fifths):
    """Human-readable circle-of-fifths label, e.g. '2 sharps', '3 flats',
    'no sharps/flats' at 0."""
    if fifths == 0:
        return "no sharps/flats"
    if fifths > 0:
        return f"{fifths} sharp{'s' if fifths != 1 else ''}"
    return f"{-fifths} flat{'s' if fifths != -1 else ''}"
