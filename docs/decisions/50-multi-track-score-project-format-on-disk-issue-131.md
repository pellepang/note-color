# Multi-track score project format on disk (issue #131)

#128's research answered this by *running* music21 10.5.0 rather than
reading its docs, and the recommendation is adopted essentially whole.

### One multi-part MusicXML file

Not MEI (music21's subconverter is **read-only** — it cannot write MEI at
all), not MIDI (no notation layer, no per-note colour), not a folder of
per-track files plus a manifest (contradicts every file convention in this
repo and buys nothing in scope).

- **One track = one `<score-part>`, identified by a unique
  `<part-name>`.** Never `Part.id` — measured not to survive a round trip.
  A grand-staff track is a `PartStaff` pair plus a brace `StaffGroup`,
  which is exactly what `save_score()` already writes.
- **Per-track metadata lives in a versioned JSON manifest** in the
  *score-level* `<miscellaneous>` block via
  `md.setCustom("note-color.project", …)`, keyed to tracks by part name,
  additive, degrading to "one untitled track" when absent. **Never
  per-part `<identification>`** — music21 silently drops it. The manifest
  carries instrument, source stem, which model produced the track, and
  confidence.
- **Tempo, time signature and key stay score-global**, written on the
  first Part and never on the `Score` — a `MetronomeMark` inserted on a
  `Score` is silently discarded.

### Compatibility: no migration, one guard

**Every existing `.musicxml` is already a valid one-track project**, and a
one-track project written by the new writer is byte-compatible with what
the current editor reads and writes. There is no migration and no version
bump.

The one required change to shipped code is a **guard in
`load_score()`**. #128 found that today it iterates `parsed.parts` with no
count check, so a 4-track file loads **silently wrong** — flattened into
four-note chords and then written back as two staves. That is data loss on
save, and it is reachable now by opening any multi-part MusicXML from
anywhere. Refusing is strictly better than silently destroying. Map #85
excluded *foreign MusicXML import fidelity* from its scope, which is why
this was never caught; excluding fidelity is not the same as accepting
silent corruption.

### Two things the research flagged that this decision commits to

- **Chord symbols get a home**, needed by #141's Real Book output, which
  did not exist when #128 ran. They are MusicXML `<harmony>` elements
  attached to the part carrying the melody — that is precisely what the
  element is for — rather than a synthetic extra track, so one score
  renders as either a full part or a lead sheet without re-transcribing.
  `music21`'s `harmony.ChordSymbol` is the in-memory form. **The round
  trip through this repo's own `save_score()`/`load_score()` is
  unverified**; #141 verifies it before anything depends on it.
- **Tempo is stored as a curve, not a scalar.** #127 measured the
  single-value assumption as the MuseScore/Finale failure mode (Fme
  9.9–15.3 against PM2S's 61.7, with constant tempo named as the cause),
  and `score_editor_state.py` holds exactly one `tempo_bpm` float today.
  A curve is expressible as multiple `<direction>` metronome marks, but
  **nothing has verified music21 round-trips several of them**, and
  `load_score()` currently reads only `tempo_list[0]`. The format commits
  to the curve; the verification is a build task, not an assumption.

### The data-model problem this does not solve

`EditorColumn` is a **monophonic-rhythm model**: one shared column
sequence across both staves, with a column's duration defined by when the
next column starts. #128 found `load_score()`'s offset-lockstep assumption
is **already broken today**, before any multi-track work — parts with
independent rhythms read back as a longer timeline with durations
fabricated from onset gaps.

Multi-track makes this unavoidable rather than causing it. A per-track
column sequence is the direction, but it reaches into map #85's finished
editor and is left to #138's spec to face rather than being decided in a
format ticket.

### Unverified, and recorded as such

**MuseScore and Dorico interop is entirely untested** — neither is
installed here. Whether either preserves `<miscellaneous-field>` entries
across import/export, and whether they render this repo's per-note colours
as expected, is unknown. Worst case the manifest is dropped and the file
degrades to one part per track with no provenance, which is a real but
survivable loss. This is the largest gap in the format decision.
