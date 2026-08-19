from color_map import note_to_hsl, hsl_to_rgb255, fifths_index
import config

# pitch_class -> expected fifths-scheme hue, per the confirmed circle-of-fifths table
FIFTHS_HUE_TABLE = {
    0: 0,     # C
    7: 30,    # G
    2: 60,    # D
    9: 90,    # A
    4: 120,   # E
    11: 150,  # B
    6: 180,   # F#
    1: 210,   # Db
    8: 240,   # Ab
    3: 270,   # Eb
    10: 300,  # Bb
    5: 330,   # F
}


def test_c_is_hue_zero():
    hue, sat, light = note_to_hsl(0, 4)
    assert hue == config.HUE_OFFSET_DEG % 360


def test_fsharp_is_opposite_hue():
    hue, _, _ = note_to_hsl(6, 4)
    assert hue == (180 + config.HUE_OFFSET_DEG) % 360


def test_lightness_monotonic_in_octave():
    lightnesses = [note_to_hsl(0, oct_)[2] for oct_ in range(config.MIN_OCTAVE, config.MAX_OCTAVE + 1)]
    assert lightnesses == sorted(lightnesses)


def test_octave_clamped_outside_range():
    below = note_to_hsl(0, config.MIN_OCTAVE - 5)
    at_min = note_to_hsl(0, config.MIN_OCTAVE)
    above = note_to_hsl(0, config.MAX_OCTAVE + 5)
    at_max = note_to_hsl(0, config.MAX_OCTAVE)
    assert below == at_min
    assert above == at_max


def test_rgb_in_valid_range():
    for pitch_class in range(12):
        hue, sat, light = note_to_hsl(pitch_class, 4)
        r, g, b = hsl_to_rgb255(hue, sat, light)
        for c in (r, g, b):
            assert 0 <= c <= 255


def test_fifths_hue_matches_confirmed_table():
    for pitch_class, expected_hue in FIFTHS_HUE_TABLE.items():
        hue, _, _ = note_to_hsl(pitch_class, 4, scheme="fifths")
        assert hue == expected_hue


def test_fifths_index_matches_hue_step():
    for pitch_class, expected_hue in FIFTHS_HUE_TABLE.items():
        assert fifths_index(pitch_class) == expected_hue // 30


def test_chromatic_scheme_unaffected_by_fifths_addition():
    for pitch_class in range(12):
        hue, sat, light = note_to_hsl(pitch_class, 4, scheme="chromatic")
        assert hue == pitch_class * 30


def test_hue_override_replaces_scheme_hue():
    hue, sat, light = note_to_hsl(0, 4, scheme="fifths", hue_override=123)
    assert hue == 123
    # Saturation/lightness are untouched by the override.
    _, base_sat, base_light = note_to_hsl(0, 4, scheme="fifths")
    assert sat == base_sat
    assert light == base_light


def test_hue_override_wraps_into_0_360():
    hue, _, _ = note_to_hsl(0, 4, hue_override=400)
    assert hue == 40
