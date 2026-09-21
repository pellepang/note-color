# Color theory and the twelve pitch classes

Research for wayfinder ticket
[#242](https://github.com/pellepang/note-color/issues/242), under map
[#240](https://github.com/pellepang/note-color/issues/240) (Color View — the lens
workbench). The map's **standing decision 7** makes the color mapping a
first-class, live-editable, A/B-comparable object, so the question this document
has to answer is not "what is the right mapping" but **"which mappings are worth
offering as comparable alternatives, and why would a user switch."**

Everything numeric below was computed against this repo's actual
`src/notecolor/analysis/color_map.py` and `settings/config.py` values, with a
throwaway script implementing Oklab from Ottosson's published matrices and CVD
simulation from the Machado (2009) matrices as shipped in `colour-science`.
Sources are cited inline; where I could not verify something from a primary
source, it says so.

---

## 0. The baseline, stated precisely

`color_map.note_to_hsl()` today:

- hue = `(step * 30 + HUE_OFFSET_DEG) % 360`, where `step` is either the
  chromatic `pitch_class` or `fifths_index(pitch_class) = (pitch_class * 7) % 12`
- saturation = `BASE_SATURATION = 0.75`, fixed
- lightness = linear in octave over `BASE_LIGHTNESS_RANGE = (0.18, 0.82)`,
  clamped to octaves 2–6
- `tab` overrides lightness to a fixed `0.5`
  ([decision 09](../decisions/09-tab-view-s-note-color-ignores-octave-and-uses-fixed-lightness-config-t.md))
- fifths ordering is additive, chromatic stays the default
  ([decision 06](../decisions/06-circle-of-fifths-color-scheme-is-additive-not-a-replacement.md))
- per-note `[colors]` hue overrides (issue #41) replace hue only

So the mapping already has four separable degrees of freedom — **ordering**
(chromatic vs fifths), **color space** (HSL), **what octave drives** (lightness,
or nothing), and **per-note overrides**. That factoring is the right skeleton for
a scheme object; sections 6–7 push on it.

---

## 1. Color wheels, and what each one is actually ordering by

The important thing about the four canonical wheels is that **they are not four
attempts at the same thing**. They order by different quantities, so "twelve
evenly spaced hues" means four different sets of colors.

### Itten (Bauhaus, 1961) — a *mixing* wheel

Itten's twelve-part circle is built symmetrically from three subtractive
primaries (red, yellow, blue), their three secondaries and six tertiaries. It is
a pigment-mixing model, not a perception model, and it is uneven as a perceptual
scale: the hue circle has "relatively large hue steps in the yellow-green-blue
sector, mainly because it is structured symmetrically around just three of the
four psychological primaries"
([Briggs, *The Dimensions of Colour*](http://www.huevaluechroma.com/113.php)).
It also fails on its own terms — complementaries that should mix to neutral grey
mix to brown, and Itten's stated primaries cannot in practice be mixed into his
stated secondaries
([Briggs, RYB hue circle](http://www.huevaluechroma.com/072.php); the same
criticism is Küppers'). Itten's lasting contribution here is the *vocabulary*
(complementary / analogous / triadic / split-complementary), not the geometry.

### Munsell — a *perceptual-equal-step* wheel, measured

Munsell separates Hue, Value and Chroma as independently varying, perceptually
uniform dimensions: five principal hues (R, Y, G, B, P) plus five intermediates,
each subdivided ten ways for 100 integer hues; Value is 11 visually equal steps
from black to white; Chroma increases in perceptually equal steps out from the
neutral axis ([SPIE Optipedia, *Munsell System*](https://spie.org/publications/spie-publication-resources/optipedia-free-optics-information/pm105_53_munsell_system);
[BabelColor, Munsell description](https://babelcolor.com/munsell.htm)). The
1943 renotation put it on measured experimental footing. Munsell is the
historical ancestor of everything in section 2, and Ottosson still uses Munsell
chart data as a *test* of chroma prediction
([Ottosson, Oklab](https://bottosson.github.io/posts/oklab/)).

### NCS — an *elementary-percept* wheel

NCS is built on six elementary colours — white, black, yellow, red, blue, green
— with the four chromatic elementaries arranged in a circle around a
white/black axis; a colour's "nuance" is its blackness and chromaticness, e.g.
`NCS S 1040-R20B` is 10% blackness, 40% chromaticness, hue "red with 20% blue"
([NCS Colour, *Learn the NCS System*](https://ncscolour.com/en-int/pages/the-system)).
NCS is the wheel that takes seriously that there are **four** unique hues, not
three — which is exactly the axis on which Itten's wheel is criticised. For our
purposes NCS matters mainly as evidence that "how many landmark hues are there"
has no single answer, and twelve is not one of the candidates anyone proposes.

### The modern wheel: OKLCH hue angle

See section 2. This is the one with a defensible claim to "30° apart means
equally different."

**Takeaway for #242:** none of the historical wheels has twelve as a natural
number of stations. Twelve comes from music, not from color. Every scheme below
is therefore a projection of a 12-element set onto a continuum that does not
itself want to be divided into twelve.

---

## 2. How badly evenly-spaced HSL hue lies

This is the strongest finding in the document, and it is quantified against our
actual constants.

### What the spec says

CSS Color 4 is unusually blunt about this, and it is a normative-body primary
source:

> A disadvantage of HSL over OkLCh is that hue manipulation changes the visual
> lightness, and that hues are not evenly spaced apart. […] because the lightness
> is simply the mean of the gamma-corrected red, green and blue components it
> does not correspond to the visual perception of lightness across hues.
>
> — [CSS Color Module Level 4, §7](https://www.w3.org/TR/css-color-4/)

> The hue angle in HSL is not perceptually uniform; colors appear bunched up in
> some areas and widely spaced in others.
>
> — ibid.

with the spec's own example: `hsl(220deg …)` vs `hsl(250deg …)` "look fairly
similar" while `hsl(50deg …)` vs `hsl(80deg …)` — the same 30° apart — "look
very different." Thirty degrees is exactly our step size.

CIE Lab/LCh is better but has three named defects, quoted from the same spec:
**hue linearity** ("In the blue region (LCH Hue between 270° and 330°), visual
hue departs from what LCH predicts… as a saturated blue has its Chroma
progressively reduced, it becomes noticeably purple"), **hue uniformity** ("not
perfect"), and **over-prediction of high Chroma differences**.

Oklab was built to fix those:

> Recently, Oklab, an improved Lab-like space has been developed. The
> corresponding polar form is called OkLCh. It was produced by numerical
> optimization of a large dataset of visually similar colors, and has improved
> hue linearity, hue uniformity, and chroma uniformity compared to CIE LCH.
>
> — [CSS Color Module Level 4, §9.2](https://www.w3.org/TR/css-color-4/)

and because it is uniform, color difference is just Euclidean distance
(ΔE<sub>OK</sub>, §20.3). The spec fixes **one just-noticeable difference at an
OkLCh difference of 0.02** (§14, gamut mapping) — which gives us a ruler.

Ottosson's own derivation ([bottosson.github.io/posts/oklab](https://bottosson.github.io/posts/oklab/))
reports HSV's lightness RMS error as **11.59** against Oklab's **0.20** on his
lightness dataset — HSV/HSL is, quantitatively, the worst model in his table by
more than an order of magnitude. He is equally direct about HSV hue: "Yellow,
magenta and cyan appear much lighter than red and blue."

### What that costs *us*, measured

Taking our twelve notes at `S=0.75, L=0.5` (the `tab` constants), converting to
Oklab:

| note | HSL hue | Ok L | Ok C | Ok hue | ΔE<sub>OK</sub> to next | Ok hue step |
|------|--------:|-----:|-----:|-------:|------------------------:|------------:|
| C  |   0 | 0.579 | 0.222 |  27.8 | 0.163 | 30.9 |
| C# |  30 | 0.689 | 0.154 |  58.7 | 0.238 | 51.0 |
| D  |  60 | 0.876 | 0.185 | 109.7 | 0.114 | 24.4 |
| D# |  90 | 0.813 | 0.225 | 134.1 | 0.055 |  8.5 |
| E  | 120 | 0.786 | 0.258 | 142.6 | 0.078 | 11.3 |
| F  | 150 | 0.796 | 0.195 | 153.9 | 0.130 | 40.9 |
| F# | 180 | 0.820 | 0.136 | 194.8 | 0.271 | 58.3 |
| G  | 210 | 0.596 | 0.168 | 253.1 | 0.197 | 14.8 |
| G# | 240 | 0.434 | 0.266 | 267.9 | 0.160 | 31.0 |
| A  | 270 | 0.511 | 0.254 | 298.9 | 0.190 | 29.4 |
| A# | 300 | 0.642 | 0.281 | 328.3 | 0.146 | 29.5 |
| B  | 330 | 0.598 | 0.229 | 357.8 | 0.118 | 30.0 |

Three concrete consequences:

1. **Perceived lightness spans 0.434 (G#) to 0.876 (D)** — a range of 0.442, or
   **22 JNDs** — even though HSL lightness is *constant at 0.5*. G# is visually
   half as light as D. Any lens that uses lightness to mean octave is fighting
   a 22-JND lightness signal that means nothing.
2. **Real hue steps range 8.5° (D#→E) to 58.3° (F#→G)** against a nominal 30°.
   Almost seven of the twelve notes live in a green-to-cyan crush.
3. **Neighbour separation ΔE<sub>OK</sub> ranges 0.055 to 0.271, a 4.9× ratio.**
   D# and E are 2.8 JND apart; F# and G are 13.6 JND apart. The wheel is not a
   wheel; it is a lumpy ellipse.

Oklab's own `Okhsl`/`Okhsv` exist precisely to give HSL-shaped ergonomics on a
perceptual base, at the cost of some uniformity — Ottosson is explicit that
"independent control of hue, lightness and chroma cannot be achieved in a color
space that also maps sRGB to a simple geometrical shape"
([Okhsl/Okhsv](https://bottosson.github.io/posts/colorpicker/)). For a *palette*
of twelve fixed colors we do not need that compromise: we can use OKLCH directly
and clamp chroma to the gamut.

### Gamut, practically

A constant-L, constant-C ring in OKLCH must fit inside sRGB. I bisected max
in-gamut chroma at each of the twelve 30° hue angles:

| Ok L | min C over the 12 hues | worst hue |
|-----:|-----------------------:|----------:|
| 0.55 | 0.095 | 210° (blue) |
| 0.65 | 0.112 | 210° |
| 0.70 | 0.121 | 210° |
| 0.75 | 0.128 | 270° |

So **L≈0.70, C≈0.115** is a safe, fully in-gamut, fully uniform twelve-note
ring — every neighbour exactly ΔE<sub>OK</sub> = 0.060 apart. Hexes are in
section 7. If a scheme wants more punch than C=0.115, it must either let chroma
vary per hue (giving up chroma uniformity) or use Ottosson's gamut-clipping —
he recommends "adaptive L₀ with α=0.05"
([sRGB gamut clipping](https://bottosson.github.io/posts/gamutclipping/)).

---

## 3. Color harmony vs *harmonic* relations — can they be made to line up?

This is the question the ticket asks that nobody else has asked, and the answer
is a genuine, implementable result: **it depends entirely on the ordering, and
fifths ordering aligns far better than chromatic ordering.**

Itten's relations are defined by hue angle. On a twelve-station wheel:
complementary = 180° = 6 stations; triadic = 120° = 4 stations; analogous =
adjacent; tetradic/square = 90° = 3 stations.

### Under chromatic ordering (today's default)

hue = 30° × pitch class, so hue angle = 30° × interval in semitones:

| color relation | musical interval it lands on |
|---|---|
| complementary (180°) | **tritone** (6 semitones) |
| triadic (0/120/240) | **augmented triad** (C–E–G♯) |
| square (0/90/180/270) | **diminished seventh** (C–E♭–G♭–A) |
| hexad (0/60/…/300) | **whole-tone scale** |
| analogous (adjacent) | semitone — the *most* dissonant adjacency |
| major triad C–E–G | 0° / 120° / **210°** — no named color relation |

That is a striking and slightly perverse result. Chromatic ordering makes the
**symmetrical, tonally-unstable** collections (augmented, diminished seventh,
whole-tone) come out as the textbook-beautiful color harmonies, and makes the
plain major triad come out as a shape with no name. Color theory's "most
harmonious contrast," the complementary pair, is music's most unstable interval.

### Under fifths ordering

hue = 30° × `(pitch_class * 7) % 12`. Multiplication by 7 mod 12 is the M7
operation of transformational theory; it is a bijection on ℤ₁₂, which is why the
existing `fifths_index()` one-liner works at all.

| color relation | musical meaning |
|---|---|
| analogous (adjacent, 30°) | **perfect fifth** — the most consonant interval after the octave |
| complementary (180°) | **tritone** — still, because 6×7 ≡ 6 (mod 12) |
| major triad C–E–G | 0° / 120° / 30° — a triadic pair plus an analogous neighbour |
| minor triad C–E♭–G | 0° / 270° / 30° |
| **the diatonic scale** | **seven contiguous stations — a 210° analogous arc** |

The last row is the payoff. C major's fifths indices are {11,0,1,2,3,4,5}: a
single unbroken arc of the hue circle. **A key becomes a contiguous colour
region, and modulating to a neighbouring key rotates that arc by exactly one
station.** Nothing in the chromatic ordering does that — C major in chromatic
hue is a scattered, gapped set.

So: yes, color harmony can be made to line up with harmonic relations, and the
lever is the ordering. That the tritone stays complementary in *both* orderings
is a free win worth keeping. Scriabin reached the same design conclusion a
century ago — he arranged colours onto the circle of fifths specifically so that
"adjacent colors on the spectrum are correlated with closely-related tonalities"
([Gawboy & Townsend, *Music Theory Online* 18.2](https://mtosmt.org/issues/mto.12.18.2/mto.12.18.2.gawboy_townsend.php)).

**Caveat:** all of this is angle arithmetic, and section 2 shows HSL angles are
not perceived angles. "Analogous" and "complementary" are only *true of what the
eye sees* if the ring is laid out in OKLCH. Fifths ordering and a perceptual
space are complements, not alternatives.

---

## 4. Simultaneous contrast — twelve of these adjacent on one screen

Chevreul's and then Albers' point: "Two colors placed side by side will appear
to change in hue, tonal value, and saturation; their dissimilar qualities will be
intensified and similar qualities muted"; a stimulus shifts *toward the
complement* of its surround, so "one color can look like two"
([Albers, *Interaction of Color*, 1963 — summarised at
DePaul, Color Context](http://facweb.cs.depaul.edu/sgrais/color_context.htm);
[Oberlin College Libraries on Chevreul & Albers](https://libraries.oberlin.edu/news/2024/02/21/check-it-out-color-theory-chevreul-albers)).

Three implications specific to a lens workbench:

1. **A note's colour is not a property of the note; it is a property of the
   note and everything drawn near it.** In a circle-of-fifths lens the twelve
   swatches are mutually adjacent, and each is surrounded by its analogous
   neighbours — which is the *worst* case for simultaneous contrast, because
   similar qualities get muted. This is a direct argument for keeping
   neighbouring stations far apart perceptually (section 2's 8.5° D#→E step is
   doubly bad here).
2. **Background matters as much as the palette.** The same twelve colours on a
   dark canvas and a light canvas are not the same twelve colours. A scheme
   object should therefore carry, or at least declare compatibility with, a
   background — this belongs in #247's grilling.
3. **Separating swatches with a neutral gutter largely defeats it.** That is a
   lens-chrome decision (#243), not a scheme decision, but the two interact.

There is a related effect that argues against naive "equal lightness" schemes:
the **Helmholtz–Kohlrausch effect** — more saturated colours appear brighter
than less saturated colours of the same luminance, so saturated colours need
*less* luminance to look equally bright
([Wikipedia, H–K effect](https://en.wikipedia.org/wiki/Helmholtz%E2%80%93Kohlrausch_effect),
with the effect strongest in dark surrounds; peer-reviewed treatment in
[High et al., *Color Research & Application*, 2023](https://onlinelibrary.wiley.com/doi/10.1002/col.22839)).
Oklab predicts *lightness*, not brightness, and does not model H–K; so a
constant-L OKLCH ring will still look slightly uneven in a dark room, more so
at high chroma. Worth knowing before anyone calls the uniform ring "perfect."

---

## 5. Accessibility — and the hard result

### Prevalence

The Color Universal Design project states: "One in twelve Caucasian (8%), one in
20 Asian (5%), and one in 25 African (4%) males are so-called 'red-green'
colorblind" ([Okabe & Ito, CUD, jfly](https://jfly.uni-koeln.de/color/)).
Deuteranomaly is the most common single form (~5–6% of men); tritan defects are
rare (~0.003%). Machado et al. put the worldwide figure at "approximately 200
million people"
([Machado, Oliveira & Fernandes, IEEE TVCG 15(6), 2009](https://www.inf.ufrgs.br/~oliveira/pubs_files/CVD_Simulation/CVD_Simulation.html)).

### The measurement

I simulated all twelve notes under protanopia, deuteranopia and tritanopia using
the Machado (2009) severity-1.0 matrices as published in `colour-science`
([`colour/blindness/datasets/machado2010.py`](https://github.com/colour-science/colour/blob/develop/colour/blindness/datasets/machado2010.py)),
applied in linear sRGB, then measured the **worst confusable pair** by
ΔE<sub>OK</sub>, with the spec's 1 JND = 0.02 as the ruler.

| scheme | normal | protan | deutan | tritan |
|---|---:|---:|---:|---:|
| current HSL 30° ring | 2.8 JND (D#/E) | **0.5 JND** (D#/E) | 1.5 JND (D#/E) | 0.8 JND (E/F) |
| uniform OKLCH ring (L .70 / C .115) | 3.0 JND (G#/A) | 1.2 JND | **0.5 JND** (D/E) | 0.6 JND |
| OKLCH ring, fifths order | 3.0 JND | 1.2 JND | **0.5 JND** | 0.6 JND |
| Okabe–Ito diatonic (§7, scheme E) | 4.5 JND | **4.8 JND** | **3.2 JND** | **3.1 JND** |

**This is the important negative result: fixing the colour space does not fix
accessibility.** A perfectly uniform twelve-hue ring is still indistinguishable
to a dichromat, because a hue ring at constant lightness collapses to roughly a
*line* under dichromacy — twelve stations along it cannot be 12 apart. The
uniform ring is actually *worse* under deuteranopia than the naive HSL ring.

The fix is the one CUD and ColorBrewer both arrive at: **stop relying on hue
alone.** CUD's three principles are (1) choose identifiable schemes, (2) "use not
only different colors but also a combination of different shapes, positions,
line types and coloring patterns," (3) state colour names explicitly; and for
distinguishing colours it says to use "brightness and saturation" differences,
not hue alone ([jfly](https://jfly.uni-koeln.de/color/)). ColorBrewer's
qualitative sets top out at twelve and only the least-safe ones (`Paired`,
`Set3`) go that high ([ColorBrewer](https://colorbrewer2.org/);
[Harrower & Brewer, *The Cartographic Journal* 40(1), 2003](https://www.cs.rpi.edu/~cutler/classes/visualization/S18/papers/colorbrewer.pdf)).
**Twelve is at or past the practical ceiling for a categorical palette even for
normal vision.**

Scheme E in section 7 is my attempt at the accessible answer, and it works — but
it costs the octave channel, because it spends lightness on pitch class. That is
the real trade, and it should be #247's decision, not a research conclusion.

For reference, non-text UI elements need 3:1 contrast against adjacent colour
under [WCAG SC 1.4.11](https://www.w3.org/WAI/WCAG21/Understanding/non-text-contrast.html);
a coloured note glyph carrying meaning is squarely a "graphical object."

---

## 6. What people actually associate — the empirical constraint

### Itoh et al. 2017 — the best data that exists on exactly our question

[Itoh, Sakata, Kwee & Nakada, "Musical pitch classes have rainbow hues in pitch
class–color synesthesia," *Scientific Reports* 7:17781 (2017)](https://www.nature.com/articles/s41598-017-18150-y)
([PDF](http://www.daysyn.com/Itoh_et_al_2017_-_Musical_pitch_classes_have_rainbow_hues_in_pitch_class-color_synesthesia.pdf))
tested **15 pitch-class–colour synesthetes**, all with absolute pitch, choosing
colours for each of the twelve chromatic pitch classes, twice on each of two
days ~3 months apart. Findings, quoted:

> Across-subject averaging of reported colors revealed that pitch classes have
> rainbow hues, beginning with *do*-red, *re*-yellow, and so forth, ending with
> *si*-violet, accompanied by a decrease in saturation.

> Findings suggest that the two dimensions of musical pitch, pitch class and
> pitch height, are mapped to the hue-saturation plane and the value/brightness
> dimension of color, respectively.

Quantitatively: hue regressed on pitch class (both in radians, 0 rad = red =
*do*) gave slope **1.09, 95% CI 0.94–1.25, t(130.8) = 14.1, p < 0.001**; the
pitch-class predictor values were 0.00, 0.90, 1.80, 2.70, 3.59, 4.49, 5.39 rad
— i.e. **the seven diatonic degrees spread evenly over the whole hue circle,
≈51.4° apart, not twelve stations at 30°.** Saturation fell from *do* to *si*
with slope **−0.36, 95% CI −0.50 to −0.22, t(101.5) = 5.2, p < 0.001**.
Test–retest reliability: hue slope 0.92, saturation 0.89, value 0.67.

Two further findings matter for us:

- **Enharmonics differ by name, not by sound.** "although both referred to the
  same note, for many subjects, *do-sharp* had a reddish color whereas *re-flat*
  was yellowish… the colors were linked to the verbal labels of pitch classes
  rather than the actual auditory pitches." The repo already half-encodes this:
  `NOTE_NAMES_FIFTHS` spells the flat side with flats. A scheme could take this
  seriously and colour C♯ and D♭ differently.
- **Pitch height → brightness is the separate, near-universal association** —
  "the well-known crossmodal association between pitch height and
  value/brightness," which Ward and colleagues find holds "in all seven
  synaesthetes" and which non-synesthetes also favour by intuition. **Our
  octave→lightness mapping is empirically well-founded and should stay the
  default octave channel.**

The 2024 follow-up debating "rainbow-like theory" vs a "two-step hypothesis"
([Frontiers in Psychology 15:1482714](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2024.1482714/full))
I could not fetch past a CAPTCHA; flagged as unverified.

### Palmer et al. 2013 — colour tracks emotion, not pitch

[Palmer, Schloss, Xu & Prado-León, PNAS 110:8836 (2013)](https://www.pnas.org/doi/10.1073/pnas.1212562110):
faster music in the major mode drew colours that were "more saturated, lighter,
and yellower," slower minor music "desaturated, darker, and bluer," with
0.89 < r < 0.99 between the emotional ratings of the music and of the chosen
colours. **Non-synesthetes' music–colour matching is mediated by emotion, not by
pitch class.** This is a caution against over-claiming that any pitch-class
scheme is "natural," and it is also a hint at a scheme class the map has not
considered: mode/tempo-driven global modulation of the palette (parked in §8).

### The historical schemes

- **Newton**, *Opticks* (1704): divided the spectrum into seven bands in the
  proportions of a Dorian scale, and arranged them in a circle, reasoning that
  "the affinity between the extreme red and violet, the ends of the spectrum, is
  of the same kind as between the first and last notes of the octave." He
  admitted the match could not be demonstrated from the evidence
  ([Briggs on Newton](http://www.huevaluechroma.com/071.php)).
- **Scriabin**, *Prometheus* (1910): a twelve-key colour scheme mapped onto the
  circle of fifths, realised in a notated *tastiera per luce*. Because there are
  twelve keys and only six or seven named spectral hues he "expanded his palette
  by stretching red and blue over several key areas and adding non-spectral
  colors such as 'steely-blue' and 'metallic leaden grey'." Crucially, Gawboy &
  Townsend argue from Sabaneev that **Scriabin was not a synesthete** — he
  "deliberately constructed his system of tone-color correspondence for its
  artistic and spiritual effects"
  ([MTO 18.2](https://mtosmt.org/issues/mto.12.18.2/mto.12.18.2.gawboy_townsend.php)).
  There are two source variants (Sabaneev 1911; the annotated Parisian score,
  1913) differing in hue for a few keys — so "the" Scriabin table needs a
  citation choice. I did not find RGB values in a primary source; any
  implementation is an interpretation and should say so in the UI.

---

## 7. Candidate schemes

Each is stated concretely enough to implement, with the reason a user would
switch to it. All twelve-colour lists are measured, in-gamut sRGB. The
recommended shape of a scheme object comes after.

### A. `chromatic-hsl` — the baseline *(already implemented)*

Hue = 30° × pitch class in HSL, S 0.75, L by octave. **Keep it, unchanged, as
the A/B reference.** A comparison view is worthless without the thing being
compared to. It is also genuinely the one that makes whole-tone, diminished-7th
and augmented collections pop as colour harmonies (§3).

### B. `fifths-hsl` — the baseline's sibling *(already implemented)*

Hue = 30° × `fifths_index` in HSL. **Why switch:** a key becomes a contiguous
210° arc; a perfect fifth becomes an analogous neighbour; modulation reads as a
rotation. This is the scheme for anyone watching harmony rather than melody.

### C. `chromatic-oklch` — the honest ring

`OKLCH(L = 0.70, C = 0.115, h = 30° × pitch class)`. Every neighbour exactly
ΔE<sub>OK</sub> = 0.060 (3.0 JND) apart; no lightness lie.

```
C  #d87f9b   C# #dd8273   D  #d28c50   D# #ba9b40
E  #95a952   F  #66b278   F# #31b5a1   G  #22b1c6
G# #53a7df   A  #839ae6   A# #a98ddb   B  #c783c0
```

**Why switch:** because the wheel is finally round. Twelve notes look equally
different from each other, and the D#/E crush and the F#/G chasm both disappear.
The honest cost is visible in the hexes: uniform + in-gamut + constant-L means
*muted*. This is the scheme that makes the workbench point, and the scheme
someone will complain is washed out — which is itself a good thing to be able to
A/B.

### D. `fifths-oklch` — C and B combined

Same ring, ordered by `fifths_index`. **Why switch:** it is the only scheme where
"analogous", "complementary" and "triadic" are simultaneously *true of what the
eye sees* and *meaningful about the music*. My recommendation for the Color
View's default, over the chromatic default that decision 06 set for the terminal
views — but that is #247's call, and it does have a cost: it breaks colour
identity with `fill`'s chromatic default (decision 06's exact concern, in
reverse).

```
C  #d87f9b   C# #22b1c6   D  #d28c50   D# #839ae6
E  #95a952   F  #c783c0   F# #31b5a1   G  #dd8273
G# #53a7df   A  #ba9b40   A# #a98ddb   B  #66b278
```

### E. `cud-diatonic` — the accessible scheme

Seven natural notes take the seven chromatic Okabe–Ito Color Universal Design
colours in order; the five accidentals take a **darkened variant of the natural
below them** (Oklab L × 0.5, chroma × 0.8). Parameters chosen by grid search to
maximise the worst-case pairwise ΔE<sub>OK</sub> across normal, protan, deutan
and tritan simulation simultaneously.

```
C  #e69f00   C# #653400   D  #56b4e9   D# #004569
E  #009e73   F  #f0e442   F# #625700   G  #0072b2
G# #002754   A  #d55e00   A# #620400   B  #cc79a7
```

Worst confusable pair: **4.5 JND normal, 4.8 protan, 3.2 deutan, 3.1 tritan** —
against the current scheme's 0.5 JND under protanopia. That is a ~6× improvement
in the worst case, and it is the difference between "unusable" and "usable."

**Why switch:** because you have a colour vision deficiency, or because you are
showing the thing on a projector, or because you want the black keys to read as
black keys — the scheme has a pleasant side effect of making the piano's
white/black structure legible in colour alone.

**The cost, stated plainly:** lightness now encodes accidental, so it cannot
also encode octave. A scheme that picks this must hand octave to another
channel (chroma, or a non-colour channel the lens owns — size, stroke, vertical
position). §8 lists this as the open question.

### F. `itoh-rainbow` — the empirical scheme

Hue = 360° × (diatonic degree)/7, i.e. the seven naturals spread evenly over the
whole circle ≈51.4° apart with *do* = red, plus a saturation ramp falling from
*do* to *si* (slope −0.36 normalised) and accidentals as darker variants of
their neighbour. This is Itoh et al. 2017's measured average, made into a
palette.

```
C  #e680a1   C# #8b2f52   D  #e48b53   D# #8b3a00
E  #b4a738   F  #59bc7f   F# #006931   G  #00bbcc
G# #006677   A  #6ea6f5   A# #1e549c   B  #bc8de3
```

**Why switch:** it is the only scheme on this list with human data behind it.
For a workbench whose point is seeing theory, "here is the mapping fifteen
people with pitch-class synesthesia actually report" is a strong exhibit. It is
also naturally diatonic — the scale you are playing in gets the wide, separated
hues and the chromatic notes get the shadows — which reads well and measures
reasonably (0.9–2.1 JND worst case under CVD; better than C/D, worse than E).

### G. `scriabin` — the historical scheme

Scriabin's twelve key-colours on the circle of fifths. **Why switch:** it is a
famous, specific, opinionated artefact, and letting someone put Scriabin's own
mapping next to a perceptually-uniform one *is* the Color View's thesis. Ship it
labelled as an interpretation, cite whether it follows Sabaneev 1911 or the 1913
score, and do not pretend the RGB values are his.

### H. `newton-diatonic` — the other historical scheme

Newton's seven spectral bands on the seven diatonic degrees, Dorian
proportioned. **Why switch:** same reason as G, and it pairs instructively with
F — Newton guessed a diatonic rainbow in 1704 and Itoh measured one in 2017.

### I. `tonic-relative` — the scheme that isn't absolute

Hue assigned by **scale degree relative to the detected key**, not by absolute
pitch class: the tonic is always the same colour, so I–IV–V always look the
same whatever key you are in. Requires key context from the signal, which the
chord pipeline (`chord_smoother`, `chord_templates`) can supply and a score
certainly can.

**Why switch:** because for most listeners, *function* is what is audible, not
absolute pitch. Every other scheme here assumes absolute pitch matters; this one
assumes it does not. It is the scheme that would most change what the view
teaches, and it is the one the map's "note signal" ticket (#246) has to be able
to feed — it needs a key, not just a note. **Flagging it here because it is a
signal requirement, not just a palette.**

### J. `monochrome` — the degenerate scheme

One hue; pitch class drives chroma or lightness along a single OKLCH ray.
Illegible as pitch identity, fully CVD-safe, and visually calm. **Why switch:**
performance mode, projection, or as the control case that shows how much of a
lens's readability actually comes from colour versus layout. Cheap to build,
genuinely useful as a comparison.

### The scheme object these imply

Every scheme above decomposes into the same five fields, which is the real
recommendation for #247:

| field | values seen above |
|---|---|
| **ordering** | chromatic · fifths · diatonic-degree · tonic-relative |
| **colour space** | HSL · OKLCH (· Okhsl, if a picker UI wants HSL ergonomics) |
| **pitch-class channel** | hue · hue+saturation · hue+lightness (E) |
| **octave/height channel** | lightness (default, empirically supported) · chroma · none (`tab`) · non-colour |
| **overrides** | per-note, per-field — today hue-only (#41), which is the right default |

That factoring is what makes A/B comparison meaningful: you can hold four fields
and vary one. It also subsumes the existing `--color-scheme` flag and the `[colors]`
override block without breaking either.

---

## 8. Open questions I could not close

- **Where octave goes when lightness is spent** (scheme E). Chroma has far less
  dynamic range and interacts with Helmholtz–Kohlrausch; a non-colour channel is
  a lens concern, not a scheme concern, which makes it awkward for a scheme
  object to own. Genuinely unresolved — needs #247.
- **Background as part of a scheme** (§4). Simultaneous contrast makes a palette
  meaningless without one, but binding a background into a scheme collides with
  per-lens styling.
- **Enharmonic spelling as a colour dimension.** Itoh's data say C♯ and D♭ are
  different colours to synesthetes. Our signal currently carries a pitch class,
  not a spelling; a scheme that wants this needs the signal to carry spelling
  (again #246).
- **Emotion/mode-driven global modulation** (Palmer et al.). A scheme that warms
  and lightens in major, cools and darkens in minor is well supported by the
  data and entirely unlike anything else on the list. Parked deliberately: it is
  a *modulation*, not a mapping, and would need its own slot in the object.
- **Unverified:** Frontiers 2024 rainbow-vs-two-step follow-up (CAPTCHA); exact
  Scriabin RGB values (no primary source found); the Machado matrices are
  reproduced from `colour-science`, not from the paper's own tables.

---

## Reproducing the numbers

The measurement scripts were throwaway and are not committed. They implement:
Oklab via Ottosson's `linear_srgb_to_oklab` matrices (2021-01-25 revision, quoted
on his post, public domain); sRGB transfer function per CSS Color 4; ΔE<sub>OK</sub>
as Euclidean distance in Oklab with 1 JND = 0.02 per CSS Color 4 §14; CVD via the
Machado (2009) severity-1.0 matrices applied in linear RGB; max in-gamut chroma
by bisection on C at fixed (L, h). Roughly 120 lines of numpy; regenerating is
faster than maintaining them.
