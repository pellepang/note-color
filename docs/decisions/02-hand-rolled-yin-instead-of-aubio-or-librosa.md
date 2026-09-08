# Hand-rolled YIN instead of `aubio` or `librosa`

`aubio` has unreliable PyPI wheels (especially ARM/Raspberry Pi, newer
CPython) and often needs compiling `libaubio` from source; `librosa` drags in
a heavy dependency tree (numba etc.) that's a poor fit for Pi-class installs.
Plain YIN is ~80 lines of NumPy with no exotic dependencies.
