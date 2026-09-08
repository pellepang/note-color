"""The recently-opened list.

A tiny JSON file beside the app's other settings rather than anything Qt
provides: `QSettings` would put this in a platform-specific store that nothing
else in this project knows about, while `config_store` already establishes
where this app keeps its state. Kept separate from `config.toml` because that
file is hand-edited and this one is machine-written -- the same split the
Project manifest follows.
"""

import json
import os

from notecolor.settings.config_store import config_path


def config_dir():
    """The directory `config.toml` lives in -- reused rather than re-derived,
    so this file cannot drift to a different location than the app's other
    settings."""
    return os.path.dirname(config_path())

MAX_RECENT = 8
FILENAME = "recent.json"


def _path():
    return os.path.join(config_dir(), FILENAME)


def recent_paths():
    """Most recent first, dropping anything that no longer exists.

    Filtering on read rather than pruning on write: a project on an unmounted
    drive should come back when the drive does, not be forgotten because the
    app happened to open while it was away.
    """
    try:
        with open(_path(), encoding="utf-8") as handle:
            entries = json.load(handle)
    except (OSError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, str) and os.path.exists(e)][:MAX_RECENT]


def remember_path(path):
    """Put `path` at the front. Never raises -- a recent list that cannot be
    written is a lost convenience, not a lost project."""
    if not path:
        return
    path = os.path.abspath(str(path))
    try:
        with open(_path(), encoding="utf-8") as handle:
            entries = [e for e in json.load(handle) if isinstance(e, str)]
    except (OSError, ValueError):
        entries = []
    entries = [path] + [e for e in entries if e != path]
    try:
        os.makedirs(config_dir(), exist_ok=True)
        with open(_path(), "w", encoding="utf-8") as handle:
            json.dump(entries[:MAX_RECENT], handle, indent=2)
    except OSError:
        pass
