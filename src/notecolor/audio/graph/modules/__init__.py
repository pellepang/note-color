"""Modules implemented against `graph/contract.py`.

The first two -- one per-note (`oscillator.WavetableOscillator`) and one
once-only (`delay.Delay`) -- were #202's "done when". The set has grown
since (`filter`, `envelope`, `noise`, `passthrough`, `short_delay`,
`level`), and a hosted plugin arrives here as one more entry with no
privileges the others do not have -- that is what the contract is for.
"""
