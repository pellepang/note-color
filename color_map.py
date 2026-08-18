"""Pure functions mapping a (pitch_class, octave) note to an RGB color."""

import colorsys

import config

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def fifths_index(pitch_class):
    """Position of `pitch_class` around the circle of fifths (0=C, clockwise)."""
    return (pitch_class * 7) % 12


def note_to_hsl(pitch_class, octave, scheme="chromatic"):
    step = fifths_index(pitch_class) if scheme == "fifths" else pitch_class
    hue = (step * 30 + config.HUE_OFFSET_DEG) % 360
    octv = max(config.MIN_OCTAVE, min(config.MAX_OCTAVE, octave))
    span = config.MAX_OCTAVE - config.MIN_OCTAVE
    t = (octv - config.MIN_OCTAVE) / span if span else 0.0
    lo, hi = config.BASE_LIGHTNESS_RANGE
    lightness = lo + t * (hi - lo)
    return hue, config.BASE_SATURATION, lightness


def hsl_to_rgb255(hue_deg, sat, light):
    r, g, b = colorsys.hls_to_rgb((hue_deg % 360) / 360.0, light, sat)
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))
