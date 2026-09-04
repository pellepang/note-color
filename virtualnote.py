"""`virtualnote` -- unified CLI entry point for every tool this project
offers (issue #40), retiring the old per-tool `colorize` bash dispatcher.

    virtualnote                        opens the menu (see menu_display.py)
    virtualnote fill [flags]           straight to the fill view
    virtualnote wheel [flags]          straight to the wheel view
    virtualnote circle [flags]         alias for 'wheel' (colorize's old name)
    virtualnote tab fix|onset [flags]  straight to the tab view
    virtualnote gui [flags]            straight to the GUI window

Every flag `colorize` used to forward is still accepted here, per-subcommand
(see `~/.local/bin/colorize`'s old case statement / CLAUDE.md's Running-it
section for the full surface this replicates): --color-scheme, --scroll
(rolled into the 'tab' subcommand's required positional instead, since
colorize always required it too), --dump-file, --sensitivity, --source,
--fullscreen, --debug.

Both the bare-menu path and the direct-to-tool path run through the same
long-lived process (shell.run_menu_loop() / main.run_session(), sharing one
main.SessionState) -- not a relaunch per tool -- so the '|' back-to-menu
keybind works identically either way: a direct `virtualnote fill` still
lands back at the real menu if you press '|', per #37's "every tool must
have a consistent way back to the menu without restarting the process."
"""

import argparse

import config
from main import (SessionState, _positive_float, _parse_time_signature, run_batch_transcribe, run_replay_session,
                   run_score_editor, run_session, run_synth_tool)
from shell import run_menu_loop


def _import_grid_names():
    """The `--grid` choices, read from `log_import.py` itself rather than
    restated here so the CLI and the picker's own grid prompt can never
    offer different sets. Imported inside the function: `log_import`'s
    pure half costs nothing, but this keeps `virtualnote`'s import list
    honest about what it actually needs at module scope."""
    import log_import

    return list(log_import.GRID_NAMES)


def _add_common_flags(parser):
    """Flags every tool (and the bare menu, to set defaults before a tool
    is picked) accepts. Defined once and added to each subparser (plus the
    top-level parser) rather than shared via parent-parser inheritance --
    argparse's parents= mechanism prints the flags in each subcommand's
    --help under a slightly awkward shared group; a plain helper function
    keeps every subcommand's --help self-contained, matching colorize's
    old per-subcommand feel."""
    parser.add_argument("--color-scheme", choices=["chromatic", "fifths"], default=config.DEFAULT_COLOR_SCHEME,
                         help="hue mapping for the fill/GUI views (wheel and tab views always use "
                              "the fifths layout)")
    parser.add_argument("--sensitivity", type=_positive_float, default=config.DEFAULT_SENSITIVITY,
                         help="pitch-detection sensitivity multiplier (default 1.0); higher registers "
                              "quieter/softer playing more readily. Adjustable live with Up/Down in any tool.")
    parser.add_argument("--source", choices=["mic", "loopback"], default="mic",
                         help="'mic' (default) listens to the microphone; 'loopback' listens to the "
                              "computer's own audio output instead (PipeWire/PulseAudio on Linux only), "
                              "for testing without playing anything out loud")


def _add_menu_flags(parser):
    """Only meaningful for the bare-menu screen (issue #42/#51's animated
    donut) -- added to the top-level parser only, not per-subcommand,
    since a direct `virtualnote <view>` launch may never even show the
    menu. Default None (not 'auto') so an unset flag defers to
    config.toml's [preferences].menu_perf_mode instead of silently
    overriding it -- see menu_display._resolve_perf_mode."""
    parser.add_argument("--menu-perf-mode", choices=["auto", "full", "perf"], default=None,
                         help="force the animated menu's donut into full or perf (degraded) rendering "
                              "instead of the auto-detected default; 'auto' explicitly re-enables "
                              "auto-detection, overriding a config.toml [preferences].menu_perf_mode setting")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="virtualnote", description="Unified entry point for every note-color tool -- bare, opens a menu."
    )
    _add_common_flags(parser)
    _add_menu_flags(parser)
    sub = parser.add_subparsers(dest="view")

    fill_p = sub.add_parser("fill", help="full-terminal color fill")
    _add_common_flags(fill_p)

    wheel_p = sub.add_parser("wheel", aliases=["circle"], help="circle-of-fifths ring diagram")
    _add_common_flags(wheel_p)

    tab_p = sub.add_parser("tab", help="scrolling grand-staff sheet-music note history")
    tab_p.add_argument("scroll", choices=["fix", "onset"],
                        help="'fix' pushes a new column every tick; 'onset' pushes one only on a new note-attack")
    tab_p.add_argument("--dump-file", default=None,
                        help="path for the ANSI session note-history dump written on quit "
                             "(default: note_history_<timestamp>.txt next to main.py)")
    tab_p.add_argument("--time-signature", type=_parse_time_signature, default=config.DEFAULT_TIME_SIGNATURE,
                        help="N/D time signature for barline placement (default 4/4)")
    _add_common_flags(tab_p)

    gui_p = sub.add_parser("gui", help="native pygame color window")
    gui_p.add_argument("--fullscreen", action="store_true", help="start fullscreen")
    gui_p.add_argument("--debug", action="store_true", help="show the debug overlay on start")
    _add_common_flags(gui_p)

    transcribe_p = sub.add_parser("transcribe", help="offline rhythm/tempo transcription of an audio file")
    transcribe_p.add_argument("file", help="path to the audio file to transcribe")
    transcribe_p.add_argument("--dump-file", default=None,
                               help="path for the ANSI transcription dump (default: note_history_<timestamp>.txt "
                                    "next to main.py)")
    transcribe_p.add_argument("--time-signature", type=_parse_time_signature, default=config.DEFAULT_TIME_SIGNATURE,
                               help="N/D time signature for barline placement (default 4/4)")
    transcribe_p.add_argument("--write-score", nargs="?", const="", default=None,
                               help="also write a MusicXML score file (issue #65) -- omit entirely to skip "
                                    "score export (default), pass bare for a default path "
                                    "(score_<timestamp>.musicxml next to main.py), or give an explicit path")
    transcribe_p.add_argument("--export-abc", nargs="?", const="", default=None,
                               help="also write a hand-rolled ABC notation text file -- omit entirely to skip "
                                    "ABC export (default), pass bare for a default path "
                                    "(transcription_<timestamp>.abc next to main.py), or give an explicit path")
    transcribe_p.add_argument("--play", action="store_true",
                               help="play the transcription back through an oscillator+ADSR synth (map #24's "
                                    "playback engine) after transcribing -- offline pre-rendered, so it starts "
                                    "once the whole file has been transcribed, not incrementally")
    # No _add_common_flags(transcribe_p) -- batch has no live audio, so
    # --color-scheme/--sensitivity/--source don't apply.

    replay_p = sub.add_parser("replay", help="replay a recorded .jsonl session log through the tab view")
    replay_p.add_argument("file", help="path to a .jsonl session log written by the 's' session-recording keybind")
    replay_p.add_argument("--dump-file", default=None,
                           help="path for the ANSI session note-history dump written when replay ends "
                                "(default: note_history_<timestamp>.txt next to main.py)")
    replay_p.add_argument("--speed", type=_positive_float, default=1.0,
                           help="playback speed multiplier (default 1.0, real time; 2.0 replays twice as fast)")
    replay_p.add_argument("--play", action="store_true",
                           help="also play the session back live through an oscillator+ADSR synth (map #24's "
                                "playback engine), one note per column as it's pushed on screen -- scales with "
                                "--speed the same way the visual pacing does")
    # No _add_common_flags(replay_p) -- same reasoning as transcribe: no
    # live audio, so --color-scheme/--sensitivity/--source don't apply.

    edit_p = sub.add_parser("edit", help="terminal score editor -- load, create, or import into a score")
    edit_p.add_argument("file", help="path to a MusicXML file to edit (created as a blank score if it "
                                      "doesn't exist yet), or a session_log_*.jsonl recording to quantize "
                                      "and import")
    # Import options (ticket #122, decision #110). They apply only to a
    # .jsonl argument; passing them with a MusicXML file is harmless and
    # simply unused, which is preferable to a second subcommand for what
    # is, from the user's side, still "open this in the editor".
    edit_p.add_argument("--grid", choices=_import_grid_names(), default=None,
                         help="quantization grid for importing a session recording "
                              f"(default {config.IMPORT_DEFAULT_GRID}); ignored for a MusicXML file")
    edit_p.add_argument("--tempo", type=_positive_float, default=None,
                         help="tempo in BPM to quantize an imported recording against (default: the log's own "
                              "bpm_estimate if it has one, else the recorder's reference tempo)")
    edit_p.add_argument("--out", default=None,
                         help="where an imported recording's score will be saved by the editor's own save key "
                              "(default: a .musicxml sibling of the log). Nothing is written until you save.")
    # No _add_common_flags(edit_p) -- same reasoning as transcribe/replay:
    # no live audio, so --color-scheme/--sensitivity/--source don't apply.

    sub.add_parser("synth", help="standalone synth -- play the QWERTY keyboard and pads (map #99)")
    # No _add_common_flags(synth_p) and no arguments at all: the synth is
    # an instrument, not a view of captured audio, so it takes no input
    # source and no color scheme. Everything it plays is chosen live from
    # inside the tool (decision #107's inline overlays), which is the
    # whole point of not making patch loading a separate screen.

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    # 'transcribe' never touches SessionState/audio at all (batch, offline,
    # no live capture) -- handled and returned before SessionState is even
    # constructed, mirroring how shell.py's "settings"/"credits" screens
    # bypass it entirely (see main.py's Key design decisions).
    if args.view == "transcribe":
        run_batch_transcribe(args.file, args.time_signature, args.dump_file, args.write_score, args.export_abc,
                              play=args.play)
        return

    # 'replay' likewise never touches SessionState/audio -- it re-drives
    # TabDisplay from an already-recorded .jsonl log, not live capture.
    if args.view == "replay":
        run_replay_session(args.file, args.dump_file, args.speed, play=args.play)
        return

    # 'edit' (issue #98) likewise never touches SessionState/audio -- the
    # score editor loads/creates a MusicXML file and drives its own
    # interactive loop against it, same early-return shape as
    # transcribe/replay above. Unlike those two, it does still return the
    # "menu"/"quit" sentinel (run_score_editor is reachable from
    # shell.py's live-menu 'edit' entry too) -- standalone here, though,
    # there's no menu to fall back to, so the sentinel is just ignored,
    # same as main()'s own standalone dispatch below.
    if args.view == "edit":
        if args.file.endswith(".jsonl"):
            # A session recording, not a score: quantize it into one
            # first (ticket #122). Imported locally, like every other
            # music21-backed path in this codebase, so `virtualnote edit
            # song.musicxml` pays nothing for a feature it isn't using.
            import log_import

            score = log_import.import_log(
                args.file, tempo_bpm=args.tempo,
                grid=args.grid or log_import.DEFAULT_GRID)
            run_score_editor(args.out or log_import.default_score_path(args.file), score=score)
            return
        run_score_editor(args.file)
        return

    # 'synth' (map #99, ticket #119) is the third of these: an instrument,
    # so it never opens the *microphone* and never goes through
    # run_session(). It does want a SoundEngine, but building one is
    # exactly what run_synth_tool() does when handed no session, so
    # (unlike shell.py's live-menu branch, which passes the process's
    # existing one) the standalone path stays a bare early return like
    # transcribe/replay/edit above.
    if args.view == "synth":
        run_synth_tool()
        return

    # 'circle' is colorize's old name for the wheel view -- kept as a
    # subparser alias for zero-cost backward compatibility, normalized to
    # 'wheel' here so every downstream call site only ever sees one name.
    view = "wheel" if args.view == "circle" else args.view

    session = SessionState(args.color_scheme, args.sensitivity, args.source)
    perf_mode_override = args.menu_perf_mode
    try:
        if view is None:
            run_menu_loop(session, perf_mode_override=perf_mode_override)
        else:
            try:
                session.ensure_started()
            except RuntimeError as exc:
                parser.error(str(exc))
            result = run_session(
                view,
                getattr(args, "scroll", config.DEFAULT_SCROLL_MODE),
                getattr(args, "dump_file", None),
                getattr(args, "fullscreen", False),
                getattr(args, "debug", False),
                session,
                time_signature=getattr(args, "time_signature", config.DEFAULT_TIME_SIGNATURE),
            )
            if result == "menu":
                # A direct-to-tool launch still has the real menu behind
                # it (unlike main.py standalone) -- '|' from here lands
                # you there, same as bare `virtualnote` would have.
                run_menu_loop(session, perf_mode_override=perf_mode_override)
    finally:
        session.stop()


if __name__ == "__main__":
    main()
