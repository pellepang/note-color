# VisualNote Studio skeleton

A throwaway PySide6 window for wayfinder map
[#145](https://github.com/pellepang/note-color/issues/145), ticket
[#152](https://github.com/pellepang/note-color/issues/152). None of this code
is meant to be kept.

## The visual language

The first pass was rejected for looking like generic modern software. This one
follows a stated language, which lives in `notecolor/gui/theme.py` rather than
in this prototype, because it outlives the prototype:

- **Saturation means pitch, and nothing else.** Every surface, rule, label and
  button is muted ink. The only vivid things on screen are notes, carrying this
  app's own circle-of-fifths hue -- so a C here is the colour a C is in `tab`,
  in an exported score and on the synth keyboard. Colour is information, not
  decoration. The one exception is record-armed, and it is deliberately a dull
  autumn red rather than a signal red so it can never outrank a note.
- **Kanagawa** -- the Hokusai-derived ink-wash palette. Chosen because it is
  already built around that restraint, so note hues sit on it without fighting.
- **JetBrains Mono Nerd Font** everywhere, labels included. Structural, not
  nostalgic: a DAW is a dense grid of numbers, monospace aligns columns without
  measuring, and this project's other front-end is a terminal -- the two should
  look like one program.
- **Translucent**, square corners, hairline rules, no gradients or shadows.

That rule settles the question the first pass was asking. Clip-level colour is
out: a clip is not a pitch, so it may not be vivid. Colour lives on the notes
inside, which means the arrangement shows **harmony** rather than track
identity.

## How to run it

```
.venv/bin/python prototypes/visualnote-studio-skeleton/demo.py
```

`Space` run/stop the playhead - `B` toggle the note layer - `Q` quit.

`--shot out.png` renders offscreen at a fixed size and exits.

## Things learned the hard way

- **Hyprland tiles**, so `resize()` on a shown window is ignored and a live
  grab is whatever size the compositor chose. Screenshots render the widget
  offscreen instead, which is reproducible.
- `WA_TranslucentBackground` on a `QGraphicsView` stops its viewport painting
  at all, so the background brush never lands and you get Qt's default grey.
  Transparency there comes from a stylesheet plus an alpha brush.
- A `QGraphicsView` opens **scrolled to the middle of its scene**, and the
  scrollbar has no range until after `show()` -- so an early `setValue(0)`
  silently does nothing.

## What it is not

No audio, no project loading, nothing persisted. The playhead is a `QTimer`;
the real one reads the audio callback's sample clock through
`notecolor.audio.transport`'s snapshot, because a Qt timer drifts against the
audio it is meant to be pointing at.
