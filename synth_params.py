"""The synth tool's **parameter panel** model (map #99, ticket #119,
implementing decision #107 point 5): which of a `patch_format.Patch`'s
~30 scalars are editable, in what order, and what Left/Right does to
each.

Decision #107 settled the interaction outright, and this module exists to
make that interaction *pure and testable*: **Up/Down selects a parameter,
Left/Right changes its value.** There is no focus toggle, no modifier
mode, and no direct numeric entry -- letters and numbers always play
notes instead, because an instrument must have no state in which pressing
a key does something other than sound. A synth parameter is swept by ear,
not typed; where a sweep genuinely isn't enough, `Shift` is the escape
hatch (here: `Shift`+Left/Right for a coarse jump).

**Log-scaled parameters step by ratio, not by amount.** A cutoff sweep
that adds a fixed number of Hz is unusable -- it crawls at the bottom of
the range and leaps at the top, because pitch and brightness are
perceived logarithmically. `SCALE_LOG` steps multiply instead, so one
key press is the same musical distance everywhere in the range. Envelope
times are the same story (5ms -> 10ms matters as much as 1s -> 2s), which
is why they are log too and why a log parameter's floor is its spec
minimum rather than zero.

The specs are declared per **section**, matching the patch file's own
`[osc1]`/`[filter]`/`[amp_env]` tables one-for-one, so what the panel
shows and what a hand-edited patch file contains are visibly the same
thing (decision #106's whole point in making patches hand-editable).
Which sections apply is decided by the patch's own `engine` field: a
sampler kit has no oscillators to show, and showing eight dead sections
would be worse than showing four live ones.

Everything here is pure -- a `Patch` plus plain ints in, a mutated field
or a formatted string out -- and directly unit-tested. The panel's actual
screen layout lives in `synth_display.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

import config
from color_map import NOTE_NAMES_FIFTHS

SCALE_LINEAR = "linear"
SCALE_LOG = "log"

KIND_FLOAT = "float"
KIND_INT = "int"
KIND_CHOICE = "choice"


@dataclass(frozen=True)
class ParamSpec:
    """One editable field. `section` is the attribute on `Patch` holding
    the sub-object (`"osc1"`, `"filter"`, ...) and `attr` the field on it,
    so reading and writing are one `getattr`/`setattr` pair rather than a
    per-field accessor -- the patch dataclasses are already the schema."""

    section: str
    attr: str
    label: str
    kind: str = KIND_FLOAT
    low: float = 0.0
    high: float = 1.0
    step: float = 0.05
    scale: str = SCALE_LINEAR
    options: tuple = ()
    unit: str = ""
    digits: int = 2

    @property
    def path(self):
        return f"{self.section}.{self.attr}"


def _env_specs(section, title):
    return (title, (
        ParamSpec(section, "delay", "Delay", KIND_FLOAT, 0.0, 30.0, 1.3, SCALE_LOG, unit="s", digits=3),
        ParamSpec(section, "hold", "Hold", KIND_FLOAT, 0.0, 30.0, 1.3, SCALE_LOG, unit="s", digits=3),
        ParamSpec(section, "attack", "Attack", KIND_FLOAT, 0.0, 30.0, 1.3, SCALE_LOG, unit="s", digits=3),
        ParamSpec(section, "decay", "Decay", KIND_FLOAT, 0.0, 30.0, 1.3, SCALE_LOG, unit="s", digits=3),
        ParamSpec(section, "sustain", "Sustain", KIND_FLOAT, 0.0, 1.0, 0.05),
        ParamSpec(section, "release", "Release", KIND_FLOAT, 0.0, 30.0, 1.3, SCALE_LOG, unit="s", digits=3),
    ))


def _osc_specs(section, title):
    return (title, (
        ParamSpec(section, "waveform", "Wave", KIND_CHOICE,
                  options=("saw", "square", "triangle", "sine")),
        ParamSpec(section, "octave", "Octave", KIND_INT, -2, 2, 1),
        ParamSpec(section, "semitones", "Semis", KIND_INT, -12, 12, 1),
        ParamSpec(section, "fine", "Fine", KIND_FLOAT, -100.0, 100.0, 1.0, unit="c", digits=1),
        ParamSpec(section, "pulse_width", "PW", KIND_FLOAT, 0.01, 0.99, 0.01),
        ParamSpec(section, "level", "Level", KIND_FLOAT, 0.0, 1.0, 0.05),
    ))


#: Sections for a `engine = "synth"` patch, in panel order. Mirrors the
#: TOML file's own table order so the panel and the file read alike.
SYNTH_SECTIONS = (
    _osc_specs("osc1", "OSC 1"),
    _osc_specs("osc2", "OSC 2"),
    ("NOISE", (
        ParamSpec("noise", "level", "Level", KIND_FLOAT, 0.0, 1.0, 0.05),
        ParamSpec("noise", "colour", "Colour", KIND_CHOICE, options=("white", "pink")),
    )),
    ("FILTER", (
        ParamSpec("filter", "type", "Type", KIND_CHOICE, options=("lp", "hp", "bp")),
        ParamSpec("filter", "cutoff", "Cutoff", KIND_FLOAT, 20.0, 20000.0,
                  config.SYNTH_PARAM_CUTOFF_RATIO, SCALE_LOG, unit="Hz", digits=0),
        ParamSpec("filter", "resonance", "Reso", KIND_FLOAT, 0.0, 1.0, 0.05),
        ParamSpec("filter", "env_amount", "EnvAmt", KIND_FLOAT, -1.0, 1.0, 0.05),
        ParamSpec("filter", "key_tracking", "KeyTrk", KIND_FLOAT, 0.0, 1.0, 0.05),
    )),
    _env_specs("amp_env", "AMP ENV"),
    _env_specs("filter_env", "FILTER ENV"),
    ("LFO", (
        ParamSpec("lfo", "rate", "Rate", KIND_FLOAT, 0.0, 100.0, 1.2, SCALE_LOG, unit="Hz", digits=2),
        ParamSpec("lfo", "depth", "Depth", KIND_FLOAT, 0.0, 1.0, 0.05),
        ParamSpec("lfo", "delay", "Delay", KIND_FLOAT, 0.0, 30.0, 1.3, SCALE_LOG, unit="s", digits=3),
        ParamSpec("lfo", "waveform", "Wave", KIND_CHOICE,
                  options=("sine", "triangle", "saw", "square")),
        ParamSpec("lfo", "destination", "Dest", KIND_CHOICE,
                  options=("pitch", "filter", "amp")),
    )),
)

#: The `[voice]` table applies to every engine, so it is declared once and
#: appended to whichever section list a patch's engine selects.
VOICE_SECTION = ("VOICE", (
    ParamSpec("voice", "volume", "Volume", KIND_FLOAT, 0.0, 1.0, 0.05),
    ParamSpec("voice", "glide", "Glide", KIND_FLOAT, 0.0, 10.0, 1.3, SCALE_LOG, unit="s", digits=3),
    ParamSpec("voice", "velocity_to_amp", "Vel>Amp", KIND_FLOAT, 0.0, 1.0, 0.05),
    ParamSpec("voice", "velocity_to_filter", "Vel>Filt", KIND_FLOAT, 0.0, 1.0, 0.05),
))

SF2_SECTION = ("SF2", (
    ParamSpec("sf2", "bank", "Bank", KIND_INT, 0, 128, 1),
    ParamSpec("sf2", "preset", "Preset", KIND_INT, 0, 127, 1),
))


def sections_for(patch):
    """The panel's sections for this patch's own engine. A sampler kit
    gets `[voice]` only: its sound lives in its zones (which are files,
    edited by importing a sample onto a pad), not in a bank of knobs, and
    a wall of dead oscillator rows would misrepresent what is actually
    adjustable."""
    engine = getattr(patch, "engine", "synth")
    if engine == "sampler":
        return (VOICE_SECTION,)
    if engine == "sf2":
        return (SF2_SECTION, VOICE_SECTION)
    return SYNTH_SECTIONS + (VOICE_SECTION,)


def specs_for(patch):
    """Every spec, flattened in panel order -- the list Up/Down indexes
    into."""
    return [spec for _title, specs in sections_for(patch) for spec in specs]


def section_of(patch, index):
    """The section title owning flat parameter `index`, for the panel's
    heading. Returns "" for an out-of-range index rather than raising --
    the panel renders whatever it is given."""
    seen = 0
    for title, specs in sections_for(patch):
        if index < seen + len(specs):
            return title
        seen += len(specs)
    return ""


# --------------------------------------------------------------------------
# Reading, writing, stepping
# --------------------------------------------------------------------------

def read(patch, spec):
    return getattr(getattr(patch, spec.section), spec.attr)


def write(patch, spec, value):
    setattr(getattr(patch, spec.section), spec.attr, value)
    return value


def _clamp(value, low, high):
    return max(low, min(high, value))


def step_value(spec, value, direction, coarse=False):
    """Pure: one Left/Right press on `spec`'s current `value`.

    Clamped at both ends rather than wrapped for numeric fields (the same
    clamp-not-wrap rule `settings_display.parse_numeric_input()` and
    `score_properties_display.spin_tempo()` already follow -- a bounded
    physical quantity has ends), and wrapped for a choice list (a short
    ring of names has no ends, exactly as the Chord builder's reels
    wrap)."""
    direction = 1 if direction > 0 else -1
    if spec.kind == KIND_CHOICE:
        options = spec.options
        if not options:
            return value
        try:
            index = options.index(value)
        except ValueError:
            index = 0
        return options[(index + direction) % len(options)]
    if spec.kind == KIND_INT:
        amount = int(spec.step) * (config.SYNTH_PARAM_COARSE_STEPS if coarse else 1)
        return int(_clamp(int(value) + direction * amount, spec.low, spec.high))
    if spec.scale == SCALE_LOG:
        ratio = float(spec.step)
        if coarse:
            ratio = ratio ** config.SYNTH_PARAM_COARSE_STEPS
        floor = max(float(spec.low), config.SYNTH_PARAM_LOG_FLOOR)
        current = float(value)
        if direction > 0:
            current = floor if current < floor else current * ratio
        else:
            current = current / ratio
            if current < floor:
                # Stepping down past the floor lands on the spec minimum
                # (usually 0.0, i.e. "off") rather than asymptotically
                # approaching it forever -- "no attack at all" is a real,
                # reachable setting on a synth and must stay reachable.
                current = float(spec.low)
        return _clamp(current, float(spec.low), float(spec.high))
    amount = float(spec.step) * (config.SYNTH_PARAM_COARSE_STEPS if coarse else 1)
    return _clamp(float(value) + direction * amount, float(spec.low), float(spec.high))


def adjust(patch, spec, direction, coarse=False):
    """Steps `spec` on `patch` in place and returns the new value."""
    return write(patch, spec, step_value(spec, read(patch, spec), direction, coarse))


def move_selection(index, count, delta):
    """Up/Down over the flat spec list. Clamped, not wrapped: a long list
    of knobs is a ruler, not a ring -- wrapping from the last row to the
    first would be a surprise on every overshoot."""
    if count <= 0:
        return 0
    return max(0, min(count - 1, int(index) + int(delta)))


def visible_range(selected, count, height):
    """Which slice of the flat spec list the panel shows, keeping the
    selected row inside it -- the same viewport-centring shape
    `score_editor_display.visible_column_range()` uses for columns."""
    height = max(1, int(height))
    if count <= height:
        return 0, count
    start = int(selected) - height // 2
    start = max(0, min(count - height, start))
    return start, start + height


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------

def format_value(spec, value):
    """The panel's right-hand column. Compact on purpose: the panel shares
    the screen with the input layer, which is the half that has to stay
    legible."""
    if spec.kind == KIND_CHOICE:
        return str(value)
    if spec.kind == KIND_INT:
        sign = "+" if (spec.low < 0 and int(value) > 0) else ""
        return f"{sign}{int(value)}{spec.unit}"
    value = float(value)
    if spec.unit == "Hz" and value >= 1000:
        return f"{value / 1000:.2f}kHz"
    if spec.unit == "s" and value < 1.0:
        return f"{value * 1000:.0f}ms"
    text = f"{value:.{spec.digits}f}"
    if spec.low < 0 and value > 0:
        text = "+" + text
    return f"{text}{spec.unit}"


def note_name(midi_pitch):
    """MIDI pitch -> this repo's own flat-biased fifths spelling plus an
    octave digit ("Eb3"), for the input layer's key captions and the
    custom-layout binding parameter."""
    return f"{NOTE_NAMES_FIFTHS[int(midi_pitch) % 12]}{int(midi_pitch) // 12 - 1}"
