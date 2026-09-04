"""The synth tool's **layouts** (map #99, build ticket #119, implementing
decision #107): which physical QWERTY key plays what, and where each key
sits on screen.

A *layout describes your hands, not a sound* (#107 decision 4). It is
therefore its own concept, saved in its own file under
`~/.config/note-color/layouts/`, and is deliberately **not** part of a
patch: binding a key arrangement into a patch would mean redoing the
arrangement every time the sound changed -- the same mistake decision
#106 already refused for MIDI CC numbers.

Four layouts, cycled with `Tab` (#107 decision 2):

1. ``two-octave`` -- the tracker/DAW convention, ``zsxdcvgbhnjm`` lower
   and ``q2w3er5t6y7u`` upper, black keys where they physically sit. No
   pads. Two octaves without shifting is a real playability difference
   and is the arrangement muscle memory already exists for.
2. ``octave-pads`` -- one octave on the upper two rows plus a bottom row
   of eight pads. The one layout where a kit and a synth patch sound
   *simultaneously* -- see `main.run_synth_tool()` for the voice-budget
   consequence #107's implementation note flagged.
3. ``pads`` -- the 4x4 square (``1234``/``qwer``/``asdf``/``zxcv``) every
   MPC/Push/Maschine tutorial assumes.
4. ``custom`` -- a copy of a built-in, then rebound key by key and saved
   to a file.

Layouts, not modes, are what resolve the collision between the pad square
and the two-octave keyboard: both want ``1234``/``qwer``/``asdf``/``zxcv``,
and they simply never coexist.

**Display shape is part of the layout, not the renderer.** Each built-in
is declared as a list of row *templates* -- plain strings in which each
character position is one display cell and a space is a gap -- so the
black keys land physically between their white neighbours and the whole
input layer reads as the keyboard it actually is. `synth_display.py` just
draws whatever `Layout.grid()` reports; it invents no geometry of its
own.

**Pads carry an index, not a pitch.** `pad_midi_key()` maps a pad index
onto the MIDI key a kit's one-key-wide zones live at (36 upward, the
General-MIDI drum convention `patch_format.Zone` already documents), so a
pad press is an ordinary `NoteOn` on the drum channel rather than a
second kind of event. Pads are numbered **bottom row first** (`zxcv` =
pads 1-4), matching hardware: on an MPC the bottom-left pad is pad 1, and
that is where a kick belongs.

Everything here is pure (plus plain TOML file I/O for the custom layout)
and directly unit-tested; nothing in this module touches a terminal, an
audio device, or a `Patch`.
"""

from __future__ import annotations

import glob
import os
import tomllib
from dataclasses import dataclass, field

import config
import score_audition

# --- Kinds a key slot can carry ---
NOTE = "note"
PAD = "pad"
UNBOUND = "unbound"
KINDS = (NOTE, PAD, UNBOUND)

#: MIDI key the *first* pad triggers. 36 is C1 -- General MIDI's kick
#: drum, and the key a one-key-wide kit zone conventionally starts at.
PAD_BASE_KEY = 36

#: MIDI channel each kind plays on. Channel 9 for pads is MIDI's own drum
#: channel, which is what makes "a kit and a synth patch at once"
#: (layout 2) a routing question rather than a new code path -- see
#: `synth_tool.ChannelRouter`.
NOTE_CHANNEL = 0
PAD_CHANNEL = 9

#: **The tracker keyboard lives in one place, and it is not this
#: module.** `score_audition.PIANO_KEY_SEMITONES` (ticket #120) is the
#: canonical `zsxdcvgbhnjm`/`q2w3er5t6y7u` key -> semitone table, and the
#: score editor's piano mode already plays from it. Deriving the synth
#: tool's note pitches from that same table rather than restating them
#: here is what keeps the two from drifting into "the same keyboard,
#: nearly" -- the failure mode a second hand-written copy guarantees
#: eventually.
#:
#: What this module adds on top is the part `score_audition` genuinely
#: has no use for: **geometry**. The score editor never draws the
#: keyboard, so it needs only "which key is which semitone"; the synth
#: tool's whole input layer is a picture of the keyboard, so it needs
#: "and which display cell does that key sit in". The row templates below
#: carry exactly that and nothing else -- every pitch they resolve comes
#: back through `_semitone_of()`.
_KEY_SEMITONES = score_audition.PIANO_KEY_SEMITONES


def _semitone_of(key):
    """The key's semitone *within its own octave*, or None if this key is
    not on the tracker keyboard. Folded modulo 12 because a row template
    already says which octave it draws -- `score_audition`'s own table
    numbers the upper row 12-23, which is the right answer for its
    two-octave-from-one-base question and the wrong one for a per-row
    template."""
    semitone = _KEY_SEMITONES.get(key)
    return None if semitone is None else semitone % 12


@dataclass(frozen=True)
class KeySlot:
    """One playable cell of the input layer.

    `key` is the physical key token as `kitty_keys.KeyEvent.key` reports
    it -- always the lowercase/unshifted form, because a QWERTY piano
    binds physical positions and a release event must produce the same
    token as its own press.
    """

    key: str
    kind: str
    value: int          # MIDI pitch when kind == NOTE; pad index when kind == PAD
    row: int
    col: int

    def midi_key(self, octave_shift=0):
        """The MIDI key this slot sends. Note slots transpose with the
        live octave shift; pads never do -- a kick is a kick, and shifting
        a kit's keys would silently point every pad at a different zone."""
        if self.kind == NOTE:
            return self.value + 12 * octave_shift
        if self.kind == PAD:
            return pad_midi_key(self.value)
        return None

    def channel(self):
        return PAD_CHANNEL if self.kind == PAD else NOTE_CHANNEL


def pad_midi_key(index):
    """Pad index (0-based) -> the MIDI key its kit zone lives at."""
    return PAD_BASE_KEY + int(index)


def pad_index_for_key(midi_key):
    """The inverse of `pad_midi_key()`; negative for a key below the pad
    range, which simply means "no pad plays this"."""
    return int(midi_key) - PAD_BASE_KEY


@dataclass
class Layout:
    """A named set of `KeySlot`s plus the row/column geometry to draw them
    in. `builtin` layouts are constructed fresh on every call (never
    shared/mutated); a custom one carries the `path` it loads from and
    saves back to."""

    name: str
    slots: list = field(default_factory=list)
    builtin: bool = True
    path: str = None

    # -- lookup ---------------------------------------------------------

    def slot_for(self, key):
        """The slot a pressed key token plays, or None. Case-insensitive
        on the key token itself so a caller that only has `poll()`'s
        legacy token (which is whatever character the terminal produced)
        still resolves -- `Shift`+letter is an *action* in this tool, and
        actions are resolved before this is ever reached."""
        if not key or len(key) != 1:
            return None
        key = key.lower()
        for slot in self.slots:
            if slot.key == key:
                return slot
        return None

    def keys(self):
        return [slot.key for slot in self.slots]

    @property
    def has_notes(self):
        return any(slot.kind == NOTE for slot in self.slots)

    @property
    def has_pads(self):
        return any(slot.kind == PAD for slot in self.slots)

    @property
    def is_dual(self):
        """True when this layout plays a synth patch *and* a kit at the
        same time (layout 2). #107's implementation note: that multiplies
        the voice budget, so `synth_tool.polyphony_for_layout()` selects a
        different `[preferences]` figure for it."""
        return self.has_notes and self.has_pads

    # -- geometry -------------------------------------------------------

    def grid(self):
        """`{row: {col: KeySlot}}` -- everything `synth_display.render()`
        needs to draw the input layer, with no geometry decisions left to
        the renderer."""
        out = {}
        for slot in self.slots:
            out.setdefault(slot.row, {})[slot.col] = slot
        return out

    def width(self):
        return max((slot.col for slot in self.slots), default=-1) + 1

    def height(self):
        return max((slot.row for slot in self.slots), default=-1) + 1

    # -- editing (the custom layout) ------------------------------------

    def rebind(self, key, kind, value):
        """Replaces the slot at `key` with a new binding, keeping its
        row/col so the input layer's shape never shifts under the user's
        hands mid-edit. Returns the new slot, or None if `key` isn't part
        of this layout at all (a layout's *shape* is fixed by whichever
        built-in it was copied from; #107 scopes custom layouts to
        rebinding keys, not to inventing new rows)."""
        for i, slot in enumerate(self.slots):
            if slot.key == key:
                kind = kind if kind in KINDS else UNBOUND
                new = KeySlot(slot.key, kind, int(value), slot.row, slot.col)
                self.slots[i] = new
                return new
        return None

    def copy(self, name=None, builtin=False, path=None):
        return Layout(name=name or self.name, slots=list(self.slots), builtin=builtin, path=path)


# --------------------------------------------------------------------------
# Built-ins
# --------------------------------------------------------------------------

def _slots_from_template(template, octave, row):
    """One row template -> its `KeySlot`s. The template supplies the
    column each key is drawn in; `_semitone_of()` supplies the pitch, so
    a key the shared table doesn't know is simply skipped rather than
    silently bound to something invented here."""
    base = (octave + 1) * 12
    slots = []
    for col, ch in enumerate(template):
        if ch == " ":
            continue
        semitone = _semitone_of(ch)
        if semitone is None:
            continue
        slots.append(KeySlot(ch, NOTE, base + semitone, row, col))
    return slots


def _pad_slots(template, row, first_index):
    slots = []
    index = first_index
    for col, ch in enumerate(template):
        if ch == " ":
            continue
        slots.append(KeySlot(ch, PAD, index, row, col))
        index += 1
    return slots


# Row templates. Each character position is one display cell; a space is a
# gap. Deliberately written out literally rather than generated, so the
# physical keyboard shape is visible in the source.
_UPPER_BLACK = " 2 3   5 6 7 "
_UPPER_WHITE = "q w e r t y u"
_LOWER_BLACK = " s d   g h j "
_LOWER_WHITE = "z x c v b n m"
_PAD_ROW_8 = "z x c v b n m ,"
_PAD_SQUARE = ("1 2 3 4", "q w e r", "a s d f", "z x c v")


def two_octave_layout(base_octave=None):
    """Layout 1: two full octaves, no pads."""
    octave = config.SYNTH_BASE_OCTAVE if base_octave is None else base_octave
    slots = (
        _slots_from_template(_UPPER_BLACK, octave + 1, 0)
        + _slots_from_template(_UPPER_WHITE, octave + 1, 1)
        + _slots_from_template(_LOWER_BLACK, octave, 2)
        + _slots_from_template(_LOWER_WHITE, octave, 3)
    )
    return Layout("two-octave", slots)


def octave_pads_layout(base_octave=None):
    """Layout 2: one octave on the upper rows plus eight pads below. The
    only built-in that is `is_dual`."""
    octave = config.SYNTH_BASE_OCTAVE if base_octave is None else base_octave
    slots = (
        _slots_from_template(_UPPER_BLACK, octave + 1, 0)
        + _slots_from_template(_UPPER_WHITE, octave + 1, 1)
        + _pad_slots(_PAD_ROW_8, 3, 0)
    )
    return Layout("octave-pads", slots)


def pad_square_layout():
    """Layout 3: the 4x4 pad square. Pads are numbered **bottom row
    first** -- `zxcv` is pads 1-4 -- so a kit's low keys (kick, snare) sit
    where hardware puts them."""
    slots = []
    for display_row, template in enumerate(_PAD_SQUARE):
        # Bottom template is display row 3 and holds pads 0-3.
        first = (len(_PAD_SQUARE) - 1 - display_row) * 4
        slots.extend(_pad_slots(template, display_row, first))
    return Layout("pads", slots)


#: Built-in constructors in Tab-cycle order. The custom layout is appended
#: at runtime by `available_layouts()` when one exists on disk -- there is
#: no point cycling onto an empty fourth slot before the user has made one.
BUILTINS = (
    ("two-octave", two_octave_layout),
    ("octave-pads", octave_pads_layout),
    ("pads", pad_square_layout),
)

BUILTIN_NAMES = tuple(name for name, _ in BUILTINS)


def builtin(name, base_octave=None):
    """A fresh instance of the named built-in, or None."""
    for builtin_name, make in BUILTINS:
        if builtin_name == name:
            try:
                return make(base_octave)
            except TypeError:
                return make()
    return None


def available_layouts(custom_paths=None, base_octave=None):
    """Every layout Tab cycles through: the three built-ins, then each
    custom layout file found on disk. Loading a custom layout that fails
    to parse is skipped rather than fatal -- the same
    degrade-don't-crash posture `patch_format.load_patch()` takes."""
    layouts = [builtin(name, base_octave) for name in BUILTIN_NAMES]
    for path in (layout_paths() if custom_paths is None else custom_paths):
        try:
            layouts.append(load_layout(path))
        except (OSError, ValueError, tomllib.TOMLDecodeError):
            continue
    return layouts


def cycle_layout(index, count, step=1):
    """Tab's own arithmetic: wrap forward through `count` layouts. Pure so
    the wrap is tested without constructing anything."""
    if count <= 0:
        return 0
    return (int(index) + int(step)) % count


# --------------------------------------------------------------------------
# Custom layout files
# --------------------------------------------------------------------------

def _config_root():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "note-color")


def layouts_dir():
    return os.path.join(_config_root(), "layouts")


def layout_paths(directory=None):
    """Every `*.toml` layout file, sorted -- a flat, non-recursive glob,
    the same shape `patch_format.patch_paths()` and
    `score_editor_picker.score_file_paths()` already use."""
    directory = directory or layouts_dir()
    return sorted(glob.glob(os.path.join(directory, "*.toml")))


def layout_from_toml(data, path=None):
    """Parse a layout table. Every field optional with a default, unknown
    keys ignored, a bad value falling back rather than raising -- the
    posture `config_store.py` set and `patch_format.py` follows."""
    name = data.get("name")
    if not isinstance(name, str) or not name:
        name = os.path.splitext(os.path.basename(path))[0] if path else "custom"
    slots = []
    for entry in data.get("keys", []) or []:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if not isinstance(key, str) or len(key) != 1:
            continue
        kind = entry.get("kind")
        kind = kind if kind in KINDS else UNBOUND
        try:
            value = int(entry.get("value", 0))
        except (TypeError, ValueError):
            value = 0
        try:
            row = int(entry.get("row", 0))
            col = int(entry.get("col", 0))
        except (TypeError, ValueError):
            row, col = 0, 0
        slots.append(KeySlot(key.lower(), kind, value, max(0, row), max(0, col)))
    return Layout(name=name, slots=slots, builtin=False, path=path)


def load_layout(path):
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    return layout_from_toml(data, path)


def layout_to_toml(layout):
    lines = [f'name = "{layout.name}"', ""]
    for slot in layout.slots:
        lines.append("[[keys]]")
        lines.append(f'key = "{slot.key}"')
        lines.append(f'kind = "{slot.kind}"')
        lines.append(f"value = {slot.value}")
        lines.append(f"row = {slot.row}")
        lines.append(f"col = {slot.col}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def save_layout(layout, path=None):
    """Writes `layout` back to its own file (creating the layouts
    directory if needed) and returns the path written."""
    path = path or layout.path or os.path.join(layouts_dir(), f"{slugify(layout.name)}.toml")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(layout_to_toml(layout))
    layout.path = path
    layout.builtin = False
    return path


def slugify(name):
    """A filename-safe stem for a layout or patch name. Deliberately
    conservative: anything but a letter, digit, dash or underscore becomes
    a dash, so a name typed in the save overlay can never escape its own
    directory."""
    out = []
    for ch in (name or "").strip():
        out.append(ch if (ch.isalnum() or ch in "-_") else "-")
    stem = "".join(out).strip("-")
    return stem or "untitled"


def new_custom_from(layout, name="custom"):
    """#107 decision 4: a custom layout is *started by copying a
    built-in* rather than authored from a blank grid -- the built-ins are
    already the arrangements worth starting from, and an empty grid is
    the one starting point that makes no sound at all."""
    return layout.copy(name=name, builtin=False, path=None)


# --------------------------------------------------------------------------
# Pad colouring (#107 decision 1: "pads tint by their assigned sample")
# --------------------------------------------------------------------------

def sample_hue_step(sample_name):
    """A stable 0-11 wheel step for a sample name, so a pad's tint is a
    property of *what is on it* rather than of a pitch class it has no
    business claiming.

    Deliberately not Python's own `hash()`, which is randomised per
    process -- a pad that changed colour on every launch would be worse
    than no colour at all. A plain byte sum is enough: the requirement is
    "different samples usually look different and one sample always looks
    the same", not cryptographic spread.
    """
    if not sample_name:
        return None
    return sum(sample_name.encode("utf-8")) % 12
