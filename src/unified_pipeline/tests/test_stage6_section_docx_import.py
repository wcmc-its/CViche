"""Stage 6 section modules must not sys.exit(1) when python-docx is absent (#544).

Six section modules under `stage6/sections/` guarded their `docx` import with
`except ImportError: print(...); sys.exit(1)`. `SystemExit` is not a subclass
of `Exception`, so it passes straight through every `except Exception` in the
codebase -- it kills `run_full_pipeline.py`'s per-stage degrade-and-continue
loop, and can kill a web worker process at import time. python-docx is a hard
dependency of stage 6, so the fix lets the ImportError propagate (with the
install hint chained onto it) instead of swallowing it into a process exit.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_section_docx_import.py -p no:cacheprovider
"""

import builtins
import importlib
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

#: The six modules that used to call sys.exit(1) on a missing docx import.
SECTION_MODULES = (
    "unified_pipeline.stage6.sections.bibliography",
    "unified_pipeline.stage6.sections.memberships",
    "unified_pipeline.stage6.sections.mentoring",
    "unified_pipeline.stage6.sections.personal_data",
    "unified_pipeline.stage6.sections.research_summary",
    "unified_pipeline.stage6.sections.research_support",
)


@pytest.mark.parametrize("module_name", SECTION_MODULES)
def test_module_still_imports_normally(module_name):
    """With python-docx actually installed, importing each module still works."""
    module = importlib.import_module(module_name)
    assert module is not None


@pytest.mark.parametrize("module_name", SECTION_MODULES)
def test_missing_docx_raises_importerror_not_systemexit(module_name, monkeypatch):
    """Blocking the docx import must surface ImportError, never SystemExit."""
    real_import = builtins.__import__

    def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "docx" or name.startswith("docx."):
            raise ImportError(f"simulated missing dependency: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    module = importlib.import_module(module_name)
    try:
        with pytest.raises(ImportError, match="python-docx is required for stage 6"):
            importlib.reload(module)
    finally:
        # Restore real docx access before reloading so the module's class
        # definitions are back in their normal, fully-loaded state for
        # whatever test runs next.
        monkeypatch.undo()
        importlib.reload(module)


if __name__ == "__main__":
    for _name in SECTION_MODULES:
        test_module_still_imports_normally(_name)
    print("OK")
