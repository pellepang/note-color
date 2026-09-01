import json

from session_player import group_columns, load_events


def _write(path, events):
    with open(path, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")


def test_load_events_sorts_by_t(tmp_path):
    path = tmp_path / "session.jsonl"
    _write(path, [
        {"t": 1.0, "kind": "note", "pc": 2, "octave": 4, "label": "D4", "duration_class": "quarter"},
        {"t": 0.0, "kind": "note", "pc": 0, "octave": 4, "label": "C4", "duration_class": "quarter"},
    ])

    events = load_events(str(path))

    assert [e["label"] for e in events] == ["C4", "D4"]


def test_load_events_skips_blank_lines(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text('{"t": 0.0, "kind": "barline"}\n\n\n', encoding="utf-8")

    events = load_events(str(path))

    assert len(events) == 1


def test_group_columns_groups_chord_tones_sharing_one_t():
    events = [
        {"t": 0.5, "kind": "note", "pc": 0, "octave": 3, "chord_name": "C"},
        {"t": 0.5, "kind": "note", "pc": 4, "octave": 3, "chord_name": "C"},
        {"t": 0.5, "kind": "note", "pc": 7, "octave": 3, "chord_name": "C"},
    ]

    columns = group_columns(events)

    assert len(columns) == 1
    kind, t, group = columns[0]
    assert kind == "notes"
    assert t == 0.5
    assert len(group) == 3


def test_group_columns_separates_distinct_timestamps():
    events = [
        {"t": 0.0, "kind": "note", "pc": 0, "octave": 4},
        {"t": 0.5, "kind": "note", "pc": 2, "octave": 4},
    ]

    columns = group_columns(events)

    assert [t for _kind, t, _group in columns] == [0.0, 0.5]


def test_group_columns_barline_is_its_own_column():
    events = [
        {"t": 0.0, "kind": "note", "pc": 0, "octave": 4},
        {"t": 1.0, "kind": "barline"},
    ]

    columns = group_columns(events)

    assert [kind for kind, _t, _group in columns] == ["notes", "barline"]


def test_group_columns_orders_note_before_barline_at_same_t():
    events = [
        {"t": 1.0, "kind": "barline"},
        {"t": 1.0, "kind": "note", "pc": 0, "octave": 4},
    ]

    columns = group_columns(events)

    assert [kind for kind, _t, _group in columns] == ["notes", "barline"]
