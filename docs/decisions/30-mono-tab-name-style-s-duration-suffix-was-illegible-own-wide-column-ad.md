# Mono `tab` *name* style's duration suffix was illegible: own wide column added (issue #83)

`_cell_text()`'s *name* style composes `f"{letter}·{suffix}"` (e.g.
"Bb·16th."), up to 8 display columns for the worst case (a 2-char
accidental letter + middle dot + the longest 5-char suffix, "whole"/
"half."/"16th."). Mono columns render at `config.TAB_COLUMN_WIDTH = 3`
— `_pad_center()` correctly clips oversized text to fit, but the
*result* was illegible: "C·whole" -> "C·w", "A·16th." -> "A·1". Chord
mode never had this problem, since `TAB_COLUMN_WIDTH_CHORD = 9` was
already sized for chord names of similar length.

Three options were on the table: widen `TAB_COLUMN_WIDTH` itself (moves
every mono column, symbol style included, denser layout lost for a
problem specific to one toggle state); abbreviate suffixes further to
fit 3 cells (a single-letter code like "w"/"h"/"q"/"e"/"s" loses
real information — dotted vs. undotted collapses onto the same letter
unless a second symbol is added, which is most of the way back to
needing more width anyway); or give mono name-style-with-duration its
own wider column, mirroring `TAB_COLUMN_WIDTH_CHORD`'s existing
precedent for exactly this "this render mode's text is wider than a
default cell" situation. Took the third option:
`config.TAB_COLUMN_WIDTH_NAME = 9` (same value as `TAB_COLUMN_WIDTH_CHORD`
by coincidence — both landed on ~9 cells for unrelated content — kept as
its own constant so the two can move independently later), selected in
`TabDisplay.render()` only when `not chord_mode and notehead_style ==
"name"`. Symbol style (whose duration glyphs are combining marks
composed onto the notehead, not extra text) keeps `TAB_COLUMN_WIDTH`
unchanged, so its already-fine compact layout is untouched. Verified via
`research/terminal-capture/capture.py --scene tab-name`: all four
duration suffixes in that scene ("C·whole", "G·4th", "D·8th", "A·16th.")
now render in full, unclipped.
