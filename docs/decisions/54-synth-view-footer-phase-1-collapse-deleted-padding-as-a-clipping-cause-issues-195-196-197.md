# Synth View footer, Phase 1: collapse deleted, and a padding rule that
# had been clipping text for three tickets (issues #195, #196, #197)

Phase 1 of the Synth View map (#191), on top of #194's screenshot
harness. Three user complaints, and the interesting part of all three is
that the previous fixes were not wrong — they were aimed at the wrong
thing.

## The footer no longer collapses (#196, closes #162)

`_CollapsingSplitter` snapped the footer fully shut once dragged below a
"playable minimum". Its docstring was a long, honest account of trying to
get Qt's native collapse logic and a hand-rolled one to agree, across
`setChildrenCollapsible` in both settings. They never could: Qt excludes
an already-hidden pane from its position math, so native collapse has no
way back, while with collapse disabled a real drag can never reach the
branch at all. Three rounds (#184, #185–#190) each improved something and
left the user's report verbatim unchanged.

**Collapse is deleted rather than tuned a fourth time.** `_FooterSplitter`
clamps to `[floor, ceiling]` and hides nothing. The user asked for the
footer to "go down and up", not to vanish; and a footer that can hide
itself on a stray drag takes the key band, the assignment pill and the
recents rail — and every click target on them — with it. That is where
the "rail/pill/keys are invisible, no popup opens" reports came from:
nothing was ever wrong with those widgets, their parent had been hidden.
If the footer should ever be hideable, that is a toggle with a way back,
not a drag gesture.

The user's sentence — "it doesn't change size and suddenly just
disappears" — turned out to describe **two unrelated bugs**, which is
probably why each previous round fixed half of it and shipped.

"Doesn't change size" was not the collapse logic at all. `main_row`'s own
`minimumSizeHint()` is the drawer's full natural height (356px), and
`QSplitter` honours it, so the pane above claimed everything the footer
wanted: a 150px drag moved the footer exactly 0px.

The range was then widened again after the user described the ends they
wanted — down to only the header rows, up to filling the canvas. Two
things were capping it, both predating the question: `KeyBoxRow`'s
vertical minimum equalled its natural size, so the key rows could grow
but never shrink; and `_FOOTER_MAX_HEIGHT` was an arbitrary 640 that
bound before anything meaningful did. Now the only thing that stops the
footer is `_CANVAS_MIN_HEIGHT` — an actual decision about how much canvas
to leave.

**A trap worth naming:** `effective_ceiling()` first read
`minimumSizeHint()` for the pane above. An explicit `setMinimumHeight()`
*overrides* the hint, so the computed ceiling equalled the floor on any
window shorter than ~840px — no travel at all on the 768px screen this is
used on. The bug, reintroduced by its own fix, and caught only because
the test derived its numbers from the real range instead of hardcoding
pixels.

## `QLabel { padding: 8px }` was clipping module names (#197, closes #164, #168)

One cause, both surfaces, both previous failed fixes.
`theme.main_stylesheet()` sets `padding: 8px` on **every** label in the
app, and `QFontMetrics` cannot see a stylesheet padding. Every name label
in the synth chrome sizes *itself* from `QFontMetrics`, so each was handed
a box exactly as big as its text and then spent 16px of it on padding:

* `_TitleBar` computed `setFixedWidth(36)` for `"OSC 1"`, leaving 20px of
  content — `OS` plus a sliver of `C`, clipped mid-glyph rather than
  elided. #164's elide arithmetic was right all along; it was measuring a
  box 16px wider than the one the text got to paint in.
* `_DrawerRow` computed `setFixedHeight(metrics.height() + 10)` and lost
  16px vertically, cutting descenders. #168 correctly replaced a
  hardcoded `22` with a metrics-derived height, and its 10px of slack was
  spent twice over.

Worth recording: the drawer defect was never a *width* problem. `Delav`
in the before-shot is `Delay` with its descender cut. Reading it as
horizontal clipping is probably part of why it kept getting an alignment
fix.

Fixed by stating `padding` explicitly where the geometry is computed.
Drawer rows zero only the *vertical* padding — the 8px horizontal inset is
what sets the rows in from the drawer's edge, and dropping it too left
row text starting further left than the group labels above them.

**The wider hazard is still open.** A global padding default on the most
generic widget class in the app means any label anywhere that derives its
own geometry from font metrics will clip silently, and look like an
alignment bug. Repairing it at the rule changes the look of every label in
the studio window, so it was not done unilaterally.

## The middle bar (#195)

The ticket said the bar to remove was `SynthView._build_layout_tabs()` and
warned that deleting it would take the clickable layout switcher with it.
That conflated two widgets in two files: the bar the user described is a
bare `QLabel` inside `SynthKeyboardBand` with no click targets, and
`_build_layout_tabs()` is the row above it. The ticket's blocking question
was moot. Asked and confirmed before writing code anyway — the cost of
asking was one message, and the cost of guessing wrong was deleting the
only mouse route to a layout switch.

## Test-harness findings

Two, both of which had been silently distorting this work.

`conftest.py` set `QT_QPA_PLATFORM` with `setdefault`, which defers to a
value already in the environment — and a Wayland developer typically
exports one. **The GUI suite was opening real windows into the live
session**, where a tiling WM resized them at will, so `resize()` in the
splitter tests never stuck and the drag assertions failed in a way that
looked exactly like an app bug. Found because the user noticed windows
tiling across their display mid-run and asked about it.

Forcing it headless then exposed a genuine suite-wide hang: an autouse
teardown in `test_synth_view.py` closed *every* `QMainWindow` in the
application, reaching into other modules' leftovers — and a
`StudioWindow` left dirty by `test_studio_window.py` answers `close()`
with a modal `QMessageBox.question()` that nothing in a test run will ever
dismiss. The suite hung there forever with no output.

## The guard, and why it is the size it is

Per #191 decision 6 this project is not adding a test layer; taste goes to
the user by question. #197's clipping had regressed twice, so it gets an
assertion and nothing more: each name label must have room for the text it
shows, measured against `contentsRect()` (Qt applies stylesheet padding as
contents margins, so the content rect is the box the glyphs really get).

The load-bearing part is that it runs against an app with
`theme.main_stylesheet()` applied. Against an unstyled `QApplication` the
assertion **passes while the real app still clips** — which is how a guard
for this bug gets written uselessly. Verified red on the pre-fix code and
green after; a guard nobody has seen fail is not yet a guard.
