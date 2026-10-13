"""Tests for util/generate_workload_layout.py."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "util" / "generate_workload_layout.py"


@pytest.fixture(scope="module")
def layout():
    spec = importlib.util.spec_from_file_location(
        "generate_workload_layout", _SCRIPT_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_workload_layout"] = module
    spec.loader.exec_module(module)
    return module


_README = """\
# Workload System

## Directory Layout

```
experiments/                                # DoE experiments
  old.yaml                                  # Removed experiment
  keep.yaml                                 # Kept experiment

workload/
  harnesses/                                # Entry points
    a.sh                                    # Harness A
```

---

## Next Section

```
untouched/
```
"""


def test_parse_comments_keys_by_full_path(layout):
    start, end = layout._section_bounds(_README)
    comments = layout.parse_comments(_README[start:end])
    assert comments == {
        "experiments/": "DoE experiments",
        "experiments/old.yaml": "Removed experiment",
        "experiments/keep.yaml": "Kept experiment",
        "workload/harnesses/": "Entry points",
        "workload/harnesses/a.sh": "Harness A",
    }


def test_render_adds_removes_and_keeps_comments(layout):
    paths = [
        "experiments/keep.yaml",
        "experiments/new.yaml",
        "workload/harnesses/a.sh",
        "workload/profiles/x/p.yaml.in",
    ]
    out = layout.render_readme(_README, paths)
    start, end = layout._section_bounds(out)
    block = out[start:end]

    assert "old.yaml" not in block
    assert "  new.yaml\n" in block
    assert "keep.yaml                                 # Kept experiment" in block
    assert "    a.sh                                    # Harness A" in block
    # directories come before files at the same level
    assert block.index("  harnesses/") < block.index("  profiles/")
    assert "      p.yaml.in\n" in block
    # content outside the section is untouched
    assert out.endswith(_README[_README.index("```\n\n---") :])


def test_render_is_idempotent(layout):
    paths = ["experiments/keep.yaml", "workload/harnesses/a.sh"]
    once = layout.render_readme(_README, paths)
    assert layout.render_readme(once, paths) == once


def test_long_names_keep_a_space_before_comment(layout):
    name = "x" * 60 + ".yaml"
    out = layout.build_tree([f"experiments/{name}"], {f"experiments/{name}": "long"})
    assert f"  {name} # long" in out


def test_tracked_files_uses_index_and_excludes(layout, tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    for rel in ("workload/README.md", "workload/__init__.py", "workload/h/a.sh"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("")
    (tmp_path / "workload/untracked.sh").write_text("")
    git("add", "workload/README.md", "workload/__init__.py", "workload/h/a.sh")

    assert layout.tracked_files(tmp_path) == ["workload/h/a.sh"]


def test_repo_readme_is_up_to_date(layout):
    readme = (_REPO_ROOT / "workload" / "README.md").read_text(encoding="utf-8")
    assert layout.render_readme(readme, layout.tracked_files(_REPO_ROOT)) == readme
