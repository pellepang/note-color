# Sampler engine: zones, velocity layers, pitch-shifting, loops, choke, sample import (map #99, ticket #116)

The *what* is ticket #116 and decision #106 (plus its #107 velocity-layer
addendum) and is not repeated here. This entry records the choices the
ticket left to the build, and how each was verified on a machine with no
speakers in the loop -- every claim below is a numerical assertion in
`tests/test_sampler.py`/`tests/test_wav_io.py`, none of it was listened to.

**A hand-rolled RIFF reader, not the stdlib `wave` module.** The ticket
said to prefer `wave`; the deliverable that actually matters is "no new
dependency", which `wav_io.py`'s ~120-line chunk walk also satisfies. Two
concrete gaps in `wave` forced the choice: it raises `wave.Error: unknown
format: 3` on any IEEE-float WAV (what most editors export by default
today), and it exposes no way to reach the `smpl` chunk where a sample's
loop points live -- both load-bearing for a sampler, neither patchable
from outside. `soundfile` was ruled out for the same reason `librosa` is
kept off the live path: it is present here only transitively through the
`[batch]` extra, and the sampler must work on a base install. The reader
is cross-checked in tests against files `wave` itself wrote (including a
stereo one), so it is held to the stdlib's output rather than only to its
own writer.

**Loop points come from the WAV, not from the patch.** Decision #106's
zone schema has no loop fields. Adding some was considered and rejected:
a loop is a property of one recording's internal structure, and putting
it into the mapping document that references the recording means moving
the sample to another kit requires re-entering its loop by hand. The
`smpl` chunk's first loop is where every sampler and sample library
already puts it. The cost is that a plain WAV with no `smpl` chunk cannot
loop until something writes one -- acceptable for v1, where the primary
use is a drum kit and every drum is a one-shot. Only the *first* loop is
honoured; a voice can only be in one loop at a time and picking any other
would be a guess.

**No resampling on load.** A source at 22050 or 48000Hz is kept at its
own rate; `sampler.playback_ratio()` multiplies the semitone transpose
`2**((pitch - root_key)/12)` by `sample_rate / engine_rate`, so the rate
mismatch rides the same single linear-interpolated read the pitch shift
already needs. Resampling on load would be a second, lossier interpolation
pass on top of one that has to happen anyway, and would move loop points
off integer frames. Verified: a 440Hz tone recorded at 22050, 48000 and
96000Hz each measure 440Hz by FFT when played at its root key through a
44100Hz engine, and last their real one second.

**Pitch shift is rate change, so it time-stretches.** An octave up plays
in half the time. That is what a hardware sampler does and the test suite
asserts it on purpose, so that a later "fix" into a time-preserving
pitch shift is a deliberate decision rather than an accident.

**Note-off means two different things, decided by whether the sample
loops.** A looping zone sustains until note-off and then fades over
`SAMPLER_RELEASE_SECONDS` (80ms); a one-shot -- any sample without a loop,
i.e. every drum -- *ignores* note-off and plays to its natural end. The
alternative (every zone releases on note-off) would truncate a kick to
the length of a QWERTY tap now that #118 delivers real key-release
events, which is the one thing a drum pad cannot do. The alternative in
the other direction (one-shots ignore note-off, but a one-key-wide zone
is what marks a one-shot) would be a kit code path by another name,
which #99 rules out. Either way the note-off is *recorded* (`released`
becomes True) so `sound_engine.select_steal_index()` still prefers a
released one-shot when the budget is full. A per-zone `loop_mode` field
(SFZ's `one_shot`/`no_loop`/`loop_continuous`) is the obvious extension
if a long non-looped sample ever needs to stop on release; not added
now, per #106's own "no speculative fields" posture.

**Choke is a 6ms fade, not a hard cut, and lives in the engine.** Dropping
a waveform mid-cycle to zero is a click; `SAMPLER_CHOKE_SECONDS` is long
enough not to click and short enough to still read as a cutoff (the
release, by contrast, is 80ms). A choke arriving during a release
shortens it and a release arriving after a choke never lengthens it --
the shorter fade always wins. Choke-group bookkeeping is in
`SamplerEngine`'s own active-voice list rather than `VoiceManager`
because a choke group is a property of the *patch's zones*, and the
voice manager deliberately knows nothing about patches (it counts voices
and enforces a budget). A voice the manager has already stolen is simply
never rendered again, so a stale entry in the engine's list is harmless
and is pruned on the next note-on. `choke()` is called from the note-on
thread while `render()` may be running in the audio callback, without
the manager's lock; the race costs at most one block of latency on the
cut, the same exposure `ToneVoice.note_off()` already has.

**A missing sample is a `SilentVoice`, not `None`.** `Engine.note_on()`'s
Protocol returns a `Voice`; making the failure case a null return would
push a `None` check into every caller of the seam. `SilentVoice` is
finished from birth, so the manager reclaims its slot on the very next
block and a kit full of missing samples costs no polyphony. The same
object covers "no zone matches this key" -- a key the user never mapped
genuinely has no sample, and papering over it with a nearest-key
fallback would be a mapping the user did not choose (the velocity-band
fallback in `select_zone()` is different: *that* gap is an accident of
layering, not a choice). A file that exists but does not decode (an MP3
renamed to `.wav`) counts as unavailable too -- `SamplerEngine.
unavailable_samples()` is `patch_format.missing_samples()` plus the
cache's own read result -- so it renders as unavailable rather than
silently never sounding.

**Sample import never clobbers.** `wav_io.import_sample()` copies into
`samples_dir()` and returns the bare name; a name already taken by a file
with *identical* bytes is reused (re-importing is idempotent), while
different bytes wanting the same name get `name_1.wav`, `name_2.wav`...
Overwriting is the one failure mode a shared-by-bare-name scheme has to
defend against, since every other patch referencing that name would
change sound. Import is also the one place in this change that raises
(`SampleImportError`): a user is standing there waiting to be told what
went wrong, unlike the playback-path readers, which return `None` so a
broken sample degrades exactly like a missing one.

**Stereo is averaged to mono**, not left-channel-only: the engine is mono
today (map #99 leaves stereo unspecified), and dropping a channel would
silently lose whatever was panned into it.

**Verification numbers.** Pitch-shift error at 0/±5/±7/±12 semitones and
across three source rates: within 0.3% of the theoretical ratio (linear
interpolation's own spectral error on a pure sine). Loop-seam continuity:
the largest sample-to-sample step over one second of a 20-cycle sine
loop is within 2% of the sine's own slope bound, i.e. no discontinuity at
the seam. Choke: 264-265 nonzero frames after the cut at 44100Hz (6ms).
Gain: -6/-20/+6dB give RMS ratios 0.501/0.100/1.995. Choke via the full
`SoundEngine._callback()` path leaves exactly the striking voice in the
manager after the fade.
