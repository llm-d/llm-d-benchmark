"""Exercise the result-discovery Bash shipped in the OCP workflows."""

import os
from pathlib import Path
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def run_step(workflow, step_name, tmp_path, directory_names):
    document = yaml.safe_load((WORKFLOWS / workflow).read_text())
    script = next(
        step["run"]
        for job in document["jobs"].values()
        for step in job.get("steps", [])
        if step.get("name") == step_name
    )
    # Relocate the runner's downloaded files into this test's private fixture.
    for name in directory_names:
        script = script.replace(f"/tmp/{name}", str(tmp_path / name))
    summary = tmp_path / "summary"
    output = tmp_path / "output"
    subprocess.run(
        ["bash", "-e", "-o", "pipefail"],
        # The downloaded GCS trees are prepared below; do not contact GCS.
        input="gcloud() { return 0; }\n" + script,
        text=True,
        check=True,
        cwd=ROOT,
        env=os.environ
        | {
            "GITHUB_WORKSPACE": str(ROOT),
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_OUTPUT": str(output),
            "GCS_PATH": "fixture",
            "LLMDBENCH_CICD_TARGET": "ocp",
        },
        capture_output=True,
    )
    return summary, output


@pytest.mark.parametrize("old_user,new_user", [("runner", "root"), ("root", "runner")])
def test_nightly_display_uses_newest_run_across_users(tmp_path, old_user, new_user):
    directories = ("standalone-results", "fma-results")
    for directory in directories:
        for user, timestamp, value in (
            (old_user, "20261109-000000-000", "old"),
            (new_user, "20261110-000000-000", "new"),
        ):
            result = (
                tmp_path
                / directory
                / "2026-11-10"
                / f"{user}-{timestamp}"
                / "results"
                / "experiment"
                / "analysis"
            )
            result.mkdir(parents=True)
            (result / "result.txt").write_text(f"{directory}: {value}\n")

    summary, _ = run_step(
        "ci-nightly-benchmark-ocp-fma.yaml", "Display results", tmp_path, directories
    )

    text = summary.read_text()
    for directory in directories:
        assert f"{directory}: new" in text
        assert f"{directory}: old" not in text


@pytest.mark.parametrize("old_user,new_user", [("runner", "root"), ("root", "runner")])
def test_keda_diagnostics_use_newest_run_across_users(tmp_path, old_user, new_user):
    directories = {
        "baseline": "baseline-results",
        "fma_keda_warmstart": "fma-keda-warmstart-results",
        "fma_keda_hotstart": "fma-keda-hotstart-results",
    }
    for directory in directories.values():
        for user, timestamp in (
            (old_user, "20261109-000000-000"),
            (new_user, "20261110-000000-000"),
        ):
            result = (
                tmp_path / directory / f"{user}-{timestamp}" / "results" / "experiment"
            )
            result.mkdir(parents=True)
            (result / "hpa.txt").write_text("fixture diagnostics\n")

    _, output = run_step(
        "ci-benchmark-ocp-fma-keda.yaml",
        "Find latest experiment results dirs",
        tmp_path,
        directories.values(),
    )

    selected = dict(line.split("=", 1) for line in output.read_text().splitlines())
    for label, directory in directories.items():
        expected = (
            tmp_path
            / directory
            / f"{new_user}-20261110-000000-000"
            / "results"
            / "experiment"
        )
        assert selected[f"{label}_exp"] == str(expected)
