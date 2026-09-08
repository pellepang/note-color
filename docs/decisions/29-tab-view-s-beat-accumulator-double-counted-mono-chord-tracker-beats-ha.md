# `tab` view's beat-accumulator double-counted mono+chord tracker beats, halving barline spacing (issue #76)

Root-caused via a research subagent
(`docs/research/tab-barline-straightness.md`): `run_terminal_tab()`'s
`beats_accumulated` was incremented by **both** the mono
`DurationTracker`'s finalization **and** every `note_stack` (chord/
multipitch) entry's finalization, summed, every hop — unconditionally,
regardless of the view's `P` display toggle, since the chord/multipitch
pipeline always runs (this codebase's documented always-on-pipeline
convention, see CLAUDE.md's Architecture). An ordinary single monophonic
note is routinely finalized independently by both trackers in the same
hop (the mono smoother's own note, and multipitch's one-note "chord" for
the same acoustic event), so summing credited that one note's duration
toward the next bar boundary roughly twice — barlines landing at
~half the correct spacing, independent of tempo-estimate accuracy; a
perfect BPM estimate would not have fixed it. `run_batch_transcribe()`
never had this bug: it already takes `max()` over every simultaneous
note at one onset (`main.py`'s `column_beats = max(column_beats,
note_beats or 0.0)`) rather than summing across mono/chord streams —
proof this was a fixable asymmetry, not something inherent to tracking
mono and chord data in parallel.

Fixed by extracting the per-hop credit into a small pure function,
`main._hop_beats(beats_values)` — takes the max across whatever
durations (mono's, if any, plus one per finalized `note_stack` entry)
finalized this hop, treating a `None` value (bpm_estimate unknown at
finalization time) as 0.0 — and calling it once per hop instead of
accumulating inline sums from two separate code blocks. Mirrors
`run_batch_transcribe()`'s existing pattern exactly, just factored out
into a named, unit-tested helper (`tests/test_main.py`) rather than
inlined per-block max-tracking, following this repo's "pure logic
unit-tested, real render loop smoke-tested" convention (see
`menu_animation.detect_perf_mode()`/`_decide_perf_mode()` for the
precedent this follows). Barline *placement* accuracy tied to live
tempo-estimate quality is unaffected by this fix and remains the
already-documented, accepted approximation (CLAUDE.md's Known
limitations) — this fix corrects the beat-*count* per hop, not the
BPM conversion factor.
