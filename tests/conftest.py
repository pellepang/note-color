import os
import sys

# src layout (ticket #146): tests import through the installed package name,
# so `src` is what goes on the path -- not the repo root, which no longer
# holds any importable module. An editable install makes this redundant;
# it stays so `pytest tests/` works in a bare checkout too.
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)
