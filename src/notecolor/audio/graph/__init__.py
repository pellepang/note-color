"""The patch graph engine (decision 56): modules, cables and the rules over
them, for the Synth View's cable-patched canvas.

Additive, deliberately (decision 56 §7). `audio/synth_engine.py`'s fixed
osc -> filter -> env path still serves score-editor audition, frozen-buffer
playback and QWERTY note entry, none of which want a graph; nothing in here
is on their path, so a half-built graph cannot regress them.

`contract.py` is the seam everything else is built against, and the one file
to read first.
"""
