# note-color

Real-time audio-to-color visualizer. Listens to the microphone, detects the
musical note currently being played, and displays a corresponding color —
fast enough to feel live during actual music.

## Goal / constraints (as given by the user)

- Not a web app.
- Must run across a range of hardware, from small devices (Raspberry Pi
  class) up to full desktops — portability mattered more than raw speed.
- User deferred most technical tradeoffs to "best-performing + easiest to
  build" judgment rather than specifying them.

## Licence

MIT — see `LICENSE`. Third-party material rules (weights, training data,
evaluation corpora) are in `docs/DECISIONS.md`.

## Architecture

The chord-mode pipeline (chroma/multipitch/chord_smoother/chord_templates)
and the rhythm pipeline (onset_detect/tempo_tracker/duration_tracker) both
always run every hop regardless of whether any view has `P` toggled on or
is even `tab` — cheap enough that gating either wasn't worth the extra
shared state (see Key design decisions). `P` is a pure render-thread-local
flag; rhythm detection has no toggle at all, live or in `tab`'s render.

Three threads, connected by non-blocking queues at every boundary, so no
stage can ever stall another. Target end-to-end latency: comfortably under
150ms.

Audio *output* (map #99, decision #105) is a separate, independently lazy
path: `SessionState.ensure_sound_engine()` opens one process-wide
`sound_engine.SoundEngine` on first use and keeps it for the process's
life, exactly as `ensure_started()` does for input — so switching tools
never drops or reopens the output device, and a tool that only plays
(the score editor) never opens the mic, nor vice versa. `virtualnote
transcribe --play` bypasses all of it (offline pre-render, still
`playback.render_offline()`); `virtualnote replay --play` builds its own
`SoundEngine`, since that entry point constructs no `SessionState`.

**Process/session lifecycle (issue #40).** `AudioCapture`, the analysis
thread, `Sensitivity`, and `SourceState` are bundled in `main.SessionState`
and created lazily — on first tool entry, not at process start — via
`SessionState.ensure_started()`, an idempotent call both `main()` (eager,
called once) and `virtualnote`'s menu shell (lazy, called before every tool
entry) can make freely. Once created, a `SessionState` persists for the
rest of the process's life: `virtualnote`'s menu loop (`shell.py`) reuses
the same one across every "pick a tool -> run it -> `|` back to menu ->
pick another tool" round-trip, so switching tools never tears down or
reopens the mic (unlike `M`'s deliberate `AudioCapture.restart()`, a real
source *change*, not a tool switch) — that persistence is what makes `|`
an instant transition rather than a relaunch with startup latency.
`main.run_session(view, ..., session)` is the shared dispatch point both
`main()` (standalone, one-shot) and `shell.run_menu_loop()` (repeated)
call into; each terminal run_* function and `run_gui()` return an explicit
sentinel, `"quit"` or `"menu"`, instead of swallowing `KeyboardInterrupt`
into an implicit `None` as before — see Key design decisions.

**Glossary note (map #145, ticket #150).** **Track** now means a DAW timeline
lane; the notation sense this project used to call a Track is a **Part** —
MusicXML's own word. `ConversionResult.parts` and the project manifest's
`parts` key carry the new name, and `read_project_manifest()` still accepts a
version 1 manifest's `tracks`. Note the words that were *not* renamed:
`BeatTracker.track()`, `DurationTracker` and `tempo_tracker` are the verb
sense — to track something — and are untouched. See `CONTEXT.md`.


## Files

Per-module responsibility reference — moved to `docs/FILES.md` to keep this always-loaded file small (same convention as `docs/DECISIONS.md` for full design rationale). One row per file; check there first.

## Key design decisions and known limitations

One-liner summaries of both — moved to `docs/SUMMARY.md` to keep this always-loaded file small; full rationale for any of them lives in `docs/decisions/` (see `docs/DECISIONS.md`'s index). Skim `docs/SUMMARY.md` first for "why did we do X" — it's one-liners, not the full writeup.

## Working practices

This repo is tracked on GitHub at `github.com/pellepang/note-color`; git is
the system of record for the project's history, not just a backup.

- After each meaningful checkpoint (a fix, a feature, a config/behavior
  change), commit with a message that describes the change and its
  rationale, then push to `origin/main`.
- Keep commits scoped to one logical change rather than batching unrelated
  work together.
- Generated run-time artifacts (e.g. `note_history_*.txt` dumps from the
  `tab` view) are gitignored, not committed.
- When a backlog item is resolved, delete it from this file rather than
  archiving it here — `git log` already preserves that history.
- New design rationale goes into `docs/DECISIONS.md`, not inlined here —
  keep this file to orientation only.

## Reference

- Full design rationale: `docs/DECISIONS.md`.
- Full original build plan and rationale (pitch detection algorithm choice,
  audio pipeline design, build order):
  `/home/pelle/.claude/plans/i-want-to-make-graceful-stallman.md`.

## Agent skills

### Running the app

How to run/test the tool, its CLI flags, keybindings, and feature
walkthroughs (including VisualNote Studio) live in the
`running-the-app` skill (`.claude/skills/running-the-app/SKILL.md`),
not here.

### Issue tracker

Issues live as GitHub issues on `pellepang/note-color`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`), used as-is. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root (neither exists yet — created lazily by `/domain-modeling`). See `docs/agents/domain.md`.
