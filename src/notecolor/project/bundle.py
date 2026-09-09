"""Reading and writing a `.ncproj` bundle.

Format and rationale: `docs/adr/0001-ncproj-project-bundle.md`.

```
MySong.ncproj/
├── project.json      the manifest -- the whole document except the audio
├── audio/            recorded takes and imported files, copied in
├── peaks/            waveform overviews, one per audio file
└── autosave/
```

**This module is the one place in the codebase that refuses rather than
degrades**, and the departure is deliberate. `config_store` and `patch_format`
both carry no version field and recover whatever they can from a damaged file,
which is right for *settings*: a mangled patch costs one sound, and refusing to
open would be less kind than carrying on. A Project is hours of someone's work.
Silently dropping a field a newer build wrote is data loss wearing a friendly
face, so a project from the future is refused by name and a project from the
past is migrated deliberately.

The exception is *content* that is merely missing rather than unreadable -- an
audio file that has gone away, an anchor with a bad number. Those degrade, and
the project still opens, exactly as `sampler.SilentVoice` keeps a kit playing
around a missing sample.
"""

import json
import os
import shutil

from notecolor.project.model import (
    AUDIO_TRACK,
    NOTE_TRACK,
    PROJECT_VERSION,
    AudioClip,
    ChordSpan,
    Note,
    NoteClip,
    Project,
    ProjectError,
    TempoMap,
    TimeSignature,
    Track,
)

MANIFEST_NAME = "project.json"
BUNDLE_SUFFIX = ".ncproj"
AUDIO_DIR = "audio"
PEAKS_DIR = "peaks"
AUTOSAVE_DIR = "autosave"
SUBDIRECTORIES = (AUDIO_DIR, PEAKS_DIR, AUTOSAVE_DIR)


class ProjectVersionError(ProjectError):
    """A project written by a build that knows more than this one does."""


class ProjectReadError(ProjectError):
    """The manifest is absent or is not readable as a project at all."""


def default_projects_dir():
    """Where a project goes when the user has not said otherwise. A Project is
    a document, so it belongs where documents live -- not in this repo, which
    is where `settings.paths.data_dir()` still puts session logs (#149 left
    that legacy separately)."""
    return os.path.join(os.path.expanduser("~"), "Music", "VisualNote")


def bundle_path(path):
    """`path` with the bundle suffix, so a user typing a bare name gets one."""
    path = str(path)
    return path if path.endswith(BUNDLE_SUFFIX) else path + BUNDLE_SUFFIX


# --------------------------------------------------------------------------
# Serialising
# --------------------------------------------------------------------------


def _note_to_dict(note):
    data = {"beat": note.start_beat, "beats": note.duration_beats,
            "pitch": note.pitch, "velocity": note.velocity}
    if note.confidence is not None:
        data["confidence"] = note.confidence
    return data


def _clip_to_dict(clip):
    common = {"name": clip.name, "beat": clip.start_beat, "beats": clip.length_beats}
    if isinstance(clip, AudioClip):
        common.update({
            "kind": AUDIO_TRACK, "source": clip.source,
            "offset_samples": clip.source_offset_samples,
            "length_samples": clip.source_length_samples,
            "gain_db": clip.gain_db,
            "fade_in_beats": clip.fade_in_beats,
            "fade_out_beats": clip.fade_out_beats,
        })
    else:
        common.update({"kind": NOTE_TRACK, "notes": [_note_to_dict(n) for n in clip.notes]})
    return common


def project_to_dict(project):
    return {
        "version": PROJECT_VERSION,
        "name": project.name,
        "sample_rate": project.sample_rate,
        "time_signature": [project.time_signature.numerator,
                           project.time_signature.denominator],
        "key_fifths": project.key_fifths,
        "key_mode": project.key_mode,
        "tempo_map": project.tempo_map.to_dict(),
        "tracks": [
            {
                "name": track.name, "kind": track.kind,
                "color_pitch_class": track.color_pitch_class,
                "muted": track.muted, "soloed": track.soloed,
                "gain_db": track.gain_db, "pan": track.pan,
                "clips": [_clip_to_dict(c) for c in track.clips],
            }
            for track in project.tracks
        ],
        "chords": [
            {"beat": c.start_beat, "end_beat": c.end_beat,
             "name": c.name, "derived": c.derived}
            for c in project.chords
        ],
    }


# --------------------------------------------------------------------------
# Deserialising
# --------------------------------------------------------------------------


def _note_from_dict(data):
    return Note(
        start_beat=float(data.get("beat", 0.0)),
        duration_beats=float(data.get("beats", 1.0)),
        pitch=int(data.get("pitch", 60)),
        velocity=float(data.get("velocity", 1.0)),
        confidence=(None if data.get("confidence") is None
                    else float(data["confidence"])),
    )


def _clip_from_dict(data):
    name = str(data.get("name", ""))
    beat = float(data.get("beat", 0.0))
    beats = float(data.get("beats", 4.0))
    if data.get("kind") == AUDIO_TRACK:
        return AudioClip(
            name=name, start_beat=beat, length_beats=beats,
            source=os.path.basename(str(data.get("source", ""))),
            source_offset_samples=int(data.get("offset_samples", 0)),
            source_length_samples=int(data.get("length_samples", 0)),
            gain_db=float(data.get("gain_db", 0.0)),
            fade_in_beats=float(data.get("fade_in_beats", 0.0)),
            fade_out_beats=float(data.get("fade_out_beats", 0.0)),
        )
    notes = []
    for entry in data.get("notes", []):
        try:
            notes.append(_note_from_dict(entry))
        except (TypeError, ValueError):
            continue  # a single unreadable note is not worth the project
    return NoteClip(name=name, start_beat=beat, length_beats=beats, notes=notes)


def project_from_dict(data):
    if not isinstance(data, dict):
        raise ProjectReadError("Project manifest is not an object.")
    version = data.get("version")
    if not isinstance(version, int):
        raise ProjectReadError("Project manifest has no version.")
    if version > PROJECT_VERSION:
        raise ProjectVersionError(
            f"This project was written by a newer version of VisualNote Studio "
            f"(project format {version}; this build understands up to "
            f"{PROJECT_VERSION}). Opening it here would silently drop whatever "
            f"that version added, so it is refused instead."
        )
    data = migrate(data)

    signature = data.get("time_signature") or [4, 4]
    tracks = []
    for entry in data.get("tracks", []):
        tracks.append(Track(
            name=str(entry.get("name", "Track")),
            kind=str(entry.get("kind", NOTE_TRACK)),
            color_pitch_class=entry.get("color_pitch_class"),
            clips=[_clip_from_dict(c) for c in entry.get("clips", [])],
            muted=bool(entry.get("muted", False)),
            soloed=bool(entry.get("soloed", False)),
            gain_db=float(entry.get("gain_db", 0.0)),
            pan=float(entry.get("pan", 0.0)),
        ))
    chords = []
    for entry in data.get("chords", []):
        try:
            chords.append(ChordSpan(
                float(entry["beat"]), float(entry["end_beat"]),
                str(entry.get("name", "")), bool(entry.get("derived", True))))
        except (KeyError, TypeError, ValueError):
            continue
    return Project(
        name=str(data.get("name", "Untitled")),
        tempo_map=TempoMap.from_dict(data.get("tempo_map")),
        time_signature=TimeSignature(int(signature[0]), int(signature[1])),
        key_fifths=int(data.get("key_fifths", 0)),
        key_mode=str(data.get("key_mode", "major")),
        sample_rate=int(data.get("sample_rate", 48000)),
        tracks=tracks,
        chords=chords,
    )


def migrate(data):
    """Bring an older manifest up to `PROJECT_VERSION`.

    Empty today because version 1 is the first. It exists anyway, and is called
    on every read, so that the first migration is a new branch in a function
    that is already wired up and already tested -- not a new mechanism invented
    under pressure the day a format change is needed.
    """
    version = data.get("version", PROJECT_VERSION)
    # if version < 2: ... data["version"] = 2
    return data


# --------------------------------------------------------------------------
# The bundle on disk
# --------------------------------------------------------------------------


def save_project(project, path):
    """Write `project` to a `.ncproj` bundle, creating it if needed."""
    path = bundle_path(path)
    for name in SUBDIRECTORIES:
        os.makedirs(os.path.join(path, name), exist_ok=True)
    manifest = os.path.join(path, MANIFEST_NAME)
    temporary = manifest + ".tmp"
    # Write-then-rename: an interrupted save leaves the previous manifest
    # intact rather than a half-written one. This is the file that holds the
    # work, so it is worth the two extra lines.
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(project_to_dict(project), handle, indent=2)
    os.replace(temporary, manifest)
    return path


def load_project(path):
    """Read a `.ncproj` bundle. Raises `ProjectError` if it cannot."""
    path = str(path)
    manifest = os.path.join(path, MANIFEST_NAME)
    if not os.path.exists(manifest):
        raise ProjectReadError(f"No {MANIFEST_NAME} in {path}.")
    try:
        with open(manifest, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ProjectReadError(f"Could not read {manifest}: {exc}") from exc
    return project_from_dict(data)


def audio_dir(path):
    return os.path.join(str(path), AUDIO_DIR)


def import_audio(bundle, source_file):
    """Copy an audio file into the bundle and return the bare name to
    reference it by.

    Copy-in, never reference-in-place: it is `patch_format`'s existing rule for
    samples, and it is what keeps a project openable after it is moved. An
    identical file already present is reused; a different file with a colliding
    name gets a suffix rather than clobbering something another clip points at.
    """
    directory = audio_dir(bundle)
    os.makedirs(directory, exist_ok=True)
    base = os.path.basename(source_file)
    stem, extension = os.path.splitext(base)
    candidate, suffix = base, 1
    while True:
        target = os.path.join(directory, candidate)
        if not os.path.exists(target):
            shutil.copy2(source_file, target)
            return candidate
        if os.path.getsize(target) == os.path.getsize(source_file):
            with open(target, "rb") as a, open(source_file, "rb") as b:
                if a.read() == b.read():
                    return candidate       # same bytes, already here
        candidate = f"{stem}_{suffix}{extension}"
        suffix += 1


def resolve_audio(bundle, clip):
    """The full path an `AudioClip` refers to, or `None` if it has gone.

    `None` rather than an exception: a project with a missing file still opens
    and everything else still plays, the posture `sampler.SilentVoice` already
    takes for a missing sample.
    """
    if not getattr(clip, "source", ""):
        return None
    path = os.path.join(audio_dir(bundle), os.path.basename(clip.source))
    return path if os.path.exists(path) else None


def missing_audio(bundle, project):
    """Every audio source the project references that is not in the bundle."""
    missing = []
    for track in project.tracks:
        for clip in track.clips:
            if isinstance(clip, AudioClip) and resolve_audio(bundle, clip) is None:
                missing.append(clip.source)
    return sorted(set(missing))
