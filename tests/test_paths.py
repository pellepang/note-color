"""Where this app's own files live (ticket #146).

These locations used to be spelled `dirname(abspath(__file__))` in modules
that happened to sit at the repo root. The src-layout move pushed those
modules several directories deeper, which would have silently started writing
session logs into `src/notecolor/notation/` and left every log and score the
app had already written invisible to the pickers that list them.

Nothing in the rest of the suite would have caught that -- every test that
touches these paths passes an explicit `tmp_path` -- so the behaviour is
pinned here directly.
"""

import os

from notecolor.settings import paths


def test_repo_root_is_the_checkout_containing_pyproject():
    root = paths.repo_root()
    assert root is not None
    assert os.path.exists(os.path.join(root, "pyproject.toml"))


def test_repo_root_is_above_the_package_not_inside_it():
    """The regression this guards: resolving to the module's own directory."""
    root = paths.repo_root()
    package = os.path.dirname(os.path.abspath(paths.__file__))
    assert package.startswith(root + os.sep)
    assert os.path.basename(root) != "settings"


def test_data_dir_is_the_repo_root_from_a_checkout():
    assert paths.data_dir() == paths.repo_root()


def test_data_dir_falls_back_to_cwd_without_a_checkout(monkeypatch, tmp_path):
    """An installed wheel has no checkout to write into; the working directory
    is the only sane default, and it must not raise."""
    monkeypatch.setattr(paths, "repo_root", lambda: None)
    monkeypatch.chdir(tmp_path)
    assert paths.data_dir() == os.getcwd()
