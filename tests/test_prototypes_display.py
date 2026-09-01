from prototypes_display import _visible_slice, _wrap_readme, list_prototypes


def _make_prototype(root, name, readme_text):
    d = root / name
    d.mkdir()
    (d / "README.md").write_text(readme_text, encoding="utf-8")


def test_list_prototypes_sorted_and_titled_from_h1(tmp_path):
    _make_prototype(tmp_path, "zeta-thing", "# Zeta thing -- prototype\n\nbody\n")
    _make_prototype(tmp_path, "alpha-thing", "# Alpha thing\n\nbody\n")

    entries = list_prototypes(str(tmp_path))

    assert [e["name"] for e in entries] == ["alpha-thing", "zeta-thing"]
    assert entries[0]["title"] == "Alpha thing"
    assert entries[1]["title"] == "Zeta thing -- prototype"


def test_list_prototypes_skips_dirs_without_readme(tmp_path):
    _make_prototype(tmp_path, "has-readme", "# Has readme\n")
    (tmp_path / "no-readme").mkdir()

    entries = list_prototypes(str(tmp_path))

    assert [e["name"] for e in entries] == ["has-readme"]


def test_list_prototypes_missing_root_returns_empty(tmp_path):
    assert list_prototypes(str(tmp_path / "does-not-exist")) == []


def test_list_prototypes_falls_back_to_name_when_readme_has_no_heading(tmp_path):
    _make_prototype(tmp_path, "bare", "\n\n")

    entries = list_prototypes(str(tmp_path))

    assert entries[0]["title"] == "bare"


def test_wrap_readme_wraps_long_lines_and_preserves_blank_lines(tmp_path):
    path = tmp_path / "README.md"
    path.write_text("one two three four five\n\nnext paragraph\n", encoding="utf-8")

    lines = _wrap_readme(str(path), width=10)

    assert lines[0] == "one two"
    assert "" in lines
    assert lines[-1] == "paragraph"


def test_visible_slice_returns_window_and_clamped_scroll():
    lines = [str(i) for i in range(10)]

    window, scroll = _visible_slice(lines, scroll=3, height=4)

    assert window == ["3", "4", "5", "6"]
    assert scroll == 3


def test_visible_slice_clamps_scroll_past_end():
    lines = [str(i) for i in range(5)]

    window, scroll = _visible_slice(lines, scroll=100, height=3)

    assert scroll == 2
    assert window == ["2", "3", "4"]


def test_visible_slice_clamps_negative_scroll():
    lines = [str(i) for i in range(5)]

    window, scroll = _visible_slice(lines, scroll=-5, height=3)

    assert scroll == 0
    assert window == ["0", "1", "2"]
