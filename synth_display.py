"""The synth tool's screen (map #99, ticket #119, decision #107 point 1):
**parameter panel above, always-visible input layer below.**

    +--------------------------------------------------+
    |  PARAMETERS   osc1 / osc2 / filter / envs / lfo   |
    |               (arrow-driven)                      |
    +--------------------------------------------------+
    |  INPUT LAYER  the playable keys for this layout   |
    |               tinted by pitch class, lit on hit   |
    +--------------------------------------------------+

The input layer is the half that makes a terminal synth playable at all:
it shows *exactly the keys the current layout plays*, in the physical
arrangement they sit in on the keyboard, so a player can see which key is
which note without counting. Keys tint by pitch class in this app's
existing circle-of-fifths palette and light on press through
`animation.ColorAnimator` -- the same treatment `fill` already gives a
detected note, pointed the other way round (map #99's note -> color
direction, which this whole subsystem stays on).

**Pads tint by their assigned sample, not by a pitch class**, because a
pad has no pitch to be honest about -- see
`synth_layout.sample_hue_step()` for why the mapping is a deterministic
byte sum rather than `hash()`.

**Overlays draw over the panel, never over the input layer** (#107 point
6). Loading a patch, saving one, or importing a sample all keep the
instrument on screen and playable underneath -- which is the entire
difference between an inline overlay and the separate screen this
project's hands-on feedback already rejected once.

Everything up to `render()` is pure (a layout/patch/state in, colours and
text lines out) and unit-tested; `render()` itself -- cursor addressing,
terminal-size fitting, the ANSI writes -- is smoke-tested manually only,
the same convention every `run_terminal_*` view's own render method in
this codebase follows.
"""

from __future__ import annotations

import os
import shutil
import sys

import config
import synth_layout
import synth_params
from animation import ColorAnimator
from color_map import fifths_index, hsl_to_rgb255, hue_for_step
from synth_layout import NOTE, PAD, UNBOUND

CELL_WIDTH = 5          # "z C3" plus one space of gutter
PANEL_MIN_ROWS = 4

_UNBOUND_RGB = (70, 70, 70)


# --------------------------------------------------------------------------
# Colour
# --------------------------------------------------------------------------

def slot_hue_step(slot, kit=None, octave_shift=0):
    """The 12-step wheel position a slot's colour comes from, or None for
    a slot with no colour to claim (unbound, or a pad with no sample).

    A note key uses `color_map.fifths_index()`'s circle-of-fifths step, so
    a C on this keyboard is the same colour as a detected C in every other
    view of this app. A pad uses its sample's own stable step instead --
    tinting a snare by "pitch class D#" would be a colour that means
    nothing."""
    if slot is None or slot.kind == UNBOUND:
        return None
    if slot.kind == PAD:
        from synth_tool import zone_for_pad

        zone = zone_for_pad(kit, slot.value)
        return synth_layout.sample_hue_step(zone.sample if zone else None)
    pitch = slot.midi_key(octave_shift)
    if pitch is None:
        return None
    return fifths_index(pitch % 12)


def slot_rgb(slot, lit=False, kit=None, octave_shift=0):
    """A slot's colour at rest or while sounding. Only lightness moves
    between the two states -- hue and saturation are the key's identity,
    exactly as `tab`'s age-fade already treats a note's colour."""
    step = slot_hue_step(slot, kit, octave_shift)
    if step is None:
        base = _UNBOUND_RGB
        return tuple(min(255, int(c * 2.2)) for c in base) if lit else base
    lightness = config.SYNTH_KEY_LIT_LIGHTNESS if lit else config.SYNTH_KEY_DIM_LIGHTNESS
    return hsl_to_rgb255(hue_for_step(step), config.BASE_SATURATION, lightness)


def slot_caption(slot, octave_shift=0, kit=None):
    """The three-character label under a key: a note name ("Db4") for a
    note key, the sample's first characters for an assigned pad, "P<n>"
    for an empty one, "--" for unbound."""
    if slot is None or slot.kind == UNBOUND:
        return "--"
    if slot.kind == NOTE:
        return synth_params.note_name(slot.midi_key(octave_shift))
    from synth_tool import zone_for_pad

    zone = zone_for_pad(kit, slot.value)
    if zone is not None and zone.sample:
        return os.path.splitext(os.path.basename(zone.sample))[0][:3]
    return f"P{slot.value + 1}"


class KeyLights:
    """One `animation.ColorAnimator` per key of the input layer.

    Per-key rather than one animator for the whole screen because several
    keys sound at once -- that is what a keyboard is -- and a shared
    animator would smear every simultaneous note into one average colour.
    The same reason chord mode already keys `fill`'s band animators per
    note.

    Pure math with an injected `dt`: no clock is read here, so a test can
    step the animation deterministically."""

    def __init__(self):
        self._animators = {}
        self._sounding = set()

    def _animator(self, key):
        if key not in self._animators:
            self._animators[key] = ColorAnimator(
                tau_ms=config.SYNTH_KEY_TAU_MS,
                pulse_decay_ms=config.SYNTH_KEY_PULSE_DECAY_MS,
                pulse_boost=config.SYNTH_KEY_PULSE_BOOST,
            )
        return self._animators[key]

    def update(self, dt, layout, sounding, kit=None, octave_shift=0):
        """Advances every key's animation one frame and returns
        `{key: rgb}`. `sounding` is the set of key tokens currently making
        a sound -- held keys under the kitty protocol, or not-yet-expired
        ones on the fixed-duration path."""
        sounding = set(sounding or ())
        onsets = sounding - self._sounding
        self._sounding = sounding
        colors = {}
        for slot in layout.slots:
            lit = slot.key in sounding
            target = slot_rgb(slot, lit, kit, octave_shift)
            colors[slot.key] = self._animator(slot.key).update(dt, target, slot.key in onsets)
        return colors


def _fg_for(rgb):
    """Black text on a light swatch, white on a dark one -- the same
    readability rule `terminal_tab_display.py` applies to its own note
    cells."""
    luma = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    return (0, 0, 0) if luma > 150 else (235, 235, 235)


def _swatch(rgb, text, width=CELL_WIDTH):
    fg = _fg_for(rgb)
    body = text.center(width)[:width]
    return (f"\033[48;2;{rgb[0]};{rgb[1]};{rgb[2]}m"
            f"\033[38;2;{fg[0]};{fg[1]};{fg[2]}m{body}\033[0m")


# --------------------------------------------------------------------------
# Text layout (pure)
# --------------------------------------------------------------------------

def input_layer_lines(layout, colors, octave_shift=0, kit=None):
    """The input layer as a list of ANSI lines -- one line per layout row,
    each cell a coloured swatch reading "<key> <caption>".

    Key and caption share one cell rather than sitting on two stacked
    lines: a terminal row is expensive (the panel above needs the space),
    and "z C3" is already unambiguous."""
    grid = layout.grid()
    width = layout.width()
    lines = []
    for row in range(layout.height()):
        cells = []
        row_slots = grid.get(row, {})
        if not row_slots:
            continue
        for col in range(width):
            slot = row_slots.get(col)
            if slot is None:
                cells.append(" " * CELL_WIDTH)
                continue
            rgb = colors.get(slot.key, slot_rgb(slot, False, kit, octave_shift))
            cells.append(_swatch(rgb, f"{slot.key} {slot_caption(slot, octave_shift, kit)}"))
        lines.append("".join(cells))
    return lines


def panel_lines(patch, selected, height, width=None):
    """The parameter panel: a section heading wherever one starts, then
    "label ......... value" per parameter, with the selected row
    reverse-videoed.

    Section headings are inline rather than a separate always-visible
    index because the panel scrolls: a heading that scrolls with its own
    parameters is the one that stays truthful."""
    width = width or 40
    specs = synth_params.specs_for(patch)
    rows = []
    seen = 0
    for title, section_specs in synth_params.sections_for(patch):
        rows.append(("header", title, ""))
        for spec in section_specs:
            rows.append(("param", spec, seen))
            seen += 1
    # Map the flat parameter index onto the row list so the viewport
    # scrolls in row space (headings included) rather than skipping them.
    selected_row = 0
    for i, (kind, payload, index) in enumerate(rows):
        if kind == "param" and index == selected:
            selected_row = i
            break
    height = max(PANEL_MIN_ROWS, int(height))
    start = max(0, min(max(0, len(rows) - height), selected_row - height // 2))
    lines = []
    for kind, payload, index in rows[start:start + height]:
        if kind == "header":
            lines.append(f"\033[1m{payload[:width]}\033[0m")
            continue
        value = synth_params.format_value(payload, synth_params.read(patch, payload))
        label = payload.label
        dots = max(1, width - len(label) - len(value) - 2)
        text = f" {label}{'.' * dots}{value}"[:width]
        lines.append(f"\033[7m{text.ljust(width)}\033[0m" if index == selected else text)
    if not specs:
        lines.append("(no editable parameters for this patch)")
    return lines


def overlay_lines(overlay, height, width=None):
    """The inline overlay, drawn in the parameter panel's own space. The
    input layer below is untouched and still playing -- that is the whole
    point of an overlay rather than a screen."""
    width = width or 46
    height = max(PANEL_MIN_ROWS, int(height))
    lines = [f"\033[1m{overlay.title}\033[0m"]
    if overlay.kind == "save":
        lines.append(f"  name: {overlay.buffer}_")
        lines.append("  enter=save   esc=cancel")
        lines.append("  (typed keys still sound -- letters always play)")
        return lines[:height]
    if overlay.directory:
        lines.append(f"  {overlay.directory}"[:width])
    body_height = max(1, height - len(lines))
    entries = overlay.entries
    if not entries:
        lines.append("  (nothing here)")
        return lines[:height]
    start = max(0, min(max(0, len(entries) - body_height), overlay.index - body_height // 2))
    for i in range(start, min(len(entries), start + body_height)):
        label = entries[i][0]
        text = f"  {label}"[:width]
        lines.append(f"\033[7m{text.ljust(width)}\033[0m" if i == overlay.index else text)
    return lines[:height]


# --------------------------------------------------------------------------
# render() -- smoke-tested manually, per this module's docstring
# --------------------------------------------------------------------------

def render(state, colors, status, help_legend=""):
    size = shutil.get_terminal_size(fallback=(80, 24))
    cols, rows = size
    layout = state.layout

    input_lines = input_layer_lines(layout, colors, state.octave_shift, state.kit)
    text_rows = 2 if help_legend else 1
    panel_height = max(PANEL_MIN_ROWS, rows - len(input_lines) - text_rows - 2)
    panel_width = min(max(30, cols - 2), 60)

    if state.overlay is not None:
        top = overlay_lines(state.overlay, panel_height, panel_width)
    else:
        top = panel_lines(state.panel_patch(), state.param_index, panel_height, panel_width)

    out = ["\033[2J"]
    line_no = 1
    for line in top:
        out.append(f"\033[{line_no};1H\033[K{line}")
        line_no += 1
    for _ in range(max(0, panel_height - len(top))):
        out.append(f"\033[{line_no};1H\033[K")
        line_no += 1
    out.append(f"\033[{line_no};1H\033[K{'-' * min(cols, panel_width)}")
    line_no += 1
    for line in input_lines:
        out.append(f"\033[{line_no};1H\033[K{line}")
        line_no += 1
    out.append(f"\033[{line_no};1H\033[K{status[:cols]}")
    line_no += 1
    if help_legend:
        out.append(f"\033[{line_no};1H\033[K{help_legend[:cols]}")
    sys.stdout.write("".join(out))
    sys.stdout.flush()
