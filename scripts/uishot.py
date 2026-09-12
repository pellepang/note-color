"""Screenshot a named UI state in a nested headless compositor (ticket #194).

    .venv/bin/python scripts/uishot.py synth-view -o /tmp/synth.png

Why this exists: every GUI ticket on this project used to end with an
agent changing layout code, being unable to see the result, handing it
over, and the user finding the obvious defect (#164/#168 went round that
loop twice). This is the missing eye.

Why a *nested* compositor, and not the two easier things:

* Not the user's live session. On Hyprland the app tiles into whatever
  they are doing; the shot is worthless and their workspace is disturbed.
  This is the user's own report (#191, #194).
* Not `QT_QPA_PLATFORM=offscreen`. No compositor, and it lies about fonts
  and DPI -- which is the exact class of defect the harness exists to
  catch. A screenshot that renders text at the wrong metrics cannot tell
  you whether a label clips.

So: a second, real compositor on a virtual output at the user's real
resolution and scale, on its own `WAYLAND_DISPLAY`, with `grim` pointed
at *that*. Nothing touches the user's session; nothing steals focus.

**The standing limit** (#191, decision 8): this is a pre-handover
self-check, never proof. An agent does not declare a GUI fix correct from
its own headless screenshot. Taste goes to the user, batched, each
question paired with the shot of the exact state being asked about.

## Compositor requirement

Needs a wlroots compositor that can run a *headless-only* backend --
`sway` (this is what the tooling below assumes) or `cage`.

Hyprland cannot be used for this even though it is what the user runs.
0.56 chooses its backend from the environment (wayland if
`WAYLAND_DISPLAY` is set, else DRM) and exposes no `AQ_BACKENDS` /
`WLR_BACKENDS` / headless-only switch; with the parent display unset and
DRM starved via `AQ_DRM_DEVICES`, `CBackend::create()` simply fails.
Nested under the user's session it uses the *wayland* backend, which
opens a real window in their session -- the thing this harness must not
do. Verified on 0.56.2, 2026-09-12.
"""

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
VENV_PYTHON = os.path.join(REPO, ".venv", "bin", "python")

#: What the app calls itself to the compositor -- kept in step with
#: `notecolor.gui.app.APP_ID` rather than duplicated by hand.
sys.path.insert(0, os.path.join(REPO, "src"))
from notecolor.gui.app import APP_ID          # noqa: E402

DEFAULT_OUTPUT = "HEADLESS-1"

INSTALL_HINT = """\
uishot needs a compositor that can run headless. Neither `sway` nor `cage`
is installed, and Hyprland cannot do it (see this file's docstring).

    sudo pacman -S sway

is ~7 MiB with wlroots0.20 and is the option ticket #194 names first.
"""


def detect_scale():
    """The user's real output scale, so font metrics match what they see.

    Read from their running Hyprland when there is one; 1.0 otherwise.
    Guessing wrong here is the whole failure mode -- a shot at the wrong
    scale answers "does this label clip" with confidence and no accuracy.
    """
    try:
        out = subprocess.run(["hyprctl", "-j", "monitors"], check=True,
                             capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return 1.0
    import json

    try:
        monitors = json.loads(out)
    except ValueError:
        return 1.0
    for monitor in monitors:
        if monitor.get("focused"):
            return float(monitor.get("scale", 1.0))
    return float(monitors[0].get("scale", 1.0)) if monitors else 1.0


def detect_resolution():
    """The user's real output resolution, same reasoning as `detect_scale`."""
    try:
        out = subprocess.run(["hyprctl", "-j", "monitors"], check=True,
                             capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return "1920x1080"
    import json

    try:
        monitors = json.loads(out)
    except ValueError:
        return "1920x1080"
    chosen = next((m for m in monitors if m.get("focused")), None)
    chosen = chosen or (monitors[0] if monitors else None)
    if not chosen:
        return "1920x1080"
    return f"{chosen.get('width', 1920)}x{chosen.get('height', 1080)}"


def free_display_name():
    """A `wayland-N` name nothing is listening on yet."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
    for n in range(20, 100):
        name = f"wayland-{n}"
        if not os.path.exists(os.path.join(runtime, name)):
            return name
    raise RuntimeError("no free wayland-N socket name in 20..99")


class NestedCompositor:
    """A headless sway on its own `WAYLAND_DISPLAY`, torn down on exit.

    Context-manager so an exception, a timeout or a Ctrl-C all take the
    compositor with them. Stray compositors are the one way this harness
    could damage a session it promises not to touch.
    """

    def __init__(self, resolution, scale, verbose=False):
        self.resolution = resolution
        self.scale = scale
        self.verbose = verbose
        self.display = free_display_name()
        self.proc = None
        self.env = None

    def __enter__(self):
        sway = shutil.which("sway")
        if not sway:
            raise RuntimeError(INSTALL_HINT)

        config = os.path.join(
            os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
            f"uishot-{os.getpid()}.conf")
        with open(config, "w") as handle:
            # No bar, no bindings, no gaps, no titlebars: the shot should
            # be the app, not a compositor's furniture.
            handle.write(
                f"output {DEFAULT_OUTPUT} resolution {self.resolution}"
                f" scale {self.scale}\n"
                "default_border none\n"
                "titlebar_border_thickness 0\n"
                "gaps inner 0\n"
                "gaps outer 0\n"
                "focus_follows_mouse no\n")
        self._config = config

        # Sockets that exist *before* sway starts, so `_await_socket` can
        # recognise sway's by its appearance rather than by guessing. The
        # guess is how an early version connected the app to the user's
        # own compositor -- the one outcome this must never have.
        self._sockets_before = self._sockets()

        env = dict(os.environ)
        env.pop("WAYLAND_DISPLAY", None)
        env.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
        env.pop("DISPLAY", None)
        env["WLR_BACKENDS"] = "headless"
        env["WLR_LIBINPUT_NO_DEVICES"] = "1"
        env["XDG_CURRENT_DESKTOP"] = "sway"

        self.proc = subprocess.Popen(
            [sway, "--config", config, "--unsupported-gpu"],
            env=env,
            stdout=None if self.verbose else subprocess.DEVNULL,
            stderr=None if self.verbose else subprocess.DEVNULL,
            start_new_session=True)

        # sway picks its own socket name; find the one that appeared.
        self.display = self._await_socket()
        self.env = dict(os.environ)
        self.env.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
        self.env["WAYLAND_DISPLAY"] = self.display
        self.env["XDG_CURRENT_DESKTOP"] = "sway"
        return self

    @staticmethod
    def _sockets():
        runtime = os.environ.get("XDG_RUNTIME_DIR",
                                 "/run/user/%d" % os.getuid())
        return {name for name in os.listdir(runtime)
                if name.startswith("wayland-") and not name.endswith(".lock")}

    def _await_socket(self, timeout=15.0):
        """sway's own socket, identified by having appeared just now.

        Strictly the new one, with no "any other socket" fallback: the
        fallback picked the user's *live* compositor on a slow start, so
        the app opened a real window in their session and `grim` shot
        their desktop. Timing out is the correct outcome instead.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"sway exited with {self.proc.returncode} before it "
                    f"came up; rerun with -v to see why")
            new = sorted(self._sockets() - self._sockets_before)
            if new:
                return new[0]
            time.sleep(0.1)
        raise RuntimeError("sway never created a wayland socket")

    def __exit__(self, *_exc):
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except OSError:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except OSError:
                    self.proc.kill()
        try:
            os.unlink(self._config)
        except (OSError, AttributeError):
            pass
        return False


def await_ready(proc, timeout):
    """Block until the state runner says it has painted, or time out."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.05)
            continue
        if line.startswith("READY"):
            return True
    return False


def capture(env, path, settle):
    """`grim` the nested output after letting the frame settle."""
    time.sleep(settle)
    subprocess.run(["grim", path], env=env, check=True)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="uishot.py",
        description="Screenshot a named UI state in a nested headless "
                    "compositor, without touching the user's session.")
    parser.add_argument("state", nargs="?",
                        help="a name from `scripts/ui_states.py --list`")
    parser.add_argument("-o", "--output", default=None,
                        help="PNG path (default: <state>.png in the cwd)")
    parser.add_argument("--resolution", default=None,
                        help="virtual output resolution "
                             "(default: the user's real one)")
    parser.add_argument("--scale", default=None, type=float,
                        help="virtual output scale "
                             "(default: the user's real one)")
    parser.add_argument("--window-size", default=None, metavar="WxH",
                        help="resize the window before shooting it")
    parser.add_argument("--settle", default=0.6, type=float,
                        help="seconds to wait after READY before grim")
    parser.add_argument("--timeout", default=30.0, type=float,
                        help="seconds to wait for the state to paint")
    parser.add_argument("--list", action="store_true",
                        help="print the known state names and exit")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="let the compositor and app log to the terminal")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    python = VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable
    states = os.path.join(HERE, "ui_states.py")

    if args.list:
        return subprocess.run([python, states, "--list"]).returncode
    if not args.state:
        build_parser().error("a state name is required (or --list)")

    if not shutil.which("grim"):
        print("uishot: `grim` is not installed", file=sys.stderr)
        return 1

    resolution = args.resolution or detect_resolution()
    scale = args.scale if args.scale is not None else detect_scale()
    path = os.path.abspath(args.output or f"{args.state}.png")

    command = [python, states, args.state]
    if args.window_size:
        command += ["--size", args.window_size]

    try:
        with NestedCompositor(resolution, scale, args.verbose) as comp:
            app = subprocess.Popen(
                command, env=comp.env, cwd=REPO,
                stdout=subprocess.PIPE, text=True,
                stderr=None if args.verbose else subprocess.DEVNULL,
                start_new_session=True)
            try:
                if not await_ready(app, args.timeout):
                    print(f"uishot: `{args.state}` never painted within "
                          f"{args.timeout:g}s; rerun with -v", file=sys.stderr)
                    return 1
                capture(comp.env, path, args.settle)
            finally:
                if app.poll() is None:
                    try:
                        os.killpg(os.getpgid(app.pid), signal.SIGTERM)
                    except OSError:
                        app.terminate()
                    try:
                        app.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        app.kill()
    except RuntimeError as exc:
        print(f"uishot: {exc}", file=sys.stderr)
        return 1

    print(f"{path}  ({resolution} @ {scale:g}x, app_id={APP_ID})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
