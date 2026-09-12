"""`visualnote` -- the VisualNote Studio entry point (map #145, milestone 1).

Wires the four pieces that already exist and owns none of their logic: a
`Project` (from a `.ncproj` bundle or a MusicXML import), a `Transport`, a
`SoundEngine`, and the arrange window.

The one join that matters is three lines long: the transport is advanced from
`SoundEngine.set_block_listener()`, so playback position comes from the clock
that actually produces sound. Everything else -- the playhead, the readouts,
the scroll-follow -- reads that position rather than keeping its own.

**Audio is optional.** No output device, or no `[synth]` extra, means the
window still opens and still shows the project; the transport bar says why
there is no sound. That is the same posture the score editor already takes
(`sound=unavailable`) and the reason a missing optional dependency has never
taken a view down in this project.
"""

import argparse
import os
import sys

#: The Wayland `app_id` / X11 `WM_CLASS` of the main window (ticket #194).
#:
#: Qt derives both from `QGuiApplication.setDesktopFileName()`, so this is
#: deliberately the basename of `packaging/visualnote.desktop` rather than
#: the Python package name: matching the installed desktop entry is what
#: makes the id mean anything to the desktop (icon lookup, the xdg-desktop
#: portal's app registration), and a name that matches nothing on disk is
#: just a string a window rule happens to agree with.
#:
#: #194's own example wrote `notecolor`; it was written before anyone had
#: checked that a desktop entry already existed under another name. The
#: window-rule snippets in the `running-the-app` skill use the value here.
APP_ID = "visualnote"

#: Reserved for the detached Synth View window (#191 standing decision 5, and
#: the shell & navigation map #192). Not used yet -- it exists so the detach
#: work inherits a name that is already documented and already distinct, and
#: so a user's Hyprland rule for it can be written before the feature lands.
SYNTH_APP_ID = "visualnote-synth"


def load_project(path):
    """A Project from whatever the user pointed at.

    Three inputs, one shape out: a `.ncproj` bundle, a MusicXML file, or
    nothing at all (an empty project, so the app opens with no arguments).
    """
    from notecolor.project.bundle import BUNDLE_SUFFIX, load_project as load_bundle
    from notecolor.project.model import Project

    if not path:
        return Project(name="Untitled")
    path = str(path)
    if path.endswith(BUNDLE_SUFFIX) or os.path.isdir(path):
        return load_bundle(path)
    from notecolor.notation.musicxml_import import import_musicxml

    return import_musicxml(path)


def start_audio(project):
    """`(transport, player, engine, error)`, with `error` set if there is no sound.

    Never raises: an audio failure degrades to a silent-but-usable window.
    """
    from notecolor.audio.transport import Transport

    transport = Transport(project.tempo_map)
    try:
        from notecolor.audio.player import ProjectPlayer
        from notecolor.audio.sound_engine import SoundEngine

        engine = SoundEngine()
        transport.sample_rate = engine.sample_rate
        player = ProjectPlayer(project, engine, transport)
        engine.set_block_listener(player.on_block)
        engine.ensure_started()
        return transport, player, engine, None
    except Exception as exc:                     # noqa: BLE001 -- see docstring
        # Deliberately broad. The failures here are a missing [synth] extra, a
        # missing output device, a busy device, a driver refusing the block
        # size -- and none of them is a reason to refuse to show the project.
        return transport, None, None, f"{type(exc).__name__}: {exc}"


def build_parser():
    parser = argparse.ArgumentParser(
        prog="visualnote",
        description="VisualNote Studio -- the windowed front-end. "
                    "The terminal tools live under `virtualnote`.")
    parser.add_argument("file", nargs="?",
                        help="a .ncproj bundle or a MusicXML file; "
                             "omit to open an empty project")
    return parser


def build_app():
    """The one `QApplication` this project makes, identity and font included.

    Factored out of `main()` so the screenshot harness (`scripts/uishot.py`,
    ticket #194) can stand a single view up in isolation and still get the
    *same* application font and app id as the real app. A harness that
    configures its own `QApplication` screenshots a program that does not
    exist -- which is the class of defect #194 was opened to end.
    """
    from PySide6 import QtWidgets

    from notecolor.gui import theme

    app = QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("visualnote studio")
    app.setApplicationDisplayName("visualnote studio")
    app.setDesktopFileName(APP_ID)
    app.setFont(theme.font(9))
    return app


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        project = load_project(args.file)
    except Exception as exc:                     # noqa: BLE001
        print(f"visualnote: cannot open {args.file}: {exc}", file=sys.stderr)
        return 1

    from PySide6 import QtWidgets

    from notecolor.gui import theme
    from notecolor.gui.studio import StudioWindow

    app = build_app()

    transport, player, engine, error = start_audio(project)
    window = StudioWindow(project, transport=transport, player=player,
                          audio_error=error, path=args.file)
    window.show()
    try:
        return app.exec()
    finally:
        if engine is not None:
            engine.stop()


if __name__ == "__main__":
    sys.exit(main())
