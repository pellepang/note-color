"""Prototype: MOD/XM-style step-sequencer grid terminal renderer (Concept
C, docs/research/notation-and-feature-ideas.md).

Standalone, throwaway -- same convention as
prototypes/issue-42-menu-animation/ and the sibling prototypes/
piano-roll-view/: a self-contained script runnable via
`.venv/bin/python prototypes/tracker-grid-view/tracker_grid.py`, imports
real app modules (config, color_map, duration_tracker) but touches no
audio/mic code and modifies nothing in the real source tree.

What this demonstrates: rows = fixed time steps quantized to a sixteenth-
note subdivision of the live/estimated tempo (row_seconds, itself an
integer multiple of this app's real hop clock, config.BLOCK_SIZE/
config.SAMPLE_RATE -- see _quantize_row_hops()); columns = up to
config.CHORD_MAX_NOTES (6) simultaneous voices/channels, mirroring chord
mode's own note_stack cap; each cell holds a compact note+duration token
in this app's own NOTE_NAMES_FIFTHS spelling (e.g. "C-4", "Bb4"), or "..."
(tracker convention: no cell means "still sustaining or silent", not a
literal absence marker) when nothing re-attacks that channel that row. A
distinct barline row (`|bar|`) marks measure boundaries, mirroring
terminal_tab_display.py's own separate BarlineEntry column type.

Input shape: same NoteEvent-compatible tuples as the piano-roll
prototype (onset_hop, onset_time, pitch_class, octave, duration_hops,
chord_name) -- see that module's docstring for why this is a local
redefinition rather than an import from batch_transcribe.py (which pulls
in librosa at module scope).
"""

import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config
from color_map import NOTE_NAMES_FIFTHS, hsl_to_rgb255, note_to_hsl
from duration_tracker import DEFAULT_DURATION_CLASS, duration_class_for_beats

NoteEvent = namedtuple(
    "NoteEvent", ["onset_hop", "onset_time", "pitch_class", "octave", "duration_hops", "chord_name"]
)

HOP_SECONDS = config.BLOCK_SIZE / config.SAMPLE_RATE  # this app's real per-hop clock

N_CHANNELS = config.CHORD_MAX_NOTES  # mirrors chord mode's own note_stack cap
BEATS_PER_BAR, _BEAT_UNIT = config.DEFAULT_TIME_SIGNATURE
ROW_SUBDIVISION = 4  # rows per beat -- sixteenth-note grid, a tracker convention default

# Compact duration codes for the cell's trailing token -- same standard
# note-value vocabulary duration_tracker.duration_class_for_beats() already
# snaps to, just abbreviated to fit a fixed-width tracker cell (no
# combining marks, no Unicode -- plain ASCII throughout, per Concept C's
# own "no Unicode/font concerns whatsoever" pitch).
_DURATION_CODES = {
    "whole": "1",
    "dotted-half": "2.",
    "half": "2",
    "dotted-quarter": "4.",
    "quarter": "4",
    "dotted-eighth": "8.",
    "eighth": "8",
    "dotted-sixteenth": "16.",
    "sixteenth": "16",
    "thirtysecond": "32",
}

EMPTY_CELL = "..."
CELL_WIDTH = 8  # "C-4 16. " etc, left-justified


def synth_melody_and_chord():
    """Same synthesized sequence as the piano-roll prototype (see that
    module's synth_melody_and_chord() for the rationale) -- an ascending
    C-major scale, a rest, then a held C4-E4-G4 chord, so the grid render
    exercises both single-voice rows and a genuine multi-channel (chord)
    row."""
    events = []
    bpm = 120.0
    beat_seconds = 60.0 / bpm
    quarter_beats = 1.0

    scale = [(0, 4), (2, 4), (4, 4), (5, 4), (7, 4), (9, 4), (11, 4), (0, 5)]
    t = 0.0
    for pc, octave in scale:
        duration_seconds = quarter_beats * beat_seconds
        duration_hops = round(duration_seconds / HOP_SECONDS)
        onset_hop = round(t / HOP_SECONDS)
        events.append(NoteEvent(onset_hop, t, pc, octave, duration_hops, None))
        t += duration_seconds

    t += beat_seconds

    chord_notes = [(0, 4), (4, 4), (7, 4)]
    chord_duration_seconds = 4 * beat_seconds
    chord_duration_hops = round(chord_duration_seconds / HOP_SECONDS)
    onset_hop = round(t / HOP_SECONDS)
    for pc, octave in chord_notes:
        events.append(NoteEvent(onset_hop, t, pc, octave, chord_duration_hops, "C"))

    return events, bpm


def _quantize_row_hops(bpm):
    """One grid row's duration, in both real seconds and this app's own
    hop count -- rows are a sixteenth-note subdivision of the given tempo,
    snapped to the nearest whole hop so row boundaries always land on a
    real analysis-hop boundary (this app has no sub-hop time resolution
    to place a note-attack at anyway)."""
    beat_seconds = 60.0 / bpm
    row_seconds = beat_seconds / ROW_SUBDIVISION
    row_hops = max(round(row_seconds / HOP_SECONDS), 1)
    return row_hops * HOP_SECONDS, row_hops


def _assign_channels(events):
    """Greedy interval-graph channel packing, the same manual-authoring
    convention real tracker software leaves to the user: walk events in
    onset order, place each into the first channel whose previous
    occupant has already ended by this note's onset, opening a new
    channel (up to N_CHANNELS) when none is free. A simultaneous chord's
    notes -- identical onset_time, all still "active" at that instant --
    necessarily land in separate channels this way, exactly mirroring
    chord mode's note_stack. Returns (channel_index -> event list,
    dropped_events) -- dropped only if a moment ever needs more than
    N_CHANNELS simultaneous voices, which this app's own chord cap
    already prevents in real detected data."""
    channel_end = [0.0] * N_CHANNELS
    channels = [[] for _ in range(N_CHANNELS)]
    dropped = []
    for e in sorted(events, key=lambda e: e.onset_time):
        end_time = e.onset_time + e.duration_hops * HOP_SECONDS
        placed = False
        for ch in range(N_CHANNELS):
            if channel_end[ch] <= e.onset_time:
                channels[ch].append(e)
                channel_end[ch] = end_time
                placed = True
                break
        if not placed:
            dropped.append(e)
    return channels, dropped


def _cell_token(e, bpm):
    letter = NOTE_NAMES_FIFTHS[e.pitch_class]
    note_part = (letter + "-")[:2] if len(letter) == 1 else letter  # "C-", "Db", "F#"
    beat_seconds = 60.0 / bpm
    beats = (e.duration_hops * HOP_SECONDS) / beat_seconds if beat_seconds else None
    dclass = duration_class_for_beats(beats) if beats else DEFAULT_DURATION_CLASS
    code = _DURATION_CODES.get(dclass, "4")
    return f"{note_part}{e.octave} {code}".ljust(CELL_WIDTH)


def _lane_rgb(pc, octave):
    hue, sat, light = note_to_hsl(pc, octave, scheme="fifths")
    return hsl_to_rgb255(hue, sat, light)


def render(events, bpm, out=sys.stdout):
    row_seconds, row_hops = _quantize_row_hops(bpm)
    channels, dropped = _assign_channels(events)

    last_end = max((e.onset_time + e.duration_hops * HOP_SECONDS) for e in events) if events else 0.0
    n_rows = max(int(last_end / row_seconds) + 2, 1)

    # grid[row][channel] -> (NoteEvent, token) | None
    grid = [[None] * N_CHANNELS for _ in range(n_rows)]
    for ch, ch_events in enumerate(channels):
        for e in ch_events:
            row = int(round(e.onset_time / row_seconds))
            if row < n_rows:
                grid[row][ch] = e

    bar_rows = ROW_SUBDIVISION * BEATS_PER_BAR

    row_note_fraction = _BEAT_UNIT * ROW_SUBDIVISION  # e.g. 4/4 time, 4 rows/beat -> 1/16 note per row
    header = f"tempo={bpm:.0f}bpm  time={BEATS_PER_BAR}/{_BEAT_UNIT}  " \
             f"row={row_seconds*1000:.1f}ms ({row_hops} hops, 1/{row_note_fraction} note)  " \
             f"channels={N_CHANNELS}"
    print(header, file=out)
    print(file=out)

    col_headers = "Row  " + "  ".join(f"Ch{c+1}".ljust(CELL_WIDTH) for c in range(N_CHANNELS))
    print(col_headers, file=out)
    print("-" * len(col_headers), file=out)

    for row in range(n_rows):
        if row > 0 and row % bar_rows == 0:
            print(f"{row:03d}  |{'-' * (len(col_headers) - 5)} bar", file=out)
        cells = []
        for ch in range(N_CHANNELS):
            e = grid[row][ch]
            if e is None:
                cells.append(EMPTY_CELL.ljust(CELL_WIDTH))
                continue
            r, g, b = _lane_rgb(e.pitch_class, e.octave)
            token = _cell_token(e, bpm)
            cells.append(f"\033[38;2;{r};{g};{b}m{token}\033[0m")
        print(f"{row:03d}  " + "  ".join(cells), file=out)

    print(file=out)
    if dropped:
        print(f"dropped {len(dropped)} note(s): exceeded {N_CHANNELS}-channel cap "
              f"at their onset time (shouldn't happen with real chord-mode data, "
              f"which already caps at config.CHORD_MAX_NOTES)", file=out)
    print(f"assigned {sum(len(c) for c in channels)} note(s) across "
          f"{sum(1 for c in channels if c)} of {N_CHANNELS} channels", file=out)


if __name__ == "__main__":
    events, bpm = synth_melody_and_chord()
    render(events, bpm)
