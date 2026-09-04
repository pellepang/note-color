"""Opt-in live session recorder: appends one JSON line per finalized note
(mono or chord-tone) to a plain-text `.jsonl` log while armed -- toggled
live via a keybind (`config.DEFAULT_KEYBINDS["session_record_toggle"]`,
default 's'), off by default, no disk writes at all unless a user
explicitly arms it.

Hooked directly into `main.analysis_loop()`, the same "every hop, not
just whatever the render thread happens to poll" placement
`reanalysis_buffer.append()` already uses (issue #77) -- `result_queue`
is a single-slot, overwrite-on-full queue, so a render-thread-side
recorder would silently miss finalized notes whenever two hops complete
between two polls. `SessionRecorder` owns no threading of its own: one
instance lives on `main.SessionState`, `.record_hop()` is called from the
analysis thread every hop, and `.toggle()`/`.armed` are read/written from
the render thread via a live keybind -- safe under CPython's GIL the same
way `Sensitivity`/`SourceState` already are elsewhere in this codebase,
and consistent with this project's existing "cheap enough to always call,
gate on a plain boolean" convention (chord mode's pipeline, `P`'s toggle).

Barlines are deliberately out of scope for v1 -- bar-boundary tracking
lives in `run_terminal_tab()`'s own beat-accumulator (render-thread-side,
`tab`-view-only), not in `analysis_loop()`, so it isn't available at this
hook point without threading `tab`-specific state into the shared
analysis loop every other view also uses. A session's bpm history is
still captured per note event, which is enough to reconstruct approximate
bar boundaries later if that's ever needed.

JSON schema (one line per finalized note):
    {"t": onset time in seconds since the recording started
     (hop_index * hop_seconds -- deliberately the note's ONSET time, not
     its finalization time, matching batch_transcribe.NoteEvent.onset_time's
     own convention; using finalization time instead would silently offset
     every note by its own duration, which is wrong for anything doing
     timing comparison against this log later),
     "pc": pitch class 0-11, "octave": int, "label": "C4"-style,
     "duration_hops": int, "duration_seconds": float,
     "duration_class": "quarter" etc. (duration_tracker.duration_class_for_beats()),
     "bpm_estimate": float or null, "chord_name": str or null (whatever
     chord_smoother recognized this hop, independent of whether the note
     itself was mono or part of a chord -- chord recognition always runs
     regardless of display mode, see main.py's Key design decisions)}.
Plain-text, additive, one event per line -- same "read the file, see
exactly what's in it" spirit as config_store.py's TOML overlay, just
append-only instead of edited in place.

**Played notes (map #99, ticket #122, decision #110).** The standalone
synth tool records into this same file, through `note_on()`/`note_off()`
below rather than `record_hop()`, adding four OPTIONAL fields:

    "source": "played" | "detected"   -- ABSENT MEANS "detected"
    "velocity": MIDI 0-127 int
    "patch": str or null  -- the patch/kit name that sounded the note
    "pad": int or null    -- 1-based pad number when a kit zone was struck

A sibling `synth_log_*.jsonl` was rejected in #110: it would mean two
writers, two readers and two schemas to keep in step forever. Because
every new field is optional and `source` defaults to "detected" when
absent, every log written before this feature stays valid and
`virtualnote replay` reads a synth recording with no changes at all.

Two consequences of a played note having no analysis pipeline behind it:

* **Timing is wall-clock** (`time.perf_counter()` at note-on, duration
  measured at note-off), and `duration_hops`/`bpm_estimate` are written
  `null`. Those two fields are meaningless outside a hop-driven pipeline,
  and filling them from a synthetic hop clock would put a fictional
  number into a file people read by hand. The audio callback's block
  count is the more accurate clock in-process but was rejected for tying
  a recording to the output device happening to be open.
* **`duration_class` is derived against `config.PLAYED_NOTE_REFERENCE_BPM`**,
  since there is no tempo estimate to snap against. The unrounded
  `duration_seconds` is written alongside it, so the derived field is
  always recomputable at another tempo -- which is what `log_import.py`
  does on the way into the score editor. This is the one place a played
  note's log line is *derived* rather than measured, and the raw
  measurement is right next to it.

A played note's `t` is measured from the **first played note of the
take**, not from the moment recording was armed -- so a replay starts on
the first note rather than after however long it took to reach for the
keyboard, and the times in the file are in whatever clock the caller
handed in. (A log that somehow mixed detected and played notes would
therefore carry two time origins; nothing in this app can produce one,
since the synth tool and the live views are never running at once.)

A note still held when recording is disarmed is finalized at that moment
rather than dropped -- a truncated note is a smaller lie than a missing
one, and `close()` runs unconditionally on process exit.
"""

import json
import os
import time

import config
from color_map import NOTE_NAMES
from duration_tracker import duration_class_for_beats

#: `source` value for a note the synth tool played, versus a note the
#: analysis pipeline detected. "detected" is what an absent field means,
#: so it is never actually written -- it exists so readers can name the
#: default rather than hard-coding a bare string.
SOURCE_PLAYED = "played"
SOURCE_DETECTED = "detected"


class SessionRecorder:
    def __init__(self, path=None):
        """`path`, if given, is used verbatim every time recording is
        armed instead of generating a fresh timestamped filename next to
        this module -- exists for test isolation (mirrors ConfigStore's
        own `path=` constructor override), not used by any real caller in
        this codebase."""
        self.armed = False
        self.path = None
        self._fixed_path = path
        self._file = None
        self._prev_pitch_class = None
        self._prev_octave = None
        self._started_at = None
        self._pending = {}   # note_on key -> the in-flight played note

    def toggle(self):
        """Flips armed state, opening/closing the backing file as needed.
        Returns the new armed state."""
        if self.armed:
            self.close()
        else:
            self._open()
        return self.armed

    def _open(self):
        self.path = self._fixed_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"session_log_{time.strftime('%Y%m%d_%H%M%S')}.jsonl",
        )
        self._file = open(self.path, "a", encoding="utf-8")
        self._started_at = None
        self._pending = {}
        self.armed = True

    def close(self, now=None):
        """Idempotent -- safe to call whether or not recording was ever
        armed (main.SessionState.stop() calls this unconditionally on
        process exit, so a still-armed recorder gets flushed/closed even
        if the user never toggled it off explicitly).

        Any played note still sounding is finalized first, at `now`
        (default: the wall clock). A held note whose key never came up
        before recording stopped is truncated rather than dropped -- the
        log then under-reports that one note's length, which is a smaller
        lie than the note not appearing at all."""
        if self._file is not None:
            for key in list(self._pending):
                self.note_off(key, now)
            self._file.close()
        self._file = None
        self._pending = {}
        self._started_at = None
        self.armed = False

    def record_hop(self, pitch_class, octave, note_stack, chord_name, duration_hops, bpm_estimate,
                    hop_index, hop_seconds):
        """Call once per analysis hop, unconditionally -- a no-op (one
        boolean check) whenever not armed. `pitch_class`/`octave` are
        THIS hop's smoothed mono note; a freshly-finalized `duration_hops`
        belongs to the *previous* hop's note instead (the same
        DurationTracker-was-one-hop-behind-what's-being-displayed-now
        pairing `run_terminal_tab()` already follows -- see main.py's Key
        design decisions), so this method tracks that previous identity
        itself rather than requiring the caller to."""
        prev_pitch_class, prev_octave = self._prev_pitch_class, self._prev_octave
        self._prev_pitch_class, self._prev_octave = pitch_class, octave

        if not self.armed:
            return

        if duration_hops is not None and prev_pitch_class is not None:
            self._write_note(prev_pitch_class, prev_octave, duration_hops, bpm_estimate, chord_name,
                              hop_index, hop_seconds)

        for entry in note_stack:
            if entry["duration_hops"] is None:
                continue
            self._write_note(entry["pitch_class"], entry["octave"], entry["duration_hops"], bpm_estimate,
                              chord_name, hop_index, hop_seconds)

    def _write_note(self, pitch_class, octave, duration_hops, bpm_estimate, chord_name, hop_index, hop_seconds):
        onset_hop = hop_index - duration_hops
        duration_seconds = duration_hops * hop_seconds
        beats = (duration_seconds * bpm_estimate / 60.0) if bpm_estimate else None
        event = {
            "t": round(onset_hop * hop_seconds, 3),
            "pc": pitch_class,
            "octave": octave,
            "label": f"{NOTE_NAMES[pitch_class]}{octave}",
            "duration_hops": duration_hops,
            "duration_seconds": round(duration_seconds, 3),
            "duration_class": duration_class_for_beats(beats),
            "bpm_estimate": bpm_estimate,
            "chord_name": chord_name,
        }
        self._write_event(event)

    # -- played notes (map #99, ticket #122, decision #110) ----------------

    def note_on(self, key, pitch_class, octave, velocity=1.0, patch=None, pad=None, now=None):
        """A played note started. `key` is whatever the caller uses to
        identify this sounding note (the synth tool passes the QWERTY key
        slot, the same thing it keys its own voice dict by), so several
        notes can be in flight at once and each pairs with its own
        note_off.

        Nothing is written yet: a note's line carries its duration, which
        isn't known until the key comes up. `now` is injectable purely so
        this is testable without real elapsed time, exactly as
        `kitty_keys.FixedDurationKeys` injects its clock.

        A no-op while not armed -- and, deliberately, no state is kept
        either, so a note begun before `Shift`+S and released after it
        does not appear in the log with a fabricated onset."""
        if not self.armed:
            return
        now = time.perf_counter() if now is None else now
        if self._started_at is None:
            # The take's own zero, set by its first note rather than by
            # arming: a played note's `t` is then measured in whatever
            # clock the caller passed, which is what makes this testable
            # with injected times at all, and it means a replay starts on
            # the first note instead of after however long it took to
            # reach for the keyboard.
            self._started_at = now
        self._pending[key] = {
            "started_at": now,
            "pitch_class": pitch_class,
            "octave": octave,
            "velocity": velocity,
            "patch": patch,
            "pad": pad,
        }

    def note_off(self, key, now=None):
        """The matching release: measures the duration and writes the
        line. A key with no pending note_on (released after recording was
        armed, or never recorded at all) is silently ignored."""
        pending = self._pending.pop(key, None)
        if pending is None or self._file is None:
            return
        now = time.perf_counter() if now is None else now
        duration_seconds = max(0.0, now - pending["started_at"])
        # `is None`, not `or`: a take whose first note landed at clock
        # zero has a perfectly valid _started_at of 0.0, which `or` would
        # quietly treat as "unset" and collapse every later note's t to 0.
        origin = pending["started_at"] if self._started_at is None else self._started_at
        onset = max(0.0, pending["started_at"] - origin)
        beats = duration_seconds * config.PLAYED_NOTE_REFERENCE_BPM / 60.0
        pitch_class, octave = pending["pitch_class"], pending["octave"]
        self._write_event({
            "t": round(onset, 3),
            "pc": pitch_class,
            "octave": octave,
            "label": f"{NOTE_NAMES[pitch_class]}{octave}",
            "duration_hops": None,
            "duration_seconds": round(duration_seconds, 3),
            "duration_class": duration_class_for_beats(beats),
            "bpm_estimate": None,
            "chord_name": None,
            "source": SOURCE_PLAYED,
            "velocity": int(round(max(0.0, min(1.0, pending["velocity"])) * 127)),
            "patch": pending["patch"],
            "pad": pending["pad"],
        })

    def _write_event(self, event):
        self._file.write(json.dumps(event) + "\n")
        self._file.flush()
