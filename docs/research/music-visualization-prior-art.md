# How music gets visualized: a survey of prior art

Research for ticket [#241](https://github.com/pellepang/note-color/issues/241), a child of
wayfinder map [#240](https://github.com/pellepang/note-color/issues/240) ("Color View — the
lens workbench"). This is seed material for the lens catalogue: the established ways of
drawing music, assessed for what each one *uniquely* shows, what it needs from the data, and
whether it survives a live monophonic mic feed.

Sources are cited inline. Where I could not verify something from a primary source, it says
so. Colour-*mapping* questions (which hue for which pitch class, and why) belong to
[#242](https://github.com/pellepang/note-color/issues/242) and are deliberately treated here
only where a system's geometry and its colouring are inseparable.

---

## 0. How to read this document

Sections 1–3 are the cross-cutting frame — three tests that turned out to separate the
useful prior art from the merely pretty, and that I think should become acceptance criteria
for a lens. Sections 4–15 are the families themselves, one per bucket the ticket named plus
four it did not. Section 16 is the summary table (the thing to skim if you only read one
part). Section 17 is what this most changes about the lens design; section 18 is what nobody
seems to have built.

---

## 1. The single most useful axis in the whole field: direct vs interpreted data

Mardirossian & Chew's ISMIR 2007 related-work section is the cleanest taxonomy I found, and
it is worth adopting wholesale because it predicts almost everything else about a
visualization's behaviour:

> "Direct data refers to data that is extracted directly from the music (such as pitch and
> onset time), while interpreted data refers to information that must be determined from
> extracted data (for example, tempo and key)."
> — [Mardirossian & Chew, *Visualizing Music: Tonal Progressions and Distributions*, ISMIR 2007](https://ismir2007.ismir.net/proceedings/ISMIR2007_p189_mardirossian.pdf)

They cross this with a second axis, **static vs dynamic** (does the picture unfold with the
music, or is it a finished artefact of the whole piece?).

This 2×2 is the right skeleton for the lens catalogue, because the two axes are exactly the
two things the map's Standing Decision 4 already cares about ("the signal has two faces"):

|                      | **Static (whole piece)**                     | **Dynamic (unfolds in time)**                    |
| -------------------- | -------------------------------------------- | ------------------------------------------------ |
| **Direct data**      | Spectrogram, piano-roll score, arc diagram   | MAM bar-graph, Melodyne blobs, VJ FFT visuals     |
| **Interpreted data** | Keyscape, self-similarity matrix, key-distribution discs | Tonnetz/Isochords, spiral array (MuSA.RT), Performance Worm |

The bottom-left cell is where nearly all the *music-theory* content lives — and it is also
the cell that is structurally hostile to a live mic, because "interpreted + static" means
"needs to have already heard the whole thing". That tension is the central design fact of
this survey (§17.1).

---

## 2. The invariance test — a real acceptance criterion, not a vibe

Mardirossian & Chew do something rare: they *validate* a music visualization by asking
whether it is invariant under the transformations that leave a piece's identity intact. They
take the list from Dorrell: pitch translation (transposition), octave translation, time
scaling (tempo), amplitude scaling (volume), and time translation.

Their findings, from the same paper:

- **Pitch translation**: the pattern stays intact and *shifts spatially* to the new key
  region. They then make the argument that matters to us: "a visualization method that uses
  only color and not spatial position to label a key would result in less similarity between
  the original and transposed pieces."
- **Octave translation**: identical output, because their space has no octave dimension.
- **Time scaling**: no effect, because they segment into a fixed *number* of slices rather
  than fixed-duration ones.
- **Amplitude scaling**: no effect, because the features are tonal.

**Why this matters for us.** The Color View is explicitly a workbench for seeing theory
(Standing Decision 2), and this gives a concrete, testable question to ask of every proposed
lens: *when I transpose the input up a fifth, does the picture change in the way the music
did?* A lens that encodes pitch class purely as hue in a fixed screen position fails the
transposition test — the picture recolours wholesale, and the *relationship* between before
and after is invisible. A lens with a spatial pitch-class geometry (wheel, lattice, helix)
passes: the picture translates. That is a strong argument for the catalogue containing at
least one *spatial* harmonic lens and not only colour-field lenses.

Note the flip side: the amplitude and octave invariances are *losses* as often as they are
virtues. A lens that is invariant to dynamics cannot show dynamics. The test is a
characterisation tool, not a scoring function.

---

## 3. The visual-variable design space, and the gaps in it

Miller, Häußler, Kraus, Keim & El-Assady (University of Konstanz) build a design space for
music notation by crossing Bertin's/Munzner's visual variables against musical meta-features
([*Analyzing Visual Mappings of Traditional and Alternative Music Notation*, arXiv:1810.10814](https://arxiv.org/pdf/1810.10814)).

Their four **musical meta-features**:

- **Rhythm** — tempo/beat, meter/time signature, pauses.
- **Harmony** — tones (pitch/frequency), note, range (octave), accidental vs normal, duration.
- **Dynamics** — intensity/volume, articulation, phrasing.
- **Instructions** — timbre/instrument, arrangement, baseline/clef, fingering.

Their **visual variables**, ordered by effectiveness for magnitude channels (best first):
position on common scale, position on unaligned scale, length (1D), tilt/angle, area (2D
size), depth (3D position), colour luminance, colour saturation, texture, curvature, volume
(3D size). Identity channels: spatial region, colour hue, motion, shape. Plus Gestalt laws
(proximity, similarity, enclosure, closure, continuity, connection) and a semantic/text
channel.

Two observations of theirs are directly load-bearing for us:

1. They classify fifteen notation techniques and **deliberately highlight the empty cells** —
   mappings no notation system has used — as the research opportunity. A lens catalogue is
   free to occupy those cells; a *notation* is not, because it has to be readable by
   performers.
2. They note that **motion and colour were historically unavailable** to Common Music
   Notation ("the lack of availability of printing color at the beginning of mass printing…
   the technological progress and digitization had not yet reached the point of using
   interactivity or motion"), and that this is why CMN's design space is narrow — not because
   the alternatives were tried and rejected.
3. Chords barely appear at all. They omitted a chord column because of "the scarcity of such
   techniques" — complete chords are rarely mapped to a visual variable directly; almost
   everything maps the constituent notes instead.

That last point is a real gap and a live opportunity: note-color already computes chord names,
qualities, bass chroma and slash-chord inversions (`CONTEXT.md`, chord-mode glossary), which
is more chord-level structure than most of this literature has available.

---

## 4. Colour-tone systems: Castel, Rimington, Scriabin, and their successors

### 4.1 What they actually were

- **Louis-Bertrand Castel** conceived the *clavecin oculaire* (ocular harpsichord) in 1725,
  explicitly derived from Newton's parallel between the seven spectral colours and the seven
  notes of the scale. The instrument had sixty small coloured glass panes, each with a curtain
  that opened when a key was struck, so that "the pressing of the keys would bring out the
  colours with their combinations and their chords" — the intent was that colour *chords* would
  correspond exactly to sounding chords
  ([Cabinet Magazine, *The Scale and the Spectrum*](https://www.cabinetmagazine.org/issues/22/peel.php);
  [Wikipedia, *Color organ*](https://en.wikipedia.org/wiki/Color_organ)).

- **Alexander Scriabin**'s *Prometheus: The Poem of Fire* (1910) carries a notated
  *Tastiera per luce* part. Gawboy & Townsend's analysis in *Music Theory Online*
  ([MTO 18.2](https://mtosmt.org/issues/mto.12.18.2/mto.12.18.2.gawboy_townsend.php)) establishes
  something most secondary accounts miss: the luce part has **two independent voices**, and
  they do completely different jobs.
  - The **fast voice** (stems up) changes with the harmonic rhythm and functions, in their
    words, as "a fundamental-bass analysis of [Scriabin's] own music" — it names which
    transposition of the mystic chord governs each passage.
  - The **slow voice** (stems down) makes only seven large-scale colour shifts across the
    entire work, delineating formal sections and programmatic stages — *not* harmonic rhythm.
  - The underlying geometry is the circle of fifths: "the circle of fifths relates tonal
    distance directly to spatial distance", and Scriabin positioned chords on it by
    orthography (one accidental apart = adjacent), not by pitch-class invariance.

  The twelve-hue palette had to be stretched to cover twelve keynotes with six or seven
  spectral hues, which is why it contains non-spectral entries like "steely-blue" and
  "metallic leaden grey"
  ([Wikipedia, *Synesthesia in Classical Composers*](https://en.wikipedia.org/wiki/Synesthesia_in_Classical_Composers)).

- **Successors** (Chromatone and friends) mostly reduce to "colour the noteheads of standard
  notation": Chromatone's own description is colouring regular staff notation with twelve
  markers, one per pitch
  ([chromatone.center](https://chromatone.center/theory/notes/color/)). Sapp lists
  "displaying music in the key colors of its synesthetic composer" as one of several arbitrary
  colour-mapping options, which is the right level of seriousness to give it (§8.2).

### 4.2 The design lesson

**Scriabin's two-voice split is the single most transferable idea in this family**, and it is
not a colour idea at all — it is a *timescale* idea. One colour stream tracks the local
harmony; a second, much slower colour stream tracks large-scale form. They are the same visual
encoding running at two different rates, and reading them together is what conveys structure.

That is directly buildable as a lens: a solid-colour field with a fast layer and a slow layer,
where the slow layer is a long-decay average. It is also the same idea as MAM's WHEEL decay
rings (§6.2) and as Sapp's keyscape vertical axis (§8.2) — three independent reinventions of
"show the same analysis at several timescales at once". That recurrence is the strongest
signal in this survey (§17.2).

**Live mic degradation**: excellent. A colour field driven by whatever pitch class is sounding
is the most graceful-degrading lens possible — it is what note-color's existing `fill` view
already does. The slow voice needs only an accumulator, not lookahead.

---

## 5. The piano-roll family: Music Animation Machine and its relatives

### 5.1 Malinowski's Music Animation Machine

Malinowski's own site is unusually good primary documentation, and the MAM Player user guide
is the best single artefact in this whole survey because it is a **shipped catalogue of
twelve interchangeable visualizations over one signal** — which is precisely what the Color
View is.

From [the MAM Player User Guide (PDF)](https://www.musanim.com/Player/MAMPlayerUserGuide.pdf),
the display types and their encodings:

1. **Piano Roll (MAM)** — the original. "Time is shown left-to-right (past is toward the left
   of center; future is toward the right), pitch is shown bottom-to-top." Customisable: note
   width, colour *by part* (per instrument/voice) or *by pitch class*, which pitch is tonic,
   light/dark inversion, note-start lines.
2. **Pitch Class (WHEEL)** — circle-of-fifths colouring, with **four concentric rings showing
   pitch history at four different decay rates**: "the innermost ring shows whether a pitch is
   sounding right now, the next ring shows whether a note has been sounding in the last second
   or two, and so on to the outermost ring which takes a long time to build up and decay."
3. **Intervals (DYAD)** — interval types (§10.1).
4. **Interval + Pitch (YARN)** — DYAD plus pitch and time; named because it looks like a
   scrolling ball of yarn.
5. **Shapes** — as YARN's pitch part, but each part gets its own *shape* rather than colour.
6. **Tonality Staff (V12)** — like DYAD "except that pitch is shown (left is low, right is
   high), and the twelve pitch classes are shown on staff lines (which move to keep the pitches
   centered)" (§10.2).
7. **Tonality Compass** — like DYAD, "except that the pitches are given (metaphorical) weight
   (depicted as size of the circles) based on dynamic level and leverage based on how low they
   are (distance of the circles from the axis); the pitch complex moves toward tonal
   equilibrium (the point at which its center of gravity is lowest)" (§10.2).
8. **Tonality Compass + Bars** — a *composite* lens: the left half of the MAM display beside
   the compass.
9. **Triads (LATTICE)** — a Tonnetz (§7.3).
10. **Part sequence (LINES)** — notes as circles sized by duration, connected sequentially per
    part by lines.
11. **Part motion (BALLS)** — as LINES, but each ball's core detaches when sounding and moves
    toward the next note in the part.
12. **Part trajectory (WEDGES)** — as LINES/BALLS with only a hint of where sounding notes are.

The fuller [Renderers list](http://www.musanim.com/Renderers/) runs to roughly a hundred
named renderers, including `Dissonance` ("shows collision of dissonant notes"),
`CriticalBand` (bar widths showing auditory critical-band phenomena), `Harmonics`,
`BarsFisheye` ("displays entire composition with 'fisheye' view of brief sections"),
`Overview` ("entire score visible at bottom of screen, with gray 'current view' highlight"),
`GenFill` ("fills background with color of closest note"), `History` ("score wraps and folds
into upper-screen space"), `Voronoi`, and `Canon`/`Round`/`Variations` for structural
comparison.

Three structural facts to steal:

- **The renderer is the unit of composition.** Malinowski calls these "software modules that
  draw notes in a given style". This is the lens contract, already validated by a hundred
  instances.
- **Cross-cutting options live outside the renderer.** "Now line", "Note start lines",
  "Invert light/dark", "Set as tonic", "Color by part / pitch class" are *global* View-menu
  settings that individual displays honour or ignore ("The Now line and Note Start lines are
  available in this display"). That is a clean precedent for the map's per-lens-vs-view-level
  chrome question ([#243](https://github.com/pellepang/note-color/issues/243)) — some settings
  are genuinely view-wide, and the tonic is one of them (it propagates from the MAM display
  into WHEEL, DYAD, V12 and COMPASS).
- **Composite displays are first-class** (display 8 is literally two displays side by side).

### 5.2 What MAM tells us about live input — the most important single data point in this survey

The MAM Player explicitly supports live MIDI ("generates displays in real-time from live MIDI
input"), and the guide states the consequence plainly:

> "Because the MAM Player can't guess what's in the future, only the past (left-hand) part of
> the piano roll display will be visible"

This is the degradation rule for the entire scrolling-score family, stated by the person who
built it. A scrolling piano roll fed live is **half a lens**: the design's core affordance —
seeing notes arrive before they sound — is unavailable, and what remains is a history strip.

Corollary for us: a scrolling lens should be authored knowing it has two modes, and the
"live" mode should probably move the now-line to the **right edge** rather than leaving half
the canvas permanently blank. Malinowski's own `NowStretch` renderer ("notes extend to touch
temporal 'now' point") and `History` ("score wraps and folds into upper-screen space") are
both ways of spending the reclaimed space.

### 5.3 DAW piano rolls and Melodyne

The DAW piano roll is the same object, standardised. Its orientation was *not* obvious at
first: the 1987 MIDI software Iconix represented pitch horizontally, while the 1986 Total
Music program assigned pitch vertically, and today's DAWs have converged on vertical pitch
([Wikipedia, *Piano roll*](https://en.wikipedia.org/wiki/Piano_roll)). The name descends from
player-piano rolls, in continuous production since at least 1896.

**Celemony Melodyne** is the interesting variant, because it draws *detected* notes from audio
rather than authored ones. Notes are drawn as "blobs" with an overlaid "pitch curve": the blob
is the note's average pitch, the curve is the actual pitch variation within it
([Celemony help centre, *Display and other options*](https://helpcenter.celemony.com/M5/doc/melodyneStudio5/en/M5tour_ViewOptions-ARA?env=dawsWithAra);
[Sound On Sound, *Celemony Melodyne DNA Editor*](https://www.soundonsound.com/reviews/celemony-melodyne-dna-editor)).
Under the polyphonic (DNA) algorithm, simultaneous notes stack vertically as a column of
independently editable blobs.

**Why this matters for us specifically**: note-color's signal comes from pitch *detection*, not
from a score. Melodyne's blob-plus-curve is the established way to draw a detected note
honestly — the quantised judgement and the continuous evidence behind it, in one mark. A lens
that draws only the quantised note throws away the vibrato, the slide, the bend, and the
detector's own wobble. Melodyne also separately shows "note separations" as grey vertical lines
— the segmentation decision made visible as its own layer, which is a good pattern for a
detection-driven system where the segmentation is often the thing that is wrong.

**Live mic degradation**: piano roll degrades to a history strip (§5.2). Melodyne's blob model
degrades *well*, because a blob-in-progress is a meaningful mark — it grows rightward while the
note sounds and its pitch curve extends. This is arguably the best "live monophonic" note
representation in the survey.

---

## 6. Wheels: the circle of fifths, the chromatic circle, and glyph variants

### 6.1 Why the circle of fifths and not the chromatic circle

Malinowski's argument ([musanim.com/mam/circle.html](http://www.musanim.com/mam/circle.html))
is about *chords*, not scales: on a circle-of-fifths wheel, "the root and the fifth are
adjacent on the wheel, and the third is more distant", because the third "adds the note to the
triad that is most distant harmonically from each of the others". A major scale's seven notes
are contiguous on the wheel (five clockwise of the tonic, one counterclockwise); a minor scale
reverses the distribution. So on a fifths wheel, **a key is an arc**, **a modulation is a
rotation of that arc**, and a cadence is a visible jump — his example is a "V-to-I cadence
(violet-to-blue)" in a Chopin nocturne bass line.

He grounds the colour direction in a perceptual claim: blue is the tonic, red lies in the
dominant direction, because "motion toward the dominant seems more 'active' compared with
motion toward the subdominant"
([musanim.com/HarmonicColoring](http://www.musanim.com/HarmonicColoring/)).

The **chromatic circle** (semitone order) is the projection of Shepard's pitch helix onto the
horizontal plane, where octave-related pitches lie on one vertical line
([Wikipedia, *Pitch space*](https://en.wikipedia.org/wiki/Pitch_space)). It shows melodic
adjacency and voice-leading steps well and tonal distance badly. The fifths circle shows tonal
distance well and melodic adjacency badly. **They are the same twelve points in two different
orders, and a lens that can rotate between them is nearly free to build** — this is one of the
cheapest genuinely instructive things in the whole catalogue, and HexaChord already ships both
as separate spaces (§7.4).

### 6.2 The decay-ring wheel

MAM's WHEEL display (§5.1, display 2) is the design to copy: four concentric rings of the same
twelve sectors, at four decay time constants from "right now" to "takes a long time to build up
and decay". It turns a memoryless instantaneous display into a display of *tonal context
accumulating* — which is the thing a key actually is, perceptually.

This is the cheapest possible way to give a live-mic lens the "whole piece" face of the signal
without needing the whole piece. It is a running average, nothing more.

### 6.3 The harmonic fingerprint glyph

Miller, Bonnici & El-Assady
([*Augmenting Music Sheets with Harmonic Fingerprints*, DocEng 2019, arXiv:1908.00003](https://arxiv.org/pdf/1908.00003))
reduce a bar of music to a single radial glyph: a **normalized histogram of pitch classes,
arranged radially in circle-of-fifths order**, with the root of the glyph's key/chord used as a
reference and colour double-encoding the pitch class alongside its angular position. The glyphs
are attached per-bar to the score so that recurring harmonic patterns can be spotted by shape
alone ("distant reading"). Their figure 1 shows an excerpt from Chopin's *Grande Valse
Brillante* where "a recurring pattern in the first four glyphs" is visible at a glance.

**The transferable idea**: a chroma vector *is* a glyph. note-color already computes a
12-element chroma vector every hop (`CONTEXT.md`), so a radial chroma glyph is close to free —
and if one glyph is cheap, a *row* of them is a whole-piece overview lens built out of the
moment-lens. That is a lens that works identically live (a strip of glyphs accumulating
rightward) and from a score (a glyph per bar under the notation). Very few techniques in this
survey have that property.

**Live mic degradation**: excellent for both. A monophonic feed produces single-spoke glyphs,
which is degenerate but not broken — and the *decay* version (a glyph over the last two
seconds) is fully meaningful even monophonically.

---

## 7. Lattices: the Tonnetz and its descendants

### 7.1 What the Tonnetz is and what it uniquely shows

The Tonnetz places pitches so that the three axis directions are perfect fifth, major third
and minor third; triads are therefore triangles, and the two triads sharing an edge differ by
one voice moving by a step. Tymoczko
([*The Generalized Tonnetz*, JMT](https://dmitri.mycpanel.princeton.edu/tonnetzes.pdf))
characterises its strength as visualizing **voice-leading efficiency and parsimony**: chords
sharing two pitches sit adjacent, so a progression's total voice movement is legible as
distance travelled. Under enharmonic equivalence and equal temperament the lattice is doubly
periodic and wraps onto a torus, with every vertex having six incident edges
([arXiv:1301.4255](https://arxiv.org/pdf/1301.4255)).

Neo-Riemannian **P, L and R** transformations are exactly "flip a triangle across one of its
three edges"
([Open Music Theory, *Neo-Riemannian Triadic Progressions*](https://viva.pressbooks.pub/openmusictheory/chapter/neo-riemannian-triadic-progressions/)).
So a chord progression becomes a *path*, and the path's shape is the analysis.

Tymoczko's caveats are equally useful: the Tonnetz "obscures" relationships beyond triads and
does not adequately represent octave equivalence or higher-dimensional pitch structure. It is a
triad instrument. Extended jazz harmony — exactly what note-color's 30-quality chord dictionary
contains — is not its natural material without generalisation.

### 7.2 Isochords

Bergstrom, Karahalios & Hart, *Isochords: Visualizing Structure in Music*, Graphics Interface
2007 ([ACM DL](https://dl.acm.org/doi/10.1145/1268517.1268565)). I could not get the full text
past a 403 from every mirror I tried, so the following is from the abstract, from
Semantic Scholar's figure captions, and from secondary descriptions in two papers that cite it
— **treat the details as second-hand**:

- The grid is "formed by unrolling the circle of fifths horizontally, staggered vertically to
  place the major third and sixth above and minor thirds and sixths below each tone."
- A chord is drawn as a triangle whose vertices are its three tones, "to emphasize their
  consonance."
- It "conveys information about interval quality, chord quality, and the chord progression
  synchronously during playback of digital music", and "can display complex voicings over
  several instruments."
- Evaluated with novice and experienced users; I did not obtain the findings.

The one design idea I would carry forward regardless of the missing detail: **chord quality
becomes shape**. Major vs minor is triangle orientation; a diminished or augmented chord fails
to close into the canonical triangle at all. That is a genuinely different encoding from
"chord name as text", and it is one of the very few answers in the literature to the
chord-column gap Miller et al. identified (§3).

### 7.3 MAM's LATTICE display, and the enharmonic drift finding

MAM's Triads (LATTICE) display: "pitches separated by an interval of a perfect fifth or
perfect fourth are adjacent on a given horizontal line. Pitches separated by a major or minor
third are adjacent on a diagonal line. When adjacent pitches are sounding, their indicators
light up in the corresponding pitch class color, and connecting lines are drawn"
([MAM Player guide](https://www.musanim.com/Player/MAMPlayerUserGuide.pdf)). A major seventh
chord shows as two perfect fifths, two major thirds and one minor third.

Malinowski's own analytical payoff from it is worth quoting as a design goal
([musanim.com/mam/hist32.html](http://www.musanim.com/mam/hist32.html)): some progressions
"came back the same way they left", while others "went into another universe" — ending at an
enharmonically equivalent but *conceptually different* pitch, e.g. C arriving at D-double-flat.

That is a music-theory fact that is **invisible in every other lens in this survey**. On an
unwrapped (non-toroidal) lattice, a comma pump is a visible drift across the plane. Note that
this requires the lattice *not* to wrap — the torus identification destroys exactly this
information. A design choice, not a detail.

### 7.4 HexaChord and the simplicial-complex generalisation

Louis Bigo's **HexaChord** ([louisbigo.com/hexachord](https://louisbigo.com/hexachord);
[Bigo & Andreatta, *Topological Structures in Computer-Aided Music Analysis*](http://repmus.ircam.fr/_media/moreno/BigoAndreatta_Computational_Musicology.pdf))
generalises the Tonnetz to simplicial complexes over chord classes, displayed as "infinite 2D
triangular tessellations". Two facts from its description matter here:

- It also ships **the chromatic circle, the fifths circle, and a voice-leading intervallic
  content space** as alternative representation spaces — the same *design instinct as the Color
  View*: one signal, several interchangeable geometries, side by side.
- It "manipulate[s] MIDI streams (coming from a MIDI file or in live from any MIDI device) in
  different pitch-class spaces" — i.e. the lattice family does run live.

**PaperTonnetz** (Garcia, Bigo, Spicher & Mackay, CHI EA 2013) is the same lattice used as a
*composition* surface on interactive paper rather than an analysis display — worth noting only
as evidence that a lattice can be an input device as well as an output one, which is relevant
if lens-to-lens linking ever becomes lens-to-synth linking.

### 7.5 Harmonic table / isomorphic layouts — the same lattice as a keyboard

The Harmonic Table note layout is the Tonnetz turned into keys: notes ascend by a perfect
fifth along the vertical axis, by four semitones (major third) on one diagonal and three
semitones (minor third) on the other
([Wikipedia, *Harmonic table note layout*](https://en.wikipedia.org/wiki/Harmonic_table_note_layout)).
The consequence: "A standard major chord in root position is formed by pressing down any three
adjacent keys in a right-facing triangle shape, and a minor chord is formed by making that a
left-facing triangle", and chord and scale shapes are identical in every key — unlike a piano
([C-Thru Music, *Some Harmonic Table chord shapes*](https://www.c-thru-music.com/cgi/?page=layout_cshapes)).
The C-Thru AXiS-49 is 14 columns of 7 notes in this layout
([AXiS-49 technical specification](https://www.c-thru-music.com/cgi/?page=spec-49)).
Wicki–Hayden is the same idea with a different interval pair
([Wikipedia, *Wicki–Hayden note layout*](https://en.wikipedia.org/wiki/Wicki%E2%80%93Hayden_note_layout)).

**For the lens catalogue this reframes the "piano" lens.** A piano-keyboard lens shows *pitch
height and which physical key*, and its shapes are key-dependent — the same chord looks
different in every key, which is a pedagogical liability for a theory workbench. A
harmonic-table lens shows *the same chord as the same shape everywhere*. Both are legitimate
lenses; they show opposite things, and the catalogue is better for having both and saying so.

**Live mic degradation**: lattices degrade poorly-but-honestly. A single monophonic note lights
one vertex and draws no edges — which correctly reports "there are no intervals here". Pairing
a lattice lens with a decay window (light recently-sounded vertices dimly, so an arpeggio
accumulates into its triangle) converts a monophonic feed into something meaningful, and is the
same trick as §6.2.

---

## 8. Key-space maps: where modulation lives

### 8.1 Lerdahl's tonal pitch space with growing discs

Mardirossian & Chew's system
([ISMIR 2007](https://ismir2007.ismir.net/proceedings/ISMIR2007_p189_mardirossian.pdf)):

- Segment the piece into *m* uniform slices (user-controlled, 5–60, via a slider — granularity
  and stability trade off against each other).
- Determine each slice's key with the **Spiral Array Center of Effect Generator** algorithm,
  which they note "can be used for both MIDI and audio input".
- Plot onto **Lerdahl's 2D tonal pitch space**: the circle of fifths runs along the horizontal
  axis, while relative and parallel major/minor relationships alternate along the vertical
  axis. The space tiles infinitely, so keys repeat across the plane.
- Each slice grows the translucent coloured disc over its key by one unit. At the end you have
  a **key distribution**, animated into existence.

Their genre finding is a nice demonstration of what the lens is *for*: western classical pieces
show "a center of interest" and return to the opening key, while the Armenian folk songs they
analysed "typically [do] not end in the key in which [they] began" and instead walk to a
neighbouring key and stay.

The design argument underneath is Tufte's: they went from a 1D histogram to a 2D plane plus
disc size plus animation specifically to raise "information resolution", and they justify
colour's four roles (label, measure, imitate, decorate) explicitly.

### 8.2 Sapp's keyscape — the best multi-timescale design in the field

Craig Sapp, *Harmonic Visualizations of Tonal Music*, ICMC 2001
([CCRMA PDF](https://ccrma.stanford.edu/~craig/papers/01/icmc01-tonal.pdf)); see also
[the Mazurka Project keyscape pages](https://mazurka.org.uk/info/keyscape/).

The motivating problem is stated very precisely and applies to note-color's own chord/key
estimation: run a key-finding algorithm over a whole piece and you get *one* answer, and it is
often wrong in one of two characteristic ways — a **fifth-relation error** (it reports the
dominant, because the secondary key areas sit to one side on the circle of fifths) or a
**modality error** (right key signature, wrong tonic). Sapp demonstrates both on the
Well-Tempered Clavier using the Krumhansl-Schmuckler algorithm: fugue no. 3 comes out G♯ major
instead of C♯ major, prelude no. 11 comes out D minor instead of F major.

His fix is to stop choosing a window size:

> "Key visualization techniques described in the following section avoid the problem of
> choosing a fixed analysis window duration by instead using all possible analysis window
> durations."

The plot: **horizontal axis = time in the score; vertical axis = the duration of the analysis
window**, coloured by the resulting key's tonic. Two variants:

- **Type 1** (discrete): top level analyses the whole piece; each level down splits into more,
  smaller equal windows. Bottom level typically one window per beat, so the bottom row is
  effectively *chord roots* and the top row is the key of the piece. He applies a logarithmic
  vertical scaling (formula given in the paper) because a linear one lets the lower levels
  visually overpower the upper ones.
- **Type 2** (continuous): a sliding window at every duration, with the result plotted as a
  single pixel at the window's *centre* — which produces the characteristic triangular shape.
  Higher resolution; "computation time for type 2 plots is about 30 to 50 times greater."

And the reading rule, which is the payoff:

> "the height to which a key region survives in a diagram demonstrates the relative strength of
> that key region. Strong modulations are represented by large vertical structures, while
> tonicizations are represented by smaller vertical structures."

**This is the only technique in the entire survey that distinguishes a modulation from a
tonicization visually**, and it does it without a music-theory label — purely from the geometry
of which key regions survive being looked at from further away. It also makes algorithm
weakness legible: Sapp reads the striped A-major bands in his Mozart example as the key-finder
compromising between overlapping D minor and F major regions. A display that shows you where
your own detector is unsure is very valuable in a project whose signal is detected rather than
authored.

His key-to-colour mapping is the rainbow on the circle of fifths collapsed to seven diatonic
pitches (C green, G blue, D indigo, A violet; F yellow, B♭ orange, E♭ red), with RGB values
tabulated. He is candid about the flaw — seven colours for twelve-plus pitch classes means E
and E♭ share red — and lists alternatives (major/minor by brightness, sharp/flat by brightness,
maximise hue distance, monochromatic for print and colour-blind viewers, relative-to-tonic
rather than absolute). The Mazurka Project pages ship **absolute and relative colourings as a
pair** for every piece — absolute meaning "light blue is G major", relative meaning "light blue
is the dominant of this piece's key". That pairing is a strong precedent for ticket
[#247](https://github.com/pellepang/note-color/issues/247)'s A/B-comparable colour schemes: the
*same* geometry under two mappings, side by side, is itself an analytical tool.

**Live mic degradation**: this is the hard case. A keyscape's whole content is the vertical
axis, and the top of that axis is *the entire piece* — it is definitionally non-causal. What
can run live is the **bottom half**: a growing triangle whose apex creeps upward as more
material accumulates. That is honest (the long-window analyses genuinely do not exist yet) and
it visibly *earns* its structure as you play, which may be more instructive than the finished
plot. Worth prototyping in [#244](https://github.com/pellepang/note-color/issues/244).

### 8.3 The SOM torus of keys

Toiviainen & Krumhansl, *Measuring and Modeling Real-Time Responses to Music: The Dynamics of
Tonality Induction*, Perception 32 (2003), [doi:10.1068/p3312](https://doi.org/10.1068/p3312).
A self-organizing map trained on the probe-tone profiles for the 24 major and minor keys
yields a toroidal key space; projecting listeners' concurrent probe-tone judgements onto it
shows "changes both in the perceived keys and in their strengths" over the course of a piece
(their stimulus was Bach's Duetto BWV 805).

Two things are notable. First, this is a *perceptual* key space, calibrated against human
listeners, not a theoretical one — the only such space in this survey. Second, it is inherently
**multi-key at once with strengths**, not a single winner. A lens that shows a blurry heat
distribution over a key space, rather than one lit key, is both more honest about detector
uncertainty and closer to how key perception actually behaves. That is the same argument as
§8.2's "show where the detector is unsure", from a different direction.

### 8.4 MuSA.RT — the real-time existence proof

Chew & François's **MuSA.RT** ("Music on the Spiral Array . Real-Time") is the system that
proves this family can run live. Chew's spiral array is a set of nested helices: an outer helix
of pitch classes (successive pitches a perfect fifth apart, so fifth-related pitches are
spatially close), with inner helices of major/minor triads and then keys generated from the
level below. Any weighted set of points sums to a **centre of effect** representing the
aggregate tonal effect
([Chew, Spiral Array Model](https://eniale.kcl.ac.uk/spiral-array-model/)).

MuSA.RT "analyzes the audio signal received from a microphone to determine pitch names,
maintain short term and longterm tonal context trackers, each a Center of Effect (CE), and
compute the closest triads (3-note chords) and keys as the music unfolds in performance"
([François, MuSA_RT](https://alexandrefrancois.org/MuSA_RT/)). It also "portrays musical memory
as a trajectory that touches on the recently visited tonal regions"
(Mardirossian & Chew's description of it).

**Two centres of effect at two timescales, from a live mic, with a memory trail.** That is
Scriabin's two-voice idea (§4.2), MAM's four decay rings (§6.2) and Sapp's vertical axis (§8.2)
arriving for the fourth time — and this time with a working real-time implementation from
microphone input on the record. It is the single closest prior system to what note-color is.

---

## 9. Signal displays: spectrogram, log-frequency spectrogram, chromagram

Müller's FMP notebooks are the primary reference
([*Log-Frequency Spectrogram and Chromagram*, AudioLabs Erlangen](https://www.audiolabs-erlangen.de/resources/MIR/FMP/C3/C3S1_SpecLogFreq-Chromagram.html)):
a log-frequency spectrogram remaps the STFT's linear Hz axis onto pitch via
`F_pitch(p) = 2^((p-69)/12) · 440`; a chromagram then sums across octaves into twelve bins,
**discarding octave entirely** — C3, C4 and C5 land in the same bin.

Sonic Visualiser is the reference implementation of "many aligned views over one signal", and
its architecture is worth noting for the Color View's layout model: **panes and layers**, where
a pane holds several layers stacked and all panes are aligned on the time axis
([Sonic Visualiser, *A Brief Reference*](https://www.sonicvisualiser.org/doc/reference/1.3/en/)).
It offers three spectrogram flavours — generic (full range, linear frequency), melodic-range,
and peak-frequency (phase-difference estimation of exact peak frequencies) — plus a chromagram
via a Vamp plugin.

**What these show that nothing else does**: everything that is not a note. Timbre, noise, the
attack transient, inharmonicity, the actual harmonic series of the sound, and the *evidence*
the pitch detector is working from. In a workbench whose entire upstream is detection, having
one lens that shows the raw evidence beside the lenses that show the interpretation is a
debugging instrument and an honesty instrument at once.

**Live mic degradation**: perfect — it is a native real-time display, and the only one in the
survey that is *better* on a live mic than on a score (a score has no spectrum). It is also the
one lens that works on unpitched material, which the map lists under "Beyond pitch" as not yet
specified.

---

## 10. Interval and consonance displays

### 10.1 DYAD

MAM's Intervals display ([musanim.com/mam/dyad.htm](http://www.musanim.com/mam/dyad.htm)):
pitches sit on the circle-of-fifths wheel; when two notes sound together, **a line is drawn
between their positions, coloured by interval type**:

| Interval | Colour |
| --- | --- |
| Fifths and fourths | blue |
| Thirds and sixths | green |
| Major seconds, minor sevenths | violet |
| Minor seconds, major sevenths | yellow |
| Tritone | red |

Malinowski's claim: "when you play a chord into DYAD, you see all the intervals that are
present. This turns out to correspond well to the effect of the chord, in terms of consonance
and dissonance" — and consonant blues and greens visibly mitigate harsh yellows and reds.

This is a **different decomposition of a chord than any other lens here**: not a name, not a
shape, not a set of pitch classes, but the *multiset of intervals* it contains. For a chord of
n notes it draws n(n−1)/2 lines, which stays legible to about six notes. It also captures
something the chord dictionary cannot: two different chord names with the same interval content
look identical, correctly.

### 10.2 The tonality staff and tonality compass

Both from [musanim.com/mam/hist30.html](http://www.musanim.com/mam/hist30.html), and both
solve the same problem in different ways:

**V12 / Tonality Staff** put pitch on the x-axis and circle-of-fifths position on the y-axis.
The flaw and the fix are instructive: "when the music moved around the circle of fifths, it
would move to one edge (of the now broken circle) then jump to the other edge" — an unrolled
circle has a seam. The fix was to let the staff lines **shift vertically to keep the notes
centred**, so that harmonic motion became continuous vertical motion. The frame moves, not the
music.

**Tonality Compass** gives each sounding pitch metaphorical *weight* (circle size from dynamic
level) and *leverage* (distance from the axis, from how low the note is), and the whole complex
rotates toward the orientation where its centre of gravity is lowest — "tonal equilibrium".
Modulation is visible as the complex physically reorienting.

**Two transferable ideas.** (1) **The seam problem is universal** to any unrolled cyclic space,
and "scroll the frame to follow the music" is a better fix than "wrap and let it jump". (2)
**A physical metaphor — weight, leverage, equilibrium — is a legitimate encoding for tonal
gravity**, and it is one of the very few that is genuinely *pre-attentive*: you see the thing
tilt before you read anything. This is also, in essence, a physicalised version of Chew's
centre of effect (§8.4).

---

## 11. Structure overviews: arcs, self-similarity, and similarity colouring

- **Arc diagrams.** Wattenberg, *Arc Diagrams: Visualizing Structure in Strings*, IEEE InfoVis
  2002; the musical application is *The Shape of Song*. Repeated passages are connected by
  translucent arcs; translucency is what lets a dense repetitive substructure stay readable
  without destroying the macro reading. Matching in *Shape of Song* was on pitch strings, and
  "where chords occur only the top note is considered"
  ([ISMIR 2021, *Visualizing Intertextual Form with Arc Diagrams*](https://archives.ismir.net/ismir2021/paper/000008.pdf),
  which is also the best modern extension of the technique).
- **Self-similarity matrices.** Foote, *Visualizing music and audio using self-similarity*, ACM
  Multimedia 1999 ([ACM DL](https://dl.acm.org/doi/10.1145/319463.319472)). Acoustic similarity
  between every pair of instants on a 2D grid; repeating and contrasting material become visible
  as off-diagonal stripes and diagonal blocks, revealing "structural and rhythmic
  characteristics."
- **Similarity-coloured notation.** Heyen, Ngo & Sedlmair, *Visual Overviews for Sheet Music
  Structure*, ISMIR 2023 ([arXiv:2308.06140](https://arxiv.org/pdf/2308.06140)): compute
  similarity between bars or sections, reduce dimensionality or cluster, and **map the result to
  colour** so similar segments get similar colours — combined with compressed/hierarchical
  notation encodings so a whole piece fits one screen without clutter. Their guitarist
  evaluation found it supported "analyzing structure, finding repetitions, and determining the
  similarity of specific segments to others."

**Live mic degradation**: all three are strictly non-causal at the whole-piece level, but all
three degrade the same graceful way — *as a growing triangle or growing strip*. An SSM can be
computed against everything heard so far; an arc can be drawn the moment its second endpoint
arrives. What you lose live is not correctness, only the final shape.

**Why this family belongs in the catalogue anyway**: it is the only family that shows *form* —
verse/chorus, exposition/development, theme and variation. Nothing in the harmony families
shows form, and form is a music-theory concept the map's Destination implicitly cares about.

---

## 12. Expression and rhythm: the Performance Worm

Dixon, Goebl & Widmer, *The Performance Worm: Real Time Visualisation of Expression based on
Langner's Tempo-Loudness Animation*, ICMC 2002; see also Langner & Goebl, *Visualizing
expressive performance in tempo-loudness space*, Computer Music Journal 27(4), 2003.

A dot moves through a 2D space of **tempo (x) against loudness (y)**, leaving a fading
trajectory behind it — the "performance path". Per the published descriptions, it "takes its
input from an audio file or directly from the sound card and works in real time", works
interactively, and lets the user switch between tempo hypotheses live
([Goebl's animations page, mdw Vienna](https://iwk.mdw.ac.at/goebl/animations.html)).

**Three reasons this matters here.** (1) It is the only technique in the survey whose subject
is *performance* rather than composition — the same notes played twice give different worms.
(2) note-color already has a `tempo_tracker` and a `duration_tracker` running every hop
(`CLAUDE.md`), so the input exists. (3) The "switch between tempo hypotheses" affordance is
exactly the "show me where the detector is unsure" pattern that keeps recurring (§8.2, §8.3).

**Live mic degradation**: native. Designed for it.

---

## 13. Lead sheet / realbook as a *visual* format

A lead sheet carries melody, chord symbols and lyrics on one page
([Wikipedia, *Lead sheet*](https://en.wikipedia.org/wiki/Lead_sheet)). Chord symbols specify
root, quality and inversion; the slash form (C7/E, A7/C♯) names an alternate bass. Everything
else — rhythm, articulation, octave, voicing, doubling, instrumentation — is deliberately left
to the performer ([Berklee, *Why Lead Sheets?*](https://www.berklee.edu/berklee-today/summer-2018/lead-sheet);
[University of Vermont, *Introduction to lead-sheet chord symbols*](https://www.uvm.edu/~dfeurzei/110/materials/lead_symbols.pdf)).
The Real Book itself was an underground student publication out of Berklee in 1974–75 that
became the standard reference ([Wikipedia, *Real Book*](https://en.wikipedia.org/wiki/Real_Book)).

**Read as a visualization rather than a reading format, the lead sheet is the field's one
working example of principled omission.** Every other technique here adds information; the lead
sheet's entire design is a claim about what can be *left out* and still leave the music
identifiable. Its answer — keep melody contour and harmonic function, discard voicing, register
and rhythm-of-accompaniment — is a defensible filtering rule in its own right.

That maps directly onto ticket [#250](https://github.com/pellepang/note-color/issues/250)'s note
filter. "Lead-sheet filter" is a meaningful preset: highest voice plus chord context, everything
else dropped. And note-color is unusually well placed to *render* one live, since it already
produces chord names with the flat-biased spelling convention and slash-chord bass resolution
(`CONTEXT.md`).

**Live mic degradation**: good, and notably the *chord* half degrades better than the melody
half on a monophonic feed — chroma-based chord estimation is already what note-color's chord
mode does, and a chord grid scrolling past is meaningful even when the melody line is a single
wandering voice.

---

## 14. DAW tooling

Beyond the piano roll (§5.3):

- **Chord tracks.** Cubase has had a Chord Track, Chord Pads and a Scale Assistant for years;
  Logic Pro added a Chord Track in Logic 11, and later versions add **Chord ID**, which analyses
  audio, identifies harmonic content and writes the detected progression into the Chord Track so
  other features can read the song's harmony
  ([CDM, *Logic Pro 12's new harmonic intelligence and Chord ID*](https://cdm.link/logic-pro-12-hands-on/)).
- The design pattern worth stealing is not the UI, it is the **architecture**: harmony is
  promoted to a *first-class timeline object that other parts of the system read*, rather than
  something a single display computes for itself. That is close to the map's Standing Decision 3
  ("one signal, designed up front") and argues that the chord context should live *in the note
  signal*, not be re-derived per lens.
- **Melodyne** (§5.3) for detected-note rendering.
- **Sonic Visualiser** (§9) for the pane/layer layout model.

## 15. VJ tooling

Resolume's documented model
([Resolume, *Parameter Animation*](https://resolume.com/support/en/parameter-animation)): full
**18-band FFT (9 left, 9 right)** audio analysis; select an FFT source in a parameter's control
drop-down and that parameter is driven by the audio of the clip, layer, group or composition it
belongs to. Frequencies are pre-grouped into **Low / Mid / High** buttons with adjustable in/out
points on the spectrum display for finer control.

Two honest conclusions about this family:

1. **The encoding is almost always energy-in-a-band → a continuous parameter.** It is
   deliberately musically shallow: no pitch, no key, no harmony. It reacts to *loudness in a
   frequency region*. That is why VJ visuals work on any genre and show nothing about any of
   them. Under the map's Standing Decision 2 ("workbench first, performance surface second"),
   this family is explicitly the thing the Color View is not.
2. **The parameter-routing model is worth stealing even so.** "Any analysis output can be bound
   to any parameter of any visual, with a per-binding range" is a good generalisation of
   Standing Decision 7's live-editable mapping, and it is a strong hint about what the parked
   "cable patching" question (map: Not yet specified) would look like if it ever unparks — it
   is the modulation matrix the Synth View already has, pointed at lens parameters.

---

## 16. Summary table

Ratings are mine. **Live-mono** = how well the technique survives a live monophonic mic feed:
*native* (designed for it), *good* (works, loses little), *partial* (works but loses its main
affordance), *needs accumulation* (only works with a decay/history window), *non-causal* (needs
the whole piece; degrades to a growing version of itself).

| Technique | Uniquely shows | Needs | Theory concept made visible | Live-mono |
| --- | --- | --- | --- | --- |
| Colour field (Castel → `fill`) | the present instant, nothing else | a moment, 1 pitch class | pitch class identity | native |
| Scriabin two-voice luce | fast harmony *and* slow form at once | a moment + long accumulator | harmonic rhythm vs formal section | native |
| MAM piano roll | pitch × time, voices, contour | a moment + future (or past) | melodic contour, voice independence | partial (history only) |
| Melodyne blob + pitch curve | the note *and* the evidence under it | a moment + intra-note pitch track | intonation, vibrato, segmentation | good |
| Fifths wheel | key as a contiguous arc; modulation as rotation | a moment (better with decay) | key, tonal distance, cadence direction | needs accumulation |
| Chromatic circle | melodic step adjacency, voice leading by semitone | a moment | interval size, chromatic motion | good |
| WHEEL decay rings | tonal context accumulating at 4 timescales | a moment + 4 accumulators | key emerging from note distribution | native |
| Harmonic fingerprint glyph | a bar's whole pitch-class distribution as one shape | a bar (or a decay window) | harmonic content, recurrence | good |
| Tonnetz / LATTICE | voice-leading parsimony; **enharmonic drift** | simultaneous notes (≥2) | parsimony, P/L/R, comma pumps | needs accumulation |
| Isochords | chord *quality as shape* | simultaneous notes (≥3) | chord quality, consonance | needs accumulation |
| Harmonic-table layout | chord shape invariant across keys | simultaneous notes | transposition invariance | needs accumulation |
| Spiral array / MuSA.RT | tonal centre as a moving point + memory trail | a moment + 2 trackers | key, tonal gravity, modulation as travel | **native** |
| Lerdahl TPS discs | cumulative key *distribution* | many slices (grows) | key palette of a piece; genre signature | non-causal (grows) |
| Sapp keyscape | **modulation vs tonicization**; detector uncertainty | whole piece, all window sizes | hierarchy of key regions | non-causal (grows) |
| SOM key torus | several keys at once, with strengths | a moment + perceptual model | tonality induction; uncertainty | good |
| Spectrogram / chromagram | everything that is not a note | a moment | harmonic series, timbre, noise | **native** |
| DYAD interval lines | a chord's interval multiset, consonance balance | simultaneous notes (≥2) | interval quality, dissonance | needs accumulation |
| Tonality compass | tonal gravity as physical tilt | a moment + dynamics | tonal equilibrium, modulation | good |
| Arc diagram | repetition structure | whole piece (grows) | form, repetition | non-causal (grows) |
| Self-similarity matrix | every-instant-to-every-instant similarity | whole piece (grows) | form, rhythm periodicity | non-causal (grows) |
| Performance Worm | expression: tempo × loudness path | a moment + beat tracking | rubato, dynamic shaping | **native** |
| Lead sheet / realbook | what can be *omitted* and still be the tune | melody + chord context | harmonic function, chord quality | good |
| VJ FFT binding | band energy → any parameter | a moment | (none) | native |

---

## 17. What this most changes about the lens design

### 17.1 "Live vs whole-piece" is not a binary — it is a growing triangle

The map's Standing Decision 4 already says the signal has two faces. The survey sharpens what
the second face costs. The non-causal techniques (keyscape, arc diagram, SSM, key-distribution
discs) do not *fail* live — they render as a partial, growing version of themselves, with the
long-window analyses simply absent until enough material exists. Sapp's vertical axis is
literally "how much music does this analysis get to look at", so a live keyscape whose apex
creeps upward is not a degraded keyscape, it is an honest one.

**Design implication**: the lens contract should let a lens declare *how much history it wants*
(a number of seconds, or "everything"), and the accumulated-history interface the map already
promises should be the same call whether the source is live or a loaded score. No lens should
need to know which it is watching — but every lens should be able to render correctly when the
answer to "how much do I have" is "three seconds so far".

### 17.2 Multi-timescale is the field's most-reinvented idea, and it should be a first-class lens property

Four independent systems, none citing each other, converged on showing one analysis at several
timescales simultaneously:

- Scriabin's fast and slow luce voices (1910)
- MAM's WHEEL with four concentric decay rings (1990s)
- Sapp's keyscape vertical axis = all window durations at once (2001)
- MuSA.RT's short-term and long-term centres of effect (2000s)

This is not a coincidence. A single instant has no key, no tonality and no form; those concepts
only exist over a window, and *which* window you pick changes the answer. Every prior system
that took theory seriously ended up refusing to pick.

**Design implication**: "which timescale" should be a standard, view-wide control (like MAM's
tonic setting, which propagates across displays), not something each lens reinvents — and
several lenses should be able to render at two or four timescales at once.

### 17.3 Spatial pitch-class geometry earns its keep; colour alone does not

Mardirossian & Chew's transposition argument (§2) is the sharpest thing in the survey for a
project called *note-color*: a lens that encodes pitch class only as hue cannot show that a
transposed passage is the *same* passage. The relationship is destroyed. A lens with a spatial
pitch-class layout — wheel, lattice, helix, key plane — shows transposition as translation or
rotation, which is what it is.

**Design implication**: the catalogue must not be all colour-field lenses. At least one wheel,
one lattice and one key-space lens are load-bearing, and the colour mapping is best understood
as a *second* channel layered on a spatial one (as the harmonic fingerprint explicitly does:
"visual double encoding of the pitch classes in color and position").

### 17.4 Showing the detector's uncertainty is a feature, not a leak

Three independent systems make analysis uncertainty visible: Sapp reads striped key bands as
the algorithm compromising between two overlapping regions; Toiviainen & Krumhansl's SOM shows
several keys with strengths rather than one winner; the Performance Worm lets you switch between
tempo hypotheses live.

note-color's signal is *detected*, not authored — from a mic, from YIN and chroma matching, with
a "no match" state that is already modelled (`CONTEXT.md`: chord name "left present but empty on
a 'no match'"). Lenses that render confidence as blur, opacity or a distribution rather than as
a single confident mark will be more truthful and more instructive than lenses that pick a
winner.

**Design implication**: the note signal should carry confidence, and at least one lens in the
catalogue should render it. Worth raising in
[#246](https://github.com/pellepang/note-color/issues/246).

### 17.5 Chord-as-shape is an open field with almost no prior art

Miller et al. surveyed fifteen notation systems and found so few techniques mapping a *whole
chord* to a visual variable that they cut the column. The exceptions are Isochords (chord
quality as triangle orientation on a Tonnetz), the harmonic-table layouts (chord quality as a
hand shape), and DYAD (chord as its interval multiset).

note-color has a 30-quality chord dictionary, bass chroma, inversion detection and slash-chord
naming already. Of every family in this survey, **chord-shape lenses are where the project's
existing capability most exceeds the prior art**. That is the most promising direction for the
"invented lenses" the map asks for, and it should get disproportionate attention in
[#244](https://github.com/pellepang/note-color/issues/244)'s sketch sheets.

### 17.6 MAM Player is the closest working precedent for the whole view, and its chrome model should be studied directly

One signal, twelve interchangeable displays, cross-cutting settings that propagate between them
(tonic, colour-by, now-line, note-start-lines), composite displays that combine two, and live
MIDI input. That is the Color View, shipped in the 2000s. Three of its decisions are worth
adopting or at least deliberately rejecting in
[#243](https://github.com/pellepang/note-color/issues/243) and
[#248](https://github.com/pellepang/note-color/issues/248):

1. **Tonic is view-wide, not per-lens.** Set it once on the piano roll; the wheel, the intervals
   display, the tonality staff and the compass all honour it.
2. **Some options are declared *unavailable* per display** ("There are no custom controls for
   this display"), rather than shown-and-ignored. A lens declaring which global settings it
   honours is a cleaner contract than pretending all of them apply.
3. **A composite is just another entry in the list**, not a special layout mode.

### 17.7 Two cheap wins worth building early

- **A rotatable wheel** — the same twelve points in circle-of-fifths or chromatic order, with a
  toggle. Nearly free, and it makes the difference between "harmonic distance" and "melodic
  distance" visible as a single animation. HexaChord ships both; no one seems to ship the
  *morph between them*.
- **A radial chroma glyph** — note-color already produces the 12-element chroma vector every
  hop. One glyph is a moment lens; a strip of glyphs is a whole-piece overview lens; the same
  code serves both faces of the signal (§6.3).

---

## 18. Gaps — things the prior art does not do

Recorded here because the map asks for invented lenses, and these are where the invention has
room:

1. **Nobody morphs between pitch-class geometries.** Several systems offer the chromatic circle
   *and* the fifths circle *and* a lattice as separate views (HexaChord, MAM). None animates the
   transition, which would make the relationship between the orderings legible rather than
   asserted.
2. **Chord-level visual encoding is nearly vacant** (§17.5), and extended/jazz harmony
   specifically so — the Tonnetz is a triad instrument by Tymoczko's own account.
3. **Detector uncertainty is rarely rendered** and never, as far as I found, rendered
   *deliberately as the subject* of a display.
4. **Unpitched material has no established visualization** in this literature outside the
   spectrogram. The map lists this under "Beyond pitch"; the prior art does not help.
5. **Lens-to-lens linking (brushing) is absent.** Sonic Visualiser aligns panes on the time
   axis, and that is as far as it goes — no system I found lets a selection in one harmonic view
   scope another. The map parks this under "Not yet specified"; it would be genuinely novel.
6. **Almost nothing in the academic literature is real-time from a microphone.** MuSA.RT and the
   Performance Worm are the exceptions. Most of the harmony literature assumes symbolic input
   (MIDI or a score), and several papers say so explicitly. Real-time-from-audio is where this
   project starts, which means a surprising amount of the catalogue will be a first
   implementation rather than a port.

---

## 19. Sources

**Primary — systems and their own documentation**

- Stephen Malinowski, [MAM Player User Guide (PDF)](https://www.musanim.com/Player/MAMPlayerUserGuide.pdf) — display types, options, live-MIDI limitation.
- Stephen Malinowski, [Renderers list](http://www.musanim.com/Renderers/), [Background](http://www.musanim.com/Background/), [History](http://www.musanim.com/mam/mamhist.htm), [Harmonic Coloring](http://www.musanim.com/HarmonicColoring/), [circle.html](http://www.musanim.com/mam/circle.html), [dyad.htm](http://www.musanim.com/mam/dyad.htm), [hist30.html](http://www.musanim.com/mam/hist30.html) (V12/compass), [hist32.html](http://www.musanim.com/mam/hist32.html) (LATTICE).
- Elaine Chew, [Spiral Array Model](https://eniale.kcl.ac.uk/spiral-array-model/); Alexandre François, [MuSA_RT](https://alexandrefrancois.org/MuSA_RT/).
- Louis Bigo, [HexaChord](https://louisbigo.com/hexachord); [GitLab repo](https://gitlab.com/lbigo/hexachord).
- [Sonic Visualiser: A Brief Reference](https://www.sonicvisualiser.org/doc/reference/1.3/en/); [features](https://sonicvisualiser.org/features.html).
- Celemony, [Melodyne 5 — Display and other options](https://helpcenter.celemony.com/M5/doc/melodyneStudio5/en/M5tour_ViewOptions-ARA?env=dawsWithAra); [Audio characteristics and algorithms](https://helpcenter.celemony.com/M5/doc/melodyneStudio5/en/M5tour_AudioAlgorithms?env=standAlone).
- C-Thru Music, [AXiS-49 technical specification](https://www.c-thru-music.com/cgi/?page=spec-49); [Harmonic Table chord shapes](https://www.c-thru-music.com/cgi/?page=layout_cshapes).
- Resolume, [Parameter Animation](https://resolume.com/support/en/parameter-animation).
- Meinard Müller, [FMP notebooks — Log-Frequency Spectrogram and Chromagram](https://www.audiolabs-erlangen.de/resources/MIR/FMP/C3/C3S1_SpecLogFreq-Chromagram.html).
- Werner Goebl, [Animations of expressive performance](https://iwk.mdw.ac.at/goebl/animations.html).

**Primary — papers**

- Anna Gawboy & Justin Townsend, [Scriabin and the Possible](https://mtosmt.org/issues/mto.12.18.2/mto.12.18.2.gawboy_townsend.php), *Music Theory Online* 18.2 (2012).
- Arpi Mardirossian & Elaine Chew, [Visualizing Music: Tonal Progressions and Distributions](https://ismir2007.ismir.net/proceedings/ISMIR2007_p189_mardirossian.pdf), ISMIR 2007.
- Craig Stuart Sapp, [Harmonic Visualizations of Tonal Music](https://ccrma.stanford.edu/~craig/papers/01/icmc01-tonal.pdf), ICMC 2001; [Mazurka Project keyscapes](https://mazurka.org.uk/info/keyscape/); [Visual Hierarchical Key Analysis](https://ccrma.stanford.edu/~craig/papers/05/p3d-sapp.pdf), *Computers in Entertainment* 3(4), 2005.
- Dmitri Tymoczko, [The Generalized Tonnetz](https://dmitri.mycpanel.princeton.edu/tonnetzes.pdf).
- Louis Bigo & Moreno Andreatta, [Topological Structures in Computer-Aided Music Analysis](http://repmus.ircam.fr/_media/moreno/BigoAndreatta_Computational_Musicology.pdf).
- Matthias Miller et al., [Analyzing Visual Mappings of Traditional and Alternative Music Notation](https://arxiv.org/pdf/1810.10814), arXiv:1810.10814 (2018).
- Matthias Miller, Alexandra Bonnici & Mennatallah El-Assady, [Augmenting Music Sheets with Harmonic Fingerprints](https://arxiv.org/pdf/1908.00003), DocEng 2019.
- Frank Heyen, Quynh Quang Ngo & Michael Sedlmair, [Visual Overviews for Sheet Music Structure](https://arxiv.org/pdf/2308.06140), ISMIR 2023.
- Petri Toiviainen & Carol Krumhansl, [Measuring and Modeling Real-Time Responses to Music](https://doi.org/10.1068/p3312), *Perception* 32 (2003).
- Jonathan Foote, [Visualizing music and audio using self-similarity](https://dl.acm.org/doi/10.1145/319463.319472), ACM Multimedia 1999.
- Martin Wattenberg, [Arc Diagrams: Visualizing Structure in Strings](http://hint.fm/papers/arc-diagrams.pdf), IEEE InfoVis 2002. (Note: hint.fm currently serves a self-signed certificate; the ISMIR 2021 extension below restates the method.)
- [Visualizing Intertextual Form with Arc Diagrams](https://archives.ismir.net/ismir2021/paper/000008.pdf), ISMIR 2021.
- Hugo B. Lima, Carlos G. R. Dos Santos & Bianchi S. Meiguins, [A Survey of Music Visualization Techniques](https://dl.acm.org/doi/10.1145/3461835), *ACM Computing Surveys* 54(7), 2021. (Abstract and citing descriptions only — the full text was behind a 403 for me.)
- Tony Bergstrom, Karrie Karahalios & John C. Hart, [Isochords: Visualizing Structure in Music](https://dl.acm.org/doi/10.1145/1268517.1268565), Graphics Interface 2007. **Full text not obtained** — details in §7.2 are from the abstract and from citing papers.
- Peter Ciuha, Bojan Klemenc & Franc Solina, [Visualization of concurrent tones in music with colours](https://dl.acm.org/doi/10.1145/1873951.1874320), ACM Multimedia 2010. **Full text not obtained** (the Ljubljana eprints host refused connection); per the abstract and citing descriptions, it colours a *whole group of concurrent tones with one colour*, based on mapping a key-spanning circle of thirds onto the colour wheel, displayed in an extended 3D piano-roll notation. Relevant to a "solid colour" chord lens; verify before relying on the mechanism.

**Secondary / reference**

- Wikipedia: [Piano roll](https://en.wikipedia.org/wiki/Piano_roll), [Pitch space](https://en.wikipedia.org/wiki/Pitch_space), [Harmonic table note layout](https://en.wikipedia.org/wiki/Harmonic_table_note_layout), [Wicki–Hayden note layout](https://en.wikipedia.org/wiki/Wicki%E2%80%93Hayden_note_layout), [Neo-Riemannian theory](https://en.wikipedia.org/wiki/Neo-Riemannian_theory), [Color organ](https://en.wikipedia.org/wiki/Color_organ), [Lead sheet](https://en.wikipedia.org/wiki/Lead_sheet), [Synesthesia in Classical Composers](https://en.wikipedia.org/wiki/Synesthesia_in_Classical_Composers).
- [Open Music Theory — Neo-Riemannian Triadic Progressions](https://viva.pressbooks.pub/openmusictheory/chapter/neo-riemannian-triadic-progressions/).
- [Cabinet Magazine — The Scale and the Spectrum](https://www.cabinetmagazine.org/issues/22/peel.php) (Castel).
- [Berklee — Why Lead Sheets?](https://www.berklee.edu/berklee-today/summer-2018/lead-sheet); [UVM — Introduction to lead-sheet chord symbols](https://www.uvm.edu/~dfeurzei/110/materials/lead_symbols.pdf).
- [CDM — Logic Pro 12's new harmonic intelligence and Chord ID](https://cdm.link/logic-pro-12-hands-on/).
- [Chromatone — colour notation](https://chromatone.center/theory/notes/color/).
