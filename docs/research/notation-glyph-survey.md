# Notation glyph survey: Unicode notehead/accidental codepoints and terminal support

Research for ticket #14
(https://github.com/pellepang/note-color/issues/14), part of the
sheet-music-notation wayfinder map (#13,
https://github.com/pellepang/note-color/issues/13). Hands real candidates
to the follow-up prototype ticket (#15), which the map already marks as
blocked on this one — that ticket is where a human compares candidates
live in their own terminal and picks. **This document does not recommend
a winner.**

## Question

What Unicode codepoints exist for notehead glyphs (filled/black, open/white)
and accidental markers (sharp, flat, natural), across two tiers —
the Musical Symbols block (U+1D100–U+1D1FF, Supplementary Multilingual
Plane) and the simpler Miscellaneous Symbols block (U+2669–U+266F, Basic
Multilingual Plane) — and what's knowable about how each renders in this
project's actual terminal targets: fixed-column-grid-breaking double-width
risk (East_Asian_Width) and font coverage on Linux desktop and Raspberry
Pi OS Bookworm.

Primary sources used directly (not summaries or secondary write-ups):
Unicode 17.0.0's `EastAsianWidth.txt` and `UnicodeData.txt`
(https://www.unicode.org/Public/UCD/latest/ucd/EastAsianWidth.txt,
https://www.unicode.org/Public/UCD/latest/ucd/UnicodeData.txt), fetched and
grepped verbatim — quoted lines below. Font-coverage claims are explicitly
flagged as secondary, since Unicode.org itself publishes no font-coverage
data.

## Tier 1 — Musical Symbols block (U+1D100–U+1D1FF, SMP, "high-fidelity")

Same block as the two clef glyphs already in production use
(`terminal_tab_display.py`'s `TREBLE_CLEF_GLYPH` = U+1D11E, `BASS_CLEF_GLYPH`
= U+1D122) — see "Existing precedent" below.

Confirmed directly from `UnicodeData.txt` (General_Category field) and
`EastAsianWidth.txt`, which lists this whole span as Neutral:

```
1D100..1D126 ; N # So [39] MUSICAL SYMBOL SINGLE BARLINE..MUSICAL SYMBOL DRUM CLEF-2
1D129..1D164 ; N # So [60] MUSICAL SYMBOL MULTIPLE MEASURE REST..MUSICAL SYMBOL ONE HUNDRED TWENTY-EIGHTH NOTE
```

| Codepoint | Official name | Gen. Cat. | EAW |
|---|---|---|---|
| U+1D157 | MUSICAL SYMBOL VOID NOTEHEAD | So | N |
| U+1D158 | MUSICAL SYMBOL NOTEHEAD BLACK | So | N |
| U+1D156 | MUSICAL SYMBOL PARENTHESIS NOTEHEAD | So | N |
| U+1D159 | MUSICAL SYMBOL NULL NOTEHEAD | So | N |
| U+1D11E | MUSICAL SYMBOL G CLEF (existing, precedent) | So | N |
| U+1D122 | MUSICAL SYMBOL F CLEF (existing, precedent) | So | N |

Other in-block notehead *shape* variants exist too, in case a stylistic
alternative is ever wanted (all Gen. Cat. So, EAW N, per the same
`1D129..1D164 ; N` line): U+1D143 X NOTEHEAD, U+1D146/1D147 SQUARE NOTEHEAD
WHITE/BLACK, U+1D148–1D151 various TRIANGLE NOTEHEAD orientations,
U+1D152/1D153 MOON NOTEHEAD WHITE/BLACK, U+1D15A/1D15B CLUSTER NOTEHEAD
WHITE/BLACK.

Canonical decompositions in `UnicodeData.txt` field 5 confirm 1D157/1D158
are the "real" round noteheads used to build full note glyphs: U+1D15E
MUSICAL SYMBOL HALF NOTE decomposes to `1D157 1D165` (void notehead + stem),
U+1D15F MUSICAL SYMBOL QUARTER NOTE decomposes to `1D158 1D165` (black
notehead + stem).

**Gap found — no plain accidentals in this block.** Exhaustively grepping
every `1D1xx` codepoint between the clefs and the noteheads, the block's
*complete* accidental set is: U+1D12A DOUBLE SHARP, U+1D12B DOUBLE FLAT,
U+1D12C FLAT UP, U+1D12D FLAT DOWN, U+1D12E NATURAL UP, U+1D12F NATURAL
DOWN, U+1D130 SHARP UP, U+1D131 SHARP DOWN, U+1D132 QUARTER TONE SHARP,
U+1D133 QUARTER TONE FLAT (all Gen. Cat. So, EAW N). There is **no
"MUSICAL SYMBOL SHARP/FLAT/NATURAL"** — only compound/microtonal ones. So
even a Tier-1-only build needs to reach into Tier 2 (BMP) for an ordinary
sharp, flat, or natural sign.

## Tier 2 — Miscellaneous Symbols block (U+2669–U+266F, BMP, "simple/safe")

Quoted directly from `EastAsianWidth.txt`:

```
2667..266A     ; A  # So     [4] WHITE CLUB SUIT..EIGHTH NOTE
266B           ; N  # So         BEAMED EIGHTH NOTES
266C..266D     ; A  # So     [2] BEAMED SIXTEENTH NOTES..MUSIC FLAT SIGN
266E           ; N  # So         MUSIC NATURAL SIGN
266F           ; A  # Sm         MUSIC SHARP SIGN
```

| Codepoint | Glyph | Official name | Gen. Cat. | EAW |
|---|---|---|---|---|
| U+2669 | ♩ | QUARTER NOTE | So | **A** |
| U+266A | ♪ | EIGHTH NOTE | So | **A** |
| U+266B | ♫ | BEAMED EIGHTH NOTES | So | N |
| U+266C | ♬ | BEAMED SIXTEENTH NOTES | So | **A** |
| U+266D | ♭ | MUSIC FLAT SIGN (Unicode 1.0 name: FLAT) | So | **A** |
| U+266E | ♮ | MUSIC NATURAL SIGN (Unicode 1.0 name: NATURAL) | So | N |
| U+266F | ♯ | MUSIC SHARP SIGN (Unicode 1.0 name: SHARP) | **Sm** | **A** |

Per the ticket, ♩/♪/♫/♬ depict stemmed/beamed *rhythm* notation — out of
scope for a pitch-only v1 notehead, listed here for completeness only.
♭/♮/♯ are the accidentals actually relevant to this ticket. Note the mixed
risk profile within that one trio: ♮ (natural) is Neutral width, but ♭ and
♯ are Ambiguous.

**Ambiguous width is the genuinely risky category** — Unicode leaves the
rendered width up to context/font/terminal policy, and in practice many
terminal emulators render Ambiguous-width symbols (which cluster near CJK
punctuation/symbol ranges) as double-width, especially under a CJK locale
or a "wide ambiguous" terminal setting. That's a real risk to this app's
fixed-column grid for ♭/♯ specifically, even though they're in the
"simple" BMP tier.

### BMP fallback plain-shape candidates (non-music-specific stand-ins)

Also checked, as a third fallback tier of plain geometric shapes (not
music glyphs) in case neither music-specific tier renders acceptably:

```
2020..2022     ; A  # Po     [3] DAGGER..BULLET
25CB           ; A  # So         WHITE CIRCLE
25CE..25D1     ; A  # So     [4] BULLSEYE..CIRCLE WITH RIGHT HALF BLACK   (covers 25CF BLACK CIRCLE)
25E6..25EE     ; N  # So     [9] WHITE BULLET..UP-POINTING TRIANGLE WITH RIGHT HALF BLACK
26AA..26AB     ; W  # So     [2] MEDIUM WHITE CIRCLE..MEDIUM BLACK CIRCLE
2B1D..2B2F     ; N  # So    [19] BLACK VERY SMALL SQUARE..WHITE VERTICAL ELLIPSE   (covers 2B24)
```

| Codepoint | Glyph | Name | EAW | Note |
|---|---|---|---|---|
| U+2022 | • | BULLET | A | risky |
| U+25CF | ● | BLACK CIRCLE | A | risky |
| U+25CB | ○ | WHITE CIRCLE | A | risky |
| U+25E6 | ◦ | WHITE BULLET | **N** | safe, small open dot |
| U+2B24 | ⬤ | BLACK LARGE CIRCLE | **N** | safe, filled circle |
| U+26AA | ⚪ | MEDIUM WHITE CIRCLE | **W** | unsafe — Wide, breaks grid, despite looking like an obvious notehead stand-in (emoji-presentation) |
| U+26AB | ⚫ | MEDIUM BLACK CIRCLE | **W** | unsafe — Wide, breaks grid, same reason |

## East_Asian_Width default-rule check

The ticket asked specifically whether Musical Symbols' codepoints are
explicitly listed in `EastAsianWidth.txt` or fall under some default rule
for unassigned supplementary-plane codepoints. Quoted verbatim from the
file's own header (lines 19–27): `@missing: 0000..10FFFF; N` is the
universal default for anything not explicitly listed; the only documented
default exceptions are unassigned CJK Extension-A/CJK Unified
Ideographs/CJK Compatibility Ideographs ranges (default Wide) and
**unassigned codepoints in Planes 2 and 3** (U+20000–U+2FFFD,
U+30000–U+3FFFD, default Wide). Musical Symbols lives in Plane 1 (SMP,
U+10000–U+1FFFF), which that Plane-2/3 exception doesn't cover — and the
question is moot regardless, since every Tier-1 codepoint above is
*explicitly* listed with EAW=N (confirmed by direct grep, not by falling
back to any default).

## Font coverage findings

Unicode.org publishes no font-coverage data — everything in this section
is secondary-sourced and flagged accordingly.

- **Noto Music** (dedicated font, not Noto Sans Symbols) — Google Fonts'
  own specimen page states it "contains 579 glyphs and supports 559
  characters from 4 Unicode blocks: Byzantine Musical Symbols, Musical
  Symbols, Ancient Greek Musical Notation, Miscellaneous Symbols"
  (https://fonts.google.com/noto/specimen/Noto+Music, cross-checked
  against https://fontvs.com/font/google/noto-music/). This is a font
  vendor's own specimen page, not Unicode.org, but is first-party for the
  font itself.
- **Debian bookworm packaging** — confirmed directly from
  https://packages.debian.org/bookworm/fonts-noto-core (primary Debian
  source, since Raspberry Pi OS Bookworm is Debian-based): the
  `fonts-noto-core` package explicitly bundles "Noto Music" among its
  core-weight font families.
- **Whether `fonts-noto-core` ships by default on Raspberry Pi OS
  Bookworm specifically — not confirmed.** Pulling the actual
  RPi-Distro/pi-gen image-builder package lists
  (https://github.com/RPi-Distro/pi-gen, the official RPi OS build
  scripts) and grepping every stage (stage0–stage5) for font packages:
  none of stage0–stage2's explicit lists mention any font package;
  stage3/4 pull in Raspberry-Pi-specific meta-packages (`rpd-graphics`,
  `rpd-applications`, etc.) whose transitive font dependencies aren't
  traceable from public docs alone. Community reports (Raspberry Pi
  Forums, secondary) list `fonts-noto-mono`, `fonts-noto-color-emoji`,
  `fonts-noto-cjk`/`fonts-noto-cjk-extra`, `fonts-noto-ui-extra`,
  `fonts-noto-unhinted`, and `fonts-noto-extra` (via LibreOffice) as
  present by default on RPi OS Desktop
  (https://forums.raspberrypi.com/viewtopic.php?p=2365019) — but
  `fonts-noto-core`/Noto Music specifically was not named in any source
  found, so it may need an explicit `sudo apt install fonts-noto-core`,
  or may already be present transitively; genuinely unconfirmed either
  way.
- **DejaVu Sans Mono** — fileformat.info's per-block coverage view
  (secondary aggregator, not Unicode.org or DejaVu's own docs) reports
  substantial Musical Symbols block coverage (233 characters, individually
  confirming G Clef, F Clef, Double Sharp, Void Notehead, and Notehead
  Black all present:
  https://www.fileformat.info/info/unicode/font/dejavu_sans_mono/blockview.htm?block=musical_symbols)
  and Miscellaneous Symbols block coverage of 149/256 including all seven
  of ♩♪♫♬♭♮♯. Confidence here is low-to-moderate: fileformat.info's
  giant combined character-list page (as opposed to its dedicated
  per-block view) returned a contradictory "no SMP support beyond
  U+1D00" result on an earlier fetch — almost certainly a truncation
  artifact of that page rather than real data, but it means this claim
  rests on one specific page working correctly rather than a
  cross-verified source; DejaVu's own expected coverage docs page
  (`dejavu-fonts.github.io/Coverage.html`) 404'd and couldn't be used to
  corroborate.
- **Existing empirical precedent** (per this project's own status docs,
  not re-derived here): U+1D11E and U+1D122 — same block as every Tier-1
  notehead/accidental candidate above — are already confirmed rendering
  correctly live in this project's terminal displays (see `CLAUDE.md`
  Status section, and `terminal_tab_display.py`). Whether that credit
  belongs to DejaVu Sans Mono itself or to fontconfig glyph-fallback onto
  some other installed font (Noto Music, Symbola, etc.) isn't
  distinguishable from data gathered here — but it does establish that
  *something* on the actual target machine resolves this block today,
  which should temper, not resolve, the font-coverage uncertainty above
  for the rest of the block's noteheads/accidentals.

## Summary for the prototype ticket (#15)

No winner is picked here — that's #15's job, comparing candidates live in
a real terminal. What this survey hands off:

- **Tier 1 (Musical Symbols, SMP)**: U+1D157 (void/open notehead), U+1D158
  (black/filled notehead) — both EAW=N (safe, single-width, confirmed by
  explicit listing not default). No plain sharp/flat/natural exists in
  this block; any Tier-1 build must borrow Tier 2's ♭/♮/♯ for
  accidentals. Font coverage plausible (Noto Music ships in Debian's
  `fonts-noto-core`, DejaVu Sans Mono appears to cover it per a
  secondary source) and tempered by the two clef glyphs' own working
  precedent in this exact block, but not fully confirmed for Raspberry
  Pi OS Bookworm's actual default install.
- **Tier 2 (Miscellaneous Symbols, BMP)**: ♭ U+266D, ♮ U+266E, ♯ U+266F
  for accidentals — but ♭ and ♯ are EAW=Ambiguous (real double-width risk
  on some terminals/locales), while ♮ is Neutral (safe). ♩♪♫♬ are
  rhythm-notation glyphs, out of scope for a pitch-only notehead, and
  also mostly Ambiguous-width. No plain notehead-shaped glyph exists in
  this block at all.
- **Tier 3 (BMP geometric fallback)**: U+2B24 (⬤, filled) and U+25E6 (◦,
  open) are both EAW=Neutral (safe) and visually read as noteheads despite
  not being music-specific glyphs — a plain-shape safety net if either
  music tier proves unreliable in practice. U+26AA/U+26AB (⚪/⚫) look like
  even better notehead stand-ins visually but are confirmed EAW=Wide and
  would break the grid — ruled out on data alone, not taste.
