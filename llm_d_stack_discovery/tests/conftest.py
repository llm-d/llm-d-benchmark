"""Shared pytest fixtures for llm_d_stack_discovery tests.

Also ensures the in-repo ``llmd_benchmark_report`` package (under
``benchmark-report/`` at the repo root) is importable regardless of where
pytest is invoked from and without requiring a pip install, since our
tests consume it for shared schema definitions.
"""

import sys
from pathlib import Path

# Repo root is three levels above this file:
#   llm_d_stack_discovery/tests/conftest.py -> llm_d_stack_discovery/tests
#   -> llm_d_stack_discovery -> <repo-root>
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "benchmark-report"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
