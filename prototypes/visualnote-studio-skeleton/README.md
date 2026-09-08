# VisualNote Studio skeleton

A throwaway PySide6 window for wayfinder map
[#145](https://github.com/pellepang/note-color/issues/145), ticket
[#152](https://github.com/pellepang/note-color/issues/152) — something concrete
to react to before the real arrange window is built. None of this code is meant
to be kept.

## What it shows

Transport bar, track-header column, arrange canvas with a live time ruler and a
moving playhead, a bottom editor pane, and Inspector/Mixer docks that are
deliberately empty — the skeleton scope map #145 settled.

## The question it is actually asking

**How does this project's fifths colour palette coexist with DAW chrome?**
Colour on every note is the whole point of the app, but a DAW surface is
designed to recede, and saturated per-pitch hues fight it. Two takes, toggled
live:

- **Clip-level colour** (`take=clip`) — each clip is filled with its part's
  pitch hue. The arrangement is instantly readable at a glance and unmistakably
  *this* app, but five saturated blocks are loud, and the colour is claimed by
  something as coarse as a whole clip.
- **Note-level colour** (`take=note`) — clips are neutral graphite and the
  **notes inside** carry the hue. Quieter, and the colour then means something
  precise: you are reading harmony off the arrangement rather than track
  identity. Costs legibility when clips are small.

Two chromes are included for the same reason, because "old Logic Pro" is
genuinely ambiguous: Logic 9 was a light silver-metallic window, everything
since has gone dark. The colour question answers differently against each.

## How to run it

```
.venv/bin/python prototypes/visualnote-studio-skeleton/demo.py
```

Keys: `C` colour take · `T` chrome · `Space` run/stop the playhead · `Q` quit.

`--shot out.png` renders all four combinations offscreen and exits, which is
how the screenshots on the ticket were made.

## What it is not

The playhead here is driven by a `QTimer`. The real one reads the audio
callback's own sample clock through a lock-free snapshot — a Qt timer would
drift against the audio it is supposed to be pointing at. The rendering
approach (`QGraphicsView`/`QGraphicsScene`, default `MinimalViewportUpdate`)
follows ticket [#147](https://github.com/pellepang/note-color/issues/147)'s
research, and this prototype is the first real check of it: it holds a 60fps
playhead over ~40 clip and note items without visible cost, but that is a far
smaller scene than a real project.

Nothing here plays audio, opens a project, or persists anything.
