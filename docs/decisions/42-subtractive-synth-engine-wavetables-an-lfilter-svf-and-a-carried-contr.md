# Subtractive synth engine: wavetables, an `lfilter` SVF, and a carried control grid (map #99, ticket #113, research #103, decision #111)

**Mip-mapped wavetables, despite being the slower option.** #103 measured
PolyBLEP oscillators at -25..-53 dBc of alias energy and mip-mapped
wavetables at -86..-118 — 60-90 dB cleaner — and #100 corrected the
accompanying cost claim: wavetables are cheaper per *voice* but ~10%
slower per *block* once vectorized across voices, because each voice
gathers from its own mip band rather than sharing one polynomial. The
cleanliness is why they were chosen anyway; alias grit is the single
most identifiable "cheap software synth" artifact, and this project has
no budget pressure that 10% of a block would relieve. Measured here at
the shipped 4096-sample table: **-105 to -113 dBc across 55-1760 Hz**,
inside #103's range, against **-30 dBc** for an unbandlimited saw
rendered by the same measurement.

That measurement has a trap in it, recorded because it cost real time
during the build: a *narrow* harmonic-exclusion mask reports the 4096
table at ~-37 dBc. That figure is the Hann window's own leakage skirt
around the strong low-order harmonics, not the oscillator. The alias
floor is only meaningful with a guard band wide enough to clear the
window's skirt (`HARMONIC_GUARD_HZ = 20` in the tests), and a control
measurement on a signal known to be dirty is what keeps the metric from
being vacuous — both are in `tests/test_synth_engine.py`.

**Tables are built by inverse FFT, not by summing sine arrays.** The
#100 prototype summed one NumPy array per partial in a Python loop; a
band can hold 337 partials, and #103 flagged the resulting startup cost
as unmeasured. `np.fft.irfft` of the exact harmonic series produces the
identical table for a whole band set in ~1ms. They are also deliberately
*not* peak-normalized: Gibbs overshoot shrinks with partial count, so
normalizing each band would make the sparse high bands louder than the
dense low ones, and a level that changes as you play up the keyboard is
worse than 9% of headroom.

**A square has no table of its own.** It is two reads of the *saw* table
a duty cycle apart (`pulse(p,d) = saw(p-d) - saw(p) + (2d-1)`, the two
ramps cancelling to flat ±1). This is exactly as band-limited as the saw
table it reads and makes `pulse_width` a continuous parameter — a table
set per pulse width would be one set per parameter *value*, and PWM
would be impossible. It inherits an overshoot from each of the two saws,
so its Gibbs peak is ~1.18 rather than the saw's ~1.09; that is the
correct behaviour of the construction, not a bug.

**The filter is a 2-pole SVF through `scipy.signal.lfilter`, and the
Moog ladder is unavailable.** #103/#111 settled the SciPy question; what
this build adds is the confirmation that the bilinear transform with the
`g = tan(pi*fc/sr)` prewarp puts the corner exactly where the patch asks:
measured **-3.010 dB at cutoff** at Butterworth damping, at 100/440/1000/
5000 Hz, with 12 dB/octave slopes and a peak gain of exactly `1/k` at
maximum resonance (measured 20.01 dB against a predicted 20.00). The
ladder is not merely unimplemented: its saturation sits *inside* the
feedback loop, so it is not a fixed-coefficient LTI recurrence and
`lfilter` cannot run it at all. That is a known limitation, not an
oversight.

**Resonance is linear in damping `k = 1/Q`, not in Q.** The knob's
audible effect is how peaked the response is at cutoff, which is `1/k` in
dB; linear in Q would do almost nothing for the first three quarters of
the travel. `SYNTH_DAMPING_MAX = sqrt(2)` (Butterworth — flat, no peak,
which is what "no resonance" should mean) down to `SYNTH_DAMPING_MIN =
0.1` (Q = 10, +20 dB). **Self-oscillation is deliberately unreachable**:
a *linear* SVF at k = 0 is a marginally-stable oscillator with unbounded
output, not the musical squeal an analog ladder gives — that behaviour
comes from the nonlinearity, which this topology does not have. Offering
it here would only offer a way to blow up the mix.

**The control sub-block grid is carried across `render()` calls.** #103
settled 64 samples as the price knee (+1.98ms at 32 voices versus
+0.40ms for block-rate coefficients, corroborated by FluidSynth's own
`FLUID_BUFSIZE = 64`). What it did not settle is what happens when a
block is not a multiple of 64. The first implementation restarted the
sub-block grid at every `render()` call, which re-times every filter
coefficient update against the *block boundary* instead of against the
note — so the voice's output depended on how its samples happened to be
cut up. Measured on a ragged 37/291/672 split against one 1000-sample
render: **37.7% of peak divergence without the carry-over, 8.9% with it**
(a 4.2x reduction; the residual is the unavoidable short sub-block at
each block's end). With this app's own constant 512-sample block the
offset is always 0 and the carry is identity, so this costs the real
audio path nothing — it matters for a short final callback block, a
reconfigured `SoundEngine.block_size`, and any test rendering ragged
chunks. Sub-block-aligned splits are bit-exact (measured 1.5e-11).

**The amp envelope is audio-rate; the filter modulation is not.** An amp
envelope is a plain elementwise gain costing ~0.13% of a block, while a
per-block-constant gain is audibly steppy on a fast attack. Filter
modulation has to be control-rate regardless, because `lfilter`'s
contract is constant coefficients per call. `DahdsrEnvelope` therefore
exposes `block()` (materializes samples) and `advance()` (walks without
materializing, returning the control-rate value) over **one shared
segment walk**, so the two rates can never drift apart.

**DAHDSR, and `sustain <= 0` ends the note.** SF2's delay and hold ahead
of a conventional ADSR, cheap and worth having since the same voice model
is meant to host #117's SF2 engine. A zero sustain finishes at the end of
decay rather than sitting at silence forever — a voice that can never be
heard again should give its polyphony slot back, which is what hardware
synths and FluidSynth's own -100 dB voice kill both do. Release computes
its rate *at note-off* from wherever the envelope then is, so the fade
always takes exactly `release` seconds regardless of which stage it
interrupted, and it is idempotent: a second note-off must not restart the
fade, or repeated note-offs would ring a note on indefinitely.

**Everything modulates cutoff in octaves, never in Hz** — pitch is
logarithmic, and a fixed Hz offset means something completely different
at C1 and at C7. Velocity *closes* the filter at low velocity rather than
opening it above nominal, so velocity 1.0 is always the patch's stated
cutoff and never an over-bright surprise. The amp LFO likewise only
attenuates from unity rather than boosting past it, so a tremolo can
never push a voice into the master soft-clip on its own.

**Saw and square LFOs start at an extreme, and that is not a defect.**
Sine and triangle start at 0, so an LFO with either fades in from no
modulation at note-on. Neither a saw nor a square *has* a zero at phase
0 — a square that started at 0 would not be a square — so `Lfo.delay`,
not the waveform's starting value, is what holds modulation off for those
two. (An earlier docstring in this module claimed all four started at
zero; it was wrong, and the test that asserts the real behaviour is what
found it.)

**Verified numerically, with the machine muted.** All 144 new unit tests
run with no audio device: oscillator cleanliness by FFT against an alias
floor (with a dirty control signal to prove the metric bites), the filter
against the analytic response of its own coefficients through
`scipy.signal.freqz`, pole radii checked inside the unit circle at every
cutoff a modulation can reach (including past Nyquist, where the clamp
takes over), envelope stages asserted sample by sample at a 1000 Hz rate
where one sample is one millisecond, and rendering one block of 1024
versus two of 512 as the direct test that voice state survives a block
boundary — which no voice with a reset oscillator phase, filter `zi`,
envelope stage or LFO phase can pass. Measured throughput on this machine
at 44100 Hz / 512-frame blocks (11.61ms deadline): **16 voices at 5.14ms
per block**, 44% of deadline, consistent with #100's 24-40 voice figures.
**Not verified: that any of it sounds correct.** The machine was muted for
the whole build. Timbre, the init patch's voicing, and whether the 64-
sample control rate is genuinely free of stepping on a fast filter sweep
all need a listening pass.
