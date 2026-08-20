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
from main import SessionState, _positive_float, run_session
from shell import run_menu_loop


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
    _add_common_flags(tab_p)

    gui_p = sub.add_parser("gui", help="native pygame color window")
    gui_p.add_argument("--fullscreen", action="store_true", help="start fullscreen")
    gui_p.add_argument("--debug", action="store_true", help="show the debug overlay on start")
    _add_common_flags(gui_p)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

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
