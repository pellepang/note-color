"""sys.path bootstrap for this prototype's two scripts.

Two things need to be importable that live in two different places:

1. The real note-color modules at the repo root (`color_map`,
   `staff_map`, `terminal_tab_display`, `main.RawKeys`) -- reused
   read-only for rendering, same convention every prototype in this repo
   follows (see `prototypes/score-editor-cursor-concept/demo.py`).
2. The *sibling* prototype's own scoring module,
   `prototypes/session-log-and-practice-mode/practice_scorer.py` (and the
   `session_player.SessionPlayer` class it imports) -- reused rather than
   reimplemented, per this prototype's own scope ("build on it, don't
   duplicate").

The ordering matters and is the one subtle bit here: the repo root *also*
now ships its own real `session_player.py` (issue #55-era, module-level
`load_events()`/`group_columns()` -- a different shape than the sibling
prototype's class-based `SessionPlayer`). `practice_scorer.py` does
`from session_player import SessionPlayer`, so the sibling prototype's own
`session_player.py` must resolve *first* -- the sibling directory goes
before the repo root on sys.path, mirroring exactly the bug (and fix)
`session-log-and-practice-mode/_repo_paths.py`'s own docstring already
documents for the reverse case (its `demo.py` importing its own local
`session_recorder`/`session_player` ahead of the real repo-root ones).

Read-only: nothing here ever writes to the real repo or to the sibling
prototype's files.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
SIBLING_PROTOTYPE_DIR = os.path.abspath(
    os.path.join(_HERE, "..", "session-log-and-practice-mode")
)

for path in (SIBLING_PROTOTYPE_DIR, REPO_ROOT):
    if path not in sys.path:
        sys.path.append(path)
