"""Kitty keyboard protocol (map #99, ticket #118) -- everything that does
NOT need a real terminal: the negotiation byte sequences, the capability
probe (including every fallback path), the event-encoding parser, its
cross-check against `main._parse_csi_params()`, legacy-token
compatibility, and both held-note policies.

`main.RawKeys`' own byte-level I/O -- negotiation over a real fd, the
queue, focus-loss release synthesis -- is in `tests/test_rawkeys.py`,
driven over an `os.pipe()` so it runs with no TTY present.
"""

import pytest

import kitty_keys as kk
import main


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
    """Cross-check against main._parse_csi_params() itself, not a copy --
    parse_key_event() is a strict generalisation of it, and the existing
    Shift+Arrow handling (issue #98) is the degenerate case of the same
    grammar, so the two must agree on every arrow form."""
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
