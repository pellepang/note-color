"""note-color: real-time audio-to-colour analysis, notation and sound tools.

Package layout (wayfinder map #145, ticket #146):

- ``analysis``  -- detection and music/colour theory: pitch, chroma, multipitch,
  smoothers, chord templates, onset/tempo/duration, staff and colour maps.
- ``audio``     -- capture, the sound engine and its voices, effects, file I/O,
  and the process-wide live session.
- ``notation``  -- scores, score editing state, export formats, session logs.
- ``convert``   -- the offline audio-to-multi-track-score converter (map #123).
- ``project``   -- the DAW document: Project, Track, Clip, tempo map (map #145).
- ``settings``  -- configuration, the TOML overlay store, and the patch format.
- ``tui``       -- the terminal front-end. May import a UI toolkit.
- ``gui``       -- the windowed front-ends. May import a UI toolkit.

The one architectural rule, enforced by ``tests/test_package_boundary.py``:
**no module outside ``tui`` and ``gui`` may import a UI toolkit** (blessed,
pygame, PySide6) or reach into a front-end package. Everything else is core,
and both front-ends are built on top of it -- parity is promised on the core,
not on the pixels.
"""
