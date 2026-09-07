"""Patch format (map #99, decision #106 + its #107 velocity-layer addendum;
built by issue #115): the on-disk TOML schema, the in-memory patch model,
loading/saving, per-field defaults, malformed-file degradation, and zone
selection for the sampler.

A **Patch** is one hand-editable TOML file describing how to make sound,
for any of the three engines. One file kind, not three: every patch
declares `engine = "synth" | "sampler" | "sf2"`, so "load a patch" stays
one code path and one browser UI regardless of what's inside. Patches
live under `~/.config/note-color/patches/` (XDG-aware, see
`patches_dir()`), deliberately *not* inside `config.toml`.

This module owns the *format and the model only* -- no audio rendering.
The subtractive synth (#113), effects chain (#114), sampler (#116) and
SF2 player (#117) each consume a `Patch` produced here.

Posture, copied deliberately from `config_store.py` rather than invented:
**additive overlay with graceful degradation.** There is no version
field. Every field is optional and has a documented default; a missing
field means its default; an unknown key is ignored; a value of the wrong
type or outside its documented range falls back to (or is clamped into)
the default rather than raising. A *malformed* file loads as far as it
parses -- `load_patch()` retries the longest leading prefix of the file
that is valid TOML -- and fills the rest in from defaults, rather than
refusing to open. The worst case for any bad patch is a blander sound,
never a crash and never an unopenable file.

Schema (every table and every field optional):

    name = "Fat bass"
    engine = "synth"          # "synth" | "sampler" | "sf2"; default "synth"

    [osc1]                    # and [osc2], identical shape
    waveform = "saw"          # saw | square | triangle | sine
    octave = 0                # -2..2
    semitones = 0             # -12..12
    fine = 0.0                # cents, -100..100 (osc2 detune = the fat sound)
    pulse_width = 0.5         # 0.01..0.99, square only
    level = 1.0               # 0..1  (osc2 defaults to 0.0, i.e. off)

    [noise]
    level = 0.0               # 0..1
    colour = "white"          # white | pink

    [filter]
    type = "lp"               # lp | hp | bp
    cutoff = 12000.0          # Hz, 20..20000        -- MIDI CC 74
    resonance = 0.1           # 0..1                 -- MIDI CC 71
    env_amount = 0.0          # bipolar, -1..1
    key_tracking = 0.0        # 0..1, how much cutoff follows pitch

    [amp_env]                 # and [filter_env], identical shape
    delay = 0.0               # seconds, 0..30 (SF2 has these ahead of attack)
    hold = 0.0
    attack = 0.005            #                      -- MIDI CC 73
    decay = 0.1               #                      -- MIDI CC 75
    sustain = 0.8             # 0..1 level
    release = 0.2             #                      -- MIDI CC 72

    [lfo]
    rate = 5.0                # Hz, 0..100           -- MIDI CC 76
    depth = 0.0               # 0..1                 -- MIDI CC 77
    delay = 0.0               # seconds, 0..30       -- MIDI CC 78
    waveform = "sine"         # sine | triangle | saw | square
    destination = "pitch"     # pitch | filter | amp

    [voice]
    polyphony = 16            # 1..128
    glide = 0.0               # portamento seconds, 0..10
    velocity_to_amp = 1.0     # 0..1
    velocity_to_filter = 0.0  # 0..1
    volume = 0.8              # 0..1

    [[effects]]               # ordered chain; file order IS chain order
    type = "delay"            # unknown types are kept but ignored by #114
    time = 0.25               # any further keys ride along as params

    [[zones]]                 # sampler patches only
    sample = "snare_hard.wav" # BARE NAME under ~/.config/note-color/samples/
    low_key = 38              # 0..127
    high_key = 38
    root_key = 38             # the key the sample was recorded at
    low_vel = 96              # 0..127, default 0
    high_vel = 127            # 0..127, default 127
    gain = 0.0                # dB, -60..24
    choke_group = 0           # non-zero: cuts other zones in the same group

    [sf2]                     # sf2 patches only
    soundfont = "piano.sf2"   # BARE NAME under ~/.config/note-color/samples/
    bank = 0                  # 0..128
    preset = 0                # 0..127

**MIDI CC numbers are deliberately not stored in a patch.** The standard
numbers are documented above (cutoff 74, resonance 71, attack 73, decay
75, release 72, LFO rate 76 / depth 77 / delay 78) so the mapping is
obvious when MIDI device support lands, but a CC assignment describes the
*controller*, not the *sound*: storing it per-patch would mean re-editing
every patch on changing keyboards, and would bake one machine's hardware
wiring into a file meant to be shared. CC mapping belongs in
`config.toml` when MIDI arrives.

**Samples are referenced by bare name, never by path.** A patch
containing `/home/pelle/...` is not shareable; `sample_path()` resolves a
bare name against `samples_dir()`. A missing sample leaves its zone
silent and *unavailable* (`zone_available()`), never a crash -- the kit
still loads.
"""

from __future__ import annotations

import copy
import dataclasses
import glob
import os
import tomllib
from dataclasses import dataclass, field

ENGINES = ("synth", "sampler", "sf2")
DEFAULT_ENGINE = "synth"

OSC_WAVEFORMS = ("saw", "square", "triangle", "sine")
LFO_WAVEFORMS = ("sine", "triangle", "saw", "square")
NOISE_COLOURS = ("white", "pink")
FILTER_TYPES = ("lp", "hp", "bp")
LFO_DESTINATIONS = ("pitch", "filter", "amp")

MIN_KEY, MAX_KEY = 0, 127
MIN_VELOCITY, MAX_VELOCITY = 0, 127

#: The standard MIDI CC numbers for this parameter set, documented here
#: (and in the module docstring) for the future MIDI-mapping work -- see
#: decision #106 for why they are *not* a patch field.
STANDARD_MIDI_CC = {
    "filter.cutoff": 74,
    "filter.resonance": 71,
    "amp_env.attack": 73,
    "amp_env.decay": 75,
    "amp_env.release": 72,
    "lfo.rate": 76,
    "lfo.depth": 77,
    "lfo.delay": 78,
}


# --- Coercion helpers: every one degrades to `default`, never raises ---

def _number(value, default, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(min(max(value, low), high))


def _integer(value, default, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return int(min(max(int(value), low), high))


def _choice(value, default, allowed):
    if not isinstance(value, str):
        return default
    lowered = value.strip().lower()
    return lowered if lowered in allowed else default


def _text(value, default):
    return value if isinstance(value, str) else default


def _table(data, name):
    value = data.get(name)
    return value if isinstance(value, dict) else {}


# --- The model ---

@dataclass
class Oscillator:
    waveform: str = "saw"
    octave: int = 0
    semitones: int = 0
    fine: float = 0.0
    pulse_width: float = 0.5
    level: float = 1.0

    @classmethod
    def from_toml(cls, data, level_default=1.0):
        return cls(
            waveform=_choice(data.get("waveform"), "saw", OSC_WAVEFORMS),
            octave=_integer(data.get("octave"), 0, -2, 2),
            semitones=_integer(data.get("semitones"), 0, -12, 12),
            fine=_number(data.get("fine"), 0.0, -100.0, 100.0),
            pulse_width=_number(data.get("pulse_width"), 0.5, 0.01, 0.99),
            level=_number(data.get("level"), level_default, 0.0, 1.0),
        )


@dataclass
class Noise:
    level: float = 0.0
    colour: str = "white"

    @classmethod
    def from_toml(cls, data):
        return cls(
            level=_number(data.get("level"), 0.0, 0.0, 1.0),
            colour=_choice(data.get("colour"), "white", NOISE_COLOURS),
        )


@dataclass
class Filter:
    type: str = "lp"
    cutoff: float = 12000.0
    resonance: float = 0.1
    env_amount: float = 0.0
    key_tracking: float = 0.0

    @classmethod
    def from_toml(cls, data):
        return cls(
            type=_choice(data.get("type"), "lp", FILTER_TYPES),
            cutoff=_number(data.get("cutoff"), 12000.0, 20.0, 20000.0),
            resonance=_number(data.get("resonance"), 0.1, 0.0, 1.0),
            env_amount=_number(data.get("env_amount"), 0.0, -1.0, 1.0),
            key_tracking=_number(data.get("key_tracking"), 0.0, 0.0, 1.0),
        )


@dataclass
class Envelope:
    """DAHDSR -- SF2's delay/hold ahead of a conventional ADSR (cheap, and
    worth having since #103 found the SF2 generator list is a complete
    voice model capable of hosting all three engines)."""

    delay: float = 0.0
    hold: float = 0.0
    attack: float = 0.005
    decay: float = 0.1
    sustain: float = 0.8
    release: float = 0.2

    @classmethod
    def from_toml(cls, data):
        blank = cls()
        return cls(
            delay=_number(data.get("delay"), blank.delay, 0.0, 30.0),
            hold=_number(data.get("hold"), blank.hold, 0.0, 30.0),
            attack=_number(data.get("attack"), blank.attack, 0.0, 30.0),
            decay=_number(data.get("decay"), blank.decay, 0.0, 30.0),
            sustain=_number(data.get("sustain"), blank.sustain, 0.0, 1.0),
            release=_number(data.get("release"), blank.release, 0.0, 30.0),
        )


@dataclass
class Lfo:
    rate: float = 5.0
    depth: float = 0.0
    delay: float = 0.0
    waveform: str = "sine"
    destination: str = "pitch"

    @classmethod
    def from_toml(cls, data):
        return cls(
            rate=_number(data.get("rate"), 5.0, 0.0, 100.0),
            depth=_number(data.get("depth"), 0.0, 0.0, 1.0),
            delay=_number(data.get("delay"), 0.0, 0.0, 30.0),
            waveform=_choice(data.get("waveform"), "sine", LFO_WAVEFORMS),
            destination=_choice(data.get("destination"), "pitch", LFO_DESTINATIONS),
        )


@dataclass
class VoiceSettings:
    """Per-patch voice/global settings. `polyphony` here is the patch's own
    preference; decision #105 makes the *process* polyphony cap a
    `[preferences]` setting, and a voice manager is free to take the
    smaller of the two -- that reconciliation belongs to #112, not here."""

    polyphony: int = 16
    glide: float = 0.0
    velocity_to_amp: float = 1.0
    velocity_to_filter: float = 0.0
    volume: float = 0.8

    @classmethod
    def from_toml(cls, data):
        return cls(
            polyphony=_integer(data.get("polyphony"), 16, 1, 128),
            glide=_number(data.get("glide"), 0.0, 0.0, 10.0),
            velocity_to_amp=_number(data.get("velocity_to_amp"), 1.0, 0.0, 1.0),
            velocity_to_filter=_number(data.get("velocity_to_filter"), 0.0, 0.0, 1.0),
            volume=_number(data.get("volume"), 0.8, 0.0, 1.0),
        )


@dataclass
class EffectSpec:
    """One entry in the `[[effects]]` chain: a `type` plus whatever further
    keys that effect takes, kept verbatim in `params`. Unknown types (and
    unknown params) survive a load/save round trip untouched rather than
    being dropped -- a patch written for a newer build that grew a reverb
    must not be silently stripped of it by an older one. #114's chain is
    what decides which types it can actually render."""

    type: str = ""
    params: dict = field(default_factory=dict)

    @classmethod
    def from_toml(cls, data):
        params = {k: v for k, v in data.items() if k != "type"}
        return cls(type=_text(data.get("type"), ""), params=params)


@dataclass
class Zone:
    """A sample mapped to a key range (with the root key it was recorded
    at) *and* a velocity band (#107's addendum: velocity-layered samples
    are how real dynamics happen, since QWERTY plays at full velocity).
    `low_vel`/`high_vel` default to the full 0..127 band, so any zone
    written before that addendum keeps exactly its old meaning."""

    sample: str = ""
    low_key: int = MIN_KEY
    high_key: int = MAX_KEY
    root_key: int = 60
    low_vel: int = MIN_VELOCITY
    high_vel: int = MAX_VELOCITY
    gain: float = 0.0
    choke_group: int = 0

    @classmethod
    def from_toml(cls, data):
        low_key = _integer(data.get("low_key"), MIN_KEY, MIN_KEY, MAX_KEY)
        high_key = _integer(data.get("high_key"), MAX_KEY, MIN_KEY, MAX_KEY)
        low_vel = _integer(data.get("low_vel"), MIN_VELOCITY, MIN_VELOCITY, MAX_VELOCITY)
        high_vel = _integer(data.get("high_vel"), MAX_VELOCITY, MIN_VELOCITY, MAX_VELOCITY)
        if low_key > high_key:
            low_key, high_key = high_key, low_key
        if low_vel > high_vel:
            low_vel, high_vel = high_vel, low_vel
        return cls(
            sample=os.path.basename(_text(data.get("sample"), "")),
            low_key=low_key,
            high_key=high_key,
            root_key=_integer(data.get("root_key"), 60, MIN_KEY, MAX_KEY),
            low_vel=low_vel,
            high_vel=high_vel,
            gain=_number(data.get("gain"), 0.0, -60.0, 24.0),
            choke_group=_integer(data.get("choke_group"), 0, 0, 127),
        )

    def key_span(self):
        return self.high_key - self.low_key + 1

    def velocity_span(self):
        return self.high_vel - self.low_vel + 1

    def matches_key(self, key):
        return self.low_key <= key <= self.high_key

    def matches_velocity(self, velocity):
        return self.low_vel <= velocity <= self.high_vel

    def velocity_distance(self, velocity):
        """0 inside the band, else how far outside it `velocity` fell --
        the ordering `select_zone()`'s nearest-band fallback uses so a kit
        never goes quiet just because a velocity landed in an unmapped
        gap."""
        if velocity < self.low_vel:
            return self.low_vel - velocity
        if velocity > self.high_vel:
            return velocity - self.high_vel
        return 0


@dataclass
class Sf2Selection:
    """SF2's bank+preset **Program** selection, plus the soundfont file the
    program lives in (bare name, same shareability rule as a sample). Not
    enumerated by decision #106's section list, but an `engine = "sf2"`
    patch is meaningless without it -- see docs/DECISIONS.md."""

    soundfont: str = ""
    bank: int = 0
    preset: int = 0

    @classmethod
    def from_toml(cls, data):
        return cls(
            soundfont=os.path.basename(_text(data.get("soundfont"), "")),
            bank=_integer(data.get("bank"), 0, 0, 128),
            preset=_integer(data.get("preset"), 0, 0, 127),
        )


@dataclass
class Patch:
    name: str = "Untitled"
    engine: str = DEFAULT_ENGINE
    osc1: Oscillator = field(default_factory=Oscillator)
    osc2: Oscillator = field(default_factory=lambda: Oscillator(level=0.0))
    noise: Noise = field(default_factory=Noise)
    filter: Filter = field(default_factory=Filter)
    amp_env: Envelope = field(default_factory=Envelope)
    filter_env: Envelope = field(default_factory=Envelope)
    lfo: Lfo = field(default_factory=Lfo)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    effects: list = field(default_factory=list)
    zones: list = field(default_factory=list)
    sf2: Sf2Selection = field(default_factory=Sf2Selection)

    def copy(self):
        return copy.deepcopy(self)

    def is_kit(self):
        """A **Kit** is the degenerate sampler patch whose zones are each
        one key wide (a drum kit) -- what the synth tool's pad grid is a
        view onto. Not a separate file kind, just a shape."""
        return (
            self.engine == "sampler"
            and bool(self.zones)
            and all(z.key_span() == 1 for z in self.zones)
        )


def new_patch(name="Untitled", engine=DEFAULT_ENGINE):
    """A brand-new patch, every field at its documented default -- the
    starting point the synth tool's 'new patch' action hands the user."""
    return Patch(name=name, engine=_choice(engine, DEFAULT_ENGINE, ENGINES))


# --- Directories ---

def _config_root():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "note-color")


def patches_dir():
    return os.path.join(_config_root(), "patches")


def samples_dir():
    return os.path.join(_config_root(), "samples")


def patch_paths(directory=None):
    """Every `*.toml` patch file in `directory` (default `patches_dir()`),
    sorted by name -- a flat, non-recursive glob, mirroring
    `score_editor_picker.score_file_paths()`/`stats_display.
    session_log_paths()`. A missing directory yields an empty list."""
    directory = directory or patches_dir()
    return sorted(glob.glob(os.path.join(directory, "*.toml")))


def sample_path(name, directory=None):
    """Resolve a zone's bare `sample` name against `samples_dir()`. A name
    carrying any directory component is reduced to its basename first, so
    a hand-edited absolute path in a shared patch can never escape the
    samples directory."""
    if not name:
        return None
    return os.path.join(directory or samples_dir(), os.path.basename(name))


def zone_available(zone, directory=None):
    """True when the zone's sample actually exists on disk. A False here
    means the zone stays silent and renders as unavailable -- decision
    #106's rule -- while the rest of the kit still loads and sounds."""
    path = sample_path(zone.sample, directory)
    return bool(path) and os.path.isfile(path)


def missing_samples(patch, directory=None):
    """Bare names of every zone sample (and the SF2 soundfont, if any) the
    patch references but that isn't present -- what a browser UI shows as
    'unavailable' without having to re-stat per zone itself."""
    missing = []
    for zone in patch.zones:
        if zone.sample and not zone_available(zone, directory):
            missing.append(zone.sample)
    if patch.engine == "sf2" and patch.sf2.soundfont:
        path = sample_path(patch.sf2.soundfont, directory)
        if not os.path.isfile(path):
            missing.append(patch.sf2.soundfont)
    seen, unique = set(), []
    for name in missing:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


# --- Zone selection ---

def select_zone(zones, key, velocity):
    """The zone a sampler note-on sounds, or None.

    Matches on key range **and** velocity band, per #107's addendum:

    1. Only zones whose key range contains `key` are candidates. A key
       outside every zone genuinely has no sample -- that's a mapping the
       user chose, not an accident to paper over.
    2. Among those, zones whose velocity band contains `velocity` win, and
       the **narrowest** such band wins among them (a specific
       hard-snare layer beats a catch-all 0-127 zone under it).
    3. If *no* band contains the velocity, the **nearest** band is used
       rather than falling silent -- a kit must never go quiet because a
       velocity landed in an unmapped gap.

    Ties break by narrower key span, then by file order, so selection is
    deterministic for a hand-written patch."""
    candidates = [z for z in zones if z.matches_key(key)]
    if not candidates:
        return None
    exact = [z for z in candidates if z.matches_velocity(velocity)]
    pool = exact or candidates
    return min(
        enumerate(pool),
        key=lambda pair: (
            pair[1].velocity_distance(velocity),
            pair[1].velocity_span(),
            pair[1].key_span(),
            pair[0],
        ),
    )[1]


def choked_zones(zones, sounding_zone):
    """Every zone a newly-sounding zone cuts: same non-zero `choke_group`,
    excluding itself. Zero means 'chokes nothing', so an ordinary kit
    needs no bookkeeping at all (open/closed hi-hat is the case this
    exists for)."""
    if not sounding_zone or not sounding_zone.choke_group:
        return []
    return [
        z for z in zones
        if z.choke_group == sounding_zone.choke_group and z is not sounding_zone
    ]


# --- Loading ---

def patch_from_toml(data):
    """Build a `Patch` from an already-parsed TOML mapping. Pure: no file
    I/O, no exceptions -- anything unrecognised degrades to its default.
    `load_patch()` is this plus reading (and partially recovering) a
    file."""
    if not isinstance(data, dict):
        data = {}
    effects = [
        EffectSpec.from_toml(e) for e in data.get("effects", [])
        if isinstance(e, dict)
    ] if isinstance(data.get("effects"), list) else []
    zones = [
        Zone.from_toml(z) for z in data.get("zones", [])
        if isinstance(z, dict)
    ] if isinstance(data.get("zones"), list) else []
    return Patch(
        name=_text(data.get("name"), "Untitled"),
        engine=_choice(data.get("engine"), DEFAULT_ENGINE, ENGINES),
        osc1=Oscillator.from_toml(_table(data, "osc1"), level_default=1.0),
        osc2=Oscillator.from_toml(_table(data, "osc2"), level_default=0.0),
        noise=Noise.from_toml(_table(data, "noise")),
        filter=Filter.from_toml(_table(data, "filter")),
        amp_env=Envelope.from_toml(_table(data, "amp_env")),
        filter_env=Envelope.from_toml(_table(data, "filter_env")),
        lfo=Lfo.from_toml(_table(data, "lfo")),
        voice=VoiceSettings.from_toml(_table(data, "voice")),
        effects=effects,
        zones=zones,
        sf2=Sf2Selection.from_toml(_table(data, "sf2")),
    )


def parse_patch_text(text):
    """Parse patch TOML source, recovering as much as possible from a
    malformed file: on a decode error, the longest leading prefix of the
    file's lines that *is* valid TOML is used and everything after it
    dropped. This is the literal form of decision #106's "loads as far as
    it parses and falls back to defaults rather than refusing to open" --
    a typo in the last `[[zones]]` entry costs you that entry, not the
    whole kit."""
    try:
        return tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        pass
    good, buffer = {}, []
    for line in text.splitlines():
        buffer.append(line)
        try:
            good = tomllib.loads("\n".join(buffer))
        except (tomllib.TOMLDecodeError, ValueError):
            continue
    return good


def load_patch(path):
    """Load a patch file. An unreadable/absent file yields an
    all-defaults patch named after the file, exactly as
    `config_store.ConfigStore` treats a missing `config.toml` -- never an
    exception."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return new_patch(name=patch_name_for_path(path))
    patch = patch_from_toml(parse_patch_text(text))
    if not patch.name:
        patch.name = patch_name_for_path(path)
    return patch


def patch_name_for_path(path):
    return os.path.splitext(os.path.basename(path))[0] or "Untitled"


# --- Saving ---

def save_patch(patch, path):
    """Write `patch` to `path` as TOML, creating the directory if needed.

    Every field is written explicitly, not just the ones differing from
    their default: the file is meant to be *hand-edited*, and a
    fully-populated file is self-documenting in a way a sparse one isn't
    (this is where the sparse-overlay analogy with `config_store.py`
    deliberately stops -- that file overlays a running program's
    constants; this one *is* the sound). Only the sections that apply to
    the patch's engine are written."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(patch_to_toml(patch))
    return path


def patch_to_toml(patch):
    lines = [
        f"name = {_dump_value(patch.name)}",
        f"engine = {_dump_value(patch.engine)}",
        "",
    ]
    if patch.engine in ("synth", "sampler"):
        if patch.engine == "synth":
            lines += _dump_table("osc1", patch.osc1)
            lines += _dump_table("osc2", patch.osc2)
            lines += _dump_table("noise", patch.noise)
        lines += _dump_table("filter", patch.filter)
        lines += _dump_table("amp_env", patch.amp_env)
        lines += _dump_table("filter_env", patch.filter_env)
        lines += _dump_table("lfo", patch.lfo)
        lines += _dump_table("voice", patch.voice)
    if patch.engine == "sf2":
        lines += _dump_table("sf2", patch.sf2)
        lines += _dump_table("voice", patch.voice)
    for effect in patch.effects:
        lines.append("[[effects]]")
        lines.append(f"type = {_dump_value(effect.type)}")
        for key, value in effect.params.items():
            rendered = _dump_value(value)
            if rendered is not None:
                lines.append(f"{key} = {rendered}")
        lines.append("")
    if patch.engine == "sampler":
        for zone in patch.zones:
            lines.append("[[zones]]")
            lines += _dump_fields(zone)
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _dump_table(name, obj):
    return [f"[{name}]"] + _dump_fields(obj) + [""]


def _dump_fields(obj):
    lines = []
    for f in dataclasses.fields(obj):
        rendered = _dump_value(getattr(obj, f.name))
        if rendered is not None:
            lines.append(f"{f.name} = {rendered}")
    return lines


def _dump_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (list, tuple)):
        rendered = [_dump_value(v) for v in value]
        if any(r is None for r in rendered):
            return None
        return "[" + ", ".join(rendered) + "]"
    return None
