"""Cross-source note holding (issue #173, decision 72): the shared piece
that keeps a computer-keyboard hold and a MIDI-keyboard hold from cutting
each other off.

The problem is specific to `poly.PolyGraph.note_off()`, whose contract is
"release every voice sounding this pitch" (poly.py's own docstring) --
right for a single input source (only one thing could be holding a pitch)
but wrong the moment a second source can hold the same pitch
independently: if the computer keyboard is holding a C4 and a MIDI
keyboard's C4 is released, the naive call `bridge.note_off(60)` would
silence *both*, even though the keyboard's finger is still down.

This is deliberately not fixed inside `poly.py` -- `PolyGraph.note_off()`'s
pitch-based release is simple, correct for its one caller before this
ticket, and shared by every other pitch-based release site; changing its
contract to carry a source/holder concept would ripple into every module
that calls it. A small reference count at the dispatch layer, in front of
the call, is the narrower fix: a source `press()`es a pitch when it starts
sounding it, `release()`s when its own hold ends, and only the last
release actually reaches `note_off()`.

No device, no engine and no Qt anywhere in this file -- plain bookkeeping,
directly unit-testable, and safe to share between `gui/synth_view.py`'s
computer-keyboard dispatch and `audio/midi_input.py`'s MIDI dispatch
without either importing the other.
"""

from __future__ import annotations


class NoteHoldGate:
    """`{pitch: {source_label, ...}}` -- who is currently holding each
    pitch. Both sources share one instance; each names itself with a
    label (`"keys"`, `"midi"`) so pressing the same pitch on both, then
    releasing one, does not release the other's copy.

    Deliberately not thread-safe by locking -- both callers in this
    project drive it from the Qt UI thread (`gui/synth_view.py`'s own
    event handlers, and `audio/midi_input.py`'s raw bytes are marshalled
    onto that same thread via a Qt queued signal before `press()`/
    `release()` are ever called -- see that module's docstring for why).
    A gate touched from two real threads at once would need its own lock;
    this one does not need one because it is never handed to a second
    thread.
    """

    def __init__(self):
        self._holders: dict[int, set] = {}

    def press(self, pitch, source):
        """Records that `source` now holds `pitch`. Does not say whether
        this is the first press -- every press is audible (a retrigger is
        a real re-strike, not a no-op), so there is nothing for the caller
        to act on here beyond bookkeeping."""
        self._holders.setdefault(int(pitch), set()).add(source)

    def release(self, pitch, source):
        """Records that `source` no longer holds `pitch`. Returns True
        when nothing else holds it any more -- the caller's cue that the
        underlying voice(s) may actually be released. Returns True for a
        pitch this gate never saw held (nothing to protect), so a caller
        that presses through a path this gate was not told about (should
        not happen, but degrades safely) is not silently stuck holding a
        phantom lock forever."""
        pitch = int(pitch)
        holders = self._holders.get(pitch)
        if holders is None:
            return True
        holders.discard(source)
        if holders:
            return False
        del self._holders[pitch]
        return True

    def held_by(self, source):
        """Every pitch `source` currently holds, per this gate's own
        bookkeeping -- what a device-unplugged/window-closed cleanup walks
        to release exactly its own notes and nobody else's."""
        return [pitch for pitch, holders in self._holders.items() if source in holders]

    def clear_source(self, source):
        """Removes `source` from every pitch it holds, same release
        semantics as `release()` per pitch (True only once no one else
        holds it). Returns `[(pitch, should_release), ...]` so a caller
        can decide per pitch whether to call the underlying note-off --
        used for a clean device-disconnect/panic path rather than looping
        `release()` while mutating `held_by()`'s own list."""
        results = []
        for pitch in self.held_by(source):
            results.append((pitch, self.release(pitch, source)))
        return results
