"""Shared sys.path bootstrap so every script in this prototype directory
can `import config`, `import color_map`, `import duration_tracker` -- the
real note-color modules -- without copying their logic. Import this module
(for its side effect) before importing any real note-color module.

Appends REPO_ROOT to the end of sys.path rather than inserting it at the
front: this prototype has its own local `session_recorder.py`/
`session_player.py` (this directory's whole reason for existing --
they're what the real, same-named repo-root modules were ported from),
and Python already puts a directly-run script's own directory at
sys.path[0] automatically. Inserting REPO_ROOT ahead of that would shadow
this prototype's local modules with the real ones every time their names
collide -- a real bug hit once the real `session_player.py` actually
shipped at the repo root (see CLAUDE.md's session recording/playback
entries): `demo.py`'s `from session_player import SessionPlayer` started
resolving to the real module (no `SessionPlayer` class there at all)
instead of this directory's own. Appending keeps this prototype's own
files authoritative for anything it defines itself, falling back to the
real repo only for names it doesn't (`config`, `color_map`,
`duration_tracker`).

Read-only: nothing here ever writes to the real repo.
"""

import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.append(REPO_ROOT)
