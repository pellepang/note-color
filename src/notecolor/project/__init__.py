"""The DAW document: Project, Track, Clip and the tempo map (map #145).

`model` holds the data, `bundle` reads and writes the `.ncproj` directory.
Format and rationale: `docs/adr/0001-ncproj-project-bundle.md`.
"""

from notecolor.project.model import (  # noqa: F401
    AUDIO_TRACK, CONSTANT, LINEAR, NOTE_TRACK, PROJECT_VERSION,
    AudioClip, ChordSpan, Note, NoteClip, Project, ProjectError,
    TempoAnchor, TempoMap, TimeSignature, Track,
)
from notecolor.project.bundle import (  # noqa: F401
    BUNDLE_SUFFIX, MANIFEST_NAME, ProjectReadError, ProjectVersionError,
    bundle_path, default_projects_dir, import_audio, load_project,
    missing_audio, resolve_audio, save_project,
)
