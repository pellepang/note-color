# `tab` view's note color ignores octave and uses fixed lightness (`config.TAB_NOTE_LIGHTNESS = 0.5`)

`fill`/GUI intentionally scale lightness by octave (darker = lower, per
`note_to_hsl()`). In `tab`, octave already has a job — it sets the note's row
on the staff — so also using it for lightness made a low C render too dark
to read as red and a high C wash out toward white. The first fix reused
`BASE_LIGHTNESS_RANGE`'s top end (0.82, matching the wheel view's peak pulse
brightness), but that's close enough to white to read as pastel when held
continuously rather than shown as a brief pulse — lightness 0.5 is where a
given hue/saturation looks most vivid in HSL, so that's what
`_tab_note_rgb()` in `main.py` uses instead. Each note letter is still one
fixed, recognizable color (C is always red); only its vertical position
moves with octave.
