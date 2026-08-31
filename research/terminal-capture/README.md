# Terminal capture

A small dev/agent tool that renders a `TabDisplay` (or any raw-ANSI writer's)
output to a PNG, so rendering bugs — misaligned columns, wrong glyph widths,
color mistakes — can be looked at directly as an image instead of squinted at
as raw escape-code text.

Built from `docs/research/terminal-visual-capture-for-agents.md`'s
recommendation: `pyte` (pure-Python ANSI/VT100 terminal emulator — parses the
escape stream into a grid of styled cells, using the same `wcwidth`-based
column bookkeeping this app's own `_pad_center()` uses) + Pillow (rasterizes
that grid to a PNG, one glyph per cell, honoring each cell's real on-screen
width). Two new dev-only dependencies, not part of the shipped app's runtime
surface.

## Usage

```
.venv/bin/python research/terminal-capture/capture.py [--out PATH] [--cols N] [--rows N]
```

Builds a `TabDisplay`, pushes a handful of synthetic notes/durations/a
barline (deliberately exercising the exact bug class `d7d2ea0` fixed —
mixed duration-glyph widths ahead of a barline column), calls `render()`
once, feeds the raw ANSI bytes it wrote to a `pyte.Screen`, and rasterizes
the resulting cell grid to `research/terminal-capture/output/sample_tab.png`
(or `--out`).

## Why this shape, not a live pty capture

A first version drives `TabDisplay` directly (no real terminal, no `pty`,
no audio) — it already produces real ANSI output through the exact same
`render()` code path a live session uses, and is enough to catch the
column-alignment bug class this tool exists for. Wrapping a full
`virtualnote tab --source loopback` session in a real `pty` is a natural
next step if a bug only reproduces under live/audio-driven conditions, but
adds real complexity (spawning the app, feeding it synthetic audio, timing
the capture) for no benefit on a static-content alignment bug — not built
until there's a concrete case that needs it.

## Known limitation

`pyte` and this project's own `wcwidth`-based width math agree on the
*same* width table — so a bug caused by a genuine `wcwidth` table gap (a
terminal emulator disagreeing with the table, the residual risk
`docs/research/terminal-rendering-performance.md` flagged as unfixable
app-side) won't show up here. This tool catches "did our own math match
what wcwidth says," not "does every real terminal agree with wcwidth."
