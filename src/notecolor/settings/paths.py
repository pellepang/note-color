"""Where this app's own files live on disk.

Until the src-layout move (wayfinder map #145, ticket #146) every one of these
locations was spelled `os.path.dirname(os.path.abspath(__file__))` inside a
module that happened to sit at the repo root -- so session logs, `tab` view
dumps and score files all landed beside `main.py`, and the pickers that list
them globbed the same place.

The move broke that by accident rather than by decision: those modules now sit
several directories deep inside the package, so the same expression would have
started writing user data into `src/notecolor/notation/` and would have left
every existing log and score invisible to the app that wrote them. This module
exists so there is exactly one answer to "where do this app's files go", and so
that changing that answer later is a deliberate act in one place rather than a
silent consequence of moving a file.

Behaviour is deliberately unchanged: from a source checkout this still resolves
to the repo root. `docs/DECISIONS.md` is the place to argue for moving it
somewhere XDG-shaped, which is a real question -- writing user data into a git
checkout is odd -- but a separate one from this restructure.
"""

import os

#: Marks the top of a source checkout. Present in a clone, absent in a wheel.
_ROOT_MARKER = "pyproject.toml"


def repo_root():
    """The source checkout this package was imported from, or None if it was
    installed as a wheel and there is no checkout to speak of."""
    directory = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.exists(os.path.join(directory, _ROOT_MARKER)):
            return directory
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


def data_dir():
    """Where session logs, `tab` dumps and loose score files live.

    The repo root from a checkout -- preserving where every such file has been
    written to date -- and the current working directory otherwise, which is
    the only sane default for an installed copy with no checkout to write into.
    """
    return repo_root() or os.getcwd()
