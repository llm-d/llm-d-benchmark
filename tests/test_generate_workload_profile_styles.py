"""Tests for util/generate_workload_profile_styles.py."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "util" / "generate_workload_profile_styles.py"


@pytest.fixture(scope="module")
def styles():
    spec = importlib.util.spec_from_file_location(
        "generate_workload_profile_styles", _SCRIPT_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_workload_profile_styles"] = module
    spec.loader.exec_module(module)
    return module


_README = """\
### Available Profiles by Harness

#### beta (1 profile)

#### alpha (2 profiles)

### Profile Configuration Styles

Each harness uses a different YAML schema for its profiles:

**alpha** -- Hand-written alpha intro:
```yaml
stale: true
```

**removed** -- Harness that no longer exists:
```yaml
x: 1
```

---

## Next
"""


def _make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    for rel, text in files.items():
        path = tmp_path / "workload" / "profiles" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git("add", "workload")
    return tmp_path


def test_pick_example_prefers_sanity_random(styles):
    assert styles.pick_example(["a.yaml", "sanity_random.yaml.in"]) == (
        "sanity_random.yaml.in"
    )
    assert styles.pick_example(["a.yaml", "sanity.yaml.in"]) == "sanity.yaml.in"
    assert styles.pick_example(["b.yaml", "a.yaml"]) == "b.yaml"


def test_example_body_strips_noise_and_truncates(styles, monkeypatch):
    text = "---\n# header\n\nkey: 1  # inline kept\n  # indented comment\nb: 2\n"
    assert styles.example_body(text) == "key: 1  # inline kept\nb: 2\n"

    monkeypatch.setattr(styles, "MAX_EXAMPLE_LINES", 2)
    assert styles.example_body("a: 1\nb: 2\nc: 3\n") == "a: 1\nb: 2\n# ...\n"


def test_render_readme(styles, tmp_path):
    repo = _make_repo(
        tmp_path,
        {
            "alpha/sanity_random.yaml.in": "# comment\nfresh: true\n",
            "alpha/other.yaml.in": "other: true\n",
            "beta/b.yaml": "b: 1\n",
            "gamma/g.yaml": "g: 1\n",
        },
    )
    out = styles.render_readme(_README, repo)
    start, end = styles._section_bounds(out)
    section = out[start:end]

    assert section.startswith("\nEach harness uses a different YAML schema")
    assert (
        "**alpha** -- Hand-written alpha intro (from `sanity_random.yaml.in`):\n"
        "```yaml\nfresh: true\n```\n"
    ) in section
    assert "stale: true" not in section
    assert "**removed**" not in section
    assert f"**gamma** -- {styles.DEFAULT_INTRO} (from `g.yaml`):" in section
    # Available Profiles order first (beta, alpha), then the rest sorted
    assert section.index("**beta**") < section.index("**alpha**")
    assert section.index("**alpha**") < section.index("**gamma**")
    assert out.endswith("```\n\n---\n\n## Next\n")
    assert styles.render_readme(out, repo) == out


def test_repo_readme_is_up_to_date(styles):
    readme = (_REPO_ROOT / "workload" / "README.md").read_text(encoding="utf-8")
    assert styles.render_readme(readme, _REPO_ROOT) == readme
