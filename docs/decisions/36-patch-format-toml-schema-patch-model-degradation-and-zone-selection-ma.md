# Patch format: TOML schema, patch model, degradation, and zone selection (map #99, issue #115)

`patch_format.py` implements decision [#106](https://github.com/pellepang/note-color/issues/106)
and its [#107](https://github.com/pellepang/note-color/issues/107)
velocity-layer addendum: one hand-editable TOML file per **Patch** under
`~/.config/note-color/patches/`, declaring `engine = "synth" | "sampler"
| "sf2"`, with the ~30-scalar parameter set issue #103's research doc
(`docs/research/subtractive-synth-numpy.md` §5) established. Every
resolved point of #106 lands verbatim and isn't re-argued here — no
version field, every field optional with a documented default, unknown
keys ignored, samples referenced by bare name, MIDI CCs documented but
never stored. What follows is only the handful of implementation
judgments #106 didn't settle.

**Degradation is three-layered, not one.** #106 said "loads as far as it
parses and falls back to defaults rather than refusing to open," which
`config_store.py`'s own all-or-nothing `except TOMLDecodeError: self.
_data = {}` doesn't actually achieve — that reproduces *defaults*, not
"as far as it parses." A patch is a much bigger, hand-edited document
than a keybind overlay, so losing a whole 40-line kit to one typo in its
last zone is a materially worse outcome than losing three keybind
remaps. `parse_patch_text()` therefore first tries the whole file, and
only on a decode error re-parses the longest *leading prefix of lines*
that is valid TOML, keeping whatever that yields. A broken table header
costs you everything after it and nothing before it. Layer two is
per-field: a wrong-typed value falls back to its default rather than
raising (`_number`/`_integer`/`_choice`/`_text` all degrade, never throw;
`bool` is explicitly excluded from the numeric coercions, since Python's
`bool` is an `int` and `polyphony = true` should mean "nonsense, use the
default", not 1). Layer three is `load_patch()` itself: an unreadable or
absent file yields an all-defaults patch named after the file, exactly
`ConfigStore`'s posture toward a missing `config.toml`.

**Out-of-range values are clamped, not defaulted.** #106 documented
ranges but not what happens outside them. Clamping is chosen over
defaulting because it preserves the user's evident *intent* — someone
who hand-types `cutoff = 999999` wants the filter wide open, and snapping
that to the 12000 Hz default would be a surprising, silent reversal of a
deliberate edit, where clamping to 20000 gives them what they meant.
Enumerated fields (waveform, filter type, LFO destination, engine) have
no "nearest" to clamp to and so fall back to their default instead. This
also means no downstream engine (#113/#116/#117) ever has to defend
against a negative cutoff or a zero-voice polyphony — the model is the
validation boundary.

**Reversed ranges are normalised, not rejected.** A zone written
`low_key = 60, high_key = 40` is unambiguous about which keys it means,
so `Zone.from_toml()` swaps rather than dropping the zone; same for a
reversed velocity band.

**Zone selection's tie-breaking chain.** #106's addendum settled the
rules (key range *and* velocity band; narrowest wins; nearest band rather
than silence) but not their precedence or how genuine ties resolve.
`select_zone()` orders candidates by `(velocity_distance, velocity_span,
key_span, file order)` over a single pass: a zone whose band actually
contains the velocity has distance 0 and so always beats a merely-nearest
one; among those, the narrowest band wins (a hard-snare layer beats the
catch-all zone under it); a velocity-tie breaks on the narrower key span,
which is the same "more specific mapping wins" instinct one dimension
over; and file order is the last resort so a hand-written patch behaves
identically on every load. The key-range test comes first and has *no*
nearest-fallback: a key outside every zone is a mapping the user chose,
not a gap to paper over — the "never fall silent" rule is explicitly
about velocity landing in an unmapped *band*, and extending it to keys
would make a 3-key kit sound on all 128.

**`[sf2]` is a section #106 didn't enumerate.** #106's section list
covers the synth and the sampler, but an `engine = "sf2"` patch is
meaningless without saying *which* **Program** it selects. `Sf2Selection`
(`soundfont` / `bank` / `preset`) fills that in, using CONTEXT.md's
already-pinned Program vocabulary and the same bare-name shareability
rule samples follow — an SF2 file with a `/home/...` path in it is no
more shareable than a sample with one.

**Unknown effect types survive a round trip.** `EffectSpec` keeps
`type` plus every other key verbatim in `params`, and `patch_to_toml()`
writes them back. #104's chain skips a type it can't render, but the
*format* must not drop it: a patch written by a build that grew a reverb
would otherwise be silently stripped of that reverb by an older build
that opened and re-saved it — data loss disguised as forward
compatibility. Ignoring at render time and preserving at storage time are
different questions with different right answers.

**Saving writes every field explicitly, not just non-defaults.** This is
where the analogy with `config_store.py` deliberately stops.
`config.toml` is a sparse *overlay* over a running program's constants,
so writing only what the user set is the whole point; a patch *is* the
sound, and is meant to be opened in an editor and changed by hand — a
fully-populated file is self-documenting (every knob visible with its
current value) where a three-line one tells a reader nothing about what
else exists. Only the sections that apply to the declared engine are
written, so a kit file isn't padded with oscillator settings that can
never do anything.

**Sample names are basename'd on read as well as on write.**
`Zone.from_toml()` and `sample_path()` both reduce whatever they're given
to a basename, so a hand-edited (or maliciously shared) patch containing
`sample = "../../etc/passwd"` can only ever resolve inside
`samples_dir()`. #106's bare-name rule is about shareability; this makes
it also hold as a containment property, at no cost.

**Per-field defaults live on the dataclasses, not in `config.py`.** This
repo's convention puts tunable constants in `config.py`, but these aren't
tunables the app reads — they're the *schema's* documented defaults, one
per field, meaningful only next to the field they belong to and needed by
`from_toml()` and the dataclass constructor alike. Splitting them into
`config.py` would put a patch's documentation two files away from its
model for no gain. `patch_format.py`'s module docstring is the schema
reference; `config.py` stays the home of what the *engines* tune.

**Where this stops.** `patch_format.py` renders no audio and imports no
audio library — it is the format, the model, load/save, defaults,
degradation and zone selection only. `Patch.voice.polyphony` is the
patch's own preference; reconciling it with decision #105's process-wide
`[preferences]` polyphony cap belongs to the voice manager (#112), not
here. `zone_available()`/`missing_samples()` report a missing sample;
leaving that zone silent and rendering it as unavailable is #116's job.
