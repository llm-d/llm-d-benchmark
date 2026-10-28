"""``--harness`` overrides must not pair a harness with the wrong script extension."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmdbenchmark.run.steps.step_07_deploy_harness import resolve_harness_executable

HARNESSES_DIR = Path(__file__).resolve().parents[1] / "workload" / "harnesses"


@pytest.mark.parametrize(
    ("harness", "configured", "expected"),
    [
        # guides/fast-model-actuation defaults to nop (.py); a slash-test
        # --harness inference-perf override must fall back to the .sh script.
        ("inference-perf", "llm-d-benchmark.py", "llm-d-benchmark.sh"),
        ("nop", "llm-d-benchmark.sh", "llm-d-benchmark.py"),
        # Correct pairings are left alone.
        ("inference-perf", "llm-d-benchmark.sh", "llm-d-benchmark.sh"),
        ("nop", "llm-d-benchmark.py", "llm-d-benchmark.py"),
    ],
)
def test_resolves_against_shipped_scripts(harness, configured, expected):
    resolved = resolve_harness_executable(harness, configured, HARNESSES_DIR)
    assert resolved == expected
    assert (HARNESSES_DIR / f"{harness}-{resolved}").is_file()


def test_unknown_harness_keeps_configured_value():
    assert (
        resolve_harness_executable(
            "no-such-harness", "llm-d-benchmark.sh", HARNESSES_DIR
        )
        == "llm-d-benchmark.sh"
    )


def test_custom_executable_is_respected(tmp_path):
    (tmp_path / "inference-perf-custom.py").write_text("")
    assert (
        resolve_harness_executable("inference-perf", "custom.py", tmp_path)
        == "custom.py"
    )


def test_missing_harnesses_dir_keeps_configured_value(tmp_path):
    assert (
        resolve_harness_executable(
            "inference-perf", "llm-d-benchmark.py", tmp_path / "absent"
        )
        == "llm-d-benchmark.py"
    )
