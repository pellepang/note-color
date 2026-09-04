"""Score editor audition, piano-mode note entry, playback and metronome
(wayfinder map #99, build ticket #120, implementing decision #108).

Everything in this module is pure, side-effect-free logic over plain
data -- the two-octave keyboard map, the piano-mode state machine, the
press-together/press-in-sequence chord grouping, the beat schedule a
playhead walks, the metronome's click grid, and the loop-region
arithmetic. `main.run_score_editor()` owns every side effect (the
terminal, the render loop, the `sound_engine.SoundEngine`), exactly the
"pure logic unit-tested, interactive loop smoke-tested" split
`score_editor_display.py` / `rhythm_reanalysis.py` already follow.

The one place this module touches audio at all is the two thin
`sound_*` helpers at the bottom, which take an engine object and issue
note-ons against it. They are deliberately thin enough to unit-test
against a fake engine that records calls, so no audio device is needed
to prove the editor asks for the right notes for the right lengths.

Design decisions this implements (settled in #108, not re-opened here):

* **Piano entry is a mode.** The editor already binds `r i x u U z c w t
  , .` as commands, so letters cannot simultaneously be notes. A key
  enters piano mode; letters become the two-octave tracker keyboard
  (`PIANO_LOWER_ROW`/`PIANO_UPPER_ROW`, the same layout the standalone
  synth tool uses); the same key or Esc leaves. Deliberately opposite to
  the synth tool's mode-free design: there playing is the job, here
  editing is.
* **Press together = one column (a chord); press in sequence =
  successive columns.** That distinction is only observable with the
  kitty protocol's key-release events (#118), so `PianoEntry` takes an
  `advance_between_groups` flag: True with releases available, False
  without -- and False is exactly #108's stated degraded path, "place
  notes in one column without advancing", with the arrow keys moving on.
* **Duration comes from the editor's `,`/`.` setting**, never from how
  long a key was held -- so nothing here measures hold time at all.
* **Playback is expressed in beats, not seconds.** A schedule entry
  carries an absolute `start_beat` measured from the score's first
  column, which is what makes the metronome's bar grid line up with the
  score's own barlines no matter which column playback started from.
  Seconds only appear at the edge, where a tempo is applied.
"""

from collections import namedtuple

import config
from duration_tracker import DEFAULT_DURATION_CLASS, beats_for_duration_class

# --------------------------------------------------------------------------
# The two-octave tracker keyboard
# --------------------------------------------------------------------------

#: Lower octave: the `zsxdcvgbhnjm` home/bottom rows, white keys on the
#: bottom row and black keys on the row above, exactly as a tracker lays
#: them out. Upper octave: `q2w3er5t6y7u`, the same shape one row up.
#: These are *layout positions*, matched against `kitty_keys.KeyEvent.key`
#: (which is normalised lowercase and modifier-independent), so a release
#: always maps to the same note its own press did.
PIANO_LOWER_ROW = "zsxdcvgbhnjm"
PIANO_UPPER_ROW = "q2w3er5t6y7u"

PIANO_KEY_SEMITONES = {}
for _semitone, _key in enumerate(PIANO_LOWER_ROW):
    PIANO_KEY_SEMITONES[_key] = _semitone
for _semitone, _key in enumerate(PIANO_UPPER_ROW):
    PIANO_KEY_SEMITONES[_key] = _semitone + 12
del _semitone, _key


def pitch_for_key(key, base_octave):
    """(pitch_class, octave) for a piano-mode key, or None if this key
    isn't on the keyboard at all. `base_octave` is the octave the lower
    row's leftmost key (`z`, a C) sounds in; the upper row is one octave
    above it. Never clamps -- `main` decides what to do with a pitch
    outside the staff, and clamping here would silently transpose a note
    the user asked for."""
    semitone = PIANO_KEY_SEMITONES.get(key)
    if semitone is None:
        return None
    combined = base_octave * 12 + semitone
    return combined % 12, combined // 12


def clamp_base_octave(octave):
    """Keeps the keyboard's lower octave inside this app's own pitch
    range, leaving room for the upper row's octave above it -- the same
    `config.MIN_OCTAVE`/`MAX_OCTAVE` bounds the detection path uses, so a
    typed note can always be spelled by `staff_map.staff_row()`."""
    return max(config.MIN_OCTAVE, min(config.MAX_OCTAVE - 1, octave))


# --------------------------------------------------------------------------
# The mode state machine
# --------------------------------------------------------------------------

EDIT_MODE = "edit"
PIANO_MODE = "piano"


def toggle_mode(mode):
    """The `piano_mode` keybind (and Esc, which only ever leaves)."""
    return EDIT_MODE if mode == PIANO_MODE else PIANO_MODE


def is_piano_note_event(mode, key, mods):
    """True when this keystroke should sound/place a note rather than run
    an editor command. Only ever in piano mode, only for a key that is on
    the keyboard, and only with **no modifier held** -- that last rule is
    load-bearing, not defensive: `m` is a note (B) and `Shift`+`M` is the
    metronome toggle, so a modified press must fall through to the normal
    keybind dispatch or the two would be indistinguishable. Caps Lock and
    Num Lock are ignored, since neither is a deliberate chord."""
    if mode != PIANO_MODE or key not in PIANO_KEY_SEMITONES:
        return False
    import kitty_keys

    deliberate = mods & ~(kitty_keys.MOD_CAPS_LOCK | kitty_keys.MOD_NUM_LOCK)
    return deliberate == 0


# --------------------------------------------------------------------------
# Press-together / press-in-sequence grouping (#108 decision 2)
# --------------------------------------------------------------------------

SAME_COLUMN = "same_column"
NEW_COLUMN = "new_column"


class PianoEntry:
    """Turns a stream of key presses and releases into "this note joins
    the column being built" vs "this note starts the next column".

    The rule is the one a musician expects untaught: while any piano key
    is still down, further presses are part of the same chord; once every
    key has come up, the next press starts a new column and the cursor
    advances onto it. The very first press after entering piano mode (or
    after the caller moves the cursor itself) lands in the column the
    cursor is already on rather than skipping one -- `reset()` is how the
    caller says so.

    `advance_between_groups=False` is the degraded path for a terminal
    with no key-release reporting (#108: "place notes in one column
    without advancing"): every press joins the current column, and the
    arrow keys are how the user moves on. Without releases there is no
    honest way to tell a chord from a sequence, and guessing by a timing
    heuristic would silently mangle either fast melodies or slow chords.
    """

    def __init__(self, advance_between_groups=True):
        self.advance_between_groups = advance_between_groups
        self._held = set()
        self._group_open = False

    @property
    def held(self):
        return set(self._held)

    def reset(self):
        """Forget the current group -- called when the caller moves the
        cursor, leaves piano mode, or starts playback, so the next press
        lands where the cursor now is instead of one column past it."""
        self._held.clear()
        self._group_open = False

    def press(self, key):
        """Returns SAME_COLUMN or NEW_COLUMN for this press."""
        if not self.advance_between_groups:
            # Degraded path: with no releases to observe, `_held` only
            # ever records the key just pressed (there is nothing that
            # could ever take one out of it), so the caller still sees a
            # freshly-started group -- and every press joins the current
            # column, with the arrows moving on.
            self._held = {key}
            return SAME_COLUMN
        if self._held or not self._group_open:
            self._held.add(key)
            self._group_open = True
            return SAME_COLUMN
        self._held.add(key)
        return NEW_COLUMN

    def release(self, key):
        """Note a key coming up. Returns True when that emptied the
        group (the next press will start a new column)."""
        self._held.discard(key)
        return not self._held


# --------------------------------------------------------------------------
# Audition targets
# --------------------------------------------------------------------------

def audition_targets(action, column, row):
    """Which notes a cursor movement should sound, as a list of
    (pitch_class, octave) -- empty for "sound nothing".

    Left/Right moves onto a whole column, so the column's full chord
    sounds; that is the movement melody-proofreading actually uses.
    Up/Down moves between staff rows *within* a column, so it sounds only
    a note genuinely sitting on the row moved onto -- arrowing through
    empty staff rows stays silent, since sounding the pitch of every
    empty row would make vertical navigation a siren rather than
    feedback."""
    if action in ("LEFT", "RIGHT"):
        return [(note.pitch_class, note.octave) for note in column.notes]
    if action in ("UP", "DOWN"):
        from score_editor_display import note_index_at_row

        index = note_index_at_row(column, row)
        if index is None:
            return []
        note = column.notes[index]
        return [(note.pitch_class, note.octave)]
    return []


# --------------------------------------------------------------------------
# Playback: the beat schedule, the playhead, the loop region
# --------------------------------------------------------------------------

#: One column's slot on the playback timeline. `start_beat` is absolute
#: (measured from the score's own first column) rather than relative to
#: wherever playback began, which is what keeps the metronome's bar grid
#: aligned with the score's barlines regardless of the start column.
ScheduledColumn = namedtuple("ScheduledColumn", "index start_beat beats")


def build_schedule(columns):
    """Every column's absolute beat position, in order. A "beat" is a
    quarter note here, the same unit `duration_tracker.py` and
    `score_writer.QUARTER_LENGTHS` already use throughout this repo."""
    schedule = []
    beat = 0.0
    for index, column in enumerate(columns):
        beats = beats_for_duration_class(column.duration_class)
        schedule.append(ScheduledColumn(index, beat, beats))
        beat += beats
    return schedule


def playback_range(num_columns, cursor_col, loop_range=None):
    """The inclusive (start_col, end_col) playback covers, or None when
    there is nothing to play.

    Without a loop region: from the cursor to the last column, which is
    what "play from cursor" means. With one marked (`[`/`]`): playback
    ends at the region's end, and starts at the cursor when the cursor is
    inside the region -- so a marked section can still be picked up from
    the middle -- but from the region's start when the cursor sits
    outside it, since starting before or after a marked region and
    stopping at its end is not a thing anyone asked for."""
    if num_columns <= 0:
        return None
    cursor_col = max(0, min(num_columns - 1, cursor_col))
    if loop_range is None:
        return cursor_col, num_columns - 1
    lo, hi = loop_range
    lo = max(0, min(num_columns - 1, lo))
    hi = max(0, min(num_columns - 1, hi))
    if lo > hi:
        lo, hi = hi, lo
    start = cursor_col if lo <= cursor_col <= hi else lo
    return start, hi


def schedule_slice(schedule, start_col, end_col):
    """The entries of `schedule` for columns [start_col, end_col]."""
    return [entry for entry in schedule if start_col <= entry.index <= end_col]


def playhead_index(entries, elapsed_beats):
    """Which column is sounding `elapsed_beats` (absolute, same origin as
    `ScheduledColumn.start_beat`) into playback, or None once playback
    has run past the last entry -- which is how the caller knows to
    stop."""
    for entry in entries:
        if entry.start_beat <= elapsed_beats < entry.start_beat + entry.beats:
            return entry.index
    if entries and elapsed_beats < entries[0].start_beat:
        return entries[0].index
    return None


def due_entries(entries, previous_beats, now_beats):
    """Schedule entries whose onset falls in (previous_beats,
    now_beats] -- the ones to trigger on this frame. Half-open at the
    bottom so a frame boundary can never fire the same column twice, and
    inclusive at the top so a column whose onset lands exactly on a frame
    still sounds. Pass `previous_beats=-inf` on the very first frame to
    catch a column starting exactly at the playback origin."""
    return [entry for entry in entries if previous_beats < entry.start_beat <= now_beats]


def beats_to_seconds(beats, tempo_bpm):
    """A beat is a quarter note, `tempo_bpm` is quarter notes per
    minute -- the same convention `score_editor_state.EditorScore.
    tempo_bpm` carries and `music21`'s MetronomeMark writes out."""
    if tempo_bpm <= 0:
        tempo_bpm = 1.0
    return beats * 60.0 / tempo_bpm


def seconds_to_beats(seconds, tempo_bpm):
    if tempo_bpm <= 0:
        tempo_bpm = 1.0
    return seconds * tempo_bpm / 60.0


def duration_seconds(duration_class, tempo_bpm):
    """How long a column of this duration class actually sounds -- what
    audition and playback hand `SoundEngine.schedule_note_off()`."""
    return beats_to_seconds(beats_for_duration_class(duration_class), tempo_bpm)


# --------------------------------------------------------------------------
# The metronome (#108 decision 5)
# --------------------------------------------------------------------------

def beat_grid(time_signature):
    """(click_beats, clicks_per_bar) for a time signature, in quarter-note
    beats. 4/4 clicks once a quarter, four to a bar; 6/8 clicks once an
    eighth, six to a bar -- the denominator names the note value that
    gets the click, exactly as it does on paper."""
    numerator, denominator = time_signature
    denominator = denominator if denominator > 0 else 4
    numerator = numerator if numerator > 0 else 4
    return 4.0 / denominator, numerator


def metronome_clicks(origin_beat, end_beat, time_signature):
    """Every (beat, is_downbeat) click between `origin_beat` (inclusive)
    and `end_beat` (exclusive), on the score's own absolute beat grid --
    so a playback started mid-bar still gets its downbeat where the bar
    genuinely falls, rather than treating wherever the cursor happened to
    sit as beat one."""
    click_beats, clicks_per_bar = beat_grid(time_signature)
    clicks = []
    n = int(origin_beat / click_beats)
    while n * click_beats < origin_beat - 1e-9:
        n += 1
    while True:
        beat = n * click_beats
        if beat >= end_beat - 1e-9:
            break
        clicks.append((beat, n % clicks_per_bar == 0))
        n += 1
    return clicks


def due_clicks(clicks, previous_beats, now_beats):
    """The same (previous, now] window `due_entries()` uses, for clicks."""
    return [click for click in clicks if previous_beats < click[0] <= now_beats]


# --------------------------------------------------------------------------
# The thin audio edge
# --------------------------------------------------------------------------

def sound_notes(engine, notes, seconds, velocity=None):
    """Sound `notes` (an iterable of (pitch_class, octave)) for `seconds`
    through a `sound_engine.SoundEngine`, and return the voice ids.

    A no-op returning [] when `engine` is None -- which is the whole
    degradation story for a machine with no audio device or without the
    `[synth]` extra installed: the editor stays fully usable and simply
    makes no sound, rather than refusing to open.

    Note-offs go through `schedule_note_off()` rather than a timer here,
    so their timing is counted by the audio callback's own frame clock
    (#112) and this function never blocks the render loop."""
    if engine is None:
        return []
    import sound_engine

    velocity = config.EDITOR_AUDITION_VELOCITY if velocity is None else velocity
    voice_ids = []
    for pitch_class, octave in notes:
        event = sound_engine.NoteOn.from_pitch_class(pitch_class, octave, velocity=velocity)
        voice_id = engine.note_on(event)
        engine.schedule_note_off(voice_id, max(seconds, 0.0))
        voice_ids.append(voice_id)
    return voice_ids


def sound_metronome_click(engine, is_downbeat):
    """One metronome click: a short, high, fixed pitch, a fifth higher on
    a downbeat so bar one is audibly distinct without needing a second
    sound source. Deliberately synthesised through the same engine as
    every other sound in this app rather than a bundled click sample --
    there is no sample library here, and map #99's sampler is about the
    user's own WAVs."""
    if engine is None:
        return None
    import sound_engine

    pitch = (config.EDITOR_METRONOME_DOWNBEAT_PITCH if is_downbeat
             else config.EDITOR_METRONOME_PITCH)
    event = sound_engine.NoteOn(pitch, velocity=config.EDITOR_METRONOME_VELOCITY)
    voice_id = engine.note_on(event)
    engine.schedule_note_off(voice_id, config.EDITOR_METRONOME_CLICK_SECONDS)
    return voice_id


def new_column_duration(columns, cursor_col):
    """The duration class a column appended by piano-mode entry inherits:
    whatever the column just written carries, so a run of eighth notes
    stays eighth notes without re-pressing `,` for every one."""
    if 0 <= cursor_col < len(columns):
        return columns[cursor_col].duration_class
    if columns:
        return columns[-1].duration_class
    return DEFAULT_DURATION_CLASS
