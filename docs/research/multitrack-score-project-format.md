# Multi-track score project format, and multi-track score-editing prior art

Research for issue
[#128](https://github.com/pellepang/note-color/issues/128), a child of map
[#123](https://github.com/pellepang/note-color/issues/123) ("audio file to
editable multi-track score"). The map's standing decision is **multi-track
output**: a score is saved as a project of several tracks, one per
transcribed stem, viewable and editable one at a time or together,
DAW-style. Today `score_writer.py` and the map [#85](https://github.com/pellepang/note-color/issues/85)
score editor both assume exactly one two-staff grand staff. This document
answers what that project should look like on disk, and what breaks on the
way there.

The four resolved siblings were read from map #123's Decisions-so-far
rather than re-derived. Two of their findings are load-bearing here:

- #124 — on **real commercial band audio the best open model reaches
  37.87% onset F1** (25.29% if the instrument label must match). A
  converter that is wrong about three notes in five is not a converter
  whose output a human reads passively; it is one whose output a human
  *reviews*. That makes per-track provenance and per-note confidence
  format requirements, not nice-to-haves.
- #125 — **no open model emits the score layer** (barlines, meter, key,
  note values), and code/weight licences diverge per project. "Which model
  produced this track" is therefore a per-track fact that genuinely
  varies within one project file, not a global one.

Everything executed on this machine is marked **measured here** and was
run with this repo's own interpreter (`.venv/bin/python`, Python 3.14.7,
`music21` 10.5.0), following #124's convention. Claims I could not verify
from a primary source or a run are marked **unverified** in place. Scripts
live in the session scratchpad, not in the repo; every one of them is
reproducible from the code quoted below.

## Questions

1. Can a one-track project stay readable by the existing single-grand-staff
   editor, unchanged?
2. Single multi-part MusicXML, or a folder of per-track files plus a
   manifest?
3. What per-track metadata does a DAW-shaped UI need, and can MusicXML
   carry it?
4. Where does per-note transcription confidence live?
5. What is the prior art, and what is the minimum viable version of it?

## Summary of the answer, before the evidence

**Recommendation: one multi-part MusicXML file, with a JSON track manifest
embedded in score-level `<miscellaneous>` metadata, and each track carried
as one `<score-part>` identified by `<part-name>`.** A track that needs a
grand staff is a `PartStaff` pair braced by a `StaffGroup` — exactly the
shape `score_editor_state.save_score()` already writes, nested as one
track among several. Everything in that sentence was verified by running
it; nothing requires a new dependency, an XML post-processing pass, or a
container format.

Five findings shaped it, in descending order of how much they constrain
the answer.

1. **`music21` cannot write MEI.** Measured here: its MEI subconverter is
   read-only. MEI is off the table for this repo without adopting a second
   score library, which settles that format option without any further
   argument.
2. **Per-part `<identification><miscellaneous>` — MusicXML's own native
   per-track metadata slot — is silently dropped by `music21`.** Score-level
   `<miscellaneous>` is not: `md.setCustom()` writes it, and a full JSON
   manifest round-trips *equal* through `music21`'s own writer and reader.
   That single asymmetry is why the manifest is score-level-and-keyed
   rather than attached to each part.
3. **`Part.id` does not survive a round trip; `<part-name>` does.** Whatever
   `Part.id` is set to on write is replaced on disk by a generated
   `P<md5>`, and on read `music21` sets `Part.id` *from* `<part-name>`. A
   part with no `partName` at all comes back with a Python object id — an
   integer that changes every process. Track identity has exactly one
   stable carrier, and it is the part name.
4. **The existing editor reads a multi-part file without crashing, and
   destroys it on save.** `load_score()` flattens every part into one
   column list. This is the compatibility answer, and it is worse than
   "incompatible" would have been, because it is silent.
5. **`load_score()`'s offset-lockstep assumption is already broken today**,
   before multi-track exists. A 3-part, 4-beat score with independent
   rhythms reads back as an **8-column, 8-quarter-beat** editor timeline —
   double the true length. So does a two-note file written by this repo's
   own `write_score()` with staggered onsets. Multi-track does not
   introduce this bug; it makes it unavoidable.

## 1. Compatibility: what the existing editor does with a multi-track file

This was the ticket's first priority and it is answerable entirely from
the code plus one run.

`score_editor_state.load_score()` builds its columns like this
(`score_editor_state.py`, `load_score`):

```python
for part in parsed.parts:
    for element in part.flatten().notesAndRests:
        offset_key = round(float(element.offset), 6)
        entry = by_offset.setdefault(offset_key, {"notes": [], "quarter_length": 0.0})
        entry["quarter_length"] = max(entry["quarter_length"], float(element.quarterLength))
```

It iterates **`parsed.parts`** — all of them, however many there are — and
merges everything sharing an offset into one `EditorColumn`. There is no
part count check anywhere in the module.

### 1a. Reading a 4-track file: silent flattening, not a crash

**Measured here.** A 4-part score (guitar / bass / keys / drums, four
quarter notes each, one colour per part) written by `music21` and read by
this repo's own `load_score()`:

```
n columns: 4
  col 0: dur=quarter notes=[(0, 4), (0, 2), (4, 5), (0, 4)]
  col 1: dur=quarter notes=[(4, 4), (7, 2), (2, 5), (0, 4)]
  col 2: dur=quarter notes=[(7, 4), (9, 2), (0, 5), (0, 4)]
  col 3: dur=quarter notes=[(9, 4), (5, 2), (11, 4), (0, 4)]
```

Four tracks became four four-note "chords" on one grand staff. No
exception, no warning. Every note is present and correctly pitched; all
track identity is gone.

The danger is the next keystroke. The editor's `w`/`save` calls
`save_score()`, which writes **two** parts split by `staff_map.staff_row()`.
So the sequence "open a 4-track project in today's editor, press `w`" is a
lossy, irreversible conversion of a multi-track project into a two-staff
piano reduction, presented to the user as a normal save. `main.run_score_editor()`
already tracks `dirty` and confirms on quit, but nothing in that path can
know the file it loaded had four tracks.

**Consequence for the answer to question 1.** A *one-track* project can be
byte-compatible with what the editor already reads and writes — that
follows directly, because a one-track project written as a braced
`PartStaff` pair *is* the file `save_score()` produces (verified in §2b).
A multi-track project cannot be made safe for the old editor by choice of
format; the editor needs a guard. The cheapest honest guard is a check in
`load_score()` — count distinct `partName`s, and refuse (or open
read-only) when there is more than one track. That is a small change to a
module this map is not otherwise touching, and it is the thing that makes
the migration story safe rather than merely documented.

### 1b. The lockstep assumption breaks — and it is already broken

`save_score()`'s docstring states the invariant it relies on:

> A Rest column is written as an explicit `music21.note.Rest` on *both*
> staves (not just skipped, unlike `write_score()`'s batch-conversion
> gaps) so the two parts' offsets stay in lockstep column-for-column —
> `load_score()` relies on that lockstep to merge them back into one
> flat column list.

That invariant holds only because `save_score()` itself manufactures it.
Nothing in `load_score()` enforces or checks it, and an `EditorScore` is a
*sequence* of columns whose start times are the running sum of the
preceding durations (`offset += quarter_length`), not an offset-addressed
timeline. Any part whose notes start between another part's note starts
therefore injects a new column and lengthens the whole score.

**Measured here.** Three parts, all exactly 4 quarter-beats long, none in
lockstep — track A four quarters, track B two halves, track C eight
eighths (14 note events in total):

```
n columns: 8
  col 0: dur=half     notes=[(0, 4), (0, 2), (7, 5)]
  col 1: dur=eighth   notes=[(7, 5)]
  col 2: dur=quarter  notes=[(0, 4), (7, 5)]
  col 3: dur=eighth   notes=[(7, 5)]
  ... (repeats)
sum of column durations: 8.0 quarter-beats; true score length: 4.0
```

The editor's own timeline is **twice** the score's real length, and no
column's duration matches the note it holds. This is not a rounding
artefact; it is the data model being the wrong shape for concurrent parts.

The same failure is reachable today with two parts and no multi-track
feature at all. **Measured here**, a `TranscriptionResult` with exactly two
notes — one treble at hop 0, one bass half a beat later — through this
repo's real `score_writer.write_score()` and then `load_score()`:

```
columns: [('eighth', [(0, 4)]), ('eighth', [(0, 2)]), ('sixteenth', [])]
```

Two notes in, three columns out, durations fabricated from the *gaps*
between onsets rather than the notes' own measured lengths, plus a
spurious empty trailing column. So "open a batch-transcribed score in the
editor" is already unreliable whenever the bass and treble parts do not
attack together — which, on band material, is nearly always.

This matters for #128 beyond a bug report: it means the multi-track format
question cannot be answered purely at the file layer. **`EditorColumn` is
a monophonic-rhythm data model.** Whatever goes on disk, a multi-track
editor needs a per-track column sequence (each track keeping its own
lockstep internally, exactly as today's two staves do) rather than one
shared one. That is a data-model finding, and it belongs in the spec
ticket, not only here.

## 2. On-disk shape

### 2a. What `music21` can actually write

**Measured here**, by enumerating `music21.converter.subConverters`:

| Format | Reads | Writes |
|---|---|---|
| MusicXML (`.musicxml`, `.xml`, `.mxl`) | yes | **yes** |
| MIDI | yes | yes |
| MEI | yes | **no** |
| ABC | yes | **no** |
| Humdrum, Capella, MuseData, NoteworthyText | yes | no |
| LilyPond, Braille, Vexflow, RomanText, Scala | — | yes |

**MEI is eliminated.** `ConverterMEI` has no output extensions; `music21`
parses MEI and cannot emit it. Adopting MEI would mean adopting a second
score library (`verovio`, or hand-rolling a writer the way `abc_export.py`
hand-rolls ABC for exactly this reason — CLAUDE.md records that `music21`
"can *read* ABC but has no ABC *writer*"). Nothing in this map's
requirements needs what MEI offers over MusicXML, so paying that is not
justified.

**MIDI-plus-metadata is eliminated on a different ground.** MIDI has no
notation layer at all — no note values, no barlines as such, no key
signature meaningfully, and critically no per-note colour, which decision
[#30](https://github.com/pellepang/note-color/issues/30) named as the
single requirement that settled MusicXML in the first place ("the only
format surveyed with a native per-note `color` attribute"). #125 already
found the score layer has to be built rather than adopted; storing the
output in a format that cannot express it is backwards. MIDI stays what
#30 left it as — a plausible interop target, not the score of record.

That leaves the two options the ticket actually poses against each other.

### 2b. Option A — one multi-part MusicXML file

**Measured here.** A four-part score round-trips with:

| Property | Survives? | Detail |
|---|---|---|
| Part count | **yes** | 4 written, 4 read |
| `Part.id` | **no** | written as generated `P<md5>`; read back *from* `<part-name>` |
| `partName` / `partAbbreviation` | **yes** | the only stable identity carrier |
| Instrument name + `midiProgram` | **yes** | `Electric Guitar`/26, `Electric Bass`/33, `Piano`/0 |
| Per-note `color` | **yes** | all four distinct colours intact, on `<note color=…>` *and* `<notehead color=…>` |
| Percussion clef + `Unpitched` | **yes** | `<unpitched>` written, `PercussionClef` read back |
| Colour on an `Unpitched` note | **yes** | `#FF00FF` / `#00FFFF` survived — this repo's colour convention extends to a drum track |
| Score-level `MetronomeMark` | **no** | a `MetronomeMark` inserted on the `Score` produced **zero** `<direction>` elements |
| `metadata.title` | partly | written as both `<work-title>` and `<movement-title>`; read back into `metadata.movementName`, **not** `.title` |
| `metadata.composer` | yes | |

Two of those are traps worth naming.

**The tempo trap.** `score_editor_state.save_score()` inserts its
`MetronomeMark` on the `treble` *Part*, and that works — the control
round trip returned `tempo back: 120.0`. Inserting the same object on the
`Score` instead silently produces no tempo marking at all. A multi-track
writer that "tidies up" by moving tempo to the score level would lose
tempo, and `load_score()` would quietly substitute `DEFAULT_TEMPO_BPM`
(90.0) with no error. Tempo belongs on a part — by convention, the first.

**The identity trap.** Because identity rides on `<part-name>`, two tracks
named the same thing are indistinguishable. **Measured here**: two parts
both named "Guitar" come back with `Part.id == 'Guitar'` twice; a part with
no `partName` comes back with `id=139962801427280`, a process-local
integer. Track names must therefore be *made* unique at write time
(`Guitar`, `Guitar 2`) rather than assumed unique, and a track must never
be written without a name.

**A track can itself be a grand staff.** This is the finding that makes
option A fit this repo specifically. **Measured here**, a score containing
one braced `PartStaff` pair ("Keys"), one single-staff `Part` ("Bass"), and
one percussion `Part` ("Drums"):

```
parts read back: 4
  type=PartStaff partName='Keys'  clefs=['TrebleClef']    elems=['Note']      colours=['#AA0000']
  type=PartStaff partName='Keys'  clefs=['BassClef']      elems=['Note']      colours=['#AA0000']
  type=Part      partName='Bass'  clefs=['BassClef']      elems=['Note']      colours=['#00AA00']
  type=Part      partName='Drums' clefs=['PercussionClef'] elems=['Unpitched'] colours=[None]
StaffGroups: [('brace', ['Keys', 'Keys'])]
<part-group> count in XML: 0
<staves> count in XML: 1
```

`music21` collapsed the `PartStaff` pair into **one** `<score-part>` with
`<staves>2` (not a `<part-group>`), and split it back into two `PartStaff`
objects sharing a `partName` on read, with the brace `StaffGroup` intact.

That is precisely the file `save_score()` already writes for a one-track
score. So **a one-track multi-track project and a today's-editor score are
the same bytes** — question 1's compatibility requirement is satisfied by
construction, not by a compatibility shim. The migration story for every
existing `.musicxml` in the repo is: it is already a valid one-track
project; adding a manifest is optional and its absence must mean
"one untitled track", not "malformed".

One consequence for implementers: `len(score.parts)` is **4** in the run
above, for **3** tracks. Track count is the number of distinct `partName`s,
never the number of parts.

### 2c. Option B — a folder of per-track files plus a manifest

This shape is not hypothetical; MusicXML 4.0 standardises it. A compressed
`.mxl` file is a zip containing `META-INF/container.xml` naming one or
more `<rootfile>`s, and the container may hold "parts that are referenced
with the `<part-link>` element" plus other media, with subfolders
recommended. `<part-link>` exists specifically so "MusicXML data for both
score and parts [can] be contained within a single compressed MusicXML
file", via `xlink:href` paths relative to the zip root.

**Measured here**, `music21` *can* write `.mxl`:

```
outputExt ('musicxml', 'xml', 'mxl')
zip entries: ['t.musicxml', 'META-INF/container.xml']
```

But note what it wrote: one rootfile, no `<part-link>`, and **no
`mimetype` entry** — the spec asks for an uncompressed `mimetype` entry
holding `application/vnd.recordare.musicxml` as the archive's first file.
So `music21` gives a container, not a multi-document project; producing a
real `<part-link>` project means this repo writing the container and the
link elements itself, and reading them back itself, because `music21`
models neither.

Weighed against what this repo actually does today, option B loses:

- **It contradicts every file convention here.** Scores are single files
  next to `main.py` (`score_editor_picker.score_file_paths()` is a flat
  `*.musicxml`/`*.xml` glob), session logs are single `.jsonl` files, ABC
  export is a single file, patches and layouts are single TOML files. A
  directory-as-a-document would be the first compound artefact in the
  project.
- **The picker would have to change shape.** `score_file_paths()` globs
  files; a folder-per-project needs directory traversal, an "is this
  directory a project?" test, and a not-a-project failure mode.
- **It buys nothing this map needs.** The reasons real applications use
  containers — embedded images, separate engraved part layouts, alternate
  PDF/audio renditions — are all out of scope (#30 scoped out
  articulations, dynamics, slurs and lyrics; #85 scoped out an
  engraving-quality formatter, still out of scope).
- **`music21` would stop being the whole persistence layer.** Today
  `load_score()`/`save_score()` are two functions. Option B adds zip
  handling, a manifest schema *and* a link-resolution pass, in a module
  whose stated design goal is that "no music21 object escapes either
  function".

The one genuine argument for B is caching: map #123 already accepts that
separated stems and model outputs are written to disk, and a project
folder is the obvious place for them. That argument does not survive
contact with the map's own framing — the cache is explicitly "ephemeral
working data, not the score of record". Binding ephemeral data into the
document's on-disk identity is the opposite of what that distinction says.
A cache directory keyed by input-audio hash, sibling to the score rather
than containing it, gets the benefit with none of the format cost.

**Verdict: option A**, with option B reachable later without a rewrite —
`.mxl` is a zip *around* the same `<score-partwise>` document, so promoting
a single file into a container later is additive, exactly the property #30
relied on when it scoped multi-part scores out ("MusicXML is additive, so
these can be picked up later without a format change").

## 3. Per-track metadata

The DAW-shaped UI the map wants needs, per track: a **name**, an
**instrument**, **mute** and **solo**, a **display colour**, the **source
stem** it came from, a **transcription confidence**, and **which model
produced it**. MusicXML carries the first two natively and none of the
rest — `<score-part>`'s children are `<identification>`, `<part-link>`,
`<part-name>`, `<part-name-display>`, `<part-abbreviation>`,
`<part-abbreviation-display>`, `<group>`, `<score-instrument>`,
`<player>`, `<midi-device>`/`<midi-instrument>`. There is no visibility,
mute, solo or provenance element. (`<midi-instrument>` has volume and pan,
which are a mix state, not a mute state, and would be a misuse.)

MusicXML's own answer to "metadata the format does not support" is
`<miscellaneous>`, holding `<miscellaneous-field name=…>` entries — and
`<score-part>` allows its own `<identification>`, so the standard *does*
have a native per-track key/value slot.

**`music21` cannot reach it. Measured here.** A hand-injected per-part
`<identification><miscellaneous>` carrying four `note-color.*` fields,
parsed by `music21` and re-written:

```
injected file, per-part misc fields present: 5
parsed OK, parts: 4
md.all() sample: [('filePath', '…/inject.musicxml'), ('note-color.project', 'v1')]
SURVIVED re-write? note-color fields in output: 1
```

The one survivor is the *score-level* field. The four per-part fields were
read as nothing and written as nothing. `music21` does not model
`<score-part><identification>`, so anything placed there is lost on the
first save — the worst possible failure mode, since the file still parses.

Score-level `<miscellaneous>`, by contrast, is fully supported in both
directions through the public API. **Measured here**:

```python
md.setCustom("note-color.project", json.dumps(manifest))
md.setCustom("note-color.version", "1")
```

```
miscellaneous-field count on disk: 4
read back keys: ['note-color.project', 'note-color.version']
round-tripped manifest equal: True
```

A complete JSON manifest — `{"tracks": [{"name": "Gtr", "stem": "other",
"confidence": 0.38, "model": "mt3-v1", "muted": false, "color":
"#FF0000"}]}` — came back **equal** to what went in, through `music21`'s
own writer and reader, with no XML post-processing.

**So the recommended carrier is a JSON manifest in score-level
`<miscellaneous>`, keyed to tracks by `<part-name>`.** Properties:

- One namespaced key (`note-color.project`) plus a `note-color.version`
  integer, following `config_store.py`/`patch_format.py`'s established
  additive-and-degrade posture: an absent manifest means one untitled
  track, an unknown key is ignored, a malformed value falls back to its
  default rather than raising.
- The *musical* content stays in ordinary MusicXML, so MuseScore, Dorico
  and anything else still opens the file and shows all the tracks. They
  ignore the manifest; nothing is hidden from them.
- The manifest is redundant with, never authoritative over, what MusicXML
  models natively. Track name is `<part-name>`; instrument is
  `<score-instrument>`. The manifest carries only what MusicXML has no
  element for — stem, confidence, model, mute, solo, display colour — so a
  file edited in MuseScore and brought back has at worst a stale manifest
  entry, not a contradiction about what notes exist.
- Mute/solo are arguably session state rather than document state (the
  same category as the `tab` view's `frozen`/`scroll_offset`, which this
  repo deliberately never persists). Storing them is cheap and a DAW does
  persist them, but if the choice is contested, dropping them costs
  nothing.

**Unverified:** whether MuseScore or Dorico preserve unknown
`<miscellaneous-field>` entries through their own import/export. Neither is
installed on this machine, so no interop test was run at all — the
MuseScore/Dorico half of the ticket's "does it import" question is
genuinely open. The risk is bounded: worst case a round trip through
MuseScore strips the manifest, and the file degrades to "one track per
part, no provenance", which is still a valid project.

## 4. Per-note confidence — the harder half

#124's 37.87% is the reason this section exists. A reviewer needs to know
*which* notes to distrust, and a per-track average confidence does not
answer that.

Four carriers were tested. **Measured here:**

| Carrier | Written to disk | Read back by `music21` |
|---|---|---|
| `note.style.color` | yes | **yes** |
| `note.lyric` | yes (`<lyric>`) | **yes** |
| `note.editorial.<custom>` | **no** | no |
| `note.id` | **yes** (`<note id="n0">`) | **no** |

`editorial` is not a persistence channel — a `confidence` attribute set on
`note.editorial` was simply absent after a round trip.

`note.id` is the interesting one and the most frustrating. The ids *are*
written (`['n0', 'n1', 'n2', 'n3']` present in the file) but come back as
Python object ids (`[139909688504576, …]`) — `music21`'s reader does not
restore them. So the natural design (a side-table in the manifest keyed by
stable note id) writes cleanly and cannot be read back through `music21`
alone. Two ways out, neither free:

- **Key by `(part-name, offset, pitch)`** instead of by id. Needs no extra
  parsing, but the key is invalidated by exactly the edits a reviewer
  makes — moving or retuning a note silently orphans its confidence entry.
  Acceptable if orphaned entries are treated as "unknown", not as an error.
- **A second, lightweight XML pass** for ids only (`xml.etree` over the
  same file, no new dependency), joined to `music21`'s parse by document
  order. Robust to edits, but introduces a second reader of the same file
  and a new way for the two to disagree.

`lyric` survives both directions and is the pragmatic fallback, but it is
engraving-visible: a confidence number would print under every notehead in
MuseScore. Rejected on that basis unless it is opt-in.

**Colour deserves an explicit mention and an explicit rejection.** It is
the one per-note channel that round-trips perfectly, and it is already
fully spent: `note_hex_color()` is a fixed-lightness fifths-hue mapping
whose whole purpose, recorded in decision #98's rationale, is that "a note
reads as the same color in an exported score as it does live". Encoding
confidence in hue would break the one cross-view invariant this repo has
gone out of its way to protect. Lightness is not free either —
`TAB_NOTE_LIGHTNESS` is fixed precisely so octave does not leak into
colour. If confidence ever wants a visual channel, it should be a
renderer-side treatment (the editor drawing low-confidence noteheads
differently from a manifest lookup), not a change to what is stored.

**Recommendation for confidence: store it per note in the manifest, keyed
by `(part-name, offset, pitch)`, and treat a missing or orphaned entry as
"unknown" rather than "confident".** Ship per-track aggregate confidence
first — it is trivially expressible, immediately useful for ordering a
review pass ("start with the keys track, it scored 0.31"), and does not
depend on resolving the id problem. Per-note is a strict addition on top.

**Unverified:** whether the models #125 surveyed emit a usable per-note
confidence at all. #125 reports onset F1 figures, not calibrated
per-note scores, and a model's raw output probability is not the same
thing as a reviewer-meaningful confidence. This is a real dependency, and
it belongs to #130/#131 rather than to the format.

## 5. Prior art, briefly

**MuseScore** separates a `MasterScore` from `Excerpt` objects. The master
holds the single source of truth — including the `TimeSigMap`, `TempoMap`
and repeat list — and parts are derived excerpts referencing back into it,
with changes propagating through a change channel. The lesson: **timing is
score-global, content is per-part.** That matches the recommendation
above (one time signature, one key, one tempo at the score level; notes
per track) and matches #127's separate finding that a tempo *curve*, not a
scalar, is what should live at that global level.

**Dorico** goes further and separates content from presentation entirely:
*players* hold instruments, *flows* are stretches of music, and *layouts*
"can contain any combination of players and flows", sharing their musical
content — change a note in the full score and the part layout updates,
while page formatting is independent per layout. The lesson: **"view one
track" and "view all tracks" are two layouts over one content store, not
two documents.** For this repo that means the multi-track view is a
rendering concern, and the file format does not need a notion of "which
view you were in" — consistent with how `tab`'s freeze, scrollback and
marks are all deliberately session-local and never persisted.

**DAW score/piano-roll views** are the source of the mute/solo/colour
vocabulary and of the per-track show/hide expectation. Their structural
contribution is smaller than it looks: a DAW track list is a flat,
ordered, named list with per-track toggles, which is exactly the manifest
above.

**Minimum viable version**, in the order the map should build it:

1. N tracks in one file, each a `<score-part>` named uniquely; the editor
   shows **one track at a time**, chosen from a picker; the manifest
   carries name, instrument, stem, model, and a per-track confidence.
2. Show/hide toggles and a combined view, once per-track column sequences
   exist in the data model (§1b) — because "all tracks together" is
   precisely the case that today's `EditorColumn` cannot represent.
3. Per-note confidence, mute/solo, cross-track editing.

Step 1 needs no new UI paradigm at all: it is today's editor plus a track
picker, and the track picker is the same shape as
`score_editor_picker.run_score_editor_picker()`, which already exists.

## Recommendation

1. **One multi-part MusicXML file.** Not MEI (`music21` cannot write it),
   not MIDI (no notation layer, no per-note colour), not a folder
   (contradicts every file convention in this repo and buys nothing in
   scope).
2. **One track = one `<score-part>`, identified by a unique
   `<part-name>`.** Never `Part.id` — it does not survive. A grand-staff
   track is a `PartStaff` pair plus a brace `StaffGroup`, which is what
   `save_score()` already writes.
3. **A JSON track manifest in score-level `<miscellaneous>`** via
   `md.setCustom("note-color.project", …)`, keyed to tracks by part name,
   versioned, additive, degrading to "one untitled track" when absent.
   Never per-part `<identification>` — `music21` drops it silently.
4. **Tempo, time signature and key stay score-global** and are written on
   a Part (the first), never on the `Score` — a `MetronomeMark` inserted on
   the `Score` is silently discarded.
5. **Migration: no migration.** Every existing `.musicxml` is already a
   valid one-track project. A one-track project written by the new writer
   is byte-compatible with what the old editor reads and writes.
6. **Add a guard to `load_score()`** — refuse, or open read-only, when a
   file contains more than one track. This is the one change to existing
   code the recommendation requires, and without it the compatibility
   story is "the old editor silently destroys multi-track files".
7. **Treat `EditorColumn` as needing a per-track column sequence.** The
   shared-column model is already broken for concurrent parts, today,
   before multi-track exists.

## What this document does not settle

- **MuseScore and Dorico interop is entirely unverified.** Neither is
  installed here. Whether they preserve `<miscellaneous-field>` entries
  across an import/export, and whether they render this repo's per-note
  colours as expected, are both untested. This is the largest gap.
- **Whether models emit usable per-note confidence** (§4) — a #130/#131
  question, and per-note confidence storage is contingent on it.
- **Percussion notation rendering.** `Unpitched` notes, `PercussionClef`
  and colour on unpitched notes all round-trip cleanly, so the *format*
  side is fine; whether `terminal_tab_display.py`/`score_editor_display.py`
  can draw a percussion staff at all is the map's own still-open question,
  untouched here.
- **The tempo curve.** #127 recommends storing a tempo curve rather than
  the single `tempo_bpm` scalar `score_editor_state.py` holds today. A
  curve is expressible in MusicXML as multiple `<direction>`/metronome
  marks, but nothing here verified that `music21` round-trips several of
  them, and `load_score()` currently reads only `tempo_list[0]`.
- **Whether a re-run at a higher accuracy setting reconciles with user
  edits** — explicitly open on map #123, and the manifest's per-track
  `model`/`confidence` fields are a prerequisite for it rather than an
  answer to it.
- **Mute/solo as document state vs. session state** (§3) — recorded as a
  judgment call, not settled.

## Sources

Primary sources, and things run on this machine.

**Run here** (Python 3.14.7, `music21` 10.5.0, this repo's `.venv`):
four-part round trip; this repo's `score_editor_state.load_score()` against
a 4-part file; ragged 3-part rhythm merge; `score_writer.write_score()`
staggered-onset round trip; metadata-carrier survival (`style.color`,
`lyric`, `editorial`, `note.id`); nested `PartStaff`/`StaffGroup`/
percussion round trip; duplicate and absent `partName`; hand-injected
per-part and score-level `<miscellaneous>`; `md.setCustom()` manifest round
trip; subconverter output-format enumeration; `.mxl` write.

**Repo code**: `score_writer.py`, `score_editor_state.py`,
`score_editor_picker.py`, `CLAUDE.md` (score editor and config sections),
`docs/DECISIONS.md` §"Score editor data layer: a second `music21`
importer" (issue #98).

**Issues**: [#30](https://github.com/pellepang/note-color/issues/30)
(MusicXML + `music21` decision, and its explicit scoping-out of
multi-part scores as additive-later),
[#123](https://github.com/pellepang/note-color/issues/123) (map, and the
#124/#125/#126/#127 findings quoted from its Decisions-so-far),
[#85](https://github.com/pellepang/note-color/issues/85)/[#98](https://github.com/pellepang/note-color/issues/98)
(the existing score editor).

**Specifications** (W3C MusicXML 4.0 reference):
[`<miscellaneous>`](https://www.w3.org/2021/06/musicxml40/musicxml-reference/elements/miscellaneous/),
[`<score-part>`](https://www.w3.org/2021/06/musicxml40/musicxml-reference/elements/score-part/),
[`<part-link>`](https://www.w3.org/2021/06/musicxml40/musicxml-reference/elements/part-link/),
[compressed `.mxl` files](https://www.w3.org/2021/06/musicxml40/tutorial/compressed-mxl-files/).

**Prior art**:
[MuseScore `masterscore.h`](https://github.com/musescore/MuseScore/blob/master/src/engraving/dom/masterscore.h)
(MasterScore/Excerpt model),
[Dorico: players, layouts and flows](https://archive.steinberg.help/dorico/v5/en/dorico/topics/setup_mode/setup_mode_players_layouts_flows_c.html),
[Dorico: layouts](https://archive.steinberg.help/dorico/v5/en/dorico/topics/program_concepts/program_concepts_layouts_c.html).
