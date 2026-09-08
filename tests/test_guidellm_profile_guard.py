"""Tests for the guidellm harness pre-flight profile guard.

The installed guidellm (pinned via ``build/Dockerfile``) rejects an
unsupported/unavailable ``profile`` (e.g. ``replay``, which only exists on
guidellm's main branch and is not yet on PyPI) with a clear error -- but that
error gets swallowed while ``guidellm run --scenario ...`` parses its
config, and what actually surfaces is a misleading
``Error: Invalid value for --target: Field required``, sending operators off
to debug the wrong thing.

These tests pin the guard added to ``guidellm-llm-d-benchmark.sh`` that reads
the workload's ``profile`` field and rejects an unsupported value with the
real cause *before* guidellm ever runs. The guard shells out to Python to ask
the installed guidellm which profiles it actually supports (rather than
hardcoding a list), so it stays correct if the image is rebuilt with a
guidellm commit that does support ``replay``.

guidellm v0.7 moved the workload's profile from a top-level ``profile: <name>``
scalar to ``spec.profile.kind``, and replaced the ``ProfileType``/
``StrategyType`` literals the introspection imported with pydantic class
registries. Both formats are still read, so the guard works whichever version
the image is built against.
"""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

import pytest

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


# Fake yq: resolves an "a.b.c // d // \"\"" alternative expression against the
# workload YAML, which is all the guard asks of it. Real yq semantics for the
# subset used, so the test exercises the committed expression rather than a
# bash approximation of it.
_FAKE_YQ = r"""
import sys, yaml

expression, path = sys.argv[-2], sys.argv[-1]
with open(path) as handle:
    document = yaml.safe_load(handle) or {}

for alternative in (part.strip() for part in expression.split("//")):
    if alternative in ('""', "''"):
        print("")
        break
    node = document
    for key in alternative.lstrip(".").split("."):
        node = node.get(key) if isinstance(node, dict) else None
        if node is None:
            break
    if node is not None:
        print(node)
        break
else:
    print("null")
"""


def _run_guard(
    tmp_path: Path, workload_yaml: str, supported: str
) -> subprocess.CompletedProcess:
    """Run the extracted guard block against a fake yq/python3/guidellm.

    ``supported`` is the space-separated profile list the fake python3 stub
    prints back, standing in for guidellm's real registry introspection.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Mirrors the real layout: $LLMDBENCH_RUN_WORKSPACE_DIR/profiles/guidellm/<workload>
    profiles_dir = tmp_path / "profiles" / "guidellm"
    profiles_dir.mkdir(parents=True)
    workload_file = profiles_dir / "workload.yaml"
    workload_file.write_text(workload_yaml, encoding="utf-8")

    yq_impl = tmp_path / "fake_yq.py"
    yq_impl.write_text(_FAKE_YQ, encoding="utf-8")
    # Absolute interpreter path: the fake python3 below shadows it on PATH.
    _write_fake_bin(bin_dir, "yq", f'exec {sys.executable} {yq_impl} "$@"')
    _write_fake_bin(bin_dir, "python3", f'echo "{supported}"')
    _write_fake_bin(bin_dir, "guidellm", 'echo "guidellm version: 0.7.3"')

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

# Both workload formats the guard has to read. v0.7+ workloads (the format
# every profile in workload/profiles/guidellm/ now uses) nest the profile under
# spec and name it with "kind"; pre-v0.7 workloads used a flat scalar.
_NESTED = "spec:\n  profile:\n    kind: {profile}\n"
_FLAT = "profile: {profile}\n"


@pytest.mark.parametrize("layout", [_NESTED, _FLAT], ids=["nested", "flat"])
def test_unsupported_profile_fails_with_real_cause(tmp_path: Path, layout: str) -> None:
    result = _run_guard(tmp_path, layout.format(profile="replay"), _SUPPORTED)

    assert result.returncode == 1
    assert "GUARD_PASSED" not in result.stdout
    # Matches this repo's own precedent (lm-eval-llm-d-benchmark.sh): fatal
    # errors go to stderr, not stdout.
    assert "replay" in result.stderr
    assert "does not support" in result.stderr
    # The whole point: name the real cause instead of the misleading
    # "--target: Field required" guidellm would otherwise surface.
    assert "--target" not in result.stderr


@pytest.mark.parametrize("layout", [_NESTED, _FLAT], ids=["nested", "flat"])
def test_supported_profile_passes_through(tmp_path: Path, layout: str) -> None:
    result = _run_guard(tmp_path, layout.format(profile="concurrent"), _SUPPORTED)

    assert result.returncode == 0
    assert "GUARD_PASSED" in result.stdout


def test_missing_profile_field_does_not_block(tmp_path: Path) -> None:
    result = _run_guard(
        tmp_path, "spec:\n  backend:\n    target: http://x\n", _SUPPORTED
    )

    assert result.returncode == 0
    assert "GUARD_PASSED" in result.stdout


def test_introspection_failure_fails_open(tmp_path: Path) -> None:
    """If the installed guidellm can't be introspected for any reason, the
    guard must not block a run it can't actually validate."""
    result = _run_guard(tmp_path, _NESTED.format(profile="replay"), "")

    assert result.returncode == 0
    assert "GUARD_PASSED" in result.stdout


def test_introspection_reads_the_installed_guidellm() -> None:
    """The guard must not hardcode a profile list. Pin that it asks guidellm
    itself, via whichever of the two APIs the installed version exposes:
    v0.7+ pydantic class registries, or the pre-v0.7 type literals it falls
    back to."""
    guard = _extract_guard_block()

    assert "ProfileArgs.registry" in guard
    assert "SchedulingStrategy.registry" in guard
    assert "get_literal_vals(ProfileType | StrategyType)" in guard
