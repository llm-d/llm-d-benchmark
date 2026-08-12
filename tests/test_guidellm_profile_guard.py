"""Tests for the guidellm harness pre-flight profile guard.

The installed guidellm (pinned via ``build/Dockerfile``) rejects an
unsupported/unavailable ``profile`` (e.g. ``replay``, which only exists on
guidellm's main branch and is not yet on PyPI) with a clear error -- but that
error gets swallowed while ``guidellm benchmark --scenario ...`` parses its
config, and what actually surfaces is a misleading
``Error: Invalid value for --target: Field required``, sending operators off
to debug the wrong thing.

These tests pin the guard added to ``guidellm-llm-d-benchmark.sh`` that reads
the workload's ``profile`` field and rejects an unsupported value with the
real cause *before* guidellm ever runs. The guard shells out to Python to ask
the installed guidellm which profiles it actually supports (rather than
hardcoding a list), so it stays correct if the image is rebuilt with a
guidellm commit that does support ``replay``.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

_HARNESS_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "workload"
    / "harnesses"
    / "guidellm-llm-d-benchmark.sh"
)


def _extract_guard_block() -> str:
    """Pull the profile-guard block out of the real harness script.

    Testing the exact committed block (rather than a copy pasted into the
    test) means an edit that weakens or removes the guard fails this test.
    """
    content = _HARNESS_SCRIPT.read_text(encoding="utf-8")
    start = content.index("# Guard:")
    end = content.index("export LLMDBENCH_HARNESS_ARGS=")
    return content[start:end]


def _write_fake_bin(bin_dir: Path, name: str, script: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{script}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run_guard(
    tmp_path: Path, workload_yaml: str, supported: str
) -> subprocess.CompletedProcess:
    """Run the extracted guard block against a fake yq/python3/guidellm.

    ``supported`` is the space-separated profile list the fake python3 stub
    prints back, standing in for guidellm's real
    ``get_literal_vals(ProfileType | StrategyType)`` introspection.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Mirrors the real layout: $LLMDBENCH_RUN_WORKSPACE_DIR/profiles/guidellm/<workload>
    profiles_dir = tmp_path / "profiles" / "guidellm"
    profiles_dir.mkdir(parents=True)
    workload_file = profiles_dir / "workload.yaml"
    workload_file.write_text(workload_yaml, encoding="utf-8")

    # Minimal fake yq: reads a single top-level "key: value" line.
    _write_fake_bin(
        bin_dir,
        "yq",
        """
key="${2#.}"
val=$(grep -E "^${key}:" "$3" | head -1 | sed -E "s/^${key}:[[:space:]]*//")
[[ -z "$val" ]] && echo null || echo "$val"
""",
    )
    _write_fake_bin(bin_dir, "python3", f'echo "{supported}"')
    _write_fake_bin(bin_dir, "guidellm", 'echo "guidellm version: 0.6.0"')

    script = _extract_guard_block() + '\necho "GUARD_PASSED"\n'
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "LLMDBENCH_RUN_WORKSPACE_DIR": str(tmp_path),
        "LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME": "workload.yaml",
    }
    return subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


_SUPPORTED = "async concurrent constant poisson sweep synchronous throughput"


def test_unsupported_profile_fails_with_real_cause(tmp_path: Path) -> None:
    result = _run_guard(tmp_path, "profile: replay\n", _SUPPORTED)

    assert result.returncode == 1
    assert "GUARD_PASSED" not in result.stdout
    # Matches this repo's own precedent (lm-eval-llm-d-benchmark.sh): fatal
    # errors go to stderr, not stdout.
    assert "replay" in result.stderr
    assert "does not support" in result.stderr
    # The whole point: name the real cause instead of the misleading
    # "--target: Field required" guidellm would otherwise surface.
    assert "--target" not in result.stderr


def test_supported_profile_passes_through(tmp_path: Path) -> None:
    result = _run_guard(tmp_path, "profile: concurrent\n", _SUPPORTED)

    assert result.returncode == 0
    assert "GUARD_PASSED" in result.stdout


def test_missing_profile_field_does_not_block(tmp_path: Path) -> None:
    result = _run_guard(tmp_path, "target: http://x\n", _SUPPORTED)

    assert result.returncode == 0
    assert "GUARD_PASSED" in result.stdout


def test_introspection_failure_fails_open(tmp_path: Path) -> None:
    """If the installed guidellm can't be introspected for any reason, the
    guard must not block a run it can't actually validate."""
    result = _run_guard(tmp_path, "profile: replay\n", "")

    assert result.returncode == 0
    assert "GUARD_PASSED" in result.stdout
