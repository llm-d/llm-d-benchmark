#!/usr/bin/env python3
"""Regenerate the "Directory Layout" tree in workload/README.md.

The tree is rebuilt from the files git tracks (index, so staged
additions/deletions count) under the directories in ``ROOTS``.
Trailing ``# comments`` already present in the README are preserved,
keyed by path, so descriptions only need to be written once; new
entries appear without a comment and can be annotated by hand.

Run modes:

  python util/generate_workload_layout.py           # rewrite README
  python util/generate_workload_layout.py --check   # exit non-zero if stale

The ``--check`` form is what the precommit hook invokes: it rewrites
the README, fails the commit if the section changed, and asks the user
to ``git add`` and re-commit.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_README = Path("workload") / "README.md"

# Top-level directories shown in the tree, in display order.
ROOTS: tuple[str, ...] = ("experiments", "workload")

# Tracked files that are never listed.
EXCLUDED_NAMES: frozenset[str] = frozenset({"README.md", "__init__.py"})

HEADING = "## Directory Layout"
COMMENT_COLUMN = 44
INDENT = "  "


def tracked_files(repo_root: Path, roots: tuple[str, ...] = ROOTS) -> list[str]:
    """Paths in the git index under ``roots`` (staged state, not HEAD)."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *roots],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [p for p in out.split("\0") if p and Path(p).name not in EXCLUDED_NAMES]


def parse_comments(block: str) -> dict[str, str]:
    """Map ``path`` (dirs end in ``/``) -> trailing comment in an existing tree."""
    comments: dict[str, str] = {}
    stack: list[str] = []  # directory names, one per indent level
    for line in block.splitlines():
        if not line.strip():
            continue
        body, _, comment = line.partition("#")
        name = body.strip()
        if not name:
            continue
        depth = (len(body) - len(body.lstrip(" "))) // len(INDENT)
        del stack[depth:]
        path = "".join(stack) + name
        if name.endswith("/"):
            stack.append(name)
        if comment.strip():
            comments[path] = comment.strip()
    return comments


def build_tree(paths: list[str], comments: dict[str, str]) -> str:
    """Render ``paths`` as an indented tree; directories before files."""
    tree: dict = {}
    for p in paths:
        node = tree
        *dirs, leaf = p.split("/")
        for d in dirs:
            node = node.setdefault(d + "/", {})
        node[leaf] = None

    lines: list[str] = []

    def emit(node: dict, prefix: str, depth: int) -> None:
        dirs = sorted(k for k, v in node.items() if v is not None)
        files = sorted(k for k, v in node.items() if v is None)
        for name in dirs + files:
            text = INDENT * depth + name
            comment = comments.get(prefix + name)
            if comment:
                text = f"{text.ljust(COMMENT_COLUMN - 1)} # {comment}"
            lines.append(text)
            if node[name] is not None:
                emit(node[name], prefix + name, depth + 1)

    roots = [r + "/" for r in ROOTS if r + "/" in tree]
    for i, root in enumerate(roots):
        if i:
            lines.append("")
        emit({root: tree[root]}, "", 0)
    return "\n".join(lines) + "\n"


def _section_bounds(readme: str) -> tuple[int, int]:
    """Character offsets of the code block body under ``HEADING``."""
    head = readme.index(HEADING + "\n")
    fence = readme.index("```\n", head) + len("```\n")
    end = readme.index("```", fence)
    return fence, end


def render_readme(readme: str, paths: list[str]) -> str:
    start, end = _section_bounds(readme)
    comments = parse_comments(readme[start:end])
    return readme[:start] + build_tree(paths, comments) + readme[end:]


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
    new_content = render_readme(existing, tracked_files(args.repo_root))

    if new_content == existing:
        return 0
    readme_path.write_text(new_content, encoding="utf-8")
    if args.check:
        sys.stderr.write(
            f"\n{_README} Directory Layout was out of date and has been "
            f"regenerated.\nPlease `git add {_README}` and re-commit.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
