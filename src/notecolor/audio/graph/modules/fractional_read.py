"""The per-sample fractional ring read both delay modules grew for #228.

A delay whose time is being modulated cannot read one contiguous window at
a single offset the way `delay.py` and `short_delay.py` always did: the
offset is a *different, fractional* number of frames on every sample of the
block. Rounding it to whole frames instead is precisely the thing decision
67 refused to ship -- the read position then jumps a whole sample at a time
and the delay crackles on every jump, which is the opposite of the smooth
pitch sweep a chorus or a flanger is made of.

So the read becomes a gather plus an interpolation, exactly the shape
`oscillator.py`'s `_read_into()` already uses for its wavetable. This file
is that read, factored out of both modules rather than written twice,
because the two would drift and because the interpolation kernel is the
one thing in it that is likely to change (see "Why linear" below).

**It owns its own scratch.** Contract rule 2 forbids allocating in the
callback, and a fractional read needs seven block-sized temporaries
(position, floor, fraction, two index arrays, two taps). Bundling them in
one object that `allocate()` fills means each module gains one attribute
instead of seven, and means "did we allocate in `process()`" has one place
to be wrong rather than two.

## Why linear, and what it costs

Linear interpolation between the two ring samples either side of the
fractional position is the usual first answer, and #228 says to reach past
it (all-pass, cubic) only if its high-frequency loss is *audible at
modulation depth*. It is worth being precise about what that loss is: a
linear interpolator is a two-tap FIR whose response depends on the
fractional part, from perfectly flat at frac = 0 to a gentle lowpass at
frac = 0.5, where it is about -3.9 dB at Nyquist and under -0.1 dB below
about 6 kHz. On a chorus or flanger the *wet* path therefore loses a
little air at some sweep positions -- and on the modules here that wet
path is blended against an undelayed dry signal that has lost nothing, so
what survives to the output is a fraction of a fraction.

Where it would actually matter is inside a long feedback chain, since the
loss compounds once per repeat; that is the case to listen to first if a
better kernel is ever wanted. The upgrade is local to this file -- the
modules hand over a position and get samples back, and know nothing about
how many taps produced them.

## Why the caller supplies the delay in frames

`frames[:n]` is public and the caller fills it: the conversion from the
`time` parameter's seconds to frames, and the clamp that keeps the read
behind the write, are *the module's* invariants and differ between the two
(`Delay` floors at a whole block plus one and `ShortDelay` does not).
Getting those wrong is a correctness bug in the module; this file only
promises that whatever frame counts it is handed are read smoothly.
"""

from __future__ import annotations

import numpy as np


class FractionalReader:
    """Preallocated scratch for reading a ring buffer at a per-sample,
    fractional delay.

    Built in a module's `__init__`, filled in its `_allocate()`, and used
    from `process()` without allocating. One instance per module instance,
    so a per-note delay's sixteen voices get sixteen of these -- 7 x 512 x
    8 bytes = 28 kB each at the default block, against the 706 kB ring a
    single two-second `Delay` already carries.
    """

    __slots__ = ("frames", "_ramp", "_pos", "_floor", "_frac",
                 "_i0", "_i1", "_a", "_b", "_max_block")

    def __init__(self):
        self.frames = None
        self._max_block = 0

    def allocate(self, max_block):
        """Called from the owning module's `_allocate()`; the only place
        this object ever allocates."""
        self._max_block = int(max_block)
        #: How many frames back to read, per sample of the block. Public
        #: because the caller owns the conversion and the clamp -- see the
        #: module docstring.
        self.frames = np.zeros(self._max_block, dtype=np.float64)
        # 0, 1, 2, ... -- the "how far into the chunk are we" term of the
        # read position. Precomputed because building it per block is the
        # `np.arange` allocation `short_delay.py` already refuses to make
        # per chunk.
        self._ramp = np.arange(self._max_block, dtype=np.float64)
        self._pos = np.zeros(self._max_block, dtype=np.float64)
        self._floor = np.zeros(self._max_block, dtype=np.float64)
        self._frac = np.zeros(self._max_block, dtype=np.float64)
        # `intp` is what NumPy indexes with; anything else makes `take()`
        # convert, which allocates.
        self._i0 = np.zeros(self._max_block, dtype=np.intp)
        self._i1 = np.zeros(self._max_block, dtype=np.intp)
        self._a = np.zeros(self._max_block, dtype=np.float64)
        self._b = np.zeros(self._max_block, dtype=np.float64)

    def read_into(self, ring, size, write, offset, count, out):
        """`out[:count]` <- `ring` read at a moving fractional delay.

        Sample `j` of the output is taken from ring position
        `write + j - frames[offset + j]`, wrapped into `size`, with linear
        interpolation between the two whole positions either side. `offset`
        is where in `self.frames` this run starts, so a caller walking the
        block in chunks (`short_delay.py`) indexes its own per-sample delay
        correctly without a second copy of it.

        The caller owes one invariant this cannot check cheaply: every
        `frames[offset + j]` must be at least `count + 1`, so that the
        *upper* interpolation tap still lands strictly behind the write
        head and a read can never touch a sample this same call is about to
        write. Both modules establish that by clamping before calling.
        """
        if count <= 0:
            return
        pos = self._pos
        np.subtract(self._ramp[:count], self.frames[offset:offset + count],
                    out=pos[:count])
        np.add(pos[:count], float(write), out=pos[:count])
        # NumPy's `mod` follows the sign of the divisor, so a negative
        # position wraps to the top of the ring rather than staying
        # negative -- which is the wrap we want and the reason this is not
        # a hand-written conditional.
        np.mod(pos[:count], float(size), out=pos[:count])

        floor = self._floor
        np.floor(pos[:count], out=floor[:count])
        np.subtract(pos[:count], floor[:count], out=self._frac[:count])
        i0 = self._i0
        i1 = self._i1
        np.copyto(i0[:count], floor[:count], casting="unsafe")
        np.add(i0[:count], 1, out=i1[:count])
        # Only the upper tap can run off the end, and only by one.
        np.mod(i1[:count], size, out=i1[:count])

        a = self._a
        b = self._b
        # `ndarray.take(mode="wrap")` rather than `np.take(..., out=)`:
        # the default mode allocates an index copy to bounds-check
        # against, which `oscillator.py` already found and measured. The
        # indices are in range by construction here either way.
        ring.take(i0[:count], out=a[:count], mode="wrap")
        ring.take(i1[:count], out=b[:count], mode="wrap")
        # out = a + frac * (b - a), in three passes with no temporary.
        np.subtract(b[:count], a[:count], out=b[:count])
        np.multiply(b[:count], self._frac[:count], out=b[:count])
        np.add(a[:count], b[:count], out=out[:count])
