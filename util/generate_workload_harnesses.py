#!/usr/bin/env python3
"""Sync the "Available Harnesses" table in workload/README.md.

A harness is any ``workload/harnesses/<name>-llm-d-benchmark.{sh,py}``
entry-point script in the git index (so staged additions/deletions
count). The Benchmark Tool and Purpose columns are hand-written, so
the table is synced rather than regenerated:

  - existing rows keep their order and hand-written cells
  - the Script cell is always refreshed from the actual filename
  - rows for harnesses whose script is gone are dropped
  - new harnesses are appended with empty Benchmark Tool / Purpose
  - prose after the table is left untouched

Run modes:

  python util/generate_workload_harnesses.py           # rewrite README
  python util/generate_workload_harnesses.py --check   # exit non-zero if stale

The ``--check`` form is what the precommit hook invokes: it rewrites
the README, fails the commit if the section changed, and asks the user
to ``git add`` and re-commit.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_README = Path("workload") / "README.md"
_HARNESSES_DIR = "workload/harnesses"

HEADING = "### Available Harnesses"

_SCRIPT = re.compile(r"^(?P<name>.+)-llm-d-benchmark\.(?:sh|py)$")
_ROW_HARNESS = re.compile(r"^\|\s*`(?P<name>[^`]+)`")


def harness_scripts(repo_root: Path) -> dict[str, str]:
    """Map harness name -> entry-point script filename, from the git index."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", _HARNESSES_DIR],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    result: dict[str, str] = {}
    prefix = _HARNESSES_DIR + "/"
    for path in filter(None, out.split("\0")):
        filename = path[len(prefix) :]
        m = _SCRIPT.match(filename)
        if m and "/" not in filename:
            result[m.group("name")] = filename
    return dict(sorted(result.items()))


def _section_bounds(readme: str) -> tuple[int, int]:
    """Offsets of the body under ``HEADING`` up to the next ##/### heading."""
    start = readme.index(HEADING + "\n") + len(HEADING) + 1
    nxt = re.compile(r"^#{2,3} ", re.MULTILINE).search(readme, start)
    return start, nxt.start() if nxt else len(readme)


def sync_table(section: str, scripts: dict[str, str]) -> str:
    """Sync the first table in ``section`` with ``scripts``."""
    lines = section.split("\n")
    header_idx = next(i for i, line in enumerate(lines) if line.startswith("| "))
    ncols = lines[header_idx].count("|") - 1
    end = header_idx + 2  # skip header + separator
    while end < len(lines) and lines[end].startswith("|"):
        end += 1

    rows: list[str] = []
    seen: set[str] = set()
    for row in lines[header_idx + 2 : end]:
        m = _ROW_HARNESS.match(row)
        if not m or m.group("name") not in scripts or m.group("name") in seen:
            continue
        name = m.group("name")
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        cells += [""] * (ncols - len(cells))
        cells[1] = f"`{scripts[name]}`"
        rows.append(_row(cells))
        seen.add(name)
    for name, script in scripts.items():
        if name not in seen:
            rows.append(_row([f"`{name}`", f"`{script}`"] + [""] * (ncols - 2)))

    return "\n".join(lines[: header_idx + 2] + rows + lines[end:])


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def render_readme(readme: str, scripts: dict[str, str]) -> str:
    start, end = _section_bounds(readme)
    return readme[:start] + sync_table(readme[start:end], scripts) + readme[end:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Rewrite; exit non-zero if the section changed (precommit mode).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=_REPO_ROOT,
        help="Repository root (default: parent of util/).",
    )
    args = parser.parse_args(argv)

    readme_path = args.repo_root / _README
    existing = readme_path.read_text(encoding="utf-8")
    new_content = render_readme(existing, harness_scripts(args.repo_root))

    if new_content == existing:
        return 0
    readme_path.write_text(new_content, encoding="utf-8")
    if args.check:
        sys.stderr.write(
            f"\n{_README} Available Harnesses was out of date and has been "
            f"regenerated.\nNew harnesses have empty Benchmark Tool / Purpose "
            f"cells; please fill them in, `git add {_README}` and re-commit.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
