# 1. The `.ncproj` Project bundle

Date: 2026-09-08
Status: Accepted
Context: wayfinder map [#145](https://github.com/pellepang/note-color/issues/145), ticket [#149](https://github.com/pellepang/note-color/issues/149)

## Context

VisualNote Studio needs a document. Until now this project has persisted three
unrelated things — MusicXML scores, TOML patches, and JSONL session logs — and
map #123 left a fourth, the *Score project*, explicitly undecided. A DAW
document has to hold more than any of them: a tempo map, tracks, clips,
recorded audio, and the analysis laid over it.

It also has to hold something none of the existing formats do: **work a person
can lose**. That single difference drives most of what follows.

## Decision

A Project is a **bundle directory**, `MySong.ncproj/`:

```
MySong.ncproj/
├── project.json      the manifest -- the whole document except the audio
├── audio/            recorded takes and imported files, copied in
├── peaks/            waveform overviews, one per audio file
└── autosave/
```

**Stems are not in the bundle.** Separated stems are large, exactly
regenerable, and #123 already calls them "cached to disk while converting,
never part of the saved result". They live in a cache directory outside the
bundle, keyed by a hash of the source audio. A project you send someone is not
400 MB of intermediate data.

**Peaks are in the bundle**, despite also being regenerable: they are small,
and rebuilding them means re-reading every audio file just to draw the window.

**Note content lives inline in `project.json`. MusicXML is import/export
only.** MusicXML is a notation interchange format, not a working format. It
cannot express a clip's position on a timeline, and this project has already
measured three ways it loses information on a round trip: `editorial` data is
not exported at all, a `MetronomeMark` on a `Score` is silently discarded, and
consecutive rests are consolidated on write. Using it as the save format would
mean paying music21's cost on every save to store the document less faithfully
than a JSON array would.

**Imported audio is always copied in**, never referenced in place, and
referenced by bare name — the rule `patch_format` already applies to samples so
a patch stays shareable. A size threshold was considered and rejected: it makes
a project *sometimes* portable, and you would discover which kind you had only
after moving it. A missing file degrades rather than crashes, as
`sampler.SilentVoice` already does.

**The tempo map is a list of anchors**, `(beat, bpm, interpolation)`, with
`"constant"` the only interpolation implemented — a step function. A single BPM
scalar was rejected outright: #131 already established that tempo is *read out
of* beat times rather than imposed as a scalar. Ramps (`"linear"`) need
authoring UI that is far off, but the field exists now so adding them is
additive rather than a format change.

**Projects live where the user puts them**, defaulting to
`~/Music/VisualNote/`. A Project is a document and belongs where a person keeps
documents.

**The manifest carries an explicit `version` and gets real migrations.**

## Consequences

The versioning decision is a deliberate **departure from two existing
precedents in this repo**, and it should be read as one rather than as an
oversight. `config_store.py` and `patch_format.py` both carry no version field
and degrade rather than raise: an unreadable patch loads as defaults, a
malformed TOML file keeps its longest valid prefix. That is right for
*settings*, where a mangled file costs you one sound and refusing to open would
be less kind than carrying on.

A Project is not settings. It is hours of someone's work, and silently dropping
a field a newer version wrote is data loss wearing a friendly face. So this
format states its version, migrates forward deliberately, and refuses what it
cannot understand instead of quietly discarding it.

`score_writer.PROJECT_MANIFEST_VERSION` is already the seed of this and is at
2, with a working read path for version 1 (ticket #150 renamed its `tracks` key
to `parts`).

The cost accepted: two persistence postures in one codebase, which someone will
eventually mistake for inconsistency. It is not — it is the difference between
a file you can recreate in a minute and one you cannot recreate at all.

Legacy paths are untouched by this. Session logs and loose score files still
land in the repo checkout via `settings/paths.py`, which is odd and worth
fixing, but sweeping them into XDG directories alongside this decision would
bundle an unrelated migration into it and strand the logs already written.
