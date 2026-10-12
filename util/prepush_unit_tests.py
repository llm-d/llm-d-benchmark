#!/usr/bin/env python3
"""Run unit tests before a push.

testmon runs only the tests affected by changed python files; anything
it cannot see (non-python files, deletions, test-setup changes) falls
back to the full suite. CI always runs everything.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RELEVANT_PREFIXES = (
    "llmdbenchmark/",
    "tests/",
    "workload/",
    "config/",
    "benchmark-report/",
)

# Changes to these always run the full suite.
RELEVANT_ROOT_FILES = (
    "pyproject.toml",
    "uv.lock",
    "install.sh",
    ".pre-commit_requirements.txt",
    "util/prepush_unit_tests.py",
    "util/generate_sbom.py",
)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def relevant(path: str) -> bool:
    return any(path.startswith(p) for p in RELEVANT_PREFIXES) or (
        path in RELEVANT_ROOT_FILES
    )


def _git(root: Path, args: list[str]) -> list[str] | None:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.splitlines()


def pushed_files(
    root: Path,
) -> tuple[list[str] | None, list[str] | None, bool]:
    """(changed, deleted, hook_mode) for the push; Nones when unknown.

    For manual debugging, pass files directly:
        python util/prepush_unit_tests.py llmdbenchmark/foo.py
    """
    if len(sys.argv) > 1:
        return sys.argv[1:], [], False

    from_ref = os.environ.get("PRE_COMMIT_FROM_REF")
    to_ref = os.environ.get("PRE_COMMIT_TO_REF")
    if from_ref and to_ref:
        changed = _git(
            root, ["diff", "--name-only", "--diff-filter=ACMR", from_ref, to_ref]
        )
        deleted = _git(
            root, ["diff", "--name-only", "--diff-filter=D", from_ref, to_ref]
        )
        if changed is not None and deleted is not None:
            return changed, deleted, True

    return None, None, False


def resolve_python(root: Path) -> str:
    venv = root / ".venv" / "bin" / "python"
    if venv.is_file():
        return str(venv)
    return sys.executable


def has_testmon(python: str) -> bool:
    proc = subprocess.run(
        [python, "-c", "import testmon"],
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def _summarize(files: list[str], max_shown: int = 3) -> str:
    shown = ", ".join(files[:max_shown])
    if len(files) > max_shown:
        shown += f" (+{len(files) - max_shown} more)"
    return shown


def run(cmd: list[str], env: dict[str, str]) -> int:
    proc = subprocess.run(cmd, cwd=repo_root(), env=env, check=False)
    if proc.returncode != 0:
        return proc.returncode
    proc = subprocess.run(
        _package_cmd(cmd[0]),
        cwd=repo_root() / "benchmark-report",
        env=env,
        check=False,
    )
    return proc.returncode


def main() -> int:
    root = repo_root()
    python = resolve_python(root)

    changed, deleted, hook_mode = pushed_files(root)
    if changed is None:
        print("Unit tests: full suite (could not determine changed files)")
        return run(_full_cmd(python), dict(os.environ))

    changed = [f for f in changed if relevant(f)]
    deleted = [f for f in deleted if relevant(f)]
    setup_files = [f for f in changed if f in RELEVANT_ROOT_FILES]
    data_files = [
        f for f in changed if f not in RELEVANT_ROOT_FILES and not f.endswith(".py")
    ]

    if deleted:
        print(f"Unit tests: full suite (deleted: {_summarize(deleted)})")
        return run(_full_cmd(python), dict(os.environ))

    if setup_files or data_files:
        why = []
        if setup_files:
            why.append(f"test setup changed: {_summarize(setup_files)}")
        if data_files:
            why.append(f"non-python files changed: {_summarize(data_files)}")
        print(f"Unit tests: full suite ({'; '.join(why)})")
        return run(_full_cmd(python), dict(os.environ))

    if not changed:
        if hook_mode:
            print("Unit tests: nothing to do (nothing test-relevant in the push)")
            return 0
        print("Unit tests: full suite (no relevant files among the given ones)")
        return run(_full_cmd(python), dict(os.environ))

    if has_testmon(python):
        print(f"Unit tests: testmon selection (python changes: {_summarize(changed)})")
        # sysmon cannot attribute coverage per test; testmon skips affected tests.
        env = dict(os.environ, COVERAGE_CORE="ctrace")
        return run(_testmon_cmd(python), env)

    print(
        "Unit tests: full suite "
        "(pytest-testmon not installed, see .pre-commit_requirements.txt)"
    )
    return run(_full_cmd(python), dict(os.environ))


def _full_cmd(python: str) -> list[str]:
    # Mirrors the `unit-tests` job in ci-pr-benchmark.yaml.
    return [
        python,
        "-m",
        "pytest",
        "tests/",
        "-x",
        "-q",
        "-n4",
    ]


def _package_cmd(python: str) -> list[str]:
    # Mirrors the `benchmark-report-tests` job. Run from benchmark-report/, the
    # checkout's package shadows the pinned release installed in this env.
    return [python, "-m", "pytest", "tests/", "-x", "-q", "-n4"]


def _testmon_cmd(python: str) -> list[str]:
    return [*_full_cmd(python), "--testmon"]


if __name__ == "__main__":
    sys.exit(main())
