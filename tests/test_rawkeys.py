"""`main.RawKeys`' byte-level behaviour (map #99, ticket #118), driven over
a real `os.pipe()` rather than a terminal.

`fd`/`out_fd` became constructor parameters precisely so this file can
exist: every path below -- the kitty negotiation, the non-kitty fallback,
the decode queue, focus-loss release synthesis -- is exercised with no TTY
present, which is what makes them testable in CI and in an agent
environment at all. What is *not* covered here is real terminal wire
behaviour: that is smoke-tested by hand in kitty, per this repo's
convention (see `tests/`'s entry in CLAUDE.md).

The two properties every test here defends:

1. **Zero cost to existing callers.** `RawKeys()` with no arguments never
   negotiates, never writes a byte, and returns exactly the tokens it
   always has.
2. **The fallback is where this breaks.** A terminal that answers only
   DA1, or answers nothing at all, must settle quickly, must not eat a
   keystroke typed during negotiation, and must leave `poll()` working.
"""

import os
import time

import pytest

import kitty_keys as kk
import main


class _Pipe:
    """A read fd bytes can be pushed into -- stands in for a terminal well
    enough to exercise every byte path. `sink_fd` is where the escape
    sequences RawKeys emits go; a real terminal would act on them, here
    they are discarded."""

    def __init__(self):
        self.read_fd, self.write_fd = os.pipe()
        self.sink_fd = os.open(os.devnull, os.O_WRONLY)

    def send(self, data):
        os.write(self.write_fd, data)

    def close(self):
        os.close(self.read_fd)
        os.close(self.write_fd)
        os.close(self.sink_fd)


@pytest.fixture
def pipe():
    p = _Pipe()
    yield p
    p.close()


@pytest.fixture
def fake_tty(monkeypatch):
    """Make RawKeys believe the pipe is a terminal, without touching the
    real one: os.isatty() is False for a pipe, and tcgetattr() would raise
    on it."""
    monkeypatch.setattr(main.os, "isatty", lambda fd: True)
    monkeypatch.setattr(main.termios, "tcgetattr", lambda fd: None)


def _keys(pipe, want_kitty=True, **kwargs):
    return main.RawKeys(fd=pipe.read_fd, out_fd=pipe.sink_fd,
                        want_kitty=want_kitty, set_cbreak=False, **kwargs)


# ------------------------------------------------- defaults / existing callers

def test_default_construction_never_negotiates(pipe):
    """The 11 existing construction sites pass no arguments; none of them
    should pay a probe round trip or push a mode."""
    keys = _keys(pipe, want_kitty=False)
    assert keys.kitty is False
    assert keys.negotiation == "skipped"


def test_negotiation_is_skipped_when_the_fd_is_not_a_tty(pipe):
    keys = _keys(pipe)
    assert keys.active is False
    assert keys.kitty is False
    assert keys.negotiation == "no-tty"


def test_legacy_tokens_are_unchanged_without_the_protocol(pipe, fake_tty):
    keys = _keys(pipe, want_kitty=False)
    pipe.send(b"p\x1b[A\x1b[B\x1b[C\x1b[D\x1b[1;2A\x1b[1;2B")
    assert [keys.poll() for _ in range(7)] == [
        "p", "UP", "DOWN", "RIGHT", "LEFT", "SHIFT_UP", "SHIFT_DOWN"]
    assert keys.poll() is None


def test_a_bare_escape_still_returns_none_without_the_protocol(pipe, fake_tty):
    """Today's behaviour, preserved exactly: nothing in this app binds a
    bare Escape on the no-protocol path."""
    keys = _keys(pipe, want_kitty=False)
    pipe.send(b"\x1b")
    assert keys.poll() is None


# ---------------------------------------------------------------- fallbacks

def test_unsupported_terminal_still_delivers_legacy_tokens(pipe, fake_tty):
    pipe.send(b"\x1b[?62;1;6c")          # DA1 only: no kitty support
    keys = _keys(pipe)
    assert keys.kitty is False
    assert keys.negotiation == "unsupported"
    pipe.send(b"p\x1b[A\x1b[1;2B")
    assert keys.poll() == "p"
    assert keys.poll() == "UP"
    assert keys.poll() == "SHIFT_DOWN"
    assert keys.poll() is None


def test_a_silent_terminal_times_out_rather_than_hanging(pipe, fake_tty):
    start = time.monotonic()
    keys = _keys(pipe, negotiation_timeout=0.05)
    elapsed = time.monotonic() - start
    assert keys.negotiation == "pending"
    assert keys.kitty is False
    assert elapsed < 0.5
    pipe.send(b"q")
    assert keys.poll() == "q"


def test_typed_ahead_key_during_negotiation_is_not_lost(pipe, fake_tty):
    pipe.send(b"z\x1b[?27u")
    keys = _keys(pipe)
    assert keys.kitty is True
    assert keys.poll() == "z"


def test_typed_ahead_key_survives_an_unsupported_negotiation(pipe, fake_tty):
    pipe.send(b"z\x1b[?62;1;6c")
    keys = _keys(pipe)
    assert keys.kitty is False
    assert keys.poll() == "z"


def test_a_reply_split_across_reads_still_settles(pipe, fake_tty):
    """Terminals give no delivery guarantees; the probe must reassemble."""
    pipe.send(b"\x1b[?2")
    pipe.send(b"7u")
    keys = _keys(pipe, negotiation_timeout=0.5)
    assert keys.kitty is True


# ------------------------------------------------------ protocol active

def test_supported_terminal_reports_the_pushed_flags(pipe, fake_tty):
    """A fresh kitty answers the query with 0 (nothing active yet).
    Reporting that as the mode's flags reads as "the protocol is off" --
    the exact misreport found during #101's live verification."""
    pipe.send(b"\x1b[?0u\x1b[?62c")
    keys = _keys(pipe)
    assert keys.kitty is True
    assert keys.kitty_flags == kk.SYNTH_FLAGS == 27
    assert keys.kitty_flags_before == 0


def test_events_carry_press_repeat_and_release(pipe, fake_tty):
    pipe.send(b"\x1b[?27u")
    keys = _keys(pipe)
    pipe.send(b"\x1b[97;1:1u\x1b[97;1:2u\x1b[97;1:3u")
    assert [(e.key, e.event) for e in
            (keys.poll_event(), keys.poll_event(), keys.poll_event())] == [
        ("a", kk.PRESS), ("a", kk.REPEAT), ("a", kk.RELEASE)]
    assert keys.poll_event() is None


def test_poll_skips_releases_so_legacy_callers_are_unaffected(pipe, fake_tty):
    pipe.send(b"\x1b[?27u")
    keys = _keys(pipe)
    # press p, release p, press q -- a legacy caller must see "p" then "q",
    # never a None in between (which would read as "nothing was pressed").
    pipe.send(b"\x1b[112;1:1u\x1b[112;1:3u\x1b[113;1:1u")
    assert keys.poll() == "p"
    assert keys.poll() == "q"
    assert keys.poll() is None


def test_arrows_and_shift_arrows_survive_the_protocol(pipe, fake_tty):
    """Issue #98's score-editor transpose must keep working with the mode
    pushed, since kitty encodes arrows with the same final letters."""
    pipe.send(b"\x1b[?27u")
    keys = _keys(pipe)
    pipe.send(b"\x1b[1;1:1A\x1b[1;2:1A\x1b[1;2:1B\x1b[1;1:1D")
    assert [keys.poll() for _ in range(4)] == [
        "UP", "SHIFT_UP", "SHIFT_DOWN", "LEFT"]


def test_a_burst_of_several_events_in_one_read_is_all_delivered(pipe, fake_tty):
    """One read can yield several events -- a chord's releases arrive
    together. That is why poll() drains through an internal queue."""
    pipe.send(b"\x1b[?27u")
    keys = _keys(pipe)
    pipe.send(b"\x1b[97;1:1u\x1b[115;1:1u\x1b[100;1:1u")
    assert [keys.poll() for _ in range(3)] == ["a", "s", "d"]


def test_escape_becomes_reachable_with_the_protocol_active(pipe, fake_tty):
    """The one genuine behaviour change (#118): Escape arrives as CSI 27 u
    rather than a bare ESC, so a caller's `key == "\\x1b"` branch -- e.g.
    score_editor_picker.py's cancel -- starts working. Only for a view
    that opts in; every existing caller is want_kitty=False."""
    pipe.send(b"\x1b[?27u")
    keys = _keys(pipe)
    pipe.send(b"\x1b[27;1:1u")
    assert keys.poll() == "\x1b"


def test_a_modifier_press_maps_to_no_legacy_token(pipe, fake_tty):
    """A "press any key" screen must not be dismissed by a stray Shift."""
    pipe.send(b"\x1b[?27u")
    keys = _keys(pipe)
    pipe.send(b"\x1b[57441;1:1u")
    assert keys.poll() is None
    assert keys.poll_event() is None  # consumed above; nothing left


def test_shifted_text_reaches_legacy_callers_as_the_shifted_character(pipe, fake_tty):
    pipe.send(b"\x1b[?27u")
    keys = _keys(pipe)
    pipe.send(b"\x1b[97;2:1;65u")
    assert keys.poll() == "A"


def test_a_full_chord_press_and_release_drives_the_voice_policy(pipe, fake_tty):
    """End to end over a real fd: three keys down together, released in a
    different order -- the exact thing a QWERTY piano must survive."""
    pipe.send(b"\x1b[?27u")
    keys = _keys(pipe)
    held = kk.HeldKeys()
    pipe.send(
        b"\x1b[97;1:1u"    # a down
        b"\x1b[115;1:1u"   # s down
        b"\x1b[97;1:2u"    # a auto-repeat
        b"\x1b[100;1:1u"   # d down
        b"\x1b[115;1:3u"   # s up
        b"\x1b[100;1:3u"   # d up
        b"\x1b[97;1:3u"    # a up
    )
    actions = []
    while True:
        event = keys.poll_event()
        if event is None:
            break
        actions.extend(held.apply(event))
    assert actions == [
        (kk.NOTE_ON, "a"), (kk.NOTE_ON, "s"), (kk.NOTE_ON, "d"),
        (kk.NOTE_OFF, "s"), (kk.NOTE_OFF, "d"), (kk.NOTE_OFF, "a"),
    ]
    assert held.held == set()


# ------------------------------------------------------------- focus loss

def test_focus_out_releases_every_held_key(pipe, fake_tty):
    """The release for a key still down when focus is lost is delivered to
    whichever window has focus now -- never to us -- so it must be
    synthesised here or the note hangs forever."""
    pipe.send(b"\x1b[?27u")
    keys = _keys(pipe)
    held = kk.HeldKeys()
    pipe.send(b"\x1b[97;1:1u\x1b[115;1:1u\x1b[O")
    actions = []
    while True:
        event = keys.poll_event()
        if event is None:
            break
        actions.extend(held.apply(event))
    assert actions == [(kk.NOTE_ON, "a"), (kk.NOTE_ON, "s"),
                       (kk.NOTE_OFF, "a"), (kk.NOTE_OFF, "s")]
    assert held.held == set()


def test_focus_in_is_swallowed_and_holds_nothing(pipe, fake_tty):
    pipe.send(b"\x1b[?27u")
    keys = _keys(pipe)
    pipe.send(b"\x1b[97;1:1u\x1b[I")
    assert keys.poll_event().key == "a"
    assert keys.poll_event() is None
    assert [e.key for e in keys.release_all()] == ["a"]


def test_release_all_is_explicitly_callable_and_idempotent(pipe, fake_tty):
    pipe.send(b"\x1b[?27u")
    keys = _keys(pipe)
    pipe.send(b"\x1b[97;1:1u\x1b[115;1:1u")
    keys.poll_event()
    keys.poll_event()
    assert [(e.key, e.event) for e in keys.release_all()] == [
        ("a", kk.RELEASE), ("s", kk.RELEASE)]
    assert keys.release_all() == []


def test_release_all_is_empty_without_the_protocol(pipe, fake_tty):
    keys = _keys(pipe, want_kitty=False)
    pipe.send(b"a")
    assert keys.poll() == "a"
    assert keys.release_all() == []


def test_a_release_stops_tracking_the_key_as_held(pipe, fake_tty):
    pipe.send(b"\x1b[?27u")
    keys = _keys(pipe)
    pipe.send(b"\x1b[97;1:1u\x1b[97;1:3u")
    keys.poll_event()
    keys.poll_event()
    assert keys.release_all() == []


# -------------------------------------------------------------- teardown

def test_restore_pops_the_mode_and_is_idempotent(pipe, fake_tty):
    """Without the pop, the user's shell inherits a terminal reporting
    every keystroke as an escape code."""
    written = []
    pipe.send(b"\x1b[?27u")
    keys = _keys(pipe)
    keys._write = lambda data: written.append(data) or True
    keys.restore()
    assert kk.pop_sequence() in written
    assert kk.FOCUS_TRACKING_OFF in written
    written.clear()
    keys.restore()
    assert written == []


def test_poll_event_on_a_plain_terminal_synthesises_presses(pipe, fake_tty):
    """A caller written against events works on a terminal with no
    protocol too -- it just never sees a release there."""
    pipe.send(b"\x1b[?62c")
    keys = _keys(pipe)
    assert keys.kitty is False
    pipe.send(b"aA\x1b[A")
    plain, shifted, arrow = keys.poll_event(), keys.poll_event(), keys.poll_event()
    assert (plain.key, plain.event, plain.mods) == ("a", kk.PRESS, 0)
    assert (shifted.key, shifted.text, shifted.mods) == ("a", "A", kk.MOD_SHIFT)
    assert (arrow.key, arrow.event) == ("UP", kk.PRESS)


# --------------------------------------------------- over a real pty pair

def test_negotiation_and_teardown_over_a_real_pty():
    """The one test here that runs against a genuine TTY (a pty pair, no
    monkeypatching of isatty/termios): RawKeys sees a real terminal, writes
    the probe, and a thread standing in for the terminal answers it the way
    kitty would -- driving the whole handshake through push, a key event,
    and the pop on restore(). This is the closest this environment gets to
    the live kitty run issue #101 did by hand; it proves the code against
    the spec, not the spec against kitty.

    The reply is written only *after* the probe bytes are seen on the
    master side rather than queued up front, because `tty.setcbreak()`
    flushes pending input -- a real terminal cannot answer before it has
    been asked either, so this is also the honest ordering.
    """
    import pty
    import termios as _termios
    import threading

    master, slave = pty.openpty()
    seen = []
    try:
        # Local echo would send our own probe bytes straight back at us.
        attrs = _termios.tcgetattr(slave)
        attrs[3] &= ~_termios.ECHO
        _termios.tcsetattr(slave, _termios.TCSANOW, attrs)

        def fake_terminal():
            while kk.PROBE_SEQUENCE not in b"".join(seen):
                try:
                    chunk = os.read(master, 256)
                except OSError:  # the pair was torn down; nothing to answer
                    return
                if not chunk:
                    return
                seen.append(chunk)
            os.write(master, b"\x1b[?0u\x1b[?62c")   # kitty answers, flags 0
            os.write(master, b"\x1b[97;1:1u\x1b[97;1:3u")  # then 'a' down/up

        answering = threading.Thread(target=fake_terminal)
        answering.start()
        keys = main.RawKeys(fd=slave, want_kitty=True, negotiation_timeout=2.0)
        answering.join(timeout=2.0)
        try:
            assert keys.active is True
            assert keys.kitty is True
            assert keys.kitty_flags == kk.SYNTH_FLAGS
            assert keys.kitty_flags_before == 0

            deadline = time.monotonic() + 1.0
            events = []
            while len(events) < 2 and time.monotonic() < deadline:
                event = keys.poll_event()
                if event is not None:
                    events.append(event)
            assert [(e.key, e.event) for e in events] == [
                ("a", kk.PRESS), ("a", kk.RELEASE)]
        finally:
            keys.restore()

        # Drain what the terminal side actually received, bounded rather
        # than blocking: the pop lands only once restore() has run, and a
        # pty gives no guarantee about which read boundary it arrives on.
        import select as _select
        written = b"".join(seen)
        drain_deadline = time.monotonic() + 1.0
        while (kk.pop_sequence() not in written
               and time.monotonic() < drain_deadline):
            if _select.select([master], [], [], 0.1)[0]:
                written += os.read(master, 512)
        assert kk.PROBE_SEQUENCE in written
        assert kk.push_sequence(kk.SYNTH_FLAGS) in written
        assert kk.FOCUS_TRACKING_ON in written
        assert kk.pop_sequence() in written
    finally:
        os.close(master)
        os.close(slave)
