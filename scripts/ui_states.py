"""Named UI states -- "show me `synth-view`" -- for the screenshot harness.

This is the *inside* half of ticket #194. `scripts/uishot.py` starts a
nested headless compositor and then runs this file inside it; this file
knows how to stand one view up in a known state and hold it on screen.

A state is a function that takes nothing and returns the top-level widget
to shoot. Register it in `STATES` and a ticket can say "screenshot
`drawer-expanded`" and get the same frame every time. Adding one is meant
to be a short function and a dict entry, nothing more.

Two rules keep the shots honest:

* The `QApplication` comes from `notecolor.gui.app.build_app()`, so the
  font and app id are the real ones. A harness that builds its own
  application screenshots a program that does not exist.
* Audio is never opened. Every state runs against a stub controller with
  no `SoundEngine`, exactly as `tests/test_synth_view.py` does -- the
  headless compositor has no sound device, and a view that needed one to
  render would be a bug in the view.

Run directly for a quick eyeball inside an existing compositor:

    .venv/bin/python scripts/ui_states.py synth-view

It prints `READY <name>` on stdout once the window has actually painted a
frame, so the harness can capture on a signal rather than a guessed sleep.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "src"))


class _NullController:
    """The `SynthView` controller surface, wired to nothing.

    Mirrors `tests/test_synth_view.py`'s `StubController`. Kept as its own
    copy rather than imported from `tests/`: a screenshot harness that
    breaks because a test file was refactored is a harness nobody trusts.
    """

    def __init__(self, patch):
        self._patch = patch

    def sound_engine_provider(self):
        return None

    def initial_patch(self):
        return self._patch

    def record_note_on(self, pitch, velocity=1.0):
        pass

    def record_note_off(self, pitch):
        pass

    def toggle_recording(self):
        pass

    def is_recording(self):
        return False

    def toggle_play(self):
        pass

    def panic(self):
        pass


def _synth_view():
    """The Synth View as it opens: three default modules, drawer as saved."""
    from notecolor.gui.synth_view import SynthView
    from notecolor.settings import patch_format

    return SynthView(_NullController(patch_format.new_patch(name="Init")))


def _synth_view_drawer_expanded():
    view = _synth_view()
    view.drawer.set_expanded(True)
    return view


def _synth_view_drawer_collapsed():
    view = _synth_view()
    view.drawer.set_expanded(False)
    return view


def _footer_at(fraction):
    """The Synth View with the footer sized `fraction` of floor..ceiling.

    Sized through `splitter.setSizes()`, which is the same clamp a real
    drag goes through (`_DragHandle` computes a size and calls it) -- so
    these shots show the real endpoints rather than a state only a test
    can reach. `show()` first: the ceiling depends on the window's actual
    height, which is not known until the compositor has given it one.
    """
    def build():
        view = _synth_view()
        view.show()
        from PySide6 import QtWidgets

        QtWidgets.QApplication.processEvents()
        splitter = view.splitter
        floor = splitter.floor
        target = round(floor + (splitter.effective_ceiling() - floor) * fraction)
        splitter.setSizes([sum(splitter.sizes()) - target, target])
        return view

    return build


def _patched_canvas(refuse=False):
    """The Synth View with a real patch on it (ticket #211): sound cables
    across the Mix stripe, two modulation cables on knobs, and a feedback
    loop through the Delay.

    With `refuse`, it also drops a per-note output onto a once-only input
    and leaves the refusal standing -- the state the callout's wording and
    placement have to be judged in.
    """
    def build():
        from PySide6 import QtCore, QtWidgets
        from notecolor.gui import patch_graph

        view = _synth_view()
        view.resize(1280, 800)
        view.show()
        QtWidgets.QApplication.processEvents()
        # The canvas is what this state is about, so give it the room the
        # footer is not using -- the same clamp a real drag goes through.
        splitter = view.splitter
        splitter.setSizes([sum(splitter.sizes()) - splitter.floor, splitter.floor])
        QtWidgets.QApplication.processEvents()

        for type_key in ("osc2", "filter_env", "lfo", "delay", "chorus"):
            view.canvas.spawn_module(type_key, QtCore.QPoint(0, 0))
        view.canvas.tidy()

        layer = view.patch_layer
        graph = layer.graph
        for source, dest in (("osc2", "filter"), ("mix", "delay"),
                             ("delay", "chorus"), ("chorus", "delay")):
            target = patch_graph.Target("socket", dest)
            if graph.judge(source, target).ok:
                graph.connect(source, target)
        for source, node, knob in (("filter_env", "filter", "Cutoff"),
                                   ("lfo", "osc1", "Fine")):
            target = patch_graph.Target("knob", node, knob)
            if graph.judge(source, target).ok:
                graph.connect(source, target)
        layer.relayout()

        # Let the cables actually hang: the physics runs on the layer's
        # own timer, and a shot taken on frame one would show them all
        # strung tight.
        for _ in range(120):
            layer._tick()
        if refuse:
            target = patch_graph.Target("socket", "chorus")
            layer.refuse(target, graph.judge("osc1", target).reason)
        QtWidgets.QApplication.processEvents()
        return view

    return build


#: name -> zero-argument builder returning the top-level widget to shoot.
STATES = {
    "synth-view": _synth_view,
    "patch-canvas": _patched_canvas(),
    "patch-refusal": _patched_canvas(refuse=True),
    "drawer-expanded": _synth_view_drawer_expanded,
    "drawer-collapsed": _synth_view_drawer_collapsed,
    "footer-floor": _footer_at(0.0),
    "footer-mid": _footer_at(0.5),
    "footer-ceiling": _footer_at(1.0),
}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ui_states.py",
        description="Show one named UI state and hold it (see scripts/uishot.py).")
    parser.add_argument("state", nargs="?", choices=sorted(STATES),
                        help="the state to show; omit with --list")
    parser.add_argument("--list", action="store_true",
                        help="print the known state names and exit")
    parser.add_argument("--size", metavar="WxH",
                        help="resize the window before showing it")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.list:
        for name in sorted(STATES):
            print(name)
        return 0
    if not args.state:
        build_parser().error("a state name is required (or --list)")

    from PySide6 import QtCore

    from notecolor.gui.app import build_app

    app = build_app()
    widget = STATES[args.state]()
    if args.size:
        width, _, height = args.size.partition("x")
        widget.resize(int(width), int(height))
    widget.show()

    # `READY` goes out only after the window has actually painted, so the
    # harness captures a drawn frame rather than whatever the compositor
    # had up when a fixed sleep expired. Two zero-timers after the first
    # exposure: the first returns to the event loop, the second runs after
    # the frame it queued has been committed.
    def announce():
        QtCore.QTimer.singleShot(0, lambda: (
            print(f"READY {args.state}", flush=True)))

    QtCore.QTimer.singleShot(0, announce)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
