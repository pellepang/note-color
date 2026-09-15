# 70. Realtime scheduling for the audio callback thread, a read-only CPU governor check, and what re-measuring on this machine could and could not show (issue #232)

#231 diagnosed #229's callback spikes (60.2% mean against a 266.8% max at
16 voices, with real driver-reported xruns) as CPU frequency scaling, not
arithmetic: `corr(block duration, executing core's clock speed) = -0.87 to
-0.89`, and `sound_engine.py` asked the OS for nothing but ordinary
`SCHED_OTHER` scheduling. This ticket is the fix #231 sized but did not
build, plus the re-measurement #232 asked for.

## 1. What was built

`src/notecolor/audio/sound_engine.py` gained two independent, small
pieces, both landing only in `SoundEngine` (`_try_realtime_scheduling()`,
`_detect_cpu_governor()`, and the methods that call them):

**Realtime scheduling.** `SoundEngine._callback()`'s first invocation now
calls `_ensure_realtime_priority()`, which asks the OS for `SCHED_FIFO`
priority 10 on whichever thread is calling -- the real audio thread,
since PortAudio spawns and owns it and this is the only code that ever
runs there. `pid=0` to `os.sched_setscheduler` means "the calling thread,"
not "the process" (`man 2 sched_setscheduler`), which is the whole trick:
there is no Python `threading.Thread` object for PortAudio's own thread to
set a priority on ahead of time, so the request has to happen from inside
the callback itself, once, on its first real invocation.

**CPU governor detection, read-only.** `ensure_started()` calls
`_check_cpu_governor()` before opening the stream: a plain read of
`/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor`. If it reads
`powersave`, a message is printed once. Nothing is ever written back.

Both outcomes are also stored as plain attributes
(`realtime_priority_active`/`_message`, `cpu_governor`/`_message`) rather
than only printed, so a caller (a future status bar, a smoke-test script)
can read them without scraping stdout. `realtime_priority_active` starts
`None` ("not attempted yet") and becomes `True` or `False` exactly once
per stream lifetime; `stop()` resets it to `None`, because a restart opens
a new stream backed by a new OS thread whose scheduling starts over.

## 2. Did `sounddevice`/PortAudio already offer this? No -- checked, not assumed

`sounddevice.OutputStream.__init__`'s full parameter list was read
directly (`samplerate`, `blocksize`, `device`, `channels`, `dtype`,
`latency`, `extra_settings`, `callback`, `finished_callback`, `clip_off`,
`dither_off`, `never_drop_input`,
`prime_output_buffers_using_stream_callback`) -- no scheduling or priority
argument anywhere. PortAudio itself does carry thread-priority logic, but
it is host-API-internal and used automatically only by its JACK backend
(JACK's own server already runs its process at `SCHED_FIFO` and PortAudio
inherits that context); the ALSA and PipeWire host APIs this project runs
on (`sounddevice`'s default on this machine, per decision 65/67's own
measurements) do not request anything beyond ordinary scheduling. So
reaching for `os.sched_setscheduler` directly, once the library-native
option was confirmed absent, is the right order the issue asked for --
not a shortcut taken instead of checking.

## 3. Why this degrades, and what the user is told

**The overwhelmingly common case is denial, and denial must be silent
failure of the *request*, never of the *synth*.** Linux caps every
thread's realtime priority at 0 (`RLIMIT_RTPRIO`) by default; raising it
needs an administrator's `/etc/security/limits.d` rule or membership of an
`audio`/`realtime` group. This is not a hypothetical branch reasoned about
in the abstract -- it is what this exact development machine does right
now:

```
$ ulimit -r
0
$ python3 -c "import os; os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(10))"
PermissionError: [Errno 1] Operation not permitted
```

`_try_realtime_scheduling()` catches exactly this (`PermissionError`),
any other `OSError` a kernel might raise, and the platform simply not
exposing `os.sched_setscheduler` at all (no Unix realtime scheduling API,
e.g. Windows) -- all three collapse to the same outcome: `active=False`,
a one-line message ending "running at normal priority", and the callback
proceeds exactly as it did before this ticket. No exception ever reaches
`sounddevice`'s C callback trampoline; a Python exception escaping a
`sounddevice` callback tears down the stream, so the one hard requirement
here is that this function truly never raises, checked with a dedicated
test for each of the three denial shapes plus one for outright success.

Told to the user via the same `print(f"[audio] ...")` convention
`audio_capture.py` already uses for driver status (`[audio] status:
{status}`) -- a plain, greppable, one-time line, not a dialog or a
required acknowledgement. Nothing about note-on, note-off, voice stealing,
the effects bus or the patch graph changed; a user who never gets realtime
priority gets the identical synth this project shipped before #232, just
without the OS's help meeting the callback deadline.

## 4. The CPU governor: detect and tell, never touch

Flipping a system-wide governor from inside an application needs root and
would silently change every other process's power/performance tradeoff on
the machine for as long as this app happened to be running -- an
intrusive, hard-to-reverse side effect of exactly the kind this project
avoids everywhere else (`SessionState`/`SoundEngine` never touch a system
setting; `config_store` never writes outside its own file). It would also
not compose: two instances of this app, or this app alongside a DAW,
would fight over one shared, global knob. Detecting `powersave` and
saying so, once, is the whole of what decision #232 asked to be decided
here, and it is decided: **never touch it, always detect and report.**

## 5. Portability -- the hard constraint, and what could not be verified

Every piece above was written to degrade on axes that differ across
Raspberry Pi class hardware, embedded Linux, and desktops, without
assuming any of them:

- **No `os.sched_setscheduler`** (a platform with no POSIX realtime
  scheduling exposed to Python) -- same message, same fallback, no crash.
- **No `cpufreq` sysfs at all** (`_detect_cpu_governor()`'s `OSError`
  path) -- some minimal Pi/embedded images ship without it, and a
  container may not have `/sys` mounted through. Silent: `governor=None`,
  no message. Not treated as a warning, because absence of the file says
  nothing about whether the hardware is fine or not.
- **A governor other than `powersave`** (`ondemand`, `performance`,
  `schedutil` -- the last is what modern Pi OS images and recent desktop
  kernels default to) -- silent, no message, nothing assumed about being
  safe or not; only the one governor #231 actually found and named is
  called out.
- **No `rtprio` limit configured** (the default on nearly every
  distribution, Pi OS included) -- the graceful-degradation path this
  whole ticket is built around.

**CPU affinity was considered and deliberately not added.** #232's issue
text noted `sound_engine.py` requests neither realtime priority nor CPU
affinity; only the former was built. Pinning the callback to a specific
core index is a *desktop* assumption (a fixed, known core topology) that
actively works against the portability constraint -- a Raspberry Pi's
core count, big.LITTLE-style asymmetric cores (recent Pi and most ARM
desktop-class SoCs), and a user's own CPU isolation setup (`isolcpus`) all
vary in ways a hardcoded affinity mask cannot safely generalise across,
and affinity does not address the DVFS ramp-lag mechanism #231 identified
anyway -- a pinned core still downclocks under `powersave` between
callbacks. Left out rather than built speculatively.

**What could not be verified, and needs hardware or privileges this
session does not have:**

- **No Raspberry Pi available.** Every governor-file-absent and
  no-cpufreq-subsystem path above is reasoned from documented Pi OS
  configurations, not exercised on real Pi hardware. Decision 55 already
  settled that the Synth View itself targets desktop hardware first (Pi
  is not a constraint *for the Synth View specifically* -- see decision 55
  "Portability" section) but this module (`sound_engine.py`) is shared by
  every audio-producing tool in the app, including ones decision 55 never
  scoped to desktop-only, so the degrade paths above are written to be
  Pi-safe regardless.
- **No root and no passwordless `sudo` on this machine**
  (`sudo -n true` fails; `RLIMIT_RTPRIO` is 0 and no
  `/etc/security/limits.d` rule or `audio`/`realtime` group grants it) --
  so **the actual benefit of realtime scheduling could not be measured on
  this machine.** Every re-measurement below ran with the request denied,
  exactly like every user without configured privileges. This is the
  single biggest gap in this ticket: the fix is built, tested for
  correct degradation, and reasoned from the standard Linux pro-audio
  pattern (JACK/rtkit's own), but its *quantitative* effect on the
  frequency-correlated spike #231 found is unverified pending a machine
  where it can actually be granted (root, a configured `rtprio` limit, or
  `audio`/`realtime` group membership) and the governor flippable to
  `performance` for the controlled A/B #231 also could not run.

## 6. Re-measured for #232, with the code in place, on this machine

Same harness, same three patches, same convention (first 10 blocks of
each run dropped as warm-up), `scripts/mod_callback_cost.py --patch
{baseline,realistic,worst} --seconds 10`, machine unchanged
(i5-7300U, 4 cores, `powersave` on all four -- confirmed directly again
this session) since #229/#231's own runs.

**Every run below shows the code correctly detecting `powersave` and
correctly failing to obtain `SCHED_FIFO` (`PermissionError`, exactly the
"denied" path §3 describes) -- confirmed by the printed `[audio]` lines on
every single run.** These are therefore measurements of the
*graceful-degradation path*, not of realtime scheduling's benefit --
see §5.

**Load average was not this session's own to control down to #229's
0.5-1.0 window.** This is an interactive desktop with a browser and other
GUI applications the user was actively running; after letting it settle
before each block of runs, it read 1.2-1.6 (1-minute) with the 5- and
15-minute averages still descending from ~3.9/2.5 (recent unrelated
activity before this session started measuring). The same leftover
`visualnote` process #229/#231 both found and left running (steady ~30%
of one core, PID unchanged in kind though not in number since a new one
was spawned since) was present throughout and was, per that same
precedent, left running rather than killed.

| patch | runs | mean | p99 | max | xruns |
|---|---|---|---|---|---|
| baseline | 3 x 10s | 45.7% | 108.5%\* | 193.1%\* | **4 flags across 2 of 3 runs** |
| realistic | 3 x 10s | 52.7% | 159.2%\* | 225.9%\* | **1 flag in 1 of 3 runs** |
| worst case | 5 x 10s | 61.1% | 176.4%\* | 249.7%\* | **5 flags across 3 of 5 runs** |

\*p99 and max varied run to run (see raw runs below); the mean across
runs is given, not a percentile-of-percentiles.

Raw runs, in order:

- baseline: 48.2/44.6/110.5/245.9 (1 flag), 43.9/41.5/87.5/109.5 (0),
  45.1/41.6/127.5/223.9 (3)
- realistic: 52.9/48.7/139.6/212.4 (1), 52.8/47.7/182.2/231.2 (0),
  52.5/49.0/155.8/234.2 (0)
- worst: 61.7/56.3/192.9/258.2 (2), 62.3/56.9/190.3/285.8 (3),
  59.3/55.3/150.4/227.6 (0), 61.6/56.9/189.5/256.8 (0),
  60.8/56.5/159.1/220.2 (0)

**Read against #229's clean figures (60.2% mean, xruns in 4 of 5
worst-case runs, baseline/realistic 0 xruns in 3 runs each):** the mean
figures land within a few points of #229's own numbers (baseline 45.7%
vs. 47.1%, realistic 52.7% vs. 53.8%, worst 61.1% vs. 60.2%) -- consistent
with "nothing changed," which is exactly what denied realtime priority
predicts. **The one real difference is that baseline and realistic each
produced an xrun this session, where #229's clean run produced none in
either.** The straightforward reading is not that this code made anything
worse -- ambient load this session (1.2-1.6, versus #229's 0.5-1.0) was
higher and not fully controllable (see above), and #231 already
established that xrun exposure is sensitive to a governor-ramp event's
timing against the machine's own recent idle history, not to the patch
alone. Read the other way, it directly reinforces #231's own caution
about the realistic patch (see §7 below): a patch that ran three clean
10-second samples before can still xrun on the fourth kind of sample,
under marginally different ambient conditions, with the identical code.

Decision 67's cost section is updated with these figures, marked
superseded rather than replacing #229's, per that section's own existing
convention of keeping prior measurements rather than deleting them.

## 7. The two questions this was for

**Is decision 55's revisit genuinely required?** **Still not decisively
required, and this ticket could not move that answer either way.** #231's
read stands: the compiled-core seam (#145) would shorten each compute
burst and so reduce a burst's exposure to a governor ramp window, but it
does not touch the mechanism (DVFS ramp lag under `SCHED_OTHER`), and the
fix that *does* address the mechanism is the one built here -- which
this exact machine cannot grant. The honest state of decision 55's
question after this ticket is: **the fix aimed at the actual cause now
exists and degrades safely everywhere, but its effect is unmeasured**,
which is a different, narrower gap than #231 left (a fix not yet sized)
and a strictly better place to be in, but it is not "confirmed clear."
Closing this for good needs one of: a machine with `rtprio` already
configured (a proper pro-audio Linux setup, or root to add the
`/etc/security/limits.d` rule and re-log in), to see whether `SCHED_FIFO`
collapses the frequency correlation the way #231 predicted; or a Pi to
extend the same measurement to the hardware class CLAUDE.md's
portability constraint actually names. Neither was available this
session.

**Does #228 (per-sample interpolated delay reads) have real headroom
now?** **No -- if anything, less confidently than before.** #231's
caution was that the realistic patch's clean 0-xruns-in-3-runs was
"not proven headroom, merely unexercised" against the same DVFS
mechanism. This session's realistic patch produced a real xrun on its
first of three runs -- the same patch, the same code, a different sample
of the same mechanism. That is the unexercised tail #231 warned about,
now exercised. #228 should not proceed on the strength of any clean
sample of this patch; the blocker remains the identical DVFS mechanism,
unresolved by this ticket for the reason given above (no way to measure
whether realtime scheduling would have prevented it, on this machine).
The worst-case patch continues to show no scenario supporting #228
proceeding as planned.

## What fought the spec, and what is left open

- **The realtime-scheduling request's actual effect is unverified** --
  the central open item; see §5's "no root/no Pi" paragraph. Everything
  else about it (that it is attempted correctly, degrades correctly on
  every denial shape tried, and never destabilises the callback) is
  tested directly; see `tests/test_sound_engine.py`'s
  `TestRealtimeSchedulingGracefulDegradation` and
  `TestCpuGovernorDetection` classes.
- **CPU affinity was considered and not built** -- see §5's own
  paragraph on why.
- **No Raspberry Pi hardware available this session** -- every claim
  about Pi behaviour above is reasoned from documented configuration,
  not measured.
- **Ambient load during re-measurement (§6) was not this session's to
  fully control** -- an interactive desktop machine with a browser and
  other GUI applications running throughout, settled to 1.2-1.6 rather
  than #229's 0.5-1.0, plus the same long-lived leftover `visualnote`
  process #229/#231 both disclosed and left running. Reported rather
  than edited around, per this repo's own established precedent for this
  exact confound.
