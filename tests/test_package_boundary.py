"""The core/UI boundary, enforced (wayfinder map #145, ticket #146).

This project has two permanent front-ends -- the terminal app and the
windowed one -- and the standing rule is that **parity is promised on the
core, not on the pixels**: a feature is built in the core packages, surfaced
in one front-end first, and reaches the other when it has an honest
representation there.

That promise only survives if the core genuinely does not know a UI exists.
A boundary nobody checks is a boundary that is already broken, so this test
walks the real import graph rather than trusting convention. It reads every
module with `ast`, which catches lazy in-function imports too -- and this
codebase deliberately has many of those, since heavy dependencies are
imported inside the functions that need them.
"""

import ast
import os

import pytest

PACKAGE_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "notecolor"
)

#: Packages allowed to import a UI toolkit. Everything else is core.
FRONTEND_PACKAGES = ("tui", "gui")

#: Toolkits a core module must never reach for. `blessed` is the terminal
#: settings screen's scoped exception, `pygame` the legacy visualiser, and
#: `PySide6` the DAW front-end -- all front-end-only by construction.
UI_TOOLKITS = ("blessed", "pygame", "PySide6", "PyQt5", "PyQt6", "tkinter")


def _modules():
    """Every module in the package, as (relative path, top-level package)."""
    for dirpath, _dirnames, filenames in os.walk(PACKAGE_ROOT):
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            full = os.path.join(dirpath, filename)
            relative = os.path.relpath(full, PACKAGE_ROOT)
            parts = relative.split(os.sep)
            yield relative, (parts[0] if len(parts) > 1 else "")


def _imported_names(path):
    """Every module name imported by a file, at any nesting depth."""
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` has no module; relative imports stay in-package.
            if node.module and node.level == 0:
                names.append(node.module)
    return names


CORE_MODULES = [
    (relative, package)
    for relative, package in _modules()
    if package not in FRONTEND_PACKAGES
]


def test_there_are_core_modules_to_check():
    """Guard against the sweep silently passing because it found nothing."""
    assert len(CORE_MODULES) > 20, CORE_MODULES


@pytest.mark.parametrize("relative", [m for m, _ in CORE_MODULES])
def test_core_module_imports_no_ui_toolkit(relative):
    imported = _imported_names(os.path.join(PACKAGE_ROOT, relative))
    offending = [
        name
        for name in imported
        if any(name == kit or name.startswith(kit + ".") for kit in UI_TOOLKITS)
    ]
    assert not offending, (
        f"{relative} is a core module but imports {offending}. A UI toolkit "
        f"belongs only in {FRONTEND_PACKAGES}; move the code that needs it "
        f"into a front-end package, or invert the dependency so the front-end "
        f"calls into the core rather than the core reaching for a toolkit."
    )


@pytest.mark.parametrize("relative", [m for m, _ in CORE_MODULES])
def test_core_module_does_not_import_a_front_end(relative):
    imported = _imported_names(os.path.join(PACKAGE_ROOT, relative))
    offending = [
        name
        for name in imported
        if any(
            name == f"notecolor.{pkg}" or name.startswith(f"notecolor.{pkg}.")
            for pkg in FRONTEND_PACKAGES
        )
    ]
    assert not offending, (
        f"{relative} is a core module but imports the front-end module(s) "
        f"{offending}. The core must not know which UI is driving it -- that "
        f"is what lets the terminal and the DAW share it."
    )
