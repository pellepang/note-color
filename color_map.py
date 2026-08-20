"""Pure functions mapping a (pitch_class, octave) note to an RGB color."""

import colorsys

import config

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Spelling used by the fifths-order views (wheel, tab): flats on the flat
# side of the circle of fifths instead of the equivalent sharp, e.g. Ab
# rather than G#.
NOTE_NAMES_FIFTHS = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def fifths_index(pitch_class):
    """Position of `pitch_class` around the circle of fifths (0=C, clockwise)."""
    return (pitch_class * 7) % 12


def hue_for_step(step):
    """Hue (degrees) for a step 0..11 around a 12-tone wheel -- `step` is
    either a chromatic pitch_class or a fifths-order index (fifths_index()'s
    output), whichever convention the caller is already using; this is just
    the even 30-degree spacing plus config.HUE_OFFSET_DEG, shared by
    note_to_hsl() below and any view that colors a fifths-order band/wedge
    directly (menu_animation.py's donut) rather than starting from a
    chromatic pitch_class."""
    return (step * 30 + config.HUE_OFFSET_DEG) % 360


def note_to_hsl(pitch_class, octave, scheme="chromatic", hue_override=None):
    """`hue_override` (degrees, issue #41's per-note [colors] overrides)
    replaces the scheme-derived hue outright when given; saturation and
    octave-driven lightness are unaffected either way."""
    if hue_override is not None:
        hue = hue_override % 360
    else:
        step = fifths_index(pitch_class) if scheme == "fifths" else pitch_class
        hue = hue_for_step(step)
    octv = max(config.MIN_OCTAVE, min(config.MAX_OCTAVE, octave))
    span = config.MAX_OCTAVE - config.MIN_OCTAVE
    t = (octv - config.MIN_OCTAVE) / span if span else 0.0
    lo, hi = config.BASE_LIGHTNESS_RANGE
    lightness = lo + t * (hi - lo)
    return hue, config.BASE_SATURATION, lightness


def hsl_to_rgb255(hue_deg, sat, light):
    r, g, b = colorsys.hls_to_rgb((hue_deg % 360) / 360.0, light, sat)
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))
