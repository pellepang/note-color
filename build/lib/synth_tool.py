"""The standalone `synth` tool's non-rendering runtime (map #99, ticket
#119, implementing decision #107): key-to-action resolution, the engine
router that lets a kit and a synth patch sound at once, the inline
overlays for patch load/save and sample import, and the state object
`main.run_synth_tool()` drives.

Three things here are worth reading before the code:

**1. Every plain letter and number plays a note -- always.** That is the
property an instrument needs (#107 point 5), and it decides the whole key
map by elimination: parameters are arrow-driven, `Tab` cycles layouts
(nearly every other key plays a note in *some* layout, so the switch key
has to be one of the few that never does), and every remaining command is
`Shift`+key. Those `Shift` bindings are deliberately **not** remappable
through `config.toml`'s `[keybinds]`, unlike this app's nine live-view
binds and thirteen score-editor binds: a remap onto a plain letter would
silently break the always-plays invariant, and `settings_display`'s
existing reserved-key check has no way to express "any letter, ever". Same
tier as the score editor's own hardcoded Shift+Arrow transpose.

That rule covers `Shift`+S, the recording arm (ticket #122, decision
#110), even though the live views' own `session_record_toggle` *is*
remappable and #110 said this one would be too. The two are not the same
key in the same place: in `fill`/`wheel`/`tab` a letter is a command by
default and remapping one is safe, while here every letter is a note and
the reserved-key check cannot express that. Following #110's word to the
letter would put the invariant this whole tool is built on at the mercy of
one line of TOML; following #119's rule keeps `rec=`'s *meaning* identical
across all four views, which is what #110 was actually asking for.

**2. A kit and a synth patch sound simultaneously in layout 2**, which is
a *routing* question rather than a second code path: `ChannelRouter`
dispatches a `NoteOn` by its channel, pads playing on MIDI channel 9 (the
drum channel) and keys on channel 0. The voice manager's hard cap already
covers both engines' voices together -- so the thing #107's
implementation note actually warns about is not overrun but *starvation*:
one budget now feeds two hands. `polyphony_for_layout()` is that warning
made concrete, selecting a separate, lower `[preferences]` figure while a
dual layout is active.

**3. Load, save and import are inline overlays, never screens** (#107
point 6). Changing sounds is the single most common thing done on a
synth, so it must not mean leaving the instrument -- and a separate
screen is the exact shape hands-on feedback already rejected once on the
score editor's Score properties screen. `Overlay` here is a plain list
cursor plus a typed buffer; `synth_display.py` draws it *over* the
parameter panel, with the input layer still visible and still playing
underneath.

One consequence of (1) worth stating outright: in the **save** overlay,
typing a patch name both appends the character and sounds the note. That
is not an oversight -- it is the always-plays invariant holding even
where a text field would normally claim the keyboard. There is no state
in this tool in which a letter goes silent.

Everything in this module is pure or plain file I/O and is directly
unit-tested; the render loop, the terminal and the audio device all live
in `main.run_synth_tool()` and `synth_display.py`, per this repo's
"pure logic unit-tested, real I/O smoke-tested" convention.
"""

from __future__ import annotations

import os

import config
import kitty_keys
import patch_format
import synth_layout
import synth_params
from config_store import store
from synth_layout import NOTE, PAD, UNBOUND

# --------------------------------------------------------------------------
# Actions (#107 point 5: arrows for parameters, Tab for layouts, Shift for
# everything else, every plain key for notes)
# --------------------------------------------------------------------------

#: `Shift`+letter -> action. The escape hatch, not a mode: none of these
#: takes the keyboard away from playing, and each is reachable at any
#: moment including mid-note.
SHIFT_ACTIONS = {
    "P": "patch_browse",     # open/close the patch browser overlay
    "W": "patch_save",       # open the save overlay (name typed, notes still sound)
    "I": "sample_import",    # open the sample-import browser overlay
    "B": "bind_kind",        # custom layout: cycle the last-played key's kind
    "L": "layout_save",      # custom layout: write it to its own file
    "N": "layout_new",       # start a custom layout by copying the active one
    "M": "panic",            # all notes off -- the stuck-note escape
    "H": "help_toggle",      # the help legend ('h' itself plays a note here)
    "S": "record_toggle",    # arm/disarm session recording (ticket #122, decision #110)
}

#: Bare punctuation that no layout binds, so it can carry an action
#: without touching the always-plays rule. `<`/`>` nudge the last-played
#: key's binding while a custom layout is active.
PUNCT_ACTIONS = {
    "<": "bind_down",
    ">": "bind_up",
    "|": "menu",
}

# Overlay kinds.
OVERLAY_PATCH = "patch"
OVERLAY_SAVE = "save"
OVERLAY_SAMPLE = "sample"


def typed_text(event):
    """What the terminal says this keystroke produced, falling back to the
    key token when it says nothing.

    With the kitty protocol active every key arrives as an escape sequence
    carrying both a layout-position `key` and the `text` it would have
    typed (`kitty_keys.SYNTH_FLAGS` includes FLAG_ASSOCIATED_TEXT for
    exactly this reason); without it, `main.RawKeys._synthetic_press()`
    has already put the raw character in `key` and set MOD_SHIFT for an
    uppercase one. Reading `text` first and reconstructing from
    `key`+Shift second is what makes one action table work on both
    paths."""
    if event is None:
        return ""
    if event.text:
        return event.text
    key = event.key or ""
    if len(key) == 1 and (event.mods & kitty_keys.MOD_SHIFT):
        return key.upper()
    return key if len(key) == 1 else ""


def resolve_action(event, overlay=None):
    """Pure: one `kitty_keys.KeyEvent` -> an action name, or None when the
    event is not a command (in which case the caller looks the key up in
    the layout and plays it).

    `overlay` re-points the arrow keys while an overlay is open: Up/Down
    walk the overlay's list instead of the parameter list, and Enter/Esc
    confirm/cancel. Nothing else changes -- notes still sound throughout,
    which is the whole reason overlays are inline rather than screens."""
    if event is None or event.event == kitty_keys.RELEASE:
        return None
    key = event.key
    shift = bool(event.mods & kitty_keys.MOD_SHIFT)

    if key == "TAB":
        return "layout_cycle"
    if key == "ESC":
        return "overlay_cancel"
    if key == "ENTER":
        return "overlay_confirm" if overlay else None
    if key == "BACKSPACE":
        return "overlay_backspace" if overlay else None

    if key == "UP":
        if overlay:
            return "overlay_prev"
        # Shift is this tool's escape hatch (#107 point 5), and the
        # octave shift is the one piece of state that has nowhere else to
        # live: every plain letter is a note, so the transpose cannot
        # have a letter of its own. Same tier as the score editor's own
        # hardcoded Shift+Arrow transpose.
        return "octave_up" if shift else "param_prev"
    if key == "DOWN":
        if overlay:
            return "overlay_next"
        return "octave_down" if shift else "param_next"
    if key == "LEFT":
        if overlay:
            return "overlay_back"
        return "param_dec_coarse" if shift else "param_dec"
    if key == "RIGHT":
        if overlay:
            # Symmetrical with Left's "up a directory": Right descends
            # into the highlighted one, the same Left/Right pairing
            # `prototypes_display.py` already uses for its README view.
            return "overlay_forward"
        return "param_inc_coarse" if shift else "param_inc"

    text = typed_text(event)
    if text in PUNCT_ACTIONS:
        return PUNCT_ACTIONS[text]
    if len(text) == 1 and text.isalpha() and text.isupper():
        return SHIFT_ACTIONS.get(text)
    return None


# --------------------------------------------------------------------------
# Engine routing (#107's implementation note)
# --------------------------------------------------------------------------

class ChannelRouter:
    """A `sound_engine.Engine` that dispatches by MIDI channel: pads
    (channel 9) to the kit's sampler engine, everything else to the synth
    engine.

    This is what makes "layout 2 plays a kit and a synth patch at once" a
    two-line routing rule rather than a second sound path. It is also
    exactly the shape a MIDI device plugs into later (map #99's standing
    "MIDI-shaped from day one" rule) -- a controller sending on channel 10
    already lands on the drums with nothing further to write.

    A missing engine yields `sampler.SilentVoice`, never `None`: the
    `Engine` Protocol promises a `Voice`, and a null return would push a
    `None` check into the voice manager."""

    def __init__(self, note_engine=None, pad_engine=None):
        self.note_engine = note_engine
        self.pad_engine = pad_engine

    def engine_for(self, channel):
        if channel == synth_layout.PAD_CHANNEL:
            return self.pad_engine
        return self.note_engine

    def note_on(self, event, sample_rate):
        engine = self.engine_for(event.channel)
        if engine is None:
            from sampler import SilentVoice

            return SilentVoice()
        return engine.note_on(event, sample_rate)


def polyphony_for_layout(layout):
    """The live voice budget for this layout (#107's implementation note).

    A dual layout feeds two engines from one hard cap, so the risk is a
    drum hit arriving to find every slot held by sustained synth notes --
    starvation, not overrun. A separate, lower `[preferences]` figure
    keeps a margin for it; every other layout uses the ordinary standalone
    budget. Read through `config_store` on each call, so a Settings-screen
    edit applies to the very next note."""
    if layout is not None and layout.is_dual:
        return store.preference("polyphony_synth_dual", config.POLYPHONY_SYNTH_DUAL)
    return store.preference("polyphony_standalone", config.POLYPHONY_STANDALONE)


# --------------------------------------------------------------------------
# Inline overlays (#107 point 6)
# --------------------------------------------------------------------------

class Overlay:
    """A list cursor plus a typed buffer -- everything all three overlays
    need, and nothing more. Rendered over the parameter panel by
    `synth_display.py`; the input layer stays visible and playable
    underneath it."""

    def __init__(self, kind, entries=(), title="", buffer="", directory=None):
        self.kind = kind
        self.entries = list(entries)
        self.index = 0
        self.title = title
        self.buffer = buffer
        self.directory = directory

    @property
    def current(self):
        if not self.entries or not (0 <= self.index < len(self.entries)):
            return None
        return self.entries[self.index]

    def move(self, delta):
        """Clamped, like every other list cursor in this app's screens."""
        if not self.entries:
            self.index = 0
        else:
            self.index = max(0, min(len(self.entries) - 1, self.index + int(delta)))
        return self.index

    def append(self, text):
        self.buffer += text
        return self.buffer

    def backspace(self):
        self.buffer = self.buffer[:-1]
        return self.buffer


def patch_entries(directory=None):
    """`(label, path)` per patch file, for the browser overlay. The label
    carries the engine so a kit is distinguishable from a synth sound
    without opening it -- which matters here, since loading a patch routes
    it by engine (a sampler patch becomes the pads' kit, a synth patch the
    keys' sound) rather than replacing whatever was loaded before."""
    out = []
    for path in patch_format.patch_paths(directory):
        try:
            patch = patch_format.load_patch(path)
            label = f"{patch.name}  [{patch.engine}]"
        except (OSError, ValueError):
            label = f"{patch_format.patch_name_for_path(path)}  [unreadable]"
        out.append((label, path))
    return out


def sample_entries(directory):
    """One directory's worth of the sample-import browser: a `..` row, then
    sub-directories, then `.wav` files. Sorted within each group, so the
    list is stable and predictable rather than filesystem-ordered.

    The browser *starts* in `patch_format.samples_dir()` -- decision #107's
    "the samples directory listed first" -- and `..` is what makes the rest
    of the filesystem reachable from there, rather than a second mode."""
    entries = [("..", os.path.dirname(os.path.abspath(directory)), "dir")]
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return entries
    dirs, files = [], []
    for name in names:
        if name.startswith("."):
            continue
        full = os.path.join(directory, name)
        if os.path.isdir(full):
            dirs.append((name + "/", full, "dir"))
        elif name.lower().endswith(".wav"):
            files.append((name, full, "wav"))
    return entries + dirs + files


def assign_sample_to_pad(kit, pad_index, sample_name):
    """Points a pad at a sample: replaces the one-key-wide zone at that
    pad's MIDI key, or appends one if the pad was empty.

    One key wide because that is exactly what a **Kit** is
    (`patch_format.Patch.is_kit()`) -- the pad grid is a *view* onto a
    sampler patch, per map #99's standing decision, not a separate model
    with its own storage. `root_key` is set to the pad's own key so the
    sample plays back untransposed: a drum hit is a recording to be
    triggered, not a pitch to be tuned."""
    key = synth_layout.pad_midi_key(pad_index)
    for zone in kit.zones:
        if zone.low_key == key and zone.high_key == key:
            zone.sample = sample_name
            return zone
    zone = patch_format.Zone(sample=sample_name, low_key=key, high_key=key, root_key=key)
    kit.zones.append(zone)
    return zone


def zone_for_pad(kit, pad_index):
    """The zone a pad triggers, or None -- what the input layer tints
    itself by (#107 point 1: "pads tint by their assigned sample")."""
    if kit is None:
        return None
    key = synth_layout.pad_midi_key(pad_index)
    return patch_format.select_zone(kit.zones, key, 127)


# --------------------------------------------------------------------------
# Tool state
# --------------------------------------------------------------------------

class SynthToolState:
    """Everything the synth tool remembers between keystrokes, with the
    audio device and the terminal deliberately left out -- those belong to
    `main.run_synth_tool()`, so every state transition below is testable
    with no hardware and no TTY.

    `patch` is the sound the *keys* play and `kit` the sound the *pads*
    play; loading a patch routes it to one or the other by its own
    `engine` field, which is why decision #106's "one file kind, not
    three" matters here in practice -- there is one browser, one Enter,
    and no "which slot am I loading into?" question to ask the user."""

    def __init__(self, patch=None, kit=None, layouts=None):
        self.layouts = list(layouts) if layouts is not None else synth_layout.available_layouts()
        if not self.layouts:
            self.layouts = [synth_layout.two_octave_layout()]
        self.layout_index = 0
        self.octave_shift = 0
        self.patch = patch if patch is not None else _default_synth_patch()
        self.kit = kit
        self.patch_path = None
        self.param_index = 0
        self.overlay = None
        self.last_key = None          # the key slot most recently played -- the bind target
        self.help_on = True
        self.message = ""
        self.patch_dirty = False
        self.layout_dirty = False

    # -- layouts ---------------------------------------------------------

    @property
    def layout(self):
        return self.layouts[self.layout_index]

    def cycle_layout(self, step=1):
        self.layout_index = synth_layout.cycle_layout(self.layout_index, len(self.layouts), step)
        self.param_index = 0
        return self.layout

    def shift_octave(self, delta):
        """Clamped, not wrapped: an octave shift that silently wrapped
        from +3 to -3 would move the whole keyboard six octaves under the
        player's hands."""
        limit = config.SYNTH_OCTAVE_SHIFT_MAX
        self.octave_shift = max(-limit, min(limit, self.octave_shift + int(delta)))
        return self.octave_shift

    def new_custom_layout(self, name=None):
        """#107 point 4: a custom layout starts as a copy of a built-in.
        The copy is appended and selected immediately, so the very next
        keypress is already editing it."""
        base = self.layout
        name = name or f"custom-{len(self.layouts)}"
        custom = synth_layout.new_custom_from(base, name)
        self.layouts.append(custom)
        self.layout_index = len(self.layouts) - 1
        self.layout_dirty = True
        return custom

    def bind_target(self):
        """The key the custom-layout bindings act on: whichever key was
        played last. "Point at a key" is literally "play the key" here --
        which keeps arrows free for parameters, needs no mode, and lets
        the user hear what is currently bound before changing it."""
        if self.last_key is None:
            return None
        return self.layout.slot_for(self.last_key)

    def cycle_bind_kind(self):
        """note -> pad -> unbound -> note for the bind target. Returns the
        new slot, or None when there is nothing to rebind (no key played
        yet, or the active layout is a built-in -- built-ins are never
        edited in place, since `Shift`+N is one press away)."""
        slot = self.bind_target()
        if slot is None or self.layout.builtin:
            return None
        order = (NOTE, PAD, UNBOUND)
        kind = order[(order.index(slot.kind) + 1) % len(order)] if slot.kind in order else NOTE
        value = slot.value if kind == slot.kind else (60 if kind == NOTE else 0)
        self.layout_dirty = True
        return self.layout.rebind(slot.key, kind, value)

    def nudge_bind_value(self, delta):
        """One semitone (note) or one pad (pad) on the bind target."""
        slot = self.bind_target()
        if slot is None or self.layout.builtin or slot.kind == UNBOUND:
            return None
        low, high = (0, 127) if slot.kind == NOTE else (0, 127)
        value = max(low, min(high, slot.value + int(delta)))
        self.layout_dirty = True
        return self.layout.rebind(slot.key, slot.kind, value)

    def save_layout(self):
        if self.layout.builtin:
            return None
        path = synth_layout.save_layout(self.layout)
        self.layout_dirty = False
        return path

    # -- parameters ------------------------------------------------------

    def panel_patch(self):
        """Which patch the parameter panel edits.

        A pads-only layout with a kit loaded edits the *kit* -- editing an
        oscillator you cannot currently play would be a panel about
        nothing. Every other case edits the keys' patch, including a dual
        layout: the keys are the half with knobs worth sweeping, and the
        pads' sound is changed by importing a different sample onto them."""
        if self.kit is not None and self.layout.has_pads and not self.layout.has_notes:
            return self.kit
        return self.patch

    def specs(self):
        return synth_params.specs_for(self.panel_patch())

    def selected_spec(self):
        specs = self.specs()
        if not specs:
            return None
        self.param_index = max(0, min(len(specs) - 1, self.param_index))
        return specs[self.param_index]

    def move_param(self, delta):
        self.param_index = synth_params.move_selection(self.param_index, len(self.specs()), delta)
        return self.param_index

    def adjust_param(self, direction, coarse=False):
        spec = self.selected_spec()
        if spec is None:
            return None
        value = synth_params.adjust(self.panel_patch(), spec, direction, coarse)
        self.patch_dirty = True
        return value

    # -- patches ---------------------------------------------------------

    def set_patch(self, patch, path=None):
        """Routes a loaded patch by its own engine (#106's "one file kind"
        paying off): a sampler patch becomes the pads' kit, anything else
        the keys' sound."""
        if patch.engine == "sampler":
            self.kit = patch
        else:
            self.patch = patch
            self.patch_path = path
        self.param_index = 0
        self.patch_dirty = False
        return patch

    def save_patch_as(self, name, directory=None):
        """Writes the panel's current patch under `name`. Returns the path
        written. The name is slugified for the filename but kept verbatim
        as the patch's own `name` field, so a patch called "Fat bass 2"
        stays that in the browser while living in `fat-bass-2.toml`."""
        patch = self.panel_patch()
        name = (name or "").strip() or patch.name or "Untitled"
        patch.name = name
        directory = directory or patch_format.patches_dir()
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{synth_layout.slugify(name)}.toml")
        patch_format.save_patch(patch, path)
        if patch is self.patch:
            self.patch_path = path
        self.patch_dirty = False
        return path

    # -- overlays --------------------------------------------------------

    def open_overlay(self, kind, directory=None):
        if kind == OVERLAY_PATCH:
            self.overlay = Overlay(kind, patch_entries(), "Load patch")
        elif kind == OVERLAY_SAVE:
            self.overlay = Overlay(kind, (), "Save patch as", buffer=self.panel_patch().name)
        elif kind == OVERLAY_SAMPLE:
            directory = directory or patch_format.samples_dir()
            os.makedirs(directory, exist_ok=True)
            self.overlay = Overlay(kind, sample_entries(directory), "Import sample", directory=directory)
        return self.overlay

    def close_overlay(self):
        self.overlay = None

    def toggle_overlay(self, kind):
        if self.overlay is not None and self.overlay.kind == kind:
            self.close_overlay()
            return None
        return self.open_overlay(kind)

    def overlay_enter_directory(self, directory):
        self.overlay = Overlay(OVERLAY_SAMPLE, sample_entries(directory), "Import sample",
                               directory=directory)
        return self.overlay

    # -- status ----------------------------------------------------------

    def pad_index_for_slot(self, slot):
        return slot.value if slot is not None and slot.kind == PAD else None


def _default_synth_patch():
    """The tool's starting sound. Imported locally so this module stays
    importable without SciPy -- `synth_engine.default_patch()` itself only
    touches `patch_format`, but keeping the import inside the function
    means a test (or a machine without the `[synth]` extra) can exercise
    every state transition here regardless."""
    try:
        from synth_engine import default_patch

        return default_patch()
    except Exception:
        return patch_format.new_patch(name="Init")
