"""Real-time audio -> color display.

mic or system-output loopback -> AudioCapture (callback thread)
    -> analysis thread: ring buffer -> YIN pitch detect -> NoteSmoother -> color_map
    -> single-slot queue
    -> main thread: ColorAnimator -> Display (pygame window, or terminal)

GUI controls: Esc/close window to quit, F to toggle fullscreen, D to toggle
debug overlay, Up/Down to adjust pitch-detection sensitivity.
Terminal mode: Ctrl+C to quit, Up/Down for sensitivity, M to toggle the
audio source (mic <-> loopback) live, P to toggle chord mode (chroma-vector
chord recognition, up to 6 simultaneous notes) live -- terminal views only,
not the GUI. No display server required.
"""

import argparse
import math
import os
import queue
import select
import sys
import threading
import time
from typing import NamedTuple, Optional

import numpy as np

import chroma
import config
import multipitch
from audio_capture import AudioCapture, resolve_loopback_device
from pitch_detect import compute_spectrum, detect_pitch
from note_smoother import NoteSmoother
from chord_smoother import ChordSmoother
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


class RenderItem(NamedTuple):
    """Per-hop analysis result, single-slot queue item. The first 9 fields
    are the original monophonic-pipeline shape/order; `note_stack` and
    `chord_name` are chord-mode additions. Existing call sites keep
    unpacking the first 9 positionally, adding a trailing capture for the
    two new fields."""

    target_rgb: tuple
    is_onset: bool
    label: str
    freq: Optional[float]
    confidence: float
    rms: float
    fifths_idx: Optional[int]
    pitch_class: Optional[int]
    octave: Optional[int]
    note_stack: list
    chord_name: Optional[str]


def analysis_loop(capture, result_queue, stop_event, color_scheme, sensitivity):
    ring = np.zeros(config.WINDOW_SIZE, dtype=np.float64)
    smoother = NoteSmoother(config, sensitivity.value)
    chord_smoother = ChordSmoother(config)

    while not stop_event.is_set():
        try:
            block = capture.get_block(timeout=0.5)
        except queue.Empty:
            continue

        block = block.astype(np.float64)
        ring = np.concatenate([ring[len(block):], block])
        rms = float(np.sqrt(np.mean(block * block))) if len(block) else 0.0

        smoother.set_sensitivity(sensitivity.value)
        spectrum = compute_spectrum(ring)
        freq, confidence = detect_pitch(ring, config.SAMPLE_RATE, spectrum, config.FMIN, config.FMAX, config.YIN_THRESHOLD)
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

        # Chord-mode pipeline always runs, regardless of whether any
        # terminal view currently has 'P' toggled on -- validated cheap by
        # the latency budget, and it lets 'P' be a pure render-thread-local
        # flag with no shared state to coordinate.
        main_chroma = chroma.fold(spectrum, config.SAMPLE_RATE)
        bass_chroma = chroma.fold_bass(spectrum, config.SAMPLE_RATE)
        raw_notes = multipitch.detect(
            ring,
            config.SAMPLE_RATE,
            max_notes=config.CHORD_MAX_NOTES,
            min_mag_ratio=config.CHORD_PEAK_MIN_MAG_RATIO,
            harmonic_tolerance_cents=config.CHORD_HARMONIC_TOLERANCE_CENTS,
            max_peak_candidates=config.CHORD_MAX_PEAK_CANDIDATES,
        )
        chord_name, raw_stack = chord_smoother.update(main_chroma, bass_chroma, raw_notes)

        note_stack = []
        for entry in raw_stack:
            stack_hue, stack_sat, stack_light = note_to_hsl(entry["pitch_class"], entry["octave"], scheme=color_scheme)
            note_stack.append(
                {
                    "pitch_class": entry["pitch_class"],
                    "octave": entry["octave"],
                    "confidence": entry["confidence"],
                    "rgb": hsl_to_rgb255(stack_hue, stack_sat, stack_light),
                    "is_bass": entry["is_bass"],
                }
            )

        item = RenderItem(target_rgb, is_onset, label, freq, confidence, rms, fifths_idx, pitch_class, octave,
                           note_stack, chord_name)
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


def _status_text(label, freq, confidence, rms, sensitivity, source_state=None, chord_name=None, chord_mode=False):
    if chord_mode:
        text = f"chord={(chord_name or ''):<14s} sens={sensitivity.value:.2f} (up/down)"
    else:
        freq_str = f"{freq:6.1f}Hz" if freq else "  --  "
        text = (f"note={label:<4s} freq={freq_str} conf={confidence:.2f} rms={rms:.4f} "
                f"sens={sensitivity.value:.2f} (up/down)")
    if source_state is not None:
        text += f"  src={source_state.value} (m)"
        if source_state.error:
            text += f"  [source switch failed: {source_state.error}]"
    return text


def _handle_chord_mode_key(key, chord_mode):
    return not chord_mode if (key is not None and key.lower() == "p") else chord_mode


def _fade_toward(value, target, dt, tau_ms):
    tau = max(tau_ms, 1) / 1000.0
    alpha = 1.0 - math.exp(-dt / tau)
    return value + (target - value) * alpha


def _animate_note_stack(animators, note_stack, dt):
    """note_stack is already sorted lowest-note-first by ChordSmoother --
    that's also bottom-to-top order for fill's proportional bands.
    Returns a list of animated RGB tuples in that same order, one per
    active note (or a single idle color if the stack is empty)."""
    if not note_stack:
        animators.clear()
        return [config.IDLE_RGB]

    active_keys = set()
    bands = []
    for entry in note_stack:
        key = (entry["pitch_class"], entry["octave"])
        active_keys.add(key)
        is_new = key not in animators
        anim = animators.setdefault(
            key, ColorAnimator(config.CROSSFADE_TAU_MS, config.PULSE_DECAY_MS, config.ONSET_PULSE_BOOST)
        )
        bands.append(anim.update(dt, entry["rgb"], is_new))

    for stale_key in [k for k in animators if k not in active_keys]:
        del animators[stale_key]
    return bands


def run_terminal_fill(result_queue, sensitivity, capture, source_state):
    from terminal_display import TerminalDisplay

    display = TerminalDisplay(fps=config.TERMINAL_FPS)
    animator = ColorAnimator(config.CROSSFADE_TAU_MS, config.PULSE_DECAY_MS, config.ONSET_PULSE_BOOST)
    band_animators = {}
    keys = RawKeys()
    chord_mode = False

    target_rgb, is_onset, label, freq, confidence, rms = config.IDLE_RGB, False, "-", 0.0, 0.0, 0.0
    note_stack, chord_name = [], None
    dt = 1.0 / display.fps

    try:
        while True:
            key = keys.poll()
            _handle_sensitivity_key(key, sensitivity)
            _handle_source_key(key, capture, source_state)
            chord_mode = _handle_chord_mode_key(key, chord_mode)
            try:
                (target_rgb, is_onset, label, freq, confidence, rms,
                 _fifths_idx, _pitch_class, _octave, note_stack, chord_name) = result_queue.get_nowait()
            except queue.Empty:
                is_onset = False

            mode_hint = f"mode={'chord' if chord_mode else 'note'}(p)"
            if chord_mode:
                bands = _animate_note_stack(band_animators, note_stack, dt)
                status = (_status_text(label, freq, confidence, rms, sensitivity, source_state,
                                        chord_name=chord_name, chord_mode=True)
                          + f"  {mode_hint}  (Ctrl+C to quit)")
                display.render_bands(bands, status)
            else:
                rgb = animator.update(dt, target_rgb, is_onset)
                status = (_status_text(label, freq, confidence, rms, sensitivity, source_state)
                          + f"  {mode_hint}  (Ctrl+C to quit)")
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
    chord_mode = False
    wedge_fades = [0.0] * 12

    active_index = None
    label, freq, confidence, rms = "-", 0.0, 0.0, 0.0
    pulse = 0.0
    note_stack, chord_name = [], None

    try:
        while True:
            key = keys.poll()
            _handle_sensitivity_key(key, sensitivity)
            _handle_source_key(key, capture, source_state)
            chord_mode = _handle_chord_mode_key(key, chord_mode)
            is_onset = False
            try:
                (_target_rgb, is_onset, label, freq, confidence, rms,
                 active_index, _pitch_class, _octave, note_stack, chord_name) = result_queue.get_nowait()
            except queue.Empty:
                pass

            mode_hint = f"mode={'chord' if chord_mode else 'note'}(p)"
            if chord_mode:
                active_pcs = {e["pitch_class"] for e in note_stack}
                bass_pc = next((e["pitch_class"] for e in note_stack if e["is_bass"]), None)
                for pc in range(12):
                    target = 1.0 if pc in active_pcs else 0.0
                    wedge_fades[pc] = _fade_toward(wedge_fades[pc], target, dt, config.CROSSFADE_TAU_MS)
                status = (_status_text(label, freq, confidence, rms, sensitivity, source_state,
                                        chord_name=chord_name, chord_mode=True)
                          + f"  {mode_hint}  (Ctrl+C to quit)")
                display.render_chord(wedge_fades, bass_pc, status)
            else:
                pulse = 1.0 if is_onset else pulse * math.exp(-dt / pulse_decay)
                status = (_status_text(label, freq, confidence, rms, sensitivity, source_state)
                          + f"  {mode_hint}  (Ctrl+C to quit)")
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
    chord_mode = False
    prev_chord_name = None

    label, freq, confidence, rms = "-", 0.0, 0.0, 0.0
    pitch_class, octave = None, None
    note_stack, chord_name = [], None

    resolved_dump = dump_file or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"note_history_{time.strftime('%Y%m%d_%H%M%S')}.txt",
    )

    try:
        while True:
            key = keys.poll()
            _handle_sensitivity_key(key, sensitivity)
            _handle_source_key(key, capture, source_state)
            chord_mode = _handle_chord_mode_key(key, chord_mode)
            got_new = False
            is_onset = False
            try:
                (_target_rgb, is_onset, label, freq, confidence, rms,
                 _fifths_idx, pitch_class, octave, note_stack, chord_name) = result_queue.get_nowait()
                got_new = True
            except queue.Empty:
                pass

            mode_hint = f"mode={'chord' if chord_mode else 'note'}(p)"
            if chord_mode:
                notes = [
                    (e["pitch_class"], e["octave"], _tab_note_rgb(e["pitch_class"]),
                     _tab_note_label(e["pitch_class"], e["octave"]))
                    for e in note_stack
                ]
                # Chord-level onset (the recognized chord identity changing),
                # not per-note re-attack -- a strummed/arpeggiated chord
                # shouldn't spam a new column per note.
                is_chord_onset = got_new and chord_name != prev_chord_name
                if got_new:
                    prev_chord_name = chord_name

                if scroll_mode == "onset":
                    if is_chord_onset:
                        display.push_notes(notes, chord_name)
                else:  # "fix"
                    time_since_tick += dt
                    if time_since_tick >= fix_interval:
                        time_since_tick -= fix_interval
                        display.push_notes(notes, chord_name)

                status = (_status_text(label, freq, confidence, rms, sensitivity, source_state,
                                        chord_name=chord_name, chord_mode=True)
                          + f"  {mode_hint}  [{scroll_mode}] (Ctrl+C to quit)")
                display.render(status, chord_mode=True)
            else:
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
                          + f"  {mode_hint}  [{scroll_mode}] (Ctrl+C to quit)")
                display.render(status, chord_mode=False)
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
                 _fifths_idx, _pitch_class, _octave, _note_stack, _chord_name) = result_queue.get_nowait()
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
