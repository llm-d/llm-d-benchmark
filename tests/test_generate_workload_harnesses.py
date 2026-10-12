"""Tests for util/generate_workload_harnesses.py."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "util" / "generate_workload_harnesses.py"


@pytest.fixture(scope="module")
def harnesses():
    spec = importlib.util.spec_from_file_location(
        "generate_workload_harnesses", _SCRIPT_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_workload_harnesses"] = module
    spec.loader.exec_module(module)
    return module


_README = """\
### Available Harnesses

| Harness Name | Script | Benchmark Tool | Purpose |
|-------------|--------|----------------|---------|
| `beta` | `beta-llm-d-benchmark.sh` | `beta-tool` | Beta purpose |
| `gone` | `gone-llm-d-benchmark.sh` | `gone-tool` | Removed harness |
| `alpha` | `alpha-llm-d-benchmark.sh` | `alpha-tool` | Alpha purpose |

> Note after the table.

### Harness Script Contract

Untouched.
"""


def test_sync_keeps_rows_refreshes_script_drops_and_appends(harnesses):
    scripts = {
        "alpha": "alpha-llm-d-benchmark.sh",
        "beta": "beta-llm-d-benchmark.py",
        "new": "new-llm-d-benchmark.sh",
    }
    out = harnesses.render_readme(_README, scripts)

    # existing order and hand-written cells kept; Script follows the file
    assert (
        "| `beta` | `beta-llm-d-benchmark.py` | `beta-tool` | Beta purpose |\n"
        "| `alpha` | `alpha-llm-d-benchmark.sh` | `alpha-tool` | Alpha purpose |\n"
        "| `new` | `new-llm-d-benchmark.sh` |  |  |\n"
    ) in out
    assert "gone" not in out
    assert "> Note after the table." in out
    assert out.endswith("### Harness Script Contract\n\nUntouched.\n")
    assert harnesses.render_readme(out, scripts) == out


def test_harness_scripts_uses_index_and_entry_point_pattern(harnesses, tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    base = tmp_path / "workload" / "harnesses"
    base.mkdir(parents=True)
    for name in (
        "b-llm-d-benchmark.sh",
        "a-llm-d-benchmark.py",
        "helper.py",
        "collect_metrics.sh",
        "untracked-llm-d-benchmark.sh",
    ):
        (base / name).write_text("")
    git(
        "add",
        *(
            f"workload/harnesses/{n}"
            for n in (
                "b-llm-d-benchmark.sh",
                "a-llm-d-benchmark.py",
                "helper.py",
                "collect_metrics.sh",
            )
        ),
    )

    assert harnesses.harness_scripts(tmp_path) == {
        "a": "a-llm-d-benchmark.py",
        "b": "b-llm-d-benchmark.sh",
    }


def test_repo_readme_is_up_to_date(harnesses):
    readme = (_REPO_ROOT / "workload" / "README.md").read_text(encoding="utf-8")
    assert (
        harnesses.render_readme(readme, harnesses.harness_scripts(_REPO_ROOT)) == readme
    )
