# note-color

Real-time audio-to-color visualizer. This glossary covers vocabulary introduced by feature efforts that isn't already self-evident from code comments and `docs/DECISIONS.md` — the monophonic pipeline's own terms (note, pitch, onset) are covered there, not repeated here.

## Language

### Chord mode (wayfinder map [#1](https://github.com/pellepang/note-color/issues/1))

**Chroma vector**:
A 12-element vector folding all spectral energy in an analysis window into the 12 pitch classes (C, C♯, D, ... B), discarding octave. The input to chord matching.
_Avoid_: Pitch-class profile (PCP) — same concept, but this project uses "chroma vector" consistently.

**Pitch-class set**:
The set of pitch classes actually detected as sounding at a given moment (a chroma vector thresholded into present/absent), before any chord name is assigned to it. What the "no match" fallback falls back to internally when nothing in the chord dictionary matches well enough — it is not itself rendered as a chord name (see **Chord name**).

**Chord quality**:
A root-relative pattern of semitone intervals (e.g. maj = 0,4,7) that defines a family of chords across all 12 roots. The chord dictionary stores one binary pitch-class mask per quality, not per individual chord.
_Avoid_: Chord type — "quality" is the standard music-theory term and what the dictionary is keyed on.

**Chord template**:
A chord quality rotated to a specific root — one of the ~360 entries (30 qualities × 12 roots) tested against the observed chroma vector during matching.

**Chord mode**:
The detection mode (toggled via `P` in terminal views) that runs chroma-vector chord recognition instead of monophonic YIN pitch detection. `P`'s default state and direction are view-specific: opt-in (off by default, `P` switches up to chord mode) in `fill` and `wheel`; opt-out (on by default, `P` switches down to monophonic) in `tab`'s notation rendering — see wayfinder map [Sheet-music notation for the tab view](https://github.com/pellepang/note-color/issues/13).

**Notehead**:
The pitch-bearing glyph drawn at a note's staff position in the `tab` view's notation rendering — the sheet-music equivalent of the colored-letter-block cell it replaces. Has two interchangeable render styles, toggled live via `N`: *symbol* (an open/white notehead glyph, U+1D157, chosen by live in-terminal comparison — see wayfinder map [Sheet-music notation for the tab view](https://github.com/pellepang/note-color/issues/13)) and *name* (a note-name letter character, closer to the previous cell text) — cosmetic only, staff placement is identical either way.

**Bass chroma**:
A second, separate 12-bin pitch-class estimate folded only from the low-frequency portion of the spectrum (below ~250Hz). Its strongest bin is the detected bass note, used to resolve inversions/slash chords and to break ties between rotationally-symmetric chord templates (dim7, aug) — the main chroma vector alone can't do either, since it discards octave.

**Chord name**:
The text label shown for a matched chord: a root pitch class, spelled using this project's flat-biased convention (F♯ kept sharp rather than spelled G♭) uniformly across every view, followed by a quality symbol, with an optional slash-suffixed bass note (see **Slash chord**). Left present but empty on a "no match" — the pitch-class set is not rendered as a substitute name.
_Avoid_: Chord label — "label" already denotes the per-note name drawn in `tab`; keep the two distinct.

**Slash chord**:
A chord whose sounding bass note (per **Bass chroma**) differs from its own root — an inversion, or a chord voiced over a foreign bass. Rendered in a chord name as "<name>/<bass>" (e.g. "C/E"); a chord in root position omits the slash.

### Score editor (wayfinder map [#85](https://github.com/pellepang/note-color/issues/85))

**Column** (editor sense):
One time-slot in a loaded score — the simultaneous group of zero-or-more sounding notes (or a **Rest**) at one position in the piece, addressed as a unit by the editor's cursor's left/right movement. Distinct from `tab`'s own live-scrolling `TabEntry` column (same underlying idea — one moment's worth of notes — but the editor's is a fixed, random-access slot in an already-loaded score, not a column arriving in real time).

**Rest** (editor sense):
A column deliberately marked as silence, with its own duration like any note. Created only via a dedicated action, never an incidental side effect of deleting a column's last note — the editor refuses to let ordinary note removal empty a column to zero notes for exactly this reason, so "empty" is never ambiguous with "rest."
_Avoid_: Empty column — a column mid-edit with zero notes isn't a valid state; it's either got notes or it's a Rest.

**Chord builder**:
The dedicated screen reached by drilling into a column (from the main editor view), for constructing or editing that column's chord via independently adjustable **Reels**. Distinct from the main editor view's own inline pitch/duration editing, which needs no drill-in.

**Reel**:
One independently spinnable, typeahead-able component of the **Chord builder** — root, quality (a preset shortcut), or one of the third/fifth/seventh degrees. Spinning or typing into a reel updates the column's notes live, with no separate confirm step.

**Score properties screen**:
A second, separate reel-based screen (same shape as the **Chord builder**: its own local keys, one key back to the main view) for editing the three score-level properties a blank score is seeded with — time signature, key signature, and tempo — via three independently spinnable **Reels** (time signature stepping through common signatures, key signature around the circle of fifths, tempo by BPM increment). Distinct from the **Chord builder**, which edits one column's notes rather than the whole score's properties.
