#!/usr/bin/env python3
"""Report which evaluation corpora are staged locally (issue #133).

Reads `docs/eval/corpora.toml` -- the committed record of what is
expected -- and says what is present, what is missing, and how to obtain
each missing piece. Downloads nothing: the public corpora are large and
separately licensed, and the owner's own set is commercial music that
cannot be fetched at all.

    .venv/bin/python scripts/check_corpora.py
"""

import os
import sys
import tomllib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(REPO_ROOT, "docs", "eval", "corpora.toml")

#: Score formats an evaluation can actually use. A PDF of sheet music is
#: the common case and the useless one -- #132's metrics compare notation
#: to notation or notes to notes, and neither can read an image.
USABLE_SCORE_EXTENSIONS = (".musicxml", ".mxl", ".xml", ".mid", ".midi", ".mscz")
AUDIO_EXTENSIONS = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aiff", ".aif")


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0


def _dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def check_owner_set(root):
    """The owner's own audio+score pairs, which is the half nobody can
    automate. Reports each pair's usability rather than just its presence,
    because a song paired with a PDF looks staged and cannot be scored."""
    if not os.path.isdir(root):
        return [], []
    ready, problems = [], []
    for entry in sorted(os.listdir(root)):
        song_dir = os.path.join(root, entry)
        if not os.path.isdir(song_dir):
            continue
        files = os.listdir(song_dir)
        audio = [f for f in files if f.lower().endswith(AUDIO_EXTENSIONS)]
        scores = [f for f in files if f.lower().endswith(USABLE_SCORE_EXTENSIONS)]
        pdfs = [f for f in files if f.lower().endswith((".pdf", ".png", ".jpg", ".jpeg"))]
        if audio and scores:
            ready.append((entry, audio[0], scores[0]))
        elif audio and pdfs:
            problems.append((entry, "score is a PDF/image -- not machine-readable"))
        elif audio:
            problems.append((entry, "no score file"))
        elif scores:
            problems.append((entry, "no audio file"))
        else:
            problems.append((entry, "neither audio nor score found"))
    return ready, problems


def main():
    with open(MANIFEST, "rb") as handle:
        manifest = tomllib.load(handle)
    root = os.path.expanduser(manifest["meta"]["staging_root"])

    print(f"Staging root: {root}")
    print(f"  {'exists' if os.path.isdir(root) else 'MISSING -- mkdir -p ' + root}\n")

    print("Public corpora (#132's committed regression suite)")
    print("-" * 60)
    for name, spec in manifest["corpora"].items():
        if name == "owner_set":
            continue
        path = os.path.join(root, name)
        if os.path.isdir(path) and os.listdir(path):
            print(f"  [staged]  {name:12} {_human(_dir_size(path)):>8}  ({spec['licence']})")
        else:
            print(f"  [MISSING] {name:12} {spec.get('size', '?'):>8}  ({spec['licence']})")
            print(f"            {spec['url']}")
    print()

    print("Your own material (#132's real-world genre check)")
    print("-" * 60)
    ready, problems = check_owner_set(root)
    for entry, audio, score in ready:
        print(f"  [usable]  {entry}  ({audio} + {score})")
    for entry, why in problems:
        print(f"  [PROBLEM] {entry}: {why}")
    if not ready and not problems:
        print("  (nothing staged yet)")
    print(f"\n  {len(ready)} usable pair(s). #132 suggests 8-12 across genres.")
    if problems:
        print("\n  A PDF score cannot be scored automatically -- #132's metrics")
        print("  compare notation to notation or notes to notes, and neither")
        print("  reads an image. MuseScore can scan one, or a few bars can be")
        print("  entered by hand in this repo's own editor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
