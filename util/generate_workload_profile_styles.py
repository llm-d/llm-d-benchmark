#!/usr/bin/env python3
"""Regenerate "Profile Configuration Styles" in workload/README.md.

One entry per ``workload/profiles/<harness>/`` directory in the git
index, each with a YAML example taken from a real profile file so the
examples can't drift from the schemas the harnesses actually use:

  - example source: ``sanity_random.yaml.in``, else the first
    ``sanity*`` file, else the first profile alphabetically
  - comment-only lines, blank lines and ``---`` markers are dropped,
    and the example is truncated to ``MAX_EXAMPLE_LINES``
  - harnesses follow the order of "Available Profiles by Harness";
    any not listed there are appended alphabetically
  - the hand-written intro sentence of each entry is preserved; new
    harnesses get ``DEFAULT_INTRO``

Run modes:

  python util/generate_workload_profile_styles.py           # rewrite README
  python util/generate_workload_profile_styles.py --check   # exit non-zero if stale

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

HEADING = "### Profile Configuration Styles"
PROFILES_HEADING = "### Available Profiles by Harness"
DEFAULT_INTRO = "Example profile"
MAX_EXAMPLE_LINES = 25

_ENTRY = re.compile(
    r"^\*\*(?P<name>[^*]+)\*\* -- (?P<intro>.*?)(?: \(from `[^`]+`\))?:\n"
    r"```yaml\n.*?^```\n",
    re.MULTILINE | re.DOTALL,
)
_HARNESS_HEADING = re.compile(r"^#### (?P<name>\S+)", re.MULTILINE)


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


def pick_example(files: list[str]) -> str:
    """Choose the profile shown as a harness's example."""
    if "sanity_random.yaml.in" in files:
        return "sanity_random.yaml.in"
    sanity = [f for f in files if f.startswith("sanity")]
    return (sanity or files)[0]


def example_body(text: str) -> str:
    """Strip noise from a profile and cap its length."""
    lines = [
        line.rstrip()
        for line in text.splitlines()
        if line.strip() and line.strip() != "---" and not line.lstrip().startswith("#")
    ]
    if len(lines) > MAX_EXAMPLE_LINES:
        lines = lines[:MAX_EXAMPLE_LINES] + ["# ..."]
    return "\n".join(lines) + "\n"


def _section_bounds(readme: str) -> tuple[int, int]:
    """Offsets of the body under ``HEADING``, before its ``---`` rule."""
    start = readme.index(HEADING + "\n") + len(HEADING) + 1
    nxt = re.compile(r"^(?:#{2,3} |---$)", re.MULTILINE).search(readme, start)
    return start, nxt.start() if nxt else len(readme)


def harness_order(readme: str, harnesses: list[str]) -> list[str]:
    """Order of "Available Profiles by Harness", then the rest sorted."""
    order: list[str] = []
    if PROFILES_HEADING + "\n" in readme:
        start = readme.index(PROFILES_HEADING + "\n")
        nxt = re.compile(r"^#{2,3} ", re.MULTILINE).search(
            readme, start + len(PROFILES_HEADING) + 1
        )
        end = nxt.start() if nxt else len(readme)
        order = [
            m.group("name")
            for m in _HARNESS_HEADING.finditer(readme, start, end)
            if m.group("name") in harnesses
        ]
    return order + sorted(h for h in harnesses if h not in order)


def render_section(
    section: str, order: list[str], examples: dict[str, tuple[str, str]]
) -> str:
    """Rebuild the section; ``examples`` maps harness -> (file, body)."""
    first = _ENTRY.search(section)
    preamble = section[: first.start()] if first else section
    preamble = preamble.rstrip("\n") + "\n\n" if preamble.strip() else "\n"
    intros = {m.group("name"): m.group("intro") for m in _ENTRY.finditer(section)}

    entries = []
    for h in order:
        source, body = examples[h]
        intro = intros.get(h, DEFAULT_INTRO)
        entries.append(f"**{h}** -- {intro} (from `{source}`):\n```yaml\n{body}```\n")
    return preamble + "\n".join(entries) + "\n"


def render_readme(readme: str, repo_root: Path) -> str:
    harnesses = profiles_by_harness(repo_root)
    examples = {}
    for h, files in harnesses.items():
        source = pick_example(files)
        text = (repo_root / _PROFILES_DIR / h / source).read_text(encoding="utf-8")
        examples[h] = (source, example_body(text))
    start, end = _section_bounds(readme)
    section = render_section(
        readme[start:end], harness_order(readme, list(harnesses)), examples
    )
    return readme[:start] + section + readme[end:]


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
    new_content = render_readme(existing, args.repo_root)

    if new_content == existing:
        return 0
    readme_path.write_text(new_content, encoding="utf-8")
    if args.check:
        sys.stderr.write(
            f"\n{_README} Profile Configuration Styles was out of date and has "
            f"been regenerated.\nPlease `git add {_README}` and re-commit.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
