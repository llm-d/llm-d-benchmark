"""Shared pytest fixtures for llm_d_stack_discovery tests.

Also ensures the parent repository's ``llmdbenchmark`` package is
importable regardless of where pytest is invoked from, since our tests
consume ``llmdbenchmark.analysis.benchmark_report`` for shared schema
definitions.
"""

import sys
from pathlib import Path

# Repo root is three levels above this file:
#   llm_d_stack_discovery/tests/conftest.py -> llm_d_stack_discovery/tests
#   -> llm_d_stack_discovery -> <repo-root>
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
