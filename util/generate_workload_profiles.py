#!/usr/bin/env python3
"""Sync "Available Profiles by Harness" in workload/README.md.

Each ``workload/profiles/<harness>/`` directory in the git index (so
staged additions/deletions count) gets a ``#### <harness> (N profiles)``
subsection whose table lists every profile file in that directory.

The descriptive columns can't be derived from the profile files, so
the table is synced rather than regenerated:

  - existing rows keep their order and hand-written cells
  - rows for profiles that no longer exist are dropped
  - new profiles are appended with only the Profile cell filled in
  - ``(N profiles)`` counts are corrected
  - new harness directories get a new subsection; subsections for
    removed directories are dropped
  - prose around each table is left untouched

Run modes:

  python util/generate_workload_profiles.py           # rewrite README
  python util/generate_workload_profiles.py --check   # exit non-zero if stale

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
_PROFILES_DIR = "workload/profiles"

HEADING = "### Available Profiles by Harness"
DEFAULT_TABLE_HEADER = (
    "| Profile | Load Pattern | Data Type | Description |\n"
    "|---------|-------------|-----------|-------------|\n"
)

_HARNESS_HEADING = re.compile(
    r"^#### (?P<name>\S+)(?: \(\d+ profiles?\))?$", re.MULTILINE
)
_ROW_PROFILE = re.compile(r"^\|\s*`(?P<name>[^`]+)`")


def profiles_by_harness(repo_root: Path) -> dict[str, list[str]]:
    """Map harness -> profile filenames, from the git index."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", _PROFILES_DIR],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    result: dict[str, list[str]] = {}
    prefix = _PROFILES_DIR + "/"
    for path in filter(None, out.split("\0")):
        parts = path[len(prefix) :].split("/")
        if len(parts) == 2:  # direct children of a harness dir only
            result.setdefault(parts[0], []).append(parts[1])
    return {h: sorted(files) for h, files in result.items()}


def _section_bounds(readme: str) -> tuple[int, int]:
    """Offsets of the body under ``HEADING`` up to the next ##/### heading."""
    start = readme.index(HEADING + "\n") + len(HEADING) + 1
    nxt = re.compile(r"^#{2,3} ", re.MULTILINE).search(readme, start)
    return start, nxt.start() if nxt else len(readme)


def _heading(harness: str, count: int) -> str:
    return f"#### {harness} ({count} profile{'' if count == 1 else 's'})"


def sync_table(body: str, profiles: list[str]) -> str:
    """Sync the first table in a subsection ``body`` with ``profiles``."""
    lines = body.split("\n")
    header_idx = next(
        (i for i, line in enumerate(lines) if line.startswith("| Profile")), None
    )
    if header_idx is None:
        table = DEFAULT_TABLE_HEADER + "".join(_empty_row(p, 4) for p in profiles)
        return "\n\n" + table + body if body.strip() else "\n\n" + table

    ncols = lines[header_idx].count("|") - 1
    end = header_idx + 2  # skip header + separator
    while end < len(lines) and lines[end].startswith("|"):
        end += 1

    wanted = set(profiles)
    kept: list[str] = []
    seen: set[str] = set()
    for row in lines[header_idx + 2 : end]:
        m = _ROW_PROFILE.match(row)
        if m and m.group("name") in wanted and m.group("name") not in seen:
            kept.append(row)
            seen.add(m.group("name"))
    added = [_empty_row(p, ncols).rstrip("\n") for p in profiles if p not in seen]

    return "\n".join(lines[: header_idx + 2] + kept + added + lines[end:])


def _empty_row(profile: str, ncols: int) -> str:
    return f"| `{profile}` |" + "  |" * (ncols - 1) + "\n"


def render_section(section: str, harnesses: dict[str, list[str]]) -> str:
    """Rewrite the body of the Available Profiles section."""
    matches = list(_HARNESS_HEADING.finditer(section))
    preamble = section[: matches[0].start()] if matches else section
    preamble = preamble.rstrip("\n") + "\n\n" if preamble.strip() else "\n"

    existing: dict[str, str] = {}
    order: list[str] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(section)
        existing[m.group("name")] = section[m.end() : end]
        order.append(m.group("name"))

    order = [h for h in order if h in harnesses]
    order += sorted(h for h in harnesses if h not in existing)

    parts = []
    for h in order:
        body = sync_table(existing.get(h, ""), harnesses[h])
        body = body.rstrip("\n") + "\n\n"
        parts.append(_heading(h, len(harnesses[h])) + body)
    return preamble + "".join(parts)


def render_readme(readme: str, harnesses: dict[str, list[str]]) -> str:
    start, end = _section_bounds(readme)
    return readme[:start] + render_section(readme[start:end], harnesses) + readme[end:]


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
    new_content = render_readme(existing, profiles_by_harness(args.repo_root))

    if new_content == existing:
        return 0
    readme_path.write_text(new_content, encoding="utf-8")
    if args.check:
        sys.stderr.write(
            f"\n{_README} Available Profiles by Harness was out of date and has "
            f"been regenerated.\nNew profiles have empty description cells; "
            f"please fill them in, `git add {_README}` and re-commit.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
