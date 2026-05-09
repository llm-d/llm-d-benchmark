"""Checks that the planner git pin stays consistent across tracked files."""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = PROJECT_ROOT / "install.sh"
README = PROJECT_ROOT / "README.md"
UPSTREAM_VERSIONS = PROJECT_ROOT / "docs" / "upstream-versions.md"

_PLANNER_GIT_RE = re.compile(
    r'^PLANNER_GIT="git\+https://github\.com/llm-d-incubation/llm-d-planner\.git@(?P<sha>[0-9a-f]{40})"$',
)
_README_PIN_RE = re.compile(
    r'pip install "git\+https://github\.com/llm-d-incubation/llm-d-planner\.git@(?P<sha>[0-9a-f]{40})"',
)
_DOC_PIN_RE = re.compile(
    r"\| \*\*llm-d-planner \(git\)\*\* \| `(?P<sha>[0-9a-f]{40})` \| commit SHA \| `install\.sh` line (?P<line>\d+) \(`PLANNER_GIT`\) \|",
)


def test_planner_pin_is_kept_in_sync() -> None:
    install_sha = None
    install_line = None

    for line_number, line in enumerate(INSTALL_SH.read_text(encoding="utf-8").splitlines(), start=1):
        match = _PLANNER_GIT_RE.match(line)
        if match:
            install_sha = match.group("sha")
            install_line = line_number
            break

    assert install_sha is not None
    assert install_line is not None

    readme_match = _README_PIN_RE.search(README.read_text(encoding="utf-8"))
    assert readme_match is not None
    assert readme_match.group("sha") == install_sha

    doc_match = _DOC_PIN_RE.search(UPSTREAM_VERSIONS.read_text(encoding="utf-8"))
    assert doc_match is not None
    assert doc_match.group("sha") == install_sha
    assert int(doc_match.group("line")) == install_line
