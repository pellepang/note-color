"""PROTOTYPE tests -- wayfinder ticket #101 (map #99).

Covers everything about this prototype that does NOT need a real
terminal: the negotiation byte sequences, the capability probe (including
every fallback path), the event-encoding parser, legacy-token
compatibility, and both held-note policies.

Run from the repo root:
    .venv/bin/python -m pytest prototypes/kitty-key-release/ -q
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

import kitty_keys as kk
import kitty_rawkeys as kr


# ---------------------------------------------------------------- sequences

def test_synth_flags_include_event_types_and_all_as_escapes():
    assert kk.SYNTH_FLAGS & kk.FLAG_EVENT_TYPES
    assert kk.SYNTH_FLAGS & kk.FLAG_ALL_AS_ESCAPES
    assert kk.SYNTH_FLAGS == 27


def test_push_and_pop_sequences():
    assert kk.push_sequence(27) == b"\x1b[>27u"
    assert kk.push_sequence(1) == b"\x1b[>1u"
    assert kk.pop_sequence() == b"\x1b[<1u"
    assert kk.pop_sequence(2) == b"\x1b[<2u"


def test_probe_sends_query_then_da1_sentinel():
    assert kk.PROBE_SEQUENCE == b"\x1b[?u\x1b[c"


# -------------------------------------------------------------------- probe

def test_probe_detects_support():
    probe = kk.CapabilityProbe()
    probe.feed(b"\x1b[?27u\x1b[?62;c")
    assert probe.state == "supported"
    assert probe.flags == 27
    assert probe.leftover == b""


def test_probe_detects_no_support_via_da1_sentinel():
    probe = kk.CapabilityProbe()
    probe.feed(b"\x1b[?62;1;6c")  # xterm DA1, no kitty reply first
    assert probe.state == "unsupported"
    assert probe.supported is False


def test_probe_stays_pending_when_nothing_answers():
    probe = kk.CapabilityProbe()
    probe.feed(b"")
    assert probe.state == "pending"
    assert probe.settled is False


def test_probe_reassembles_a_reply_split_across_reads():
    probe = kk.CapabilityProbe()
    for chunk in (b"\x1b", b"[", b"?", b"2", b"7", b"u"):
        probe.feed(chunk)
    assert probe.state == "supported"
    assert probe.flags == 27


def test_probe_preserves_typed_ahead_input():
    """A keystroke racing the probe reply must not be eaten."""
    probe = kk.CapabilityProbe()
    probe.feed(b"q\x1b[?27ux")
    assert probe.state == "supported"
    assert probe.leftover == b"qx"


def test_probe_preserves_typed_ahead_input_on_the_unsupported_path():
    probe = kk.CapabilityProbe()
    probe.feed(b"a\x1b[?62;1;6cb")
    assert probe.state == "unsupported"
    assert probe.leftover == b"ab"


def test_probe_discards_an_unrelated_csi_question_reply():
    probe = kk.CapabilityProbe()
    probe.feed(b"\x1b[?2026;2$y\x1b[?62c")
    assert probe.state == "unsupported"
    assert probe.leftover == b""


# ------------------------------------------------------------------- parser

def test_press_repeat_release_are_distinguishable():
    press = kk.parse_key_event("97;1:1", "u")
    repeat = kk.parse_key_event("97;1:2", "u")
    release = kk.parse_key_event("97;1:3", "u")
    assert (press.key, press.event) == ("a", kk.PRESS)
    assert (repeat.key, repeat.event) == ("a", kk.REPEAT)
    assert (release.key, release.event) == ("a", kk.RELEASE)


def test_missing_fields_default_to_press_with_no_modifiers():
    event = kk.parse_key_event("97", "u")
    assert event.key == "a"
    assert event.event == kk.PRESS
    assert event.mods == 0


def test_modifier_bitmask_is_the_reported_value_minus_one():
    assert kk.parse_key_event("97;2", "u").mods == kk.MOD_SHIFT
    assert kk.parse_key_event("97;5", "u").mods == kk.MOD_CTRL
    assert kk.parse_key_event("97;6", "u").mods == kk.MOD_CTRL | kk.MOD_SHIFT
    assert kk.parse_key_event("97;3:3", "u").mods == kk.MOD_ALT


def test_associated_text_is_decoded():
    event = kk.parse_key_event("97;2:1;65", "u")
    assert event.key == "a"      # physical key, unchanged by Shift
    assert event.text == "A"


def test_shift_release_reports_the_same_key_as_its_press():
    """A note started with Shift held must stop when the key comes up,
    even if Shift was released first -- so the token is the physical key."""
    down = kk.parse_key_event("97;2:1;65", "u")
    up = kk.parse_key_event("97;1:3", "u")
    assert down.key == up.key == "a"


def test_named_keys():
    assert kk.parse_key_event("32;1:1", "u").key == "SPACE"
    assert kk.parse_key_event("13;1:3", "u").key == "ENTER"
    assert kk.parse_key_event("27;1:1", "u").key == "ESC"
    assert kk.parse_key_event("57441;1:1", "u").key == "LEFT_SHIFT"


def test_arrows_keep_their_legacy_final_letters():
    assert kk.parse_key_event("1;1:1", "A").key == "UP"
    assert kk.parse_key_event("1;1:3", "B").key == "DOWN"
    assert kk.parse_key_event("", "C").key == "RIGHT"
    shift_up = kk.parse_key_event("1;2:1", "A")
    assert shift_up.key == "UP" and shift_up.mods == kk.MOD_SHIFT


def test_tilde_functional_keys():
    assert kk.parse_key_event("5;1:1", "~").key == "PAGE_UP"
    assert kk.parse_key_event("15;1:3", "~").key == "F5"


def test_non_key_final_byte_is_rejected():
    assert kk.parse_key_event("0;1", "R") is None   # cursor position report
    assert kk.parse_key_event("62;1;6", "c") is None  # DA1


def test_unknown_event_type_is_rejected_not_guessed():
    assert kk.parse_key_event("97;1:9", "u") is None


# ------------------------------------------------------------ legacy tokens

@pytest.mark.parametrize(
    "params,final,expected",
    [
        ("97;1:1", "u", "a"),
        ("97;1:2", "u", "a"),      # auto-repeat still feeds menus
        ("97;1:3", "u", None),     # a release is invisible to legacy callers
        ("97;2:1;65", "u", "A"),
        ("32;1:1", "u", " "),
        ("13;1:1", "u", "\r"),
        ("1;1:1", "A", "UP"),
        ("1;2:1", "A", "SHIFT_UP"),
        ("1;2:1", "B", "SHIFT_DOWN"),
        ("1;1:1", "D", "LEFT"),
    ],
)
def test_legacy_token_matches_todays_rawkeys_output(params, final, expected):
    assert kk.legacy_token(kk.parse_key_event(params, final)) == expected


def test_legacy_tokens_match_the_real_apps_parser_for_arrows():
    """Cross-check against main._parse_csi_params() itself, not a copy."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import main

    for params, final in [("", "A"), ("", "B"), ("", "C"), ("", "D"),
                          ("1;2", "A"), ("1;2", "B")]:
        kitty_params = (params or "1") + (":1" if ";" in params else ";1:1")
        assert kk.legacy_token(kk.parse_key_event(kitty_params, final)) == \
            main._parse_csi_params(params, final)


# ------------------------------------------------------------ held policies

def _ev(key, event, codepoint=97):
    return kk.KeyEvent(key=key, event=event, mods=0, text="", codepoint=codepoint)


def test_held_keys_press_release_round_trip():
    held = kk.HeldKeys()
    assert held.apply(_ev("a", kk.PRESS)) == [(kk.NOTE_ON, "a")]
    assert held.held == {"a"}
    assert held.apply(_ev("a", kk.RELEASE)) == [(kk.NOTE_OFF, "a")]
    assert held.held == set()


def test_held_keys_swallow_auto_repeat():
    held = kk.HeldKeys()
    held.apply(_ev("a", kk.PRESS))
    assert held.apply(_ev("a", kk.REPEAT)) == []
    assert held.apply(_ev("a", kk.REPEAT)) == []
    assert held.held == {"a"}


def test_held_keys_track_several_simultaneous_keys():
    held = kk.HeldKeys()
    for key in "asd":
        held.apply(_ev(key, kk.PRESS))
    assert held.held == {"a", "s", "d"}
    assert held.apply(_ev("s", kk.RELEASE)) == [(kk.NOTE_OFF, "s")]
    assert held.held == {"a", "d"}
    assert held.release_all() == [(kk.NOTE_OFF, "a"), (kk.NOTE_OFF, "d")]
    assert held.held == set()


def test_held_keys_retrigger_on_a_press_with_a_lost_release():
    held = kk.HeldKeys()
    held.apply(_ev("a", kk.PRESS))
    assert held.apply(_ev("a", kk.PRESS)) == [(kk.NOTE_OFF, "a"), (kk.NOTE_ON, "a")]
    assert held.held == {"a"}


def test_held_keys_repeat_without_a_press_starts_the_note():
    held = kk.HeldKeys()
    assert held.apply(_ev("a", kk.REPEAT)) == [(kk.NOTE_ON, "a")]


def test_held_keys_ignore_a_release_for_a_key_never_pressed():
    assert kk.HeldKeys().apply(_ev("a", kk.RELEASE)) == []


def test_fixed_duration_note_ends_on_its_own():
    policy = kk.FixedDurationKeys(duration=0.3)
    assert policy.apply(_ev("a", kk.PRESS), now=10.0) == [(kk.NOTE_ON, "a")]
    assert policy.expire(now=10.2) == []
    assert policy.expire(now=10.4) == [(kk.NOTE_OFF, "a")]
    assert policy.held == set()


def test_fixed_duration_auto_repeat_extends_instead_of_retriggering():
    policy = kk.FixedDurationKeys(duration=0.3)
    policy.apply(_ev("a", kk.PRESS), now=10.0)
    assert policy.apply(_ev("a", kk.PRESS), now=10.2) == []
    assert policy.expire(now=10.4) == []       # extended to 10.5
    assert policy.expire(now=10.6) == [(kk.NOTE_OFF, "a")]


def test_fixed_duration_handles_several_keys_independently():
    policy = kk.FixedDurationKeys(duration=0.3)
    policy.apply(_ev("a", kk.PRESS), now=10.0)
    policy.apply(_ev("s", kk.PRESS), now=10.2)
    assert policy.expire(now=10.35) == [(kk.NOTE_OFF, "a")]
    assert policy.held == {"s"}
    assert policy.release_all() == [(kk.NOTE_OFF, "s")]


# -------------------------------------------- KittyRawKeys over a real pipe

class _Pipe:
    """A read fd we can push bytes into -- stands in for a terminal well
    enough to exercise every byte path without a TTY. `sink` is where the
    escape sequences KittyRawKeys emits go (a real terminal would echo
    them back to itself; here they are simply discarded)."""

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


def _keys(pipe, want_kitty=True, **kwargs):
    return kr.KittyRawKeys(fd=pipe.read_fd, out_fd=pipe.sink_fd,
                           want_kitty=want_kitty, set_cbreak=False, **kwargs)


def test_negotiation_is_skipped_when_the_fd_is_not_a_tty(pipe):
    """os.isatty() is False for a pipe, so nothing is written and nothing
    is waited for -- the same inert posture main.RawKeys already takes."""
    keys = _keys(pipe)
    assert keys.active is False
    assert keys.kitty is False
    assert keys.negotiation == "no-tty"


def test_unsupported_terminal_still_delivers_legacy_tokens(pipe, monkeypatch):
    monkeypatch.setattr(kr.os, "isatty", lambda fd: True)
    monkeypatch.setattr(kr.termios, "tcgetattr", lambda fd: None)
    pipe.send(b"\x1b[?62;1;6c")          # DA1 only: no kitty support
    keys = _keys(pipe)
    assert keys.kitty is False
    assert keys.negotiation == "unsupported"
    pipe.send(b"p\x1b[A\x1b[1;2B")
    assert keys.poll() == "p"
    assert keys.poll() == "UP"
    assert keys.poll() == "SHIFT_DOWN"
    assert keys.poll() is None


def test_a_silent_terminal_times_out_rather_than_hanging(pipe, monkeypatch):
    monkeypatch.setattr(kr.os, "isatty", lambda fd: True)
    monkeypatch.setattr(kr.termios, "tcgetattr", lambda fd: None)
    import time

    start = time.monotonic()
    keys = _keys(pipe, negotiation_timeout=0.05)
    elapsed = time.monotonic() - start
    assert keys.negotiation == "pending"
    assert keys.kitty is False
    assert elapsed < 0.5
    pipe.send(b"q")
    assert keys.poll() == "q"


def test_supported_terminal_yields_events_and_legacy_tokens(pipe, monkeypatch):
    monkeypatch.setattr(kr.os, "isatty", lambda fd: True)
    monkeypatch.setattr(kr.termios, "tcgetattr", lambda fd: None)
    pipe.send(b"\x1b[?27u\x1b[?62c")
    keys = _keys(pipe)
    assert keys.kitty is True
    assert keys.kitty_flags == 27

    pipe.send(b"\x1b[97;1:1u\x1b[97;1:3u\x1b[115;1:1u")
    a_down = keys.poll_event()
    a_up = keys.poll_event()
    s_down = keys.poll_event()
    assert (a_down.key, a_down.event) == ("a", kk.PRESS)
    assert (a_up.key, a_up.event) == ("a", kk.RELEASE)
    assert (s_down.key, s_down.event) == ("s", kk.PRESS)
    assert keys.poll_event() is None


def test_poll_skips_releases_so_legacy_callers_are_unaffected(pipe, monkeypatch):
    monkeypatch.setattr(kr.os, "isatty", lambda fd: True)
    monkeypatch.setattr(kr.termios, "tcgetattr", lambda fd: None)
    pipe.send(b"\x1b[?27u")
    keys = _keys(pipe)
    # press p, release p, press q -- a legacy caller must see "p" then "q".
    pipe.send(b"\x1b[112;1:1u\x1b[112;1:3u\x1b[113;1:1u")
    assert keys.poll() == "p"
    assert keys.poll() == "q"
    assert keys.poll() is None


def test_typed_ahead_key_during_negotiation_is_not_lost(pipe, monkeypatch):
    monkeypatch.setattr(kr.os, "isatty", lambda fd: True)
    monkeypatch.setattr(kr.termios, "tcgetattr", lambda fd: None)
    pipe.send(b"z\x1b[?27u")
    keys = _keys(pipe)
    assert keys.kitty is True
    assert keys.poll() == "z"


def test_poll_event_on_a_plain_terminal_synthesises_presses(pipe, monkeypatch):
    monkeypatch.setattr(kr.os, "isatty", lambda fd: True)
    monkeypatch.setattr(kr.termios, "tcgetattr", lambda fd: None)
    pipe.send(b"\x1b[?62c")
    keys = _keys(pipe)
    pipe.send(b"a")
    event = keys.poll_event()
    assert (event.key, event.event) == ("a", kk.PRESS)


def test_a_full_chord_press_and_release_drives_the_voice_policy(pipe, monkeypatch):
    """End to end over a real fd: three keys down together, then up in a
    different order -- the exact thing a QWERTY piano must survive."""
    monkeypatch.setattr(kr.os, "isatty", lambda fd: True)
    monkeypatch.setattr(kr.termios, "tcgetattr", lambda fd: None)
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
