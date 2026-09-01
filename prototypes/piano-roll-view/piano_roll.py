"""Prototype: piano-roll-as-text terminal renderer (Concept B,
docs/research/notation-and-feature-ideas.md).

Standalone, throwaway -- follows the same convention as
prototypes/issue-42-menu-animation/: a self-contained script runnable via
`.venv/bin/python prototypes/piano-roll-view/piano_roll.py`, imports the
real app modules it needs (config, color_map) but touches no audio/mic
code and modifies nothing in the real source tree.

What this demonstrates: one text row per chromatic pitch lane (spanning
this app's real detectable range, config.MIN_OCTAVE..config.MAX_OCTAVE-1 --
the same range note_to_hsl()'s octave-driven lightness already assumes),
time flowing left-to-right in fixed-width columns keyed to real elapsed
seconds (not a beat-accumulator guess -- see Concept B's own mockup in the
research doc), a note onset drawn as one glyph and its sustain as a run of
a second glyph, colored with this app's real per-note fifths-order HSL
coloring (color_map.note_to_hsl(..., scheme="fifths") + hsl_to_rgb255 --
the exact color math terminal_tab_display.py's _column_note_rgb() uses,
just without the age-fade term since this is a static one-shot render, not
a scrolling live view).

Input shape: a plain list of NoteEvent-compatible tuples -- same field
names/order as batch_transcribe.NoteEvent (onset_hop, onset_time,
pitch_class, octave, duration_hops, chord_name) -- defined locally here
rather than imported from batch_transcribe.py, since that module imports
librosa at module scope and this prototype has no reason to require that
dependency just to demonstrate a rendering shape.
"""

import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config
from color_map import NOTE_NAMES_FIFTHS, hsl_to_rgb255, note_to_hsl
from duration_tracker import DEFAULT_DURATION_CLASS, duration_class_for_beats

# Same field names/order as batch_transcribe.NoteEvent -- see module
# docstring for why this is a local redefinition, not an import.
NoteEvent = namedtuple(
    "NoteEvent", ["onset_hop", "onset_time", "pitch_class", "octave", "duration_hops", "chord_name"]
)

HOP_SECONDS = config.BLOCK_SIZE / config.SAMPLE_RATE  # this app's real per-hop clock

COLUMN_SECONDS = 0.1   # one rendered column = 100ms of real elapsed time
ONSET_CHAR = "●"  # ●
SUSTAIN_CHAR = "─"  # ─
EMPTY_CHAR = " "
ROW_LABEL_WIDTH = 4    # e.g. "C#4 "


def synth_melody_and_chord():
    """A short synthesized sequence sharing the shape of a real
    NoteEvent list: an ascending C-major scale (mirroring the research
    doc's own ABC mockup, "C D E F | G A B c"), a rest, then a held
    C4-E4-G4 chord -- so the render exercises both a monophonic run and a
    simultaneous multi-note (chord) moment on independent lanes, the way
    chord mode's note_stack would appear here."""
    events = []
    bpm = 120.0
    beat_seconds = 60.0 / bpm
    quarter_beats = 1.0

    scale = [
        (0, 4), (2, 4), (4, 4), (5, 4),   # C4 D4 E4 F4
        (7, 4), (9, 4), (11, 4), (0, 5),  # G4 A4 B4 C5
    ]
    t = 0.0
    for pc, octave in scale:
        duration_seconds = quarter_beats * beat_seconds
        duration_hops = round(duration_seconds / HOP_SECONDS)
        onset_hop = round(t / HOP_SECONDS)
        events.append(NoteEvent(onset_hop, t, pc, octave, duration_hops, None))
        t += duration_seconds

    t += beat_seconds  # a one-beat rest before the chord

    chord_notes = [(0, 4), (4, 4), (7, 4)]  # C4-E4-G4
    chord_duration_seconds = 4 * beat_seconds  # a held whole note
    chord_duration_hops = round(chord_duration_seconds / HOP_SECONDS)
    onset_hop = round(t / HOP_SECONDS)
    for pc, octave in chord_notes:
        events.append(NoteEvent(onset_hop, t, pc, octave, chord_duration_hops, "C"))

    return events, bpm


def _lanes():
    """Every chromatic (pitch_class, octave) lane in this app's real
    detectable range, high pitch first (top row) down to low pitch (bottom
    row) -- the conventional piano-roll vertical order. Reuses
    config.MIN_OCTAVE/config.MAX_OCTAVE directly (the same range
    note_to_hsl()'s lightness gradient assumes), not config.FMIN/FMAX's
    raw Hz bounds, since a lane is an octave+pitch-class identity, not a
    frequency -- MIN_OCTAVE..MAX_OCTAVE-1 is that identity's real usable
    span."""
    lanes = []
    for octave in range(config.MAX_OCTAVE - 1, config.MIN_OCTAVE - 1, -1):
        for pc in range(11, -1, -1):
            lanes.append((pc, octave))
    return lanes


def _lane_rgb(pc, octave):
    hue, sat, light = note_to_hsl(pc, octave, scheme="fifths")
    return hsl_to_rgb255(hue, sat, light)


def render(events, bpm, out=sys.stdout):
    lanes = _lanes()
    lane_index = {key: i for i, key in enumerate(lanes)}

    last_end = max((e.onset_time + e.duration_hops * HOP_SECONDS) for e in events) if events else 0.0
    total_seconds = last_end + 0.5
    n_cols = max(int(total_seconds / COLUMN_SECONDS) + 1, 1)

    # grid[lane_i][col] -> None | "onset" | ("sustain", rgb)
    grid = [[None] * n_cols for _ in lanes]
    rgb_by_col = [[None] * n_cols for _ in lanes]
    duration_class_by_note = []

    beat_seconds = 60.0 / bpm
    for e in events:
        key = (e.pitch_class, e.octave)
        if key not in lane_index:
            continue  # outside this app's real detectable range -- dropped, not clamped
        li = lane_index[key]
        rgb = _lane_rgb(*key)
        duration_seconds = e.duration_hops * HOP_SECONDS
        beats = duration_seconds / beat_seconds if beat_seconds else None
        dclass = duration_class_for_beats(beats) if beats else DEFAULT_DURATION_CLASS
        duration_class_by_note.append((e, dclass))

        start_col = int(round(e.onset_time / COLUMN_SECONDS))
        length_cols = max(1, int(round(duration_seconds / COLUMN_SECONDS)))
        for c in range(start_col, min(start_col + length_cols, n_cols)):
            grid[li][c] = "onset" if c == start_col else "sustain"
            rgb_by_col[li][c] = rgb

    # Only render lanes that actually carry a note, plus a bit of padding
    # above/below for legibility -- printing all ~48 chromatic lanes in
    # this app's full config.MIN_OCTAVE..MAX_OCTAVE-1 range for a 9-note
    # demo melody would be mostly blank rows; a real live view would keep
    # the full range (or a scrollable window over it), same tradeoff
    # terminal_tab_display.py's own shrink-to-terminal-height logic makes.
    active = [i for i, row in enumerate(grid) if any(row)]
    pad = 2
    lo = max(min(active) - pad, 0)
    hi = min(max(active) + pad, len(lanes) - 1)

    print(f"tempo={bpm:.0f}bpm  time=4/4  hop={HOP_SECONDS*1000:.1f}ms  "
          f"col={COLUMN_SECONDS*1000:.0f}ms  range={lanes[hi]}..{lanes[lo]}", file=out)
    print(file=out)

    for li in range(lo, hi + 1):
        pc, octave = lanes[li]
        label = f"{NOTE_NAMES_FIFTHS[pc]}{octave}".ljust(ROW_LABEL_WIDTH)
        cells = []
        for c in range(n_cols):
            state = grid[li][c]
            if state is None:
                cells.append(EMPTY_CHAR)
                continue
            r, g, b = rgb_by_col[li][c]
            glyph = ONSET_CHAR if state == "onset" else SUSTAIN_CHAR
            cells.append(f"\033[38;2;{r};{g};{b}m{glyph}\033[0m")
        print(f"{label}|{''.join(cells)}|", file=out)

    # Time axis, one tick per whole second.
    axis = [EMPTY_CHAR] * n_cols
    for sec in range(int(total_seconds) + 1):
        c = int(round(sec / COLUMN_SECONDS))
        if c < n_cols:
            label = f"{sec}s"
            for i, ch in enumerate(label):
                if c + i < n_cols:
                    axis[c + i] = ch
    print(" " * ROW_LABEL_WIDTH + " " + "".join(axis), file=out)

    print(file=out)
    print("note durations (snapped to nearest standard value, "
          "duration_tracker.duration_class_for_beats):", file=out)
    for e, dclass in duration_class_by_note:
        name = f"{NOTE_NAMES_FIFTHS[e.pitch_class]}{e.octave}"
        chord = f"  [{e.chord_name}]" if e.chord_name else ""
        print(f"  t={e.onset_time:5.2f}s  {name:<4}  {dclass}{chord}", file=out)


if __name__ == "__main__":
    events, bpm = synth_melody_and_chord()
    render(events, bpm)
