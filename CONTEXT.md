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

### Sound engine (wayfinder map [#99](https://github.com/pellepang/note-color/issues/99))

**Patch**:
A file describing how to make sound — the unit a user loads, edits, saves and shares. One hand-editable TOML file per patch under `~/.config/note-color/patches/`. Every patch declares which engine renders it (`engine = "synth" | "sampler" | "sf2"`), so a subtractive-synth sound, a sampled drum kit, and an SF2 program selection are all patches rather than three unrelated file kinds — "load a patch" stays one code path and one browser UI regardless of what is inside.
_Avoid_: Preset — an exact synonym for patch, deliberately rejected so the two can never drift apart in the code or the UI.

**Kit**:
A patch whose engine is the sampler and whose zones are each one key wide — the degenerate case of a sampler instrument, not a separate concept or file kind. What the synth tool's drum-pad mode displays; the pad grid is a *view* onto a kit, never its own engine.

**Zone**:
A sample mapped to a range of keys, with a root key it was recorded at. A range wider than one key is what lets a single recorded note play across an octave (a real sampler instrument); a kit's zones are one key wide each. Modelled on the SF2 zone concept, so the sampler and the SF2 player share one mapping vocabulary.

**Voice**:
One sounding note at runtime — the thing polyphony counts and voice stealing reclaims. Carries the per-note state that must survive across audio blocks (oscillator phase, filter state, envelope stage, LFO phase, velocity). Distinct from a **Patch**, which describes how voices are made; many voices can sound from one patch at once.

**Program**:
SF2's own term for a bank-plus-preset selection inside a soundfont. Used *only* when talking to FluidSynth — a patch that selects one is still called a patch everywhere else.

**Sample**:
An audio recording on disk that a **Zone** plays. Copied into `~/.config/note-color/samples/` on import and referenced by bare name, so a patch stays shareable rather than pointing into one machine's filesystem.

### Score editor (wayfinder map [#85](https://github.com/pellepang/note-color/issues/85))

**Column** (editor sense):
One time-slot in a loaded score — the simultaneous group of zero-or-more sounding notes (or a **Rest**) at one position in the piece, addressed as a unit by the editor's cursor's left/right movement. Distinct from `tab`'s own live-scrolling `TabEntry` column (same underlying idea — one moment's worth of notes — but the editor's is a fixed, random-access slot in an already-loaded score, not a column arriving in real time).

**Rest** (editor sense):
A column with zero notes, with its own duration like any note. Reachable two ways: `clear_to_rest` (`r`) empties a column's notes outright in one press (its main value for a multi-note chord column), and `note_toggle` (Space) can also reach zero notes one note at a time, the same key that places them — Space originally refused to remove a column's very last note specifically to keep "empty" unambiguous with "rest," but direct user feedback after hands-on use found that two-step flow (place/remove with Space, but *only* `r` to reach zero) unwanted friction; see docs/DECISIONS.md. A column with zero notes is a Rest regardless of which action produced it — there's no separate "empty, not yet a rest" state.
_Avoid_: Empty column — a column mid-edit with zero notes isn't a distinct state from Rest; it's the same thing.

**Chord builder**:
The dedicated screen reached by drilling into a column (from the main editor view), for constructing or editing that column's chord via independently adjustable **Reels**. Distinct from the main editor view's own inline pitch/duration editing, which needs no drill-in.

**Reel**:
One independently spinnable, typeahead-able component of the **Chord builder** — root, quality (a preset shortcut), or one of the third/fifth/seventh degrees. Spinning or typing into a reel updates the column's notes live, with no separate confirm step.

**Score properties (inline header editor)**:
The three score-level properties a blank score is seeded with — time signature, key signature, and tempo — shown at all times in the main editor view's own status line (`time=`/`key=`/`tempo=`) and made interactively editable in place by pressing `score_properties` (`t`): Left/Right selects a field, Up/Down (or, for time signature/tempo, typing a value directly) changes it, Enter returns to normal cursor editing. Originally a second, separate reel-based screen with the same shape as the **Chord builder** — its own local keys, one key back to the main view — reversed after direct user feedback that leaving the main view for this was unwanted friction, not from a new abstract argument (see docs/DECISIONS.md). Distinct from the **Chord builder**, which edits one column's notes rather than the whole score's properties.


### The DAW (wayfinder map [#145](https://github.com/pellepang/note-color/issues/145))

**Project**:
The document VisualNote Studio opens, edits and saves: a `.ncproj` bundle directory holding a tempo map, its **Tracks** and their **Clips**, plus the audio and notation they reference. Subsumes what the conversion effort called a *Score project*, whose on-disk shape was left undecided until this map settled it.
_Avoid_: Session, for this. A **Session** here is the live microphone-capture bundle, not a saved document — other DAWs use the word the other way round, and this repo's `SessionState` predates them in this codebase.

**Track**:
One lane of the timeline, holding **Clips** in sequence. May carry notes or audio. This is the DAW sense of the word and now the only sense — see **Part** for what it used to mean.

**Clip**:
A bounded piece of content placed on a **Track** at a musical position. Two kinds: a *Note clip* (a run of notes) and an *Audio clip* (a region of a recorded file). Both exist in the format from the start; only Note clips play at the first milestone.

**Chord track**:
A **Track** whose content is harmony rather than notes — chord spans along the ruler, derived by analysis and correctable by hand, with corrections surviving re-analysis. The one timeline object this app has that a conventional DAW does not.

**Tempo map**:
How musical position converts to time. Positions everywhere are bars and beats against this map, and seconds are derived — so changing the tempo moves the music rather than rewriting it. Audio clips anchor to a musical position while keeping their own sample length.

### Audio-to-score conversion (wayfinder map [#123](https://github.com/pellepang/note-color/issues/123))

**Conversion**:
Turning a recorded audio file into an editable **Score project**, offline and non-causally — the whole file is available at once, and the work may take up to an hour. Distinct from **Transcription** in the live sense the rest of this app uses (`virtualnote transcribe`, the detection pipeline), which is causal and per-hop. The two share an output format and nothing else; the live path is explicitly untouched by this effort.

**Stem**:
One instrument's audio, separated out of a mixed recording (vocals, bass, drums, other). An intermediate artifact of conversion, cached to disk while converting and never part of the saved result — conversion produces symbolic notation, not audio.
_Avoid_: Part, for this. A stem is audio; a **Part** is notation.

**Part**:
One instrument's notated music — what a **Stem** becomes after conversion. Viewable and editable alone or alongside the others. MusicXML's own word for it (`<score-part>`, `<part-name>`), which is also the only identity #128 measured as surviving a music21 round trip.
_Avoid_: Track, for this. This concept was called a Track until map [#145](https://github.com/pellepang/note-color/issues/145) reserved that word for a DAW timeline lane, which is what it means to anyone opening a DAW. `ConversionResult.parts` and the project manifest's `parts` key both carry the new name; a version 1 manifest's `tracks` key still reads.

**Timing oracle**:
Use of the drum **Stem** to establish beat, downbeat and meter for the whole piece, because percussion carries that information far more clearly than pitched material does. Distinct from drum *notation*, which is a separate output; the same stem serves both purposes.

**Pass**:
One stage of a conversion that consumes the previous stage's output — separation and global estimates, then transcription conditioned on those estimates, then quantization against a fixed grid. Whether more passes genuinely buy accuracy is a hypothesis this map tests, not an assumption it builds on.
