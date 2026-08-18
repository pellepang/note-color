"""Real-time audio -> color display.

mic -> AudioCapture (callback thread)
    -> analysis thread: ring buffer -> YIN pitch detect -> NoteSmoother -> color_map
    -> single-slot queue
    -> main thread: ColorAnimator -> Display (pygame window, or terminal)

GUI controls: Esc/close window to quit, F to toggle fullscreen, D to toggle debug overlay.
Terminal mode: Ctrl+C to quit. No display server required.
"""

import argparse
import math
import os
import queue
import threading
import time

import numpy as np

import config
from audio_capture import AudioCapture
from pitch_detect import detect_pitch
from note_smoother import NoteSmoother
from color_map import note_to_hsl, hsl_to_rgb255, fifths_index, NOTE_NAMES
from animation import ColorAnimator


def analysis_loop(capture, result_queue, stop_event, color_scheme):
    ring = np.zeros(config.WINDOW_SIZE, dtype=np.float64)
    smoother = NoteSmoother(config)

    while not stop_event.is_set():
        try:
            block = capture.get_block(timeout=0.5)
        except queue.Empty:
            continue

        block = block.astype(np.float64)
        ring = np.concatenate([ring[len(block):], block])
        rms = float(np.sqrt(np.mean(block * block))) if len(block) else 0.0

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


def _status_text(label, freq, confidence, rms):
    freq_str = f"{freq:6.1f}Hz" if freq else "  --  "
    return f"note={label:<4s} freq={freq_str} conf={confidence:.2f} rms={rms:.4f}"


def run_terminal_fill(result_queue):
    from terminal_display import TerminalDisplay

    display = TerminalDisplay(fps=config.TERMINAL_FPS)
    animator = ColorAnimator(config.CROSSFADE_TAU_MS, config.PULSE_DECAY_MS, config.ONSET_PULSE_BOOST)

    target_rgb, is_onset, label, freq, confidence, rms = config.IDLE_RGB, False, "-", 0.0, 0.0, 0.0
    dt = 1.0 / display.fps

    try:
        while True:
            try:
                (target_rgb, is_onset, label, freq, confidence, rms,
                 _fifths_idx, _pitch_class, _octave) = result_queue.get_nowait()
            except queue.Empty:
                is_onset = False

            rgb = animator.update(dt, target_rgb, is_onset)
            status = _status_text(label, freq, confidence, rms) + "   (Ctrl+C to quit)"
            display.render(rgb, status)
            time.sleep(dt)
    except KeyboardInterrupt:
        pass
    finally:
        display.quit()


def run_terminal_wheel(result_queue):
    from terminal_wheel_display import WheelDisplay

    display = WheelDisplay(fps=config.WHEEL_FPS)
    pulse_decay = config.PULSE_DECAY_MS / 1000.0
    dt = 1.0 / display.fps

    active_index = None
    label, freq, confidence, rms = "-", 0.0, 0.0, 0.0
    pulse = 0.0

    try:
        while True:
            is_onset = False
            try:
                (_target_rgb, is_onset, label, freq, confidence, rms,
                 active_index, _pitch_class, _octave) = result_queue.get_nowait()
            except queue.Empty:
                pass

            pulse = 1.0 if is_onset else pulse * math.exp(-dt / pulse_decay)
            status = _status_text(label, freq, confidence, rms) + "   (Ctrl+C to quit)"
            display.render(active_index, pulse, status)
            time.sleep(dt)
    except KeyboardInterrupt:
        pass
    finally:
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


def run_terminal_tab(result_queue, scroll_mode, dump_file):
    from terminal_tab_display import TabDisplay

    display = TabDisplay(fps=config.TAB_FPS)
    dt = 1.0 / display.fps
    fix_interval = 1.0 / config.TAB_FIX_HOPS_PER_SEC
    time_since_tick = 0.0

    label, freq, confidence, rms = "-", 0.0, 0.0, 0.0
    pitch_class, octave = None, None

    resolved_dump = dump_file or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"note_history_{time.strftime('%Y%m%d_%H%M%S')}.txt",
    )

    try:
        while True:
            got_new = False
            is_onset = False
            try:
                (_target_rgb, is_onset, label, freq, confidence, rms,
                 _fifths_idx, pitch_class, octave) = result_queue.get_nowait()
                got_new = True
            except queue.Empty:
                pass

            glyph_rgb = _tab_note_rgb(pitch_class)

            if scroll_mode == "onset":
                if got_new and is_onset:
                    display.push(pitch_class, octave, glyph_rgb, label)
            else:  # "fix"
                time_since_tick += dt
                if time_since_tick >= fix_interval:
                    time_since_tick -= fix_interval
                    display.push(pitch_class, octave, glyph_rgb, label)

            status = _status_text(label, freq, confidence, rms) + f"   [{scroll_mode}] (Ctrl+C to quit)"
            display.render(status)
            time.sleep(dt)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            display.dump_ansi(resolved_dump)
        finally:
            display.quit()


def run_gui(result_queue, fullscreen, start_debug):
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
                text = font.render(_status_text(label, freq, confidence, rms), True, (255, 255, 255))
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
    args = parser.parse_args()

    capture = AudioCapture(config.SAMPLE_RATE, config.BLOCK_SIZE, config.QUEUE_SIZE)
    capture.start()

    result_queue = queue.Queue(maxsize=1)
    stop_event = threading.Event()
    analysis_thread = threading.Thread(
        target=analysis_loop, args=(capture, result_queue, stop_event, args.color_scheme), daemon=True
    )
    analysis_thread.start()

    try:
        if args.terminal:
            if args.view == "wheel":
                run_terminal_wheel(result_queue)
            elif args.view == "tab":
                run_terminal_tab(result_queue, args.scroll, args.dump_file)
            else:
                run_terminal_fill(result_queue)
        else:
            run_gui(result_queue, args.fullscreen, args.debug)
    finally:
        stop_event.set()
        capture.stop()


if __name__ == "__main__":
    main()
