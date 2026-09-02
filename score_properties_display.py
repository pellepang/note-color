"""Score editor (issue #98): the Score properties screen -- opened by `t`
(score_properties) from the main editor view, closed by `b`
(score_properties_exit). Three independently spinnable Reels (Left/Right
switches focus, Up/Down spins), per #90's resolution: time signature
(steps through a small fixed set of common signatures), key signature
(steps `EditorScore.key_fifths` +/-1 around the circle of fifths, same
theming every other fifths-scheme view in this app already uses), tempo
(steps `EditorScore.tempo_bpm` by a fixed BPM increment, clamped to a
sane range).

Unlike the Chord builder (chord_builder_display.py), which stages edits
in its own working `BuilderState` until exit, this screen mutates the
real `EditorScore` passed in directly, field by field, as each reel
spins -- there's no "which notes should this become" staging ambiguity
for three independent scalar fields the way there is for a chord's notes,
so a separate working-copy/commit step would only add indirection.

Per this repo's test convention: the pure stepping functions below are
unit-tested (tests/test_score_properties_display.py); `render()`'s actual
screen layout is smoke-tested manually only.
"""

import shutil
import sys

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


def render(score, slot, status):
    """Smoke-tested manually only, per this module's docstring."""
    numerator, denominator = score.time_signature
    rows_spec = [
        ("time_signature", f"Time signature:  {numerator}/{denominator}"),
        ("key_signature", f"Key signature:   {key_fifths_label(score.key_fifths)}"),
        ("tempo", f"Tempo:           {score.tempo_bpm:.0f} BPM"),
    ]
    lines = [
        "Score properties",
        "Left/Right: switch reel  Up/Down: spin  b: done",
        "",
    ]
    for slot_name, text in rows_spec:
        marker = "> " if PROPERTY_SLOTS[slot] == slot_name else "  "
        line = f"{marker}{text}"
        if PROPERTY_SLOTS[slot] == slot_name:
            line = f"\033[7m{line}\033[0m"
        lines.append(line)
    lines.append("")
    lines.append(status)

    size = shutil.get_terminal_size(fallback=(80, 24))
    out = ["\033[2J"]
    for i, line in enumerate(lines, start=1):
        out.append(f"\033[{i};1H\033[K{line[:size.columns]}")
    sys.stdout.write("".join(out))
    sys.stdout.flush()
