# Capping harmonic_number + lowest-note tiebreak (issues #67/#68, round 2)

The evaluation-order fix above (`3e49499`) helped clean up small phantom
counts but real-mic re-verification found it wasn't enough and was a mixed
bag: chord-name accuracy barely moved (14.3%→16.3%), phantom rate got
*worse* overall, and some previously-correct plain chords (a C major
triad, C/E slash, Csus4) started returning no match at all. Both issues
were reopened with that data. This round re-validated against
`scripts/acoustic_pipeline_test.py --source loopback` (a real
PortAudio/PipeWire round trip, no room acoustics/mic coloration — the only
live-pipeline validation available in this sandboxed environment; a real
physical speaker→mic re-check is still advisable before fully trusting
these numbers in the field) instead of live mic, since no physical
mic/speaker exists here. Baseline on that signal was already much
healthier than the real-mic numbers in the issue threads (87.8% chord-name
accuracy, 0 mean phantom pcs/hop) but still showed two distinct, unresolved
problems once broken down by root cause:

**Bug 1 — `_is_harmonic_of()` had no upper bound on harmonic number
(#68's real remaining recall gap).** The pruning loop computes
`harmonic_number = round(freq / accepted_freq)` and prunes the candidate
if the predicted `accepted_freq * harmonic_number` lands within
`harmonic_tolerance_cents` — for *any* integer `harmonic_number`, however
large. That's fine for the low harmonics a real instrument's overtone
series and this app's own `chroma.HARMONIC_WEIGHTS`/
`YIN_SUBHARMONIC_MAX_MULTIPLE` conventions already treat as "the harmonics
that matter" (1-4), but at high multiples it starts pruning real,
independent notes purely because they land near *some* large integer
multiple of an already-accepted note. The more integers there are to try
(8x, 9x, 12x...), the more likely an accidental near-miss becomes — and
that likelihood grows with chord density and pitch spread, exactly #68's
"recall collapses under density" symptom. Confirmed directly against
`acoustic_pipeline_test.py`'s own `DENSITY_VOICINGS` fixtures: a 6-note
voicing (C2 D#2 F#3 A3 D4 G5) lost G5 because it sits ~2 cents from C2's
*12th* harmonic (65.41×12=784.9 vs G5's 783.99Hz); a 5-note voicing (F2 A2
C3 E4 G5) lost G5 to C3's *6th* harmonic the same way, on top of losing E4
to A2's genuine 3rd-harmonic collision (the already-documented, still-open
residual below).

Fix: capped `harmonic_number` at `CHORD_HARMONIC_MAX_NUMBER = 4`
(`multipitch._is_harmonic_of`, wired through `detect()`, `main.py`, and
`batch_transcribe.py`) — the same "1-4 is what matters" line this codebase
already draws elsewhere. Verified safe against every existing
`test_multipitch.py` fixture (none synthesizes harmonics above the 4th —
matches `scripts/acoustic_pipeline_test.py`'s own synth, which also only
generates harmonics 1-4 per note); new regression tests
`test_high_order_harmonic_near_miss_does_not_prune_a_real_independent_note`
and `test_own_low_order_harmonics_still_pruned_after_capping_harmonic_number`
confirm the cap fixes the former without weakening the latter. Real-world
tradeoff: capping means a genuinely high-order overtone (a distortion
artifact from the real mic/speaker/room, or an instrument with unusually
rich high-partial content) is no longer pruned as "obviously" belonging to
its fundamental, so it can now register as a low-confidence phantom
instead of being silently absorbed — measured on the loopback density
suite as a small phantom-rate uptick (0 → 0.0-0.33/hop at 3-5 note
density) traded for a much larger recall gain (missing pcs/hop: 0.67-1.51
→ 0-0.73 across the same density range). A clear net win on this signal;
worth re-checking against real mic/room content since analog distortion
there is likely richer than loopback's.

**Bug 2 — symmetric-chord root tiebreak had no signal at all when no note
was in the true bass register (#67's remaining real misnaming).** Once
Bug 1's phantom/missing problem was otherwise fixed, the loopback chord
suite still misnamed 6/49 chords — all with phantom_rate and missing_rate
already at 0 (the *pitch-class set* detected was exactly right). Every one
was a rotationally-symmetric quality (augmented, dim7, half-dim7/min6)
voiced entirely above `chroma.DEFAULT_BASS_CUTOFF_HZ` (~250Hz) — e.g. an
F#-A#-D augmented triad, voiced upward from F#4, consistently named
"D+". `chord_templates._resolve_tie()`'s only real disambiguation signal
was `bass_chroma`, gated to require genuine sub-250Hz content (the right
gate for genuine slash-chord naming, so as not to misread ordinary
mid-register chords as having a bass note) — but that gate meant *any*
symmetric chord voiced in the mid/treble register got zero disambiguating
signal at all, falling to the old fallback: `min(candidates, key=root)`,
a fixed but musically arbitrary "always answer with whichever root index
sorts lowest."

Fix: added `lowest_pc` — the pitch class of whichever detected note is
lowest in frequency *this hop*, unconditionally (no bass-register
requirement) — as a last-resort tiebreak in `_resolve_tie()`, tried only
after the genuine `bass_chroma` signal fails to disambiguate.
`chord_smoother._update_chord_name()` computes it as
`min(note_candidates, key=freq).pitch_class` from the same already
harmonic-pruned `multipitch.detect()` output chord-name matching already
uses (issue #56), so it costs nothing extra to compute. Rationale: absent
any other signal, "the lowest note actually played is probably the root"
is a far better default than an arbitrary index — root-position,
lowest-note-in-the-bass voicing is both this app's own acoustic-test
convention and ordinary real playing's common case. It only ever fires
when multiple templates are tied on cosine similarity (i.e. genuinely
pitch-class-set-identical, like aug/dim7/min7-vs-maj6), so it can't
override a real, better-scoring match. New tests:
`test_symmetric_augmented_triad_resolved_by_lowest_detected_note`,
`test_symmetric_dim7_resolved_by_lowest_detected_note_without_bass_chroma`,
`test_lowest_pc_tiebreak_yields_to_a_confident_bass_chroma` (confirms
`bass_chroma` still wins when both signals disagree), and
`chord_smoother`'s own
`test_symmetric_chord_name_uses_lowest_note_candidate_as_tiebreak` for the
wiring itself.

**Combined result on the loopback suite** (`--source loopback`, real
PortAudio/PipeWire round trip, this session's only available live-pipeline
validation — see caveat above): chord-name accuracy 87.8%→**100%**
(49/49); density-suite missing pcs/hop dropped to ~0 through 5-note chords
(previously 0.67-1.44) and to 0.73 at 6 notes (previously 1.51), at the
cost of a small phantom-rate uptick (0→0.0-0.33/hop) explained above.
Chromatic (100%/100%) and tempo (90/140/200bpm 100%, 280bpm 88%) suites
unaffected — re-run after both fixes landed, identical to their
pre-existing baseline. Full test suite green (278 passed, from 272).

**Residual limitation, unchanged by this round.** The near-exact (~2
cent) small-integer-ratio collision documented above (e.g. A2's 3rd
harmonic landing almost exactly on E4) is still not resolved, and isn't
expected to be — `harmonic_number` capping only stops checking multiples
*above* 4; it changes nothing for a collision at harmonic_number 2, 3, or
4, which is exactly where the genuinely inherent ambiguity lives (a fixed,
small integer ratio a real instrument's own overtone series would
produce, at exactly the harmonic numbers this codebase already treats as
"real"). The F2-A2-C3-E4-G5 voicing referenced above is a good example of
the two bugs' actual boundary: E4 collides with A2 at harmonic_number=3
(still pruned, still the documented inherent limitation) while G5 collided
with C3 at harmonic_number=6 (now fixed by the cap, since 6 is above the
new limit and gets a real chance to survive as its own note). The
genuinely inherent, harmonic_number≤4 collisions remain exactly as
described above: unresolvable from a single hop's magnitude spectrum
alone, still the documented known limitation.
