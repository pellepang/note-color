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

    app = QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("visualnote studio")
    app.setApplicationDisplayName("visualnote studio")
    app.setFont(theme.font(9))

    transport, player, engine, error = start_audio(project)
    window = StudioWindow(project, transport=transport, player=player,
                          audio_error=error)
    window.show()
    try:
        return app.exec()
    finally:
        if engine is not None:
            engine.stop()


if __name__ == "__main__":
    sys.exit(main())
