# Terminal rendering performance & architecture: how real TUI apps solve the problems note-color is now hitting

Research to inform note-color's terminal display layer (`terminal_display.py`,
`terminal_wheel_display.py`, `terminal_tab_display.py`, `menu_display.py`/
`menu_animation.py`) after commit `d7d2ea0` ("tab view: fix barline column
misalignment from combining duration glyphs") — a real column-width bug
found and hand-fixed in the field — and the project owner's own words: "we
are hitting a wall with this type of displaying faster than I thought I
would." This doc surveys prior art (grapheme-width libraries, diffing TUI
frameworks, escape-sequence batching practice, small-dependency options,
known pitfalls) and ends with prioritized prototype recommendations. It
does not duplicate `docs/research/tab-barline-straightness.md`, which
diagnosed a *different*, already-confirmed bug (beat-accumulator double-
counting) in the same view and flagged the barline-glyph-width risk this
doc's §1 follows up on — that doc's findings are treated as given, not
re-derived here.

## Question

1. Does a mature, small, pure-Python grapheme/column-width library exist
   that directly addresses the class of bug the last commit hand-fixed —
   and is it enough, or does it have its own known gaps for note-color's
   specific glyphs (astral-plane Musical Symbols combining marks)?
2. How do real high-refresh-rate terminal apps (htop, btop, tig, lazygit,
   k9s, vim, tmux) avoid full-redraw cost/flicker — is diff-based partial
   redraw worth adopting for note-color's actual frame sizes?
3. Is per-cell `write()`+flush actually the bottleneck, or is redundant
   escape-sequence volume (repeated SGR codes, cursor jumps) the real
   cost — and what does note-color's code already do?
4. Is there a case for a small pure-Python/low-dependency TUI library
   (urwid, or a heavier `blessed` role) specifically for the `tab` view,
   beyond the existing narrow `blessed`-for-Settings exception?
5. What are the documented hard pitfalls specific to scrolling/animated
   terminal content (flicker, cursor toggling, resize mid-render, wide/
   combining-character misalignment across terminal emulators)?

Findings below are cited to real, fetched sources (WebSearch/WebFetch,
listed in Sources) plus direct inspection of this repo's own source
(`terminal_tab_display.py`, `terminal_display.py`, `terminal_wheel_display.py`,
`config.py`, `requirements.txt`) and a live interpreter check of the
`wcwidth` version actually installed in `.venv` — not fabricated numbers.

## Answer

### 1. Grapheme/column-width correctness — already fixed correctly; one real gap remains, and it's not the one the code comment worries about

**The commit already adopted the right library.** `terminal_tab_display.py`
imports `wcwidth` (PyPI) and its `_pad_center()` helper (`terminal_tab_display.py:623-646`)
measures cell text with `wcwidth.wcswidth()` instead of Python's code-point-
counting `str.center()`/`str[:width]` — exactly the fix this class of bug
needs. `requirements.txt` pins `wcwidth>=0.2      # terminal_tab_display.py
-- display-width-aware cell padding for combining duration glyphs`. This is
the correct call: `wcwidth` (maintained by Jeff Quast, `jquast/wcwidth`) is
the standard Python port of the POSIX `wcwidth(3)`/`wcswidth(3)` C
functions and is what "virtually every terminal emulator's own cursor-
advance logic is built on" (the module's own comment, confirmed by
independent research below) — there is no more-authoritative small
alternative for this exact problem.

**Live-checked, not assumed: what `wcwidth` actually reports for
note-color's own glyphs.** The installed version in `.venv`
(`wcwidth.__version__` = `0.8.2`, well ahead of the `>=0.2` floor pinned in
`requirements.txt`) gives, run directly against this project's own
constants:

```
notehead alone                        wcswidth=1   per-codepoint=[1]
notehead+stem                         wcswidth=2   per-codepoint=[1, 0]
notehead+stem+flag1                   wcswidth=2   per-codepoint=[1, 0, 0]
notehead+stem+flag1+dot               wcswidth=2   per-codepoint=[1, 0, 0, 0]
notehead+flat (♭)                     wcswidth=2   per-codepoint=[1, 1]
barline (U+1D100) alone               wcswidth=1   per-codepoint=[1]
stem/flag/dot alone (unattached)      wcswidth=0   per-codepoint=[0]
```

This confirms the module docstring's claim precisely: `wcwidth.wcwidth()`
called on each combining codepoint *in isolation* returns `0` (they're
General_Category `Mc`, spacing combining marks, in Unicode's own tables),
but `wcwidth.wcswidth()` called on the *whole grapheme string*
(notehead+stem+flag+dot) returns `2`, not `1`. A naive "sum the isolated
per-codepoint widths" implementation — which is what the pre-fix
`str.center()`/manual-length code effectively approximated in spirit if not
in exact mechanism — would have predicted these combining marks contribute
0 extra columns, i.e. that the whole 4-codepoint cluster still occupies 1
column. `wcswidth()`'s actual, grapheme-cluster-aware answer (2) is what
real terminals render, because **no terminal font actually fuses a
notehead+stem+flag glyph sequence into one visual glyph** — despite
Unicode's General_Category machinery classifying them as combining marks,
they render as two side-by-side glyphs occupying two real cells. This is
the load-bearing subtlety `_pad_center()`'s fix depends on, and it is now
verified against the actual installed library, not just asserted in a
comment.

**Verdict on `wcwidth` PyPI: yes, sufficient for this bug class, with one
caveat worth acting on.** Per the wcwidth *maintainer's own* research
(Jeff Quast's blog, cited below — he also maintains `blessed`, already a
note-color dependency), `wcwidth`'s Unicode-table-driven answer is *not*
guaranteed to match every real terminal emulator's actual rendering. A
2026 survey of ~35 terminal emulators (`ucs-detect`, same author) found:

- Zero-width handling of `Mn`/`Mc` category combining marks is not
  universal: **Windows Terminal, cmd.exe, ConsoleZ, ExtraTermQt, and zoc**
  measure these as width-1 ("narrow") instead of width-0 — e.g. Vietnamese
  text with combining grave accents measures 5 columns wide in
  `Terminal.exe` when it should measure 4. This is the exact failure mode
  category note-color's combining stem/flag/dot glyphs fall into, just for
  a different script.
- Wide-character/emoji support varies by Unicode version the terminal's
  own table was built against: Konsole/iTerm2/kitty support Unicode 15.0
  (2022); VS Code's/Hyper's bundled `xterm.js` only supports Unicode 12.1
  (2019), so newer wide glyphs render 1-cell-wide and get visually
  occluded by the next character.
- Variation-selector (VS-15/VS-16) width effects: only 7 of 23 tested
  terminals handle these consistently.
- No terminal in the survey passed all four tested categories cleanly —
  overall terminal-emulator consistency on this exact class of problem is
  "problematic," in the author's own words, industry-wide.

This means: `wcwidth.wcswidth()` returning `2` for note-color's
notehead+duration-glyph cluster is *this library's* best answer and almost
certainly correct for the mainstream terminals this project's own
`docs/DECISIONS.md` already treats as the practical target (kitty,
iTerm2, gnome-terminal/VTE, wezterm — the same list cited for the OSC-8
donation link) — but it is not a mathematical guarantee for every
terminal, and the specific failure mode most likely to bite note-color
(Mc-category marks measured narrow instead of zero-width) is exactly this
project's own case, just for a different codepoint block than the
Vietnamese example.

**Actionable, low-effort improvement available today.** The exact
`wcwidth` version already installed (`0.8.2`) ships a newer API this
project isn't yet using: `wcwidth.width(text, term_program=...)` and
`wcwidth.wcstwidth(pwcs, term_program=True)`, which apply *terminal-
specific correction tables* built from the `ucs-detect` survey data,
rather than the terminal-agnostic Unicode-only table `wcswidth()` uses.
`term_program=True` (the default for `wcstwidth`) auto-detects the calling
terminal from environment variables and applies its known correction; the
existing `_pad_center()` still calls the plain `wcswidth()`, leaving this
free accuracy improvement on the table. Live-tested: for the mainstream
terminals checked (`VTE`, `iTerm.app`, `mintty`, `Konsole`, `kitty`,
`Windows Terminal`), the corrected answer for note-color's own
notehead+stem+flag cluster still came back `2` — i.e. adopting the
correction-table API would not change today's output for any terminal
checked, but it *would* future-proof against whichever terminal in the
`ucs-detect` survey turns out to disagree once someone actually hits it,
for the cost of one call-signature change (`wcswidth(s)` →
`wcstwidth(s)`, term-detection is automatic) and no new dependency (same
package, just the newer entrypoint it already ships in the venv's pinned
version). Recommended concretely in prototype idea #1 below.

**One asymmetry the current code has not addressed:** `_barline_cell()`
(`terminal_tab_display.py:657-663`) still centers `BARLINE_GLYPH` with
Python's own `.center(width)`, not `_pad_center()`'s `wcwidth`-aware
version — harmless *today* only because `BARLINE_GLYPH` is a single,
non-combining codepoint (confirmed above: `wcswidth` reports it as exactly
`1`, matching `TAB_BARLINE_WIDTH = 1`), so naive code-point centering and
`wcwidth`-aware centering coincide. But it's an inconsistency worth
closing for the same reason `docs/research/tab-barline-straightness.md`
already flagged this exact column as having "zero width margin" — if
`BARLINE_GLYPH` (or any future barline decoration) ever gained a combining
accent, only `_barline_cell()` would silently regress.

### 2. Diffing/partial-redraw architectures — real, load-bearing pattern elsewhere; likely not worth it for note-color's frame sizes

Every mature high-refresh TUI surveyed does *some* form of diff-before-
write, not full-redraw-every-frame:

- **ratatui** (Rust; cited as the reference architecture for "btop-shaped
  problems," used by `gitui`/`bottom`): maintains two full `Buffer`s sized
  to the viewport. Each frame, widgets draw into the "current" buffer;
  `Terminal::flush()` diffs it cell-by-cell against the previous buffer
  and emits ANSI only for the cells that actually changed
  (`diff_iter()`). This is the cleanest, most explicit "double-buffer +
  cell diff" implementation found.
- **prompt_toolkit** (Python; powers `ptpython`, IPython's prompt
  machinery, many CLI wizards): its `Renderer` explicitly "calculates the
  difference between the last output and the new one" every render pass,
  specifically because it must stay responsive on every keystroke over
  potentially slow/remote connections — the docs frame this as a latency
  requirement, not just a CPU one.
- **urwid** (Python; the closest thing to a "hand-rollable" Python TUI
  toolkit, see §4): uses a *canvas cache* — composite canvases from
  container/decoration widgets are cached; a widget's `_invalidate()` call
  evicts only that canvas and its direct parents from the cache, so
  unchanged subtrees are never re-rendered, and `Canvas` objects expose a
  diff method used by the display backend to emit only changed regions.
- **notcurses** (C; the highest-performance terminal library surveyed):
  builds a full cell matrix per "pile" of planes every frame but
  rasterizes it into "optimized control sequences," i.e. the optimization
  happens at the emission stage, not by skipping computation — its stated
  performance edge actually comes from bypassing character-cell rendering
  entirely via Sixel/Kitty pixel graphics protocols where available, which
  is a different axis from note-color's problem (note-color is
  intentionally staying in character-cell/ANSI truecolor territory, not
  pixel graphics).
- **ncurses** itself: the classic prior art. `refresh()` has always
  diffed the virtual screen against the physical screen and additionally
  runs a real optimal-cursor-movement algorithm (minimizing terminal
  cursor-repositioning cost, going back to the original BSD curses design
  literature) — this is the 40-year-old ancestor of every diffing
  approach found above.
- **Textual/Rich** (Python): a different primitive, not a cell diff.
  Rich/Textual operate on **Segments** (a string + style tuple) rather
  than characters — this sidesteps variable-width-character bookkeeping
  entirely at the data-model level, and a **spatial map** (a grid index of
  100×20-character tiles → widgets) gives Textual's compositor O(1)
  widget-visibility lookups so recomposing a viewport doesn't mean walking
  every widget on every frame. Textual's own writeup does not detail
  exact cell-diff mechanics or give hard frame-time numbers, so no
  benchmark citation is available here — Textual's real advantage
  documented is architectural (segment-level compositing + spatial
  indexing for scroll performance at high widget counts), not a specific
  measured latency win over full-redraw.

**Verdict for note-color: not worth adopting wholesale.** The universal
justification for diffing above is either (a) a *huge* backing buffer
(notcurses/ratatui-scale TUIs with many widgets/panes) or (b) *keystroke-
latency-sensitive interactivity* (prompt_toolkit). Note-color's actual
frame content is a full-terminal fill (`terminal_display.py`), a fixed
12-wedge ring (`terminal_wheel_display.py`), or a scrolling column strip
bounded by real terminal width (`terminal_tab_display.py`) — on the order
of a few thousand cells at most, redrawn at a fixed, moderate `fps`
(`config.py`'s `fps=20` default), not on every keystroke. A full
`\033[H`-plus-repaint (which note-color's code already does, see §3) at
that scale is cheap in absolute terms; the actual cost centers this repo
has hit (barline misalignment, status-line overflow) were *correctness*
bugs in a full-redraw model, not *performance* bugs that diffing would
fix. Introducing a double-buffer-and-diff layer would add real
complexity (a second in-memory grid, a diff routine, correctness risk
around the exact same wide-character-width math that already caused
today's bug — now duplicated into a diff comparator instead of just a
padding function) for a cost this project hasn't actually measured as a
bottleneck. Recommend: do not build a general diffing layer; if a future
profiling pass on real Pi-class hardware finds `render()` itself
(string-building + `sys.stdout.write`) is a measured percentage of frame
budget, revisit narrowly (e.g. diff only the barline/notehead composition
step, not a whole-screen buffer).

### 3. Escape-sequence batching — note-color already does the right thing at the write/flush level; the real remaining cost is redundant per-cell SGR, and it's small at this project's scale

Checked directly against note-color's own source (not assumed): every
terminal view's `render()` already builds one Python string (joining a
list of pre-built row/cell strings) and calls exactly one
`sys.stdout.write()` followed by one `sys.stdout.flush()` per frame —
`terminal_display.py:55-56,92-93`, `terminal_wheel_display.py:74-75,124-125`,
`terminal_tab_display.py:582-583`, `menu_display.py:217-218` all follow
this pattern. A full-screen clear (`\033[2J`) is emitted only on a
detected resize (`clear = "\033[2J" if size != self._last_size else ""`,
present in all three views), not every frame — matching best practice
(general research, not project-specific: batch every frame's output into
one write, since flushing after every small write "defeats buffering
efficiency" and adds syscall overhead per write; a full clear-then-redraw
is also the more flicker-prone approach vs. cursor-home-and-overwrite,
which is what note-color already does outside of resize).

**What note-color does *not* do: dedupe SGR codes across cells within a
frame.** `_note_cell()` (`terminal_tab_display.py:649-654`) and
`_barline_cell()` (`terminal_tab_display.py:657-663`) each independently
emit a full `\033[48;2;r;g;bm\033[38;2;r;g;bm...text...\033[0m` (or
foreground-only) sequence per cell/column, resetting (`\033[0m`) and
re-specifying full 24-bit color every time, even when the adjacent column
shares the same color. `terminal_display.py`'s solid fill similarly
repeats a full `bg`+content+`reset` sequence once per *row* even though
every row in a fill frame is the identical color (`render()`,
`terminal_display.py:51`: `out = [block_line] * rows` — `rows` copies of
the same escape-wrapped string, joined with `\n`, rather than one
`bg`-open + `rows` blank lines + one `reset`).

**This is real but small at note-color's scale.** For the `fill` view,
this costs roughly `rows` (≈20-50) redundant SGR emissions per frame — a
handful of bytes each, at ~20fps: negligible even on Pi-class hardware
(SGR string formatting is a cheap f-string, not a syscall; only the final
`write()` is a syscall, and there's exactly one of those already). For
`tab`, the redundant cost is per-column (a few dozen visible columns,
each already only formatted once, not per-character) — same order of
magnitude, still not per-character. **General best practice** confirmed
via research (not project-specific): a renderer that tracks "current SGR
state" and only emits a new color/style code when it actually changes from
the previous cell is the standard technique (this is effectively what
ratatui's cell-diff buys for free, and what a hand-rolled ANSI writer
would do explicitly) — but the win scales with *cell count and color
churn*, and note-color's frames are column-grained (a note occupies 3-9
identical-color cells, not individually-colored characters), so there is
comparatively little redundant-SGR volume to save versus, say, a
character-per-cell heatmap renderer. **Verdict: low priority.** Worth a
one-line fix for `terminal_display.py`'s fill (emit `bg` once, `rows`
blank lines, `reset` once — trivial, removes the one clearly-wasteful
repeated-row pattern found), not worth building general SGR-run-length
logic into `terminal_tab_display.py`'s per-column cells given the low
absolute cell count involved.

### 4. Small pure-Python/low-dependency TUI libraries — no compelling case for a broader `blessed` role or adding `urwid`; the existing narrow exception should stay narrow

Confirmed directly from source: `blessed` is already listed unconditionally
in `requirements.txt` (`blessed>=1.20`) but is actually `import`ed in
exactly one place in the whole codebase — a lazy, function-local `import
blessed` inside `settings_display.py:346` — matching CLAUDE.md's claim
that it's "the one scoped exception... settled by #37/#39's grilling
specifically for this screen's form controls." No other terminal view
imports it.

- **`blessed`**: reimplements terminal capability handling
  (`terminfo(5)`-based) from scratch rather than wrapping `curses`/
  `ncurses`, and is (per research) primarily pure Python — no C
  compilation step, so no Pi-wheel risk in the sense this project already
  worries about for `aubio`/`librosa`. It does *not*, however, do
  automatic screen diffing the way `curses`/`ratatui`/`urwid` do —
  `Terminal.fullscreen()`/`hidden_cursor()` are context managers for mode
  entry/exit, not a rendering/diffing engine. Adopting it more broadly
  would buy note-color terminfo-portability (arguably not needed — this
  project already hand-writes truecolor SGR codes directly and has made
  peace with astral-codepoint/EAW-ambiguous risk as an accepted
  terminal-emulator-dependent tradeoff) without buying diffing, so it
  would not address the actual bug class hit so far (grapheme width) or
  meaningfully change the performance profile.
- **`urwid`**: has a genuine pure-Python path — the "raw" display module
  is described in its own docs as "a pure-python display module with no
  external dependencies" that is the `MainLoop` default when no other
  backend is requested; a separate `curses` display module exists as an
  opt-in alternative with "optimized C code," not a requirement. `urwid`
  does carry an optional C extension (`urwid.str_util`) for string-width
  acceleration, but per its own architecture the pure-Python raw path
  works with zero C compilation — so it is usable on hardware/OS
  combinations with no prebuilt wheel, same posture as this project's
  existing Pi-wheel-risk filter for `aubio`/`librosa`. `urwid` would bring
  real diffing (canvas cache + composite-canvas diff, §2) essentially for
  free if adopted, but it is a genuine framework adoption — widgets,
  `MainLoop`, its own event model — not a narrow library call the way
  `wcwidth` was. **Not recommended**: this project's raw-ANSI convention
  is deliberate and already-justified (CLAUDE.md/`docs/DECISIONS.md`), and
  nothing in this research found a `tab`-view-specific problem that only
  a framework migration would fix — the confirmed bugs so far (barline
  double-counting, grapheme width) were both fixable as targeted,
  small-surface-area patches within the existing raw-ANSI model.
- **Textual/Rich**: heavier still (a real dependency tree — `rich`,
  `markdown-it-py`, `pygments`, etc.), and while a Textualize blog post
  confirms Textual has been run on Raspberry Pi hardware (a touchscreen
  control-surface project), no concrete Pi-class CPU/memory benchmark
  was found for it in this research pass — an open question, not a
  confirmed green light. Given `urwid` already covers "diffing without a
  new heavyweight dependency" more cheaply, Textual isn't a better answer
  to any problem this project actually has.
- **A specific, Pi-relevant wrinkle for *any* new dependency with a C
  extension**: `piwheels` (the standard ARM-wheel mitigation this
  project's own Pi-portability reasoning implicitly leans on) **does not
  currently provide 64-bit (aarch64/arm64) wheels at all** — confirmed
  directly from the piwheels FAQ ("The repository at piwheels.org does
  not currently support the 64-bit version of the Raspberry Pi OS...
  this requires a significant amount of work"). Since note-color's own
  documented target is **64-bit Raspberry Pi OS (Bookworm+)** (CLAUDE.md's
  Key design decisions: "32-bit is a wheel risk"), `piwheels` is not
  actually the safety net it might appear to be for this project — any
  future C-extension dependency needs either a genuine upstream
  `manylinux`/`musllinux` **aarch64** wheel published directly to PyPI
  (which `numpy` and most mainstream scientific packages do provide today,
  unlike five years ago) or to be pure Python outright. This raises,
  slightly, the bar for any future terminal-library dependency
  recommendation — one more reason `wcwidth` (confirmed pure Python) and
  `blessed`/`urwid`'s pure-Python paths are the safe picks, and a
  C-accelerated library without confirmed aarch64 PyPI wheels would need
  individual verification before adoption, not an assumption that
  "piwheels will cover it."

### 5. Known pitfalls specific to scrolling/animated terminal content

- **Flicker from clear-then-redraw** is well-documented as worse than
  cursor-home-and-overwrite; note-color already avoids the worst form of
  this (full `\033[2J` only on resize, confirmed in §3) — this is already
  the right posture, not a gap.
- **Cursor visibility toggling**: every note-color terminal view already
  hides the cursor once at construction (`\033[?25l`) and restores it once
  on `quit()` (`\033[?25h`) rather than toggling every frame — also
  already correct, confirmed directly in source for all three terminal
  views plus the menu.
- **Resize handling mid-render**: SIGWINCH-driven resize is the standard
  hard case cited across `tmux`/terminal-app bug trackers — the
  documented failure mode is a TUI that doesn't re-poll terminal size and
  ends up rendering into stale dimensions until some unrelated event (a
  keypress) forces a redraw. Note-color's own approach — calling
  `shutil.get_terminal_size()` fresh at the top of every `render()` call
  rather than caching it or relying on a signal handler — sidesteps this
  entire class of bug structurally, since it never trusts a stale size
  value across frames; this is a real, correct design choice already in
  place, not a gap to close.
- **Wide/combining-character misalignment across real terminal
  emulators** is the one pitfall category confirmed, by direct research
  (§1), to be *not fully solvable from the application layer alone* — no
  terminal in the `ucs-detect` survey passed every tested Unicode-width
  category, and this project's own `docs/DECISIONS.md` has already
  independently reached the same conclusion for the treble clef glyph's
  clipped-descent issue ("a terminal/font-stack property, not something
  fixable from the app layer"). The barline-glyph-width risk flagged by
  `tab-barline-straightness.md` (§2 of that doc) belongs to this same
  category: real, plausible, and only confirmable/fixable per-terminal,
  not from static analysis.
- **Astral-plane (SMP) codepoint support itself** varies by terminal font
  stack independent of the width-measurement question — a terminal can
  correctly *measure* a codepoint's width via `wcwidth` while its
  installed font still has no glyph for it (tofu/replacement-box
  rendering) or the terminal renders it via a fallback font at a
  different-than-expected cell width regardless of what `wcwidth` reports
  — this is a distinct risk axis from the measurement bug already fixed,
  and not something any Python-side width library can detect or correct;
  it is purely a font/terminal-emulator-configuration property on the end
  user's machine.

## Summary / prioritized prototype recommendations

Ordered by (estimated value) / (estimated effort), most attractive first:

1. **Swap `_pad_center()`'s `wcwidth.wcswidth()` call for
   `wcwidth.wcstwidth(text, term_program=True)`, and apply the same
   `wcwidth`-aware padding to `_barline_cell()` instead of `.center()`.**
   One call-site change plus one function reuse, no new dependency (same
   already-pinned package, newer API it already ships) — closes the
   remaining `_barline_cell()`/`_note_cell()` asymmetry and opts into
   terminal-specific correction tables for free future-proofing, at
   effectively zero behavioral risk (verified: returns identical results
   for every mainstream terminal checked today).
2. **Fix `terminal_display.py`'s fill view to emit one `bg` SGR + `rows`
   blank lines + one `reset`, instead of `rows` repeated full escape-
   wrapped identical-color lines.** Trivial, mechanical, the one clearly-
   wasteful redundant-SGR pattern this research actually found in the
   existing code (not a general "add SGR-run-length logic everywhere"
   project) — do this and stop; §3's analysis found the `tab` view's
   per-column redundancy not worth chasing further at its cell count.
3. **A small, standalone script (not integrated into the app) that runs
   `wcwidth.wcswidth()`/`wcstwidth()` against every glyph combination this
   project's `terminal_tab_display.py` actually composes (every
   duration-glyph × accidental × notehead-style combination), asserted
   against expected widths, run in CI.** Turns the ad-hoc live-interpreter
   check this research session did by hand into a permanent regression
   guard against a future Unicode-table update in `wcwidth` (or a future
   glyph addition) silently reintroducing a width-miscount bug of this
   exact shape.
4. **A live-terminal repro matrix for the barline-glyph-width risk
   `tab-barline-straightness.md` already flagged** (§2 of that doc) —
   run `virtualnote tab` in 2-3 real terminal emulators (at minimum one
   from each side of the `ucs-detect` divide: e.g. kitty/iTerm2/wezterm
   vs. a VTE-based one) and visually confirm the barline column doesn't
   drift. Cheap to run, resolves a flagged-but-unconfirmed risk with
   ground truth instead of further static reasoning.
5. **Lowest priority, exploratory only:** a throwaway prototype trying
   `urwid`'s raw (pure-Python) display module for just the `tab` view's
   scrolling-column composition, to get a real, measured before/after
   frame-time number on Pi-class hardware. Only worth doing if a future
   profiling pass (not done in this research) actually shows `render()`'s
   own cost, not audio-pipeline cost, dominating a frame budget on real
   target hardware — nothing found in this research indicates that's
   currently true, so this is a "build only if evidence emerges" item,
   not a near-term recommendation.

## Sources

- `wcwidth` PyPI package / GitHub (`jquast/wcwidth`): version history
  confirming 0.3.0 (2026-01-21) added `width()`/`wcstwidth()` and the
  `term_program` correction-table argument, 0.2.14 (2025-09-22) updated
  Unicode tables to 16.0/17.0 —
  https://github.com/jquast/wcwidth ,
  https://wcwidth.readthedocs.io/en/stable/intro.html ,
  https://libraries.io/pypi/wcwidth
- Jeff Quast (wcwidth/blessed maintainer), "Perfecting Terminal Character
  Width Using Correction Tables" (2026) — correction-table mechanism,
  `wcstwidth(term_program=...)` usage, general limitation that "the
  context of surrounding codepoints is required to accurately measure
  them" —
  https://www.jeffquast.com/post/perfecting-terminal-character-width-using-correction-tables/
- Jeff Quast, "Terminal Emulators Battle Royale – Unicode Edition!" (2026,
  `ucs-detect` project results) — the ~35-terminal survey: wide-char
  Unicode-version gaps (Konsole/iTerm2/kitty at 15.0 vs. xterm.js-based
  Hyper/VS Code at 12.1), Mn/Mc-as-narrow-not-zero failures in Windows
  Terminal/cmd.exe/ConsoleZ/ExtraTermQt/zoc, VS-16 support in only 7/23
  terminals, kitty's ZWJ-reduction failure, "50 million downloads/month"
  figure for the `wcwidth` PyPI package —
  https://www.jeffquast.com/post/ucs-detect-test-results/
- Live interpreter check against this repo's own `.venv` (`wcwidth
  0.8.2`): `wcswidth`/`wcwidth`/`wcstwidth` output for
  `NOTEHEAD_GLYPH`/`STEM_GLYPH`/`FLAG_GLYPHS`/`DOT_GLYPH`/`BARLINE_GLYPH`
  combinations, run directly during this research session (not a
  secondary source).
- Ratatui docs, "Rendering under the hood" and "Backends" — double-buffer
  diff (`Buffer`, `flush()`, `diff_iter()`) —
  https://ratatui.rs/concepts/rendering/under-the-hood/ ,
  https://ratatui.rs/concepts/backends/comparison/
- prompt_toolkit docs, "The rendering pipeline" — `Renderer` diffing
  last-vs-new screen, latency justification —
  https://python-prompt-toolkit.readthedocs.io/en/stable/pages/advanced_topics/rendering_pipeline.html
- Urwid docs, "Canvas Cache" and "Display Modules" — composite-canvas
  caching/invalidation, canvas diff method, pure-Python "raw" display
  module vs. optional C-accelerated `curses` module —
  https://urwid.org/manual/canvascache.html ,
  https://urwid.org/manual/displaymodules.html
- notcurses manpages (`notcurses(3)`, `notcurses_render(3)`) — plane/pile/
  cell rendering model, rasterization to "optimized control sequences,"
  pixel-graphics-protocol performance edge —
  https://notcurses.com/notcurses.3.html ,
  https://notcurses.com/notcurses_render.3.html
- Textualize blog, "Algorithms for high performance terminal apps"
  (2024-12-12) — Segment primitive, compositor cut/apply/discard/combine
  steps, spatial-map grid index for O(1) widget lookup —
  https://textual.textualize.io/blog/2024/12/12/algorithms-for-high-performance-terminal-apps/
- Textualize blog, "Raspberry Pi and Textual" — confirms Textual has been
  run on real Raspberry Pi hardware (touchscreen control-surface
  project); no CPU/memory benchmark numbers found in this pass —
  https://www.textualize.io/blog/raspberry-pi-and-textual/
- `blessed` GitHub/docs — terminfo-based reimplementation (not a
  curses/ncurses wrapper), `fullscreen()`/`hidden_cursor()` context
  managers —
  https://github.com/jquast/blessed ,
  https://blessed.readthedocs.io/en/latest/intro.html
- piwheels FAQ — confirms piwheels does **not** currently provide 64-bit
  (aarch64/arm64) Raspberry Pi OS wheels, only 32-bit —
  https://www.piwheels.org/faq.html
- Direct inspection of this repo's own source as of commit `d7d2ea0`:
  `terminal_tab_display.py` (`_pad_center()`, `_note_cell()`,
  `_barline_cell()`, module docstring/comments on `STEM_GLYPH`/
  `FLAG_GLYPHS`/`DOT_GLYPH`), `terminal_display.py`, `terminal_wheel_display.py`,
  `menu_display.py` (write/flush/clear-on-resize patterns),
  `settings_display.py` (`blessed` import site), `requirements.txt`
  (dependency pins and inline rationale comments).
- `docs/research/tab-barline-straightness.md` (this repo) — prior
  diagnosis of the beat-accumulator double-counting bug and the
  unconfirmed `TAB_BARLINE_WIDTH`-margin risk this doc's §1/§5 build on
  without re-deriving.
