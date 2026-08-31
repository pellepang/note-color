# Research: capturing this project's terminal views as images an AI agent can see

## Question

This project's terminal views (`fill`, `wheel`, `tab`) are built from raw
ANSI truecolor SGR + cursor-positioning escape codes, and have already hit
real, hard-to-spot rendering bugs in exactly the "grid/column alignment"
class — most concretely, commit `d7d2ea0` ("tab view: fix barline column
misalignment from combining duration glyphs"), where `_note_cell()`
measured cell text by Python code-point count instead of real terminal
display columns, so a row containing a notehead + combining stem/flag/dot
duration glyph (Unicode `Mc` spacing-combining-mark codepoints,
`terminal_tab_display.py`'s `STEM_GLYPH`/`FLAG_GLYPHS`/`DOT_GLYPH`)
consumed a different number of real columns than a row without one,
desyncing every column — including barlines — drawn after it on that row.

Can an AI coding agent get a static image (PNG) of a rendered terminal
frame from this app, cheaply and headlessly, specifically so it can
*see* this class of bug directly instead of inferring it from raw ANSI
bytes or text logs? This doc surveys `pyte`, recording/rasterizing tools
(`asciinema`+`agg`, `vhs`, `svg-term-cli`/`termtosvg`/`term-transcript`),
headless-real-terminal approaches (`tmux capture-pane -e`, `Xvfb`+xterm),
and ANSI-to-HTML-to-image, then gives a concrete recommendation.

## Answer

### 1. `pyte` — pure-Python terminal emulator (parser + screen-buffer model, no pixels)

**Maintenance.** `pyte` (`github.com/selectel/pyte`, LGPL-3.0, forked
originally from `vt102`) is accurately described as *inactive* rather than
actively developed: last PyPI release `0.8.2`, last repo update around
March 2025, no release in the trailing 12 months as of this writing.
That said, it is stable and complete for the VT100/xterm subset this
project actually emits (confirmed below), has ~86k weekly PyPI downloads,
and is packaged in conda-forge/Gentoo — "inactive" here reads as
"finished for its scope," not "broken."

**API.** `pyte.Screen(columns, lines)` + `pyte.Stream(screen)` (or
`ByteStream` for raw bytes); `stream.feed(data)` parses escape sequences
and updates the screen in place. The interesting surface for this use
case is `Screen.buffer`: a sparse `lines × columns` matrix of `Char`
namedtuples, each with `data` (the character or grapheme cluster), `fg`,
`bg`, and style flags (`bold`, `italics`, `underscore`, `strikethrough`,
`reverse`, `blink`). `Screen.display` also exists but only gives
plain-text rows (colors discarded) — not useful here, since color *is*
the thing being visualized.

**Wcwidth/combining-character correctness — verified directly in source,
and it is exactly this app's bug class.** Fetched and read
`pyte/screens.py`'s `Screen.draw()` directly: it iterates
`grapheme_clusters(data)` (grouping zero-width combining marks with their
base character, the same grouping `d7d2ea0`'s fix effectively
special-cased by hand for this app's notehead+combining-glyph cells), then
calls `wcswidth(char)` per cluster — **the exact same `wcwidth` package**
this project just added as a direct dependency in that commit — and
branches on the result: width-1 chars occupy one cell; width-2 chars
occupy two cells with an explicit empty-`data` continuation stub in the
second cell; width-0 combining marks attach to the previous cell without
advancing the cursor at all. Cursor position only advances by the
resolved width, never by codepoint count. This has been in `pyte` since
release `0.5.0` (Jan 2015: "`Screen.draw` now properly handles
full/ambiguous-width characters. Thanks to... `wcwidth`"), with a further
multicolumn-boundary fix in `0.6.0` (2017). Concretely: **feeding this
app's raw ANSI byte stream through `pyte` would have reproduced the
`d7d2ea0` misalignment bug exactly** (before the fix) and would visibly
show it corrected (after) — because `pyte`'s own column bookkeeping is
built on the identical width table/logic as the fix, not a separate,
possibly-divergent implementation.

**Truecolor.** `pyte` accepts 24-bit RGB SGR (`38;2;r;g;b`/`48;2;r;g;b`)
since `0.6.0` (2017: "Allowed 256 and 24bit colours in
`Screen.select_graphic_rendition`"), which covers every SGR sequence this
app's `terminal_display.py`/`terminal_tab_display.py`/
`terminal_wheel_display.py` emit (grepped directly: only `38;2;r;g;b`,
`48;2;r;g;b`, `\033[2J`, `\033[H`, `\033[?25l`/`\033[?25h` — no 256-color
palette codes, no unusual private modes beyond cursor show/hide). Fully
inside `pyte`'s supported range.

**Prior art.** Searched specifically for an existing "`pyte` + Pillow →
PNG" project/blog post; none surfaced. `pyte` is normally paired with a
*curses redraw* (its own `examples/terminal_emulator.py` re-draws into a
real terminal, not an image) or used purely for text-buffer inspection in
test suites. A rasterizer on top of `pyte`'s buffer would be a small
custom script, not a reused off-the-shelf tool — see the recommendation
below for what that script actually needs to do.

### 2. Recording-based tools

**`asciinema` + `agg`.** `asciinema` records a session to a JSON-lines
"cast" file (timestamped raw output chunks) — it does not itself
rasterize anything. `agg` (`github.com/asciinema/agg`, Rust) converts a
cast to an animated GIF using its own internal terminal emulation (the
`avt` crate) plus a real font renderer (`swash` by default, or `resvg`),
producing high-quality, properly-shaped text. This is a genuine
alternative rasterizer to a hand-rolled PIL one, but it's a Rust binary
(`cargo install`/Homebrew/Docker) — a new toolchain, and its native
output is an animated GIF, not discrete PNGs (frames would need to be
re-extracted from the GIF, an extra lossy round-trip). Its combining/wide
character handling wasn't independently verified in this pass; `avt` is a
from-scratch VT emulator in the same spirit as `pyte`.

**`vhs` (Charm).** Fully non-interactive/scriptable: a `.tape` file
specifies keystrokes, waits, and `Screenshot`/output directives, then
`vhs file.tape` renders it — genuinely headless in the sense of "no human
at a keyboard." *However*, it is not lightweight: it drives the session
through `ttyd` (serves the pty over a websocket) and then a **headless
browser** (Chromium via `go-rod`) that actually paints the terminal using
real font rendering, plus `ffmpeg` to encode the result to GIF/MP4/WebM,
or a `frames/` directory of PNGs. The official Docker image bundles
`ffmpeg` + `chromium` + `bash`. This is the heaviest dependency footprint
of every option surveyed (Go binary + `ttyd` + a full Chromium install +
`ffmpeg`), directly conflicting with the "avoid heavy toolchain" framing
of this request, despite genuinely working headlessly and supporting a
`frames/` PNG-sequence output mode.

**`svg-term-cli` / `termtosvg` / `term-transcript`.** These convert a
cast (or live capture) to SVG rather than a raster image. `term-transcript`
(Rust, `docs.rs/term-transcript`) is the most relevant: its docs
explicitly claim correct handling of combining characters and wide CJK
characters (each taking exactly two cells) — a real point in its favor for
this exact bug class. But its designed use case is capturing a *linear
CLI command's stdout* for snapshot testing/documentation embedding
(`capture`/`exec` subcommands piping a command's output through it), not
obviously a full-screen, cursor-addressed, repeatedly-redrawing TUI like
this app's `\033[2J\033[H`-per-frame views — its docs and examples center
on scrollback-style transcripts, and full-screen/alternate-screen cursor
redraw semantics weren't confirmed as in scope. Also a new Rust-toolchain
dependency (`cargo install`).

### 3. Headless real terminal + screenshot

**`tmux capture-pane -e`.** Captures a pane's content *as text with
embedded SGR escape codes reconstructed for the visible grid* (`-e`
flag), i.e. tmux has already done the pty-plumbing and window-sizing work
and hands back a resolved, already-correctly-column-aligned ANSI
snapshot. This is a legitimate *capture* mechanism (arguably simpler than
opening a raw pty yourself, since tmux handles `TIOCSWINSZ`/session
lifecycle), but it still isn't pixels — the captured text+escapes would
still need to go through some rasterizer (`pyte` again, or a browser) to
become an image. Net effect: a viable alternative front-end for *feeding*
a `pyte`-based rasterizer, not a competing end-to-end solution.

**`Xvfb` + a real terminal emulator (`xterm`/`kitty`/`alacritty`) +
screenshot tool.** This renders through an actual GPU/software terminal
renderer against a virtual X display, then grabs pixels (e.g. `import`,
`scrot`, or a VNC-based grab). It is the most *faithful* to what a human
eye would see (real font hinting/antialiasing/subpixel rendering), and
the heaviest to stand up (X server, a terminal emulator binary, a
screenshot utility, window-manager edge cases). For a bug class that is
about *grid/column alignment*, not sub-pixel font rendering fidelity,
this is overkill — `pyte`'s buffer model already gives exact column
positions without needing a display server at all.

### 4. ANSI-to-HTML-to-image (`ansi2html`/`aha` + headless browser)

`ansi2html` (Python, pip-installable) and `aha` ("ANSI HTML Adapter", C)
both convert an ANSI SGR stream to HTML `<span>`s. Neither does any
column-width accounting at all — they are naive text-and-escape-code-to-
markup converters with no concept of a fixed character-cell grid, no
cursor-position tracking, and no wcwidth-aware wide/combining-character
handling. Two concrete problems for this project's exact use case:

- **Redraw semantics are wrong for a full-screen TUI.** This app's
  terminal views emit `\033[2J\033[H` (clear + cursor-home) every frame
  and redraw the whole screen in place — `ansi2html`/`aha` have no cursor
  model, so naively concatenating a raw captured byte stream and handing
  it to either tool would produce garbled, overlapping HTML (every
  frame's content stacked, not the final resolved screen). They only make
  sense fed a single already-resolved frame (i.e. downstream of something
  like `pyte` or `tmux capture-pane`), not a raw multi-frame capture.
- **No grid-alignment guarantee — the one property this whole effort
  needs.** A browser laying out a `<pre>` block in a monospace font
  advances by each glyph's *font-reported* advance width via its own text
  shaping engine, not by an explicit wcwidth cell model the way a real
  terminal (or `pyte`) does. Whether a browser's shaper "gets it right"
  for a specific combining mark + notehead pairing depends on the font's
  own OpenType shaping tables and the browser's Unicode version — it is
  not guaranteed to reproduce (or fail to reproduce) the *same* alignment
  a real terminal shows, which is precisely the ambiguity this tooling
  exists to eliminate. This is flagged as the weakest candidate
  specifically because of the bug class motivating the request.

### 5. Practical recommendation

**Build a small custom `pyte` + Pillow script — no existing tool already
does this end-to-end, but the pieces are simple, correct, and dependency-light.**

High-level plan:

1. **Run the target view headlessly in a real pty.** Use Python's stdlib
   `pty`/`os.openpty()` (or `subprocess` with a pty helper) to spawn
   `virtualnote tab` (or `main.py --terminal --view tab ...`) with an
   explicit `TIOCSWINSZ` size (e.g. 80×24, or whatever the agent wants to
   test). Feed it real audio via `--source loopback` playing one of this
   project's existing synthetic test signals (sine tones / melodies
   already used by `tests/test_batch_transcribe.py` and the acoustic test
   scripts), so the capture is deterministic and repeatable rather than
   depending on a live mic. `tmux capture-pane -e` is a reasonable
   fallback front-end here if raw-pty plumbing proves fiddly — it hands
   back an already-resolved, escape-coded snapshot without needing to
   manage the pty/session lifecycle by hand.
2. **Feed the pty's raw output into `pyte`.** `screen = pyte.Screen(cols,
   rows); stream = pyte.Stream(screen); stream.feed(chunk)` as bytes
   arrive. Snapshot `screen.buffer` either periodically (e.g. every N
   ms) or right after detecting a `\033[2J`-triggered redraw boundary, to
   get a handful of discrete "frame N" snapshots rather than only the
   final state — cheap, since `Screen.buffer` is already the resolved,
   column-correct in-memory model (no reimplementation of wcwidth logic
   needed — that's `pyte`'s own job, verified above).
3. **Rasterize each snapshot with Pillow.** For each row/col in
   `screen.buffer`, look up the `Char` (empty/space if absent — sparse
   buffer), draw a filled `bg`-colored rect at `(col * cell_w, row *
   cell_h)` and the cell's `data` text in `fg` color, using **one loaded
   monospace font** via `PIL.ImageDraw`/`ImageFont`. Continuation cells
   from double-width characters already have empty `data` (per `pyte`'s
   own `Screen.draw()`, verified above) so the rasterizer does not need
   its own width logic at all — it only needs to draw whatever `data`
   each cell already holds at that cell's fixed grid position, which is
   the whole point: **alignment correctness is inherited for free from
   `pyte`'s parsing, not something the image step has to get right
   independently.**
4. **Font coverage is the one real remaining risk, and this project
   already has the fix in hand.** `tab`'s notehead/duration glyphs
   (`U+1D157`, `STEM_GLYPH`/`FLAG_GLYPHS`/`DOT_GLYPH` in the `U+1D16D`–
   `U+1D170` musical-symbols block) need a font that actually contains
   those codepoints — Pillow/FreeType does not do automatic cross-font
   fallback the way a terminal emulator's font-fallback stack might.
   This project already vendors `NotoMusic-Regular.ttf` for the treble
   clef glyph (per the existing clef-clipping investigation in
   `docs/DECISIONS.md`) — reuse that same font file for the rasterizer
   rather than sourcing a new one, and pair it with a general monospace
   font (e.g. DejaVu Sans Mono, commonly preinstalled) for everything
   else, falling back to the music font only for codepoints in that
   block.
5. **Output.** Save one PNG per captured snapshot to a scratch path and
   hand the path(s) straight to the agent's own image-reading tool — no
   further conversion needed.

Estimated size: a single new script (e.g.
`scripts/terminal_screenshot.py`), roughly 150–250 lines, with exactly
two new runtime dependencies: `pyte` and `Pillow` — both pure/wheel-clean,
pip-installable, no compiler/build toolchain, no Rust/Go/Node/browser.
Both dependencies are dev-tooling for the agent's own capture step, not
additions to the shipped app's runtime — they never touch the
Raspberry-Pi deployment-risk surface CLAUDE.md is protective about (that
concern is specifically about the app's own shipped dependencies, e.g.
the `aubio`/`librosa`-avoidance calls); this script only ever runs on
whatever machine the agent is doing the debugging from.

### Ranking

1. **`pyte` + Pillow, custom script (recommended).** Pure Python,
   verified-correct wcwidth/combining-character handling (same mechanism
   as the bug this whole effort is motivated by), truecolor support
   confirmed, no build toolchain, reuses a font this project already
   vendors. Small amount of new code, but every piece is simple and each
   was independently verified against this app's actual escape-code
   vocabulary above (not assumed).
2. **`agent-tty` (off-the-shelf, if a heavier toolchain is acceptable).**
   A real, actively-maintained tool (Apache-2.0, 151 commits, purpose-
   built for exactly "AI agent drives a TUI, gets back a PNG/video") using
   `node-pty` + a headless-Chromium-driven Ghostty build for pixel-accurate
   screenshots. Zero custom code required. Ranked below option 1 only
   because it requires Node.js + Playwright + Chromium in the dev
   environment, which is explicitly the "heavy toolchain" this request
   asked to avoid — worth revisiting if the team decides tooling weight
   matters less than zero-maintenance reuse.
3. **`tmux capture-pane -e` feeding into the same `pyte`-based
   rasterizer.** Not a different end-to-end approach, just a possible
   alternative to raw-pty plumbing for step 1 above if that proves
   fiddly — tmux already solves session/size lifecycle. No benefit over
   option 1 otherwise, so only worth reaching for as a fallback.
4. **`vhs`.** Fully scriptable/headless in the "no human present" sense
   and does support a PNG-frames output mode, but rejected primarily on
   toolchain weight — `ttyd` + a full headless Chromium + `ffmpeg` is the
   heaviest footprint surveyed, for a benefit (pixel-perfect font
   rendering) this bug class doesn't actually need.
5. **`ansi2html`/`aha` + headless-browser screenshot.** Not recommended
   at all, even as a fallback: no cursor/redraw model (garbles a
   full-screen app's raw capture unless already resolved by something
   else first) and no verified wcwidth/grid-cell guarantee — the one
   property this entire effort exists to get right. Specifically likely
   to *fail silently* at reproducing the combining-glyph column bug this
   research was motivated by.
6. **`Xvfb` + real terminal emulator + screenshot tool.** Most faithful
   to literal human-eye rendering, most infrastructure to stand up; not
   worth it when `pyte`'s buffer model already gives exact, verified
   column positions without a display server.

## Sources

- [selectel/pyte on GitHub](https://github.com/selectel/pyte) — repo,
  license, maintenance signal.
- [pyte 0.8.1-dev API reference](https://pyte.readthedocs.io/en/latest/api.html)
  — `Char` namedtuple fields, `Screen.buffer`/`Screen.display`.
- [pyte Changelog](https://pyte.readthedocs.io/en/latest/changelog.html)
  — `0.5.0` wcwidth integration, `0.6.0` 24-bit/256-color SGR support and
  multicolumn-boundary fixes, `0.8.0` malformed-color handling.
- `pyte/pyte/screens.py` (fetched and read directly from
  `raw.githubusercontent.com/selectel/pyte/master/pyte/screens.py`) —
  `Screen.draw()`'s `grapheme_clusters()`/`wcswidth()`-based cursor
  advance and double-width continuation-stub logic, confirmed firsthand.
- [pyte - Python Package Health Analysis (Snyk)](https://snyk.io/advisor/python/pyte)
  — inactivity/download-count signal.
- [charmbracelet/vhs on GitHub](https://github.com/charmbracelet/vhs) +
  its `Dockerfile` — `.tape` scripting model, `ttyd`+Chromium
  (`go-rod`)+`ffmpeg` dependency chain, `frames/` PNG-sequence output
  mode.
- [asciinema/agg on GitHub](https://github.com/asciinema/agg) — Rust
  cast-to-GIF renderer, `swash`/`resvg` backends, `gifski`-based encoding.
- [term-transcript on docs.rs](https://docs.rs/term-transcript/latest/term_transcript/)
  — combining-character/wide-CJK claims, `capture`/`exec`/`test`
  subcommand shape.
- [How to convert ANSI terminal content to HTML (dzx.fr)](https://dzx.fr/blog/how-to-convert-ansi-terminal-content-to-html/)
  and [tmux capture-pane command reference](https://www.tmux.info/docs/commands/capture-pane)
  — `tmux capture-pane -e` → `ansi2html` pipeline description.
- [coder/agent-tty on GitHub](https://github.com/coder/agent-tty) —
  `node-pty` + `libghostty-vt`/`ghostty-web` (headless-Chromium-driven
  Ghostty) architecture, license/commit-count/maintenance signal.
- [pproenca/agent-tui on GitHub](https://github.com/pproenca/agent-tui) —
  Rust PTY-based TUI automation, text-only (no raster) screenshot output,
  considered and set aside since it doesn't produce images at all.
- This repo, read directly for context: `terminal_display.py`,
  `terminal_tab_display.py`, `terminal_wheel_display.py` (grepped for
  every ANSI escape sequence actually emitted, confirming full coverage
  by `pyte`'s supported SGR/cursor range), and commit `d7d2ea0` ("tab
  view: fix barline column misalignment from combining duration
  glyphs") — the concrete bug this research is motivated by, read via
  `git show` for its exact root cause and fix.

## Caveats on this research pass

- No direct prior-art project combining `pyte` + Pillow into a
  terminal-to-PNG tool was found despite multiple targeted searches; the
  recommendation in section 5 is a from-scratch design reasoned from
  `pyte`'s verified API/behavior, not a reused, already-battle-tested
  script. It should be prototyped and smoke-tested against this app's
  actual `tab` output (ideally re-running it against the exact
  pre-`d7d2ea0` code to confirm it *does* visibly show the barline drift)
  before being trusted as a general debugging tool.
- `agent-tty`/`agent-tui`'s Unicode wide/combining-character correctness
  was not independently verified the way `pyte`'s was (no direct source
  read) — their descriptions rely on their respective underlying engines
  (Ghostty, a real terminal emulator; a Rust PTY library) being
  wcwidth-correct, which is a reasonable inference for Ghostty
  specifically (a maintained, widely-used real terminal emulator) but is
  reported here, not confirmed firsthand, unlike `pyte`'s claim.
- `agg`'s (`avt` crate) and `term-transcript`'s own combining/wide-
  character handling were taken from their own project descriptions/docs
  rather than verified by reading their source directly, unlike `pyte`'s
  `wcswidth()` call site (which was fetched and read in full). Flagged
  per this project's own research convention of distinguishing
  directly-read claims from reported ones.
- This pass did not actually run or prototype any of the candidates
  end-to-end (no `pyte`/Pillow script was written and tested against a
  live `virtualnote tab` capture) — it is a desk-research survey to
  inform a build decision, not a validated implementation.
