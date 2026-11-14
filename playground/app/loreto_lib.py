"""Import the Loreto reference implementation (loreto.py).

The Playground does not keep its own copy of loreto.py: it uses the one in the repository
root (`LORETO_SRC`), which the Docker image copies to a directory on PYTHONPATH.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType


def import_loreto(src: Path) -> ModuleType:
    try:
        module = importlib.import_module("loreto")
    except ModuleNotFoundError:
        if not (src / "loreto.py").is_file():
            raise RuntimeError(f"loreto.py not found in {src}; set LORETO_SRC to the directory that contains it") from None
        sys.path.insert(0, str(src))
        module = importlib.import_module("loreto")
    version = getattr(module, "__version__", "")
    if not version.startswith("2."):
        raise RuntimeError(f"the demo needs Loreto 2.x, found {version or 'an unversioned loreto.py'} "
                           f"at {getattr(module, '__file__', '?')}")
    return module
