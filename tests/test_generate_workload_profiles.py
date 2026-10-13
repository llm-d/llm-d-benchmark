"""Tests for util/generate_workload_profiles.py."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "util" / "generate_workload_profiles.py"


@pytest.fixture(scope="module")
def profiles():
    spec = importlib.util.spec_from_file_location(
        "generate_workload_profiles", _SCRIPT_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_workload_profiles"] = module
    spec.loader.exec_module(module)
    return module


_README = """\
## Workload Profiles

### Available Profiles by Harness

#### alpha (2 profiles)

| Profile | Load Pattern | Data Type | Description |
|---------|-------------|-----------|-------------|
| `sanity.yaml.in` | Constant | Random | Quick check |
| `gone.yaml.in` | Variable | Synthetic | Removed profile |

#### removed (1 profile)

| Profile | Description |
|---------|-------------|
| `x.yaml.in` | Harness that no longer exists |

#### beta (1 profile)

Prose before the table.

| Profile | Tasks | Description |
|---------|-------|-------------|
| `acc.yaml.in` | mmlu | Accuracy |

Prose after the table.

```bash
# not a heading
```

### Profile Configuration Styles

Untouched.
"""


def _section(profiles, readme):
    start, end = profiles._section_bounds(readme)
    return readme[start:end]


def test_sync_keeps_rows_drops_removed_and_appends_new(profiles):
    out = profiles.render_readme(
        _README,
        {
            "alpha": ["gone2.yaml.in", "sanity.yaml.in"],
            "beta": ["acc.yaml.in", "smoke.yaml.in"],
        },
    )
    section = _section(profiles, out)

    assert "#### alpha (2 profiles)" in section
    assert "| `sanity.yaml.in` | Constant | Random | Quick check |" in section
    assert "gone.yaml.in`" not in section
    assert "| `gone2.yaml.in` |  |  |  |" in section
    # column count follows the existing header (3 columns for beta)
    assert "| `smoke.yaml.in` |  |  |\n" in section
    assert "#### removed" not in section
    # prose around the table is preserved
    assert "Prose before the table." in section
    assert "Prose after the table." in section
    assert "# not a heading" in section
    assert out.endswith("### Profile Configuration Styles\n\nUntouched.\n")


def test_new_harness_appended_with_default_table(profiles):
    out = profiles.render_readme(
        _README,
        {"alpha": ["sanity.yaml.in"], "beta": ["acc.yaml.in"], "aaa": ["p.yaml"]},
    )
    section = _section(profiles, out)

    assert "#### alpha (1 profile)\n" in section
    # existing harness order kept; new ones appended after them
    assert section.index("#### beta") < section.index("#### aaa")
    assert (
        "#### aaa (1 profile)\n\n" + profiles.DEFAULT_TABLE_HEADER + "| `p.yaml` |"
    ) in section


def test_render_is_idempotent(profiles):
    harnesses = {"alpha": ["new.yaml.in"], "beta": ["acc.yaml.in"], "zz": ["z.yaml"]}
    once = profiles.render_readme(_README, harnesses)
    assert profiles.render_readme(once, harnesses) == once


def test_profiles_by_harness_uses_index(profiles, tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    base = tmp_path / "workload" / "profiles"
    for rel in ("a/two.yaml.in", "a/one.yaml.in", "b/x.yaml", "a/nested/skip.yaml"):
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text("")
    (base / "b" / "untracked.yaml").write_text("")
    git("add", "workload/profiles/a", "workload/profiles/b/x.yaml")

    assert profiles.profiles_by_harness(tmp_path) == {
        "a": ["one.yaml.in", "two.yaml.in"],
        "b": ["x.yaml"],
    }


def test_repo_readme_is_up_to_date(profiles):
    readme = (_REPO_ROOT / "workload" / "README.md").read_text(encoding="utf-8")
    assert (
        profiles.render_readme(readme, profiles.profiles_by_harness(_REPO_ROOT))
        == readme
    )
