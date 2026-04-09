#!/usr/bin/env python3
"""Render staged scenarios via ``llmdbenchmark --dry-run plan``.

Pre-commit calls this once per commit (no file args). We ask git for
the staged file list and map paths to specs:

    config/specification/<name>.yaml.j2  -> <name>
    config/scenarios/<name>.yaml         -> <name>
    llmdbenchmark/<shared>/**, templates -> cicd/kind-sim (canary)
    nothing / docs-only                  -> cicd/kind-sim (baseline)

The exhaustive per-spec render lives in CI (ci-pr-plan-rendering-
validation.yaml); locally we only render what actually changed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TextIO


# Fallback spec for shared-path and docs-only commits. Matches what CI
# runs on every PR (ci-pr-benchmark.yaml).
CANARY_SPEC = "cicd/kind-sim"

# Touching anything starting with one of these means "this could affect
# every spec". We render the canary only — CI handles the full loop.
CANARY_TRIGGER_PREFIXES: tuple[str, ...] = (
    "config/templates/",
    "llmdbenchmark/parser/",
    "llmdbenchmark/plan/",
    "llmdbenchmark/executor/",
    "llmdbenchmark/utilities/",
    "llmdbenchmark/standup/",
    "llmdbenchmark/run/",
    "llmdbenchmark/teardown/",
    "llmdbenchmark/smoketests/",
    "llmdbenchmark/cli.py",
    "llmdbenchmark/config.py",
)

_SPEC_RE = re.compile(
    r"^config/(?:specification/(?P<spec>.+)\.yaml\.j2"
    r"|scenarios/(?P<scenario>.+)\.yaml)$"
)

# Heartbeat interval for the live-progress line. Keep >= 2s so fast
# renders never draw a heartbeat at all.
_HEARTBEAT_INTERVAL_S = 2.0


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def spec_name_from_path(path: str) -> str | None:
    m = _SPEC_RE.match(path)
    if not m:
        return None
    return m.group("spec") or m.group("scenario")


def needs_canary(path: str) -> bool:
    return any(path.startswith(p) for p in CANARY_TRIGGER_PREFIXES)


def spec_exists(root: Path, spec: str) -> bool:
    return (root / "config" / "specification" / f"{spec}.yaml.j2").is_file()


def collect_specs(files: list[str], root: Path) -> dict[str, str]:
    """Map the staged file list to ``{spec: reason}`` for display."""
    specs: dict[str, str] = {}
    shared: list[str] = []

    for f in files:
        name = spec_name_from_path(f)
        if name is not None:
            # Skip deletions — the .yaml.j2 is gone, nothing to render.
            if spec_exists(root, name):
                specs[name] = f"edited {f}"
            continue
        if needs_canary(f):
            shared.append(f)

    if shared and spec_exists(root, CANARY_SPEC) and CANARY_SPEC not in specs:
        head = shared[0]
        more = f" (+{len(shared) - 1} more)" if len(shared) > 1 else ""
        specs[CANARY_SPEC] = f"shared render path touched ({head}{more})"

    return dict(sorted(specs.items()))


def _get_staged_files(root: Path) -> list[str]:
    """Files staged for the next commit, minus deletions.

    Returns [] on any git error (not a git repo, git missing, nothing
    staged). Callers fall through to the baseline canary in that case.
    """
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, FileNotFoundError):
        return []
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _open_tty() -> TextIO | None:
    # pre-commit captures stdout/stderr and only flushes it at end of
    # hook, which makes slow renders look hung. Writing to /dev/tty
    # skips the capture and lands on the user's terminal live.
    # Returns None in CI (no controlling tty); caller falls back to
    # buffered-only output there.
    try:
        return open("/dev/tty", "w", buffering=1)
    except OSError:
        return None


def _tty_write(tty: TextIO | None, msg: str) -> None:
    if tty is None:
        return
    try:
        tty.write(msg)
        tty.flush()
    except OSError:
        # tty can disappear mid-run (backgrounded shell, redirected
        # stdio). Don't crash the hook over lost progress output.
        pass


def _emit(tty: TextIO | None, msg: str) -> None:
    """Print to stdout (pre-commit buffer) and the live tty."""
    print(msg)
    _tty_write(tty, msg + "\n")


def resolve_llmdbenchmark(root: Path) -> str:
    # Prefer the venv binary — that's what picks up the user's
    # editable install and therefore their uncommitted changes.
    venv = root / ".venv" / "bin" / "llmdbenchmark"
    if venv.is_file():
        return str(venv)
    found = shutil.which("llmdbenchmark")
    if found:
        return found
    print(
        "ERROR: llmdbenchmark not found (.venv/bin/llmdbenchmark missing "
        "and not on PATH). Run ./install.sh first.",
        file=sys.stderr,
    )
    sys.exit(2)


def render_spec(entrypoint: str, spec: str, cwd: Path, tty: TextIO | None) -> bool:
    cmd = [entrypoint, "--spec", spec, "--dry-run", "plan", "-p", "precommit"]

    # Trailing whitespace keeps the column wide enough that \r
    # overwrites below don't leave tail chars from longer messages.
    _tty_write(tty, f"  -> rendering {spec} ...                        ")

    stop = threading.Event()
    start = time.monotonic()

    def heartbeat() -> None:
        # wait() before the first write — fast renders never draw one.
        while not stop.wait(_HEARTBEAT_INTERVAL_S):
            elapsed = time.monotonic() - start
            _tty_write(
                tty,
                f"\r  -> rendering {spec} ... ({elapsed:.0f}s elapsed)      ",
            )

    beat = threading.Thread(target=heartbeat, daemon=True)
    beat.start()
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    finally:
        stop.set()
        beat.join(timeout=0.5)

    elapsed = time.monotonic() - start

    if proc.returncode == 0:
        _tty_write(
            tty,
            f"\r  [OK]   {spec}  ({elapsed:.1f}s)                              \n",
        )
        # Permanent record for pre-commit's end-of-hook dump and CI logs.
        print(f"Rendering: {spec} ... OK ({elapsed:.1f}s)")
        return True

    _tty_write(
        tty,
        f"\r  [FAIL] {spec}  ({elapsed:.1f}s)                              \n",
    )
    print(f"  FAIL: {spec} ({elapsed:.1f}s)", file=sys.stderr)
    tail = "\n".join(proc.stderr.splitlines()[-40:])
    if tail:
        print(tail, file=sys.stderr)
        _tty_write(tty, tail + "\n")
    return False


def main(argv: list[str]) -> int:
    root = repo_root()

    # argv overrides git for manual debugging, e.g.
    #   python util/precommit_render_changed.py config/specification/examples/gpu.yaml.j2
    # In the hook path there's no argv so we ask git. We never use
    # pre-commit's argv even if pass_filenames gets flipped back on —
    # pre-commit batches large file lists across multiple invocations
    # and we'd only see a slice per run.
    if len(argv) > 1:
        files = argv[1:]
    else:
        files = _get_staged_files(root)

    specs = collect_specs(files, root)

    # Nothing staged, or nothing that maps to a spec → render the
    # canary anyway, so every commit proves the render path still works.
    if not specs:
        if spec_exists(root, CANARY_SPEC):
            specs = {CANARY_SPEC: "baseline sanity check (no scenarios touched)"}
        else:
            print(
                f"No scenarios touched and canary spec '{CANARY_SPEC}' "
                "not found on disk; nothing to render.",
                file=sys.stderr,
            )
            return 0

    entrypoint = resolve_llmdbenchmark(root)
    tty = _open_tty()

    # Break off the pre-commit header line before writing progress,
    # otherwise we overwrite its dots.
    _tty_write(tty, "\n")

    count = len(specs)
    _emit(tty, f"Rendering {count} {'scenario' if count == 1 else 'scenarios'}:")
    for spec, reason in specs.items():
        _emit(tty, f"  - {spec}  [{reason}]")

    failed = 0
    try:
        for spec in specs:
            if not render_spec(entrypoint, spec, root, tty):
                failed += 1
    finally:
        if tty is not None:
            try:
                tty.close()
            except OSError:
                pass

    passed = count - failed
    # stdout only. On a live terminal the per-spec [OK]/[FAIL] lines
    # already made the outcome obvious; duplicating the summary to the
    # tty just adds noise.
    print(f"Render results: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
