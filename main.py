"""Real-time audio -> color display.

mic or system-output loopback -> AudioCapture (callback thread)
    -> analysis thread: ring buffer -> YIN pitch detect -> NoteSmoother -> color_map
    -> single-slot queue
    -> main thread: ColorAnimator -> Display (pygame window, or terminal)

GUI controls: Esc/close window to quit, F to toggle fullscreen, D to toggle
debug overlay, Up/Down to adjust pitch-detection sensitivity.
Terminal mode: Ctrl+C to quit, Up/Down for sensitivity, M to toggle the
audio source (mic <-> loopback) live. No display server required.
"""

import argparse
import math
import os
import queue
import select
import sys
import threading
import time

import numpy as np

import config
from audio_capture import AudioCapture, resolve_loopback_device
from pitch_detect import detect_pitch
from note_smoother import NoteSmoother
from color_map import note_to_hsl, hsl_to_rgb255, fifths_index, NOTE_NAMES, NOTE_NAMES_FIFTHS
from animation import ColorAnimator

try:
    import termios
    import tty
    _HAS_TERMIOS = True
except ImportError:  # Windows has neither module
    _HAS_TERMIOS = False

SENSITIVITY_STEP = 1.25
SENSITIVITY_MIN = 0.1
SENSITIVITY_MAX = 10.0


class SourceState:
    """Shared between the render thread (owns the 'm' hotkey and the
    AudioCapture) and the status line (reads .value, .error every frame).
    Only the render thread ever writes it, so plain attribute access is
    fine, same rationale as Sensitivity."""

    def __init__(self, value):
        self.value = value
        self.error = None


class Sensitivity:
    """Shared between the analysis thread (reads .value every hop) and
    whichever thread owns the render loop (writes .value on a hotkey).
    Plain attribute access is safe here under CPython's GIL -- the value is
    read/written as a whole float, and staleness by one hop is harmless."""

    def __init__(self, value):
        self.value = value

    def adjust(self, factor):
        self.value = min(max(self.value * factor, SENSITIVITY_MIN), SENSITIVITY_MAX)


class RawKeys:
    """Non-blocking single-key reads from stdin, for terminal hotkeys.
    Inert (poll() always returns None) when stdin isn't a real TTY or
    termios/tty aren't available (Windows) -- terminal modes keep working,
    just without live hotkeys, in that case."""

    def __init__(self):
        self._active = _HAS_TERMIOS and sys.stdin.isatty()
        self._old_settings = None
        if self._active:
            self._old_settings = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())

    _ARROW_BY_FINAL_BYTE = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT"}

    def poll(self):
        if not self._active or not select.select([sys.stdin], [], [], 0)[0]:
            return None
        ch = sys.stdin.read(1)
        if ch != "\x1b":
            return ch
        # Arrow keys send ESC [ <letter> as one burst; if nothing follows
        # immediately it was a lone Escape keypress, not an arrow key.
        if not select.select([sys.stdin], [], [], 0)[0]:
            return None
        if sys.stdin.read(1) != "[" or not select.select([sys.stdin], [], [], 0)[0]:
            return None
        return self._ARROW_BY_FINAL_BYTE.get(sys.stdin.read(1))

    def restore(self):
        if self._active:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._old_settings)


def _positive_float(text):
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return value


def _handle_sensitivity_key(key, sensitivity):
    if key == "DOWN":
        sensitivity.adjust(1.0 / SENSITIVITY_STEP)
    elif key == "UP":
        sensitivity.adjust(SENSITIVITY_STEP)


def _handle_source_key(key, capture, source_state):
    if key is None or key.lower() != "m":
        return
    new_source = "loopback" if source_state.value == "mic" else "mic"
    try:
        if new_source == "loopback":
            device = resolve_loopback_device()
        else:
            os.environ.pop("PULSE_SOURCE", None)
            device = None
    except RuntimeError as exc:
        source_state.error = str(exc)
        return
    capture.restart(device)
    source_state.value = new_source
    source_state.error = None


def analysis_loop(capture, result_queue, stop_event, color_scheme, sensitivity):
    ring = np.zeros(config.WINDOW_SIZE, dtype=np.float64)
    smoother = NoteSmoother(config, sensitivity.value)

    while not stop_event.is_set():
        try:
            block = capture.get_block(timeout=0.5)
        except queue.Empty:
            continue

        block = block.astype(np.float64)
        ring = np.concatenate([ring[len(block):], block])
        rms = float(np.sqrt(np.mean(block * block))) if len(block) else 0.0

        smoother.set_sensitivity(sensitivity.value)
        freq, confidence = detect_pitch(ring, config.SAMPLE_RATE, config.FMIN, config.FMAX, config.YIN_THRESHOLD)
        pitch_class, octave, is_onset = smoother.update(freq, confidence, rms)

        if pitch_class is None:
            target_rgb = config.IDLE_RGB
            label = "-"
            fifths_idx = None
        else:
            hue, sat, light = note_to_hsl(pitch_class, octave, scheme=color_scheme)
            target_rgb = hsl_to_rgb255(hue, sat, light)
            label = f"{NOTE_NAMES[pitch_class]}{octave}"
            fifths_idx = fifths_index(pitch_class)

        item = (target_rgb, is_onset, label, freq, confidence, rms, fifths_idx, pitch_class, octave)
        _overwrite(result_queue, item)


def _overwrite(q, item):
    try:
        q.get_nowait()
    except queue.Empty:
        pass
    try:
        q.put_nowait(item)
    except queue.Full:
        pass


def _status_text(label, freq, confidence, rms, sensitivity, source_state=None):
    freq_str = f"{freq:6.1f}Hz" if freq else "  --  "
    text = (f"note={label:<4s} freq={freq_str} conf={confidence:.2f} rms={rms:.4f} "
            f"sens={sensitivity.value:.2f} (up/down)")
    if source_state is not None:
        text += f"  src={source_state.value} (m)"
        if source_state.error:
            text += f"  [source switch failed: {source_state.error}]"
    return text


def run_terminal_fill(result_queue, sensitivity, capture, source_state):
    from terminal_display import TerminalDisplay

    display = TerminalDisplay(fps=config.TERMINAL_FPS)
    animator = ColorAnimator(config.CROSSFADE_TAU_MS, config.PULSE_DECAY_MS, config.ONSET_PULSE_BOOST)
    keys = RawKeys()

    target_rgb, is_onset, label, freq, confidence, rms = config.IDLE_RGB, False, "-", 0.0, 0.0, 0.0
    dt = 1.0 / display.fps

    try:
        while True:
            key = keys.poll()
            _handle_sensitivity_key(key, sensitivity)
            _handle_source_key(key, capture, source_state)
            try:
                (target_rgb, is_onset, label, freq, confidence, rms,
                 _fifths_idx, _pitch_class, _octave) = result_queue.get_nowait()
            except queue.Empty:
                is_onset = False

            rgb = animator.update(dt, target_rgb, is_onset)
            status = _status_text(label, freq, confidence, rms, sensitivity, source_state) + "  (Ctrl+C to quit)"
            display.render(rgb, status)
            time.sleep(dt)
    except KeyboardInterrupt:
        pass
    finally:
        keys.restore()
        display.quit()


def run_terminal_wheel(result_queue, sensitivity, capture, source_state):
    from terminal_wheel_display import WheelDisplay

    display = WheelDisplay(fps=config.WHEEL_FPS)
    pulse_decay = config.PULSE_DECAY_MS / 1000.0
    dt = 1.0 / display.fps
    keys = RawKeys()

    active_index = None
    label, freq, confidence, rms = "-", 0.0, 0.0, 0.0
    pulse = 0.0

    try:
        while True:
            key = keys.poll()
            _handle_sensitivity_key(key, sensitivity)
            _handle_source_key(key, capture, source_state)
            is_onset = False
            try:
                (_target_rgb, is_onset, label, freq, confidence, rms,
                 active_index, _pitch_class, _octave) = result_queue.get_nowait()
            except queue.Empty:
                pass

            pulse = 1.0 if is_onset else pulse * math.exp(-dt / pulse_decay)
            status = _status_text(label, freq, confidence, rms, sensitivity, source_state) + "  (Ctrl+C to quit)"
            display.render(active_index, pulse, status)
            time.sleep(dt)
    except KeyboardInterrupt:
        pass
    finally:
        keys.restore()
        display.quit()


def _tab_note_rgb(pitch_class):
    """A note's tab-view glyph color. Always uses the fifths hue mapping,
    same as the wheel view (independent of --color-scheme, for the same
    reason the wheel is: this is a fixed note-identity color, not a
    representation of the currently-selected scheme), so a note reads as
    the same color in `tab` as it does in `wheel` -- e.g. B is green in
    both, not pink in one and green in the other. Uses a fixed lightness
    (config.TAB_NOTE_LIGHTNESS) instead of scaling by octave, unlike
    fill/GUI: octave already drives the note's row on the staff, and
    0.5 is where a given hue/saturation looks most vivid/saturated in
    HSL, rather than washing out toward white like a high lightness does."""
    if pitch_class is None:
        return config.IDLE_RGB
    hue, sat, _light = note_to_hsl(pitch_class, config.MAX_OCTAVE, scheme="fifths")
    return hsl_to_rgb255(hue, sat, config.TAB_NOTE_LIGHTNESS)


def _tab_note_label(pitch_class, octave):
    """Same fifths spelling as the wheel view (e.g. Ab, not G#), for the
    same reason as _tab_note_rgb: a note should read identically in `tab`
    as it does in `wheel`, independent of --color-scheme."""
    if pitch_class is None:
        return "-"
    return f"{NOTE_NAMES_FIFTHS[pitch_class]}{octave}"


def run_terminal_tab(result_queue, scroll_mode, dump_file, sensitivity, capture, source_state):
    from terminal_tab_display import TabDisplay

    display = TabDisplay(fps=config.TAB_FPS)
    dt = 1.0 / display.fps
    fix_interval = 1.0 / config.TAB_FIX_HOPS_PER_SEC
    time_since_tick = 0.0
    keys = RawKeys()

    label, freq, confidence, rms = "-", 0.0, 0.0, 0.0
    pitch_class, octave = None, None

    resolved_dump = dump_file or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"note_history_{time.strftime('%Y%m%d_%H%M%S')}.txt",
    )

    try:
        while True:
            key = keys.poll()
            _handle_sensitivity_key(key, sensitivity)
            _handle_source_key(key, capture, source_state)
            got_new = False
            is_onset = False
            try:
                (_target_rgb, is_onset, label, freq, confidence, rms,
                 _fifths_idx, pitch_class, octave) = result_queue.get_nowait()
                got_new = True
            except queue.Empty:
                pass

            glyph_rgb = _tab_note_rgb(pitch_class)
            tab_label = _tab_note_label(pitch_class, octave)

            if scroll_mode == "onset":
                if got_new and is_onset:
                    display.push(pitch_class, octave, glyph_rgb, tab_label)
            else:  # "fix"
                time_since_tick += dt
                if time_since_tick >= fix_interval:
                    time_since_tick -= fix_interval
                    display.push(pitch_class, octave, glyph_rgb, tab_label)

            status = (_status_text(tab_label, freq, confidence, rms, sensitivity, source_state)
                      + f"  [{scroll_mode}] (Ctrl+C to quit)")
            display.render(status)
            time.sleep(dt)
    except KeyboardInterrupt:
        pass
    finally:
        keys.restore()
        try:
            display.dump_ansi(resolved_dump)
        finally:
            display.quit()


def run_gui(result_queue, fullscreen, start_debug, sensitivity):
    import pygame
    from display import Display

    display = Display(config.WINDOW_SIZE_PX, fullscreen=fullscreen, fps=config.FPS)
    animator = ColorAnimator(config.CROSSFADE_TAU_MS, config.PULSE_DECAY_MS, config.ONSET_PULSE_BOOST)
    font = pygame.font.SysFont("monospace", 18)

    show_debug = start_debug
    target_rgb, is_onset, label, freq, confidence, rms = config.IDLE_RGB, False, "-", 0.0, 0.0, 0.0
    dt = 1.0 / config.FPS

    try:
        while display.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    display.running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        display.running = False
                    elif event.key == pygame.K_f:
                        display.toggle_fullscreen()
                    elif event.key == pygame.K_d:
                        show_debug = not show_debug
                    elif event.key == pygame.K_DOWN:
                        sensitivity.adjust(1.0 / SENSITIVITY_STEP)
                    elif event.key == pygame.K_UP:
                        sensitivity.adjust(SENSITIVITY_STEP)
            if not display.running:
                break

            try:
                (target_rgb, is_onset, label, freq, confidence, rms,
                 _fifths_idx, _pitch_class, _octave) = result_queue.get_nowait()
            except queue.Empty:
                is_onset = False

            rgb = animator.update(dt, target_rgb, is_onset)
            display.screen.fill(rgb)
            if show_debug:
                text = font.render(_status_text(label, freq, confidence, rms, sensitivity), True, (255, 255, 255))
                display.screen.blit(text, (10, 10))
            pygame.display.flip()
            dt = display.clock.tick(display.fps) / 1000.0
    finally:
        display.quit()


def main():
    parser = argparse.ArgumentParser(description="Real-time audio-to-color display")
    parser.add_argument("--fullscreen", action="store_true", help="GUI mode: start fullscreen")
    parser.add_argument("--debug", action="store_true", help="GUI mode: show debug overlay on start")
    parser.add_argument("--terminal", action="store_true", help="run in the terminal instead of a GUI window")
    parser.add_argument("--view", choices=["fill", "wheel", "tab"], default="fill",
                         help="terminal mode only: 'fill' (solid color), 'wheel' (circle-of-fifths diagram), "
                              "or 'tab' (scrolling grand-staff note history)")
    parser.add_argument("--color-scheme", choices=["chromatic", "fifths"], default=config.DEFAULT_COLOR_SCHEME,
                         help="hue mapping for the fill/GUI views (wheel and tab views always use "
                              "the fifths layout)")
    parser.add_argument("--scroll", choices=["fix", "onset"], default=config.DEFAULT_SCROLL_MODE,
                         help="'tab' view only: 'fix' pushes a new column every tick; "
                              "'onset' pushes one only on a new note-attack")
    parser.add_argument("--dump-file", default=None,
                         help="'tab' view only: path for the ANSI session note-history dump written on quit "
                              "(default: note_history_<timestamp>.txt next to main.py)")
    parser.add_argument("--sensitivity", type=_positive_float, default=config.DEFAULT_SENSITIVITY,
                         help="pitch-detection sensitivity multiplier (default 1.0); higher registers "
                              "quieter/softer playing more readily. Adjustable live with Up/Down in any mode.")
    parser.add_argument("--source", choices=["mic", "loopback"], default="mic",
                         help="'mic' (default) listens to the microphone; 'loopback' listens to the "
                              "computer's own audio output instead (PipeWire/PulseAudio on Linux only), "
                              "for testing without playing anything out loud")
    args = parser.parse_args()

    device = None
    if args.source == "loopback":
        try:
            device = resolve_loopback_device()
        except RuntimeError as exc:
            parser.error(str(exc))

    capture = AudioCapture(config.SAMPLE_RATE, config.BLOCK_SIZE, config.QUEUE_SIZE, device=device)
    capture.start()

    result_queue = queue.Queue(maxsize=1)
    stop_event = threading.Event()
    sensitivity = Sensitivity(args.sensitivity)
    source_state = SourceState(args.source)
    analysis_thread = threading.Thread(
        target=analysis_loop, args=(capture, result_queue, stop_event, args.color_scheme, sensitivity), daemon=True
    )
    analysis_thread.start()

    try:
        if args.terminal:
            if args.view == "wheel":
                run_terminal_wheel(result_queue, sensitivity, capture, source_state)
            elif args.view == "tab":
                run_terminal_tab(result_queue, args.scroll, args.dump_file, sensitivity, capture, source_state)
            else:
                run_terminal_fill(result_queue, sensitivity, capture, source_state)
        else:
            run_gui(result_queue, args.fullscreen, args.debug, sensitivity)
    finally:
        stop_event.set()
        capture.stop()


if __name__ == "__main__":
    main()
