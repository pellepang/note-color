"""Pure log-reading/grouping logic behind `virtualnote replay <file>`
(feature idea 1 in docs/research/notation-and-feature-ideas.md,
"Session recording + playback") -- the real port of
`prototypes/session-log-and-practice-mode/session_player.py`'s reading
half. That prototype only proved a `.jsonl` session log (as written by
session_recorder.SessionRecorder) was readable and its timeline
reconstructible by printing a flat text summary; the real feature instead
re-drives a genuine `TabDisplay` from these same events (main.py's
`run_replay_session()`, the same "build TabDisplay columns from
already-detected note events, no live audio" shape
main.run_batch_transcribe() already uses for a
`batch_transcribe.TranscriptionResult`) -- this module owns only the
part that doesn't need a terminal: reading the file and grouping its
lines into the columns a caller will push one at a time.

Read-only. Never writes back to the log.
"""

import json


def load_events(path):
    """All events from a session .jsonl log, sorted by onset time `t`.
    SessionRecorder already appends in time order for a single live
    session, but sorting here keeps replay correct even against a
    hand-edited or concatenated log -- same reasoning the prototype's own
    `SessionPlayer.load_events()` used."""
    events = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    events.sort(key=lambda e: e["t"])
    return events


def group_columns(events):
    """Groups `events` (as loaded by load_events()) into a time-ordered
    list of columns a caller can push onto a TabDisplay one at a time:
    ("notes", t, [event, ...]) or ("barline", t, None).

    "note" events sharing the exact same `t` become one column -- a
    chord's tones are logged as separate lines sharing one `t` and one
    `chord_name` (see session_recorder.py's schema doc), and push_notes()
    already treats a single note as a one-note "chord" uniformly (the
    same convention main.run_batch_transcribe() uses grouping
    NoteEvents by onset_hop). Each "barline" event is always its own
    column. Ties between a note column and a barline column at the exact
    same `t` place the note column first -- a barline crossed at time `t`
    is conceptually placed just after the note that crossed it (the order
    main.run_batch_transcribe() itself pushes them in: push_notes() then
    push_barline() within the same onset_hop's iteration), not before it."""
    columns = []
    notes_by_t = {}
    order = []
    for event in events:
        if event.get("kind") == "barline":
            columns.append(("barline", event["t"], None))
        else:
            t = event["t"]
            if t not in notes_by_t:
                notes_by_t[t] = []
                order.append(t)
            notes_by_t[t].append(event)
    columns.extend(("notes", t, notes_by_t[t]) for t in order)
    columns.sort(key=lambda column: (column[1], column[0] != "notes"))
    return columns
