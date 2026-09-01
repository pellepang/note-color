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
"""

import json
import os
import time

from color_map import NOTE_NAMES
from duration_tracker import duration_class_for_beats


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
        self.armed = True

    def close(self):
        """Idempotent -- safe to call whether or not recording was ever
        armed (main.SessionState.stop() calls this unconditionally on
        process exit, so a still-armed recorder gets flushed/closed even
        if the user never toggled it off explicitly)."""
        if self._file is not None:
            self._file.close()
        self._file = None
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
        self._file.write(json.dumps(event) + "\n")
        self._file.flush()
