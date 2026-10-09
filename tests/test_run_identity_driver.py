"""Driver-side run_analysis sets per-treatment run identity and scopes its
envars to each results directory.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
import yaml

from llmdbenchmark.analysis.benchmark_report.native_to_br0_2 import (
    _get_harness_meta,
)

FIXTURE = Path(__file__).parent / "fixtures" / "inference_perf_lifecycle.yaml"

EXPERIMENT_ID = "inference-perf-conc32-1786024743-hipkpq"

# The model every staged run reports.
MODEL = "Qwen/Qwen3-32B"


def _setup_run(tmp_path: Path, monkeypatch, **metadata) -> str:
    """Stage a results dir with no kubernetes context and return the results file.

    Mirrors the run-only path: the harness metadata file is the only source of
    run details.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    results_file = tmp_path / "stage_0_lifecycle_metrics.json"
    results_file.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    workload_file = tmp_path / "code_generation.yaml"
    workload_file.write_text(
        yaml.safe_dump(
            {
                "load": {"type": "concurrent"},
                "api": {"type": "completion", "streaming": True},
                "server": {"type": "vllm", "model_name": MODEL},
            }
        ),
        encoding="utf-8",
    )

    run_metadata = {
        "harness_args": f"--config_file {workload_file}",
        "harness_start": "2026-06-23T22:54:25+00:00",
        "harness_stop": "2026-06-23T23:14:35+00:00",
        "harness_delta": "PT1210S",
        "harness_version": "test-version",
        "harness_name": "inference-perf",
        "harness_workload": workload_file.name,
        "harness_rc": "0",
        "model": MODEL,
        "namespace": "llm-d-storage",
    }
    run_metadata.update(metadata)
    (tmp_path / "run_metadata.yaml").write_text(
        yaml.safe_dump(run_metadata), encoding="utf-8"
    )

    monkeypatch.setenv("LLMDBENCH_MAGIC_ENVAR", "harness_pod")
    monkeypatch.setenv("LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR", str(tmp_path))
    for envar in (
        "LLMDBENCH_BASE64_CONTEXT_CONTENTS",
        "KUBERNETES_SERVICE_HOST",
        "KUBERNETES_SERVICE_PORT",
        "LLMDBENCH_RUN_EXPERIMENT_ID",
        "LLMDBENCH_DEPLOY_CURRENT_MODEL",
        # A description exported in the developer's shell would otherwise
        # override the generated label in every test below.
        "LLMDBENCH_DESCRIPTION_TEXT",
        "LLMDBENCH_DESCRIPTION_KEYWORDS",
    ):
        monkeypatch.delenv(envar, raising=False)
    # Process-wide cache; each test stages its own metadata file.
    if hasattr(_get_harness_meta, "_cache"):
        delattr(_get_harness_meta, "_cache")

    return str(results_file)


def test_driver_side_analysis_populates_identity(tmp_path, monkeypatch) -> None:
    """run_analysis overwrites the in-pod reports, so it must set the identity too.

    The driver has neither LLMDBENCH_MAGIC_ENVAR nor the results-dir envar.
    """
    results_dir = tmp_path / f"{EXPERIMENT_ID}_1"
    _setup_run(results_dir, monkeypatch)
    for envar in ("LLMDBENCH_MAGIC_ENVAR", "LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"):
        monkeypatch.delenv(envar, raising=False)

    from llmdbenchmark.analysis import run_analysis

    assert run_analysis("inference-perf", results_dir, None) is None

    report = yaml.safe_load(
        (
            results_dir / "benchmark_report_v0.2,_stage_0_lifecycle_metrics.json.yaml"
        ).read_text(encoding="utf-8")
    )
    assert report["run"]["description"] == EXPERIMENT_ID
    assert report["run"]["eid"] == str(uuid.uuid5(uuid.NAMESPACE_URL, EXPERIMENT_ID))
    # The envar must not leak to whatever the driver analyses next.
    assert "LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR" not in os.environ


def test_sequential_directories_do_not_share_identity(tmp_path, monkeypatch) -> None:
    """One driver process converts a whole sweep, so nothing may carry over.

    Both the memoised harness metadata and a stale LLMDBENCH_RUN_EXPERIMENT_ID
    are process-wide, and either one surviving into the next directory gives
    every treatment the first one's identity -- with the suite still green,
    since every other test converts a single directory per process.
    """
    treatments = {"conc32": "Qwen/Qwen3-32B", "conc64": "meta-llama/Llama-3.1-8B"}
    experiment_ids = {
        suffix: f"inference-perf-{suffix}-178602474{index}-aaaaa{index}"
        for index, suffix in enumerate(treatments)
    }
    for suffix, model in treatments.items():
        _setup_run(
            tmp_path / f"{experiment_ids[suffix]}_1",
            monkeypatch,
            experiment_id=experiment_ids[suffix],
            model=model,
        )
    for envar in ("LLMDBENCH_MAGIC_ENVAR", "LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"):
        monkeypatch.delenv(envar, raising=False)
    # What a preceding sweep treatment would have left behind.
    monkeypatch.setenv("LLMDBENCH_RUN_EXPERIMENT_ID", experiment_ids["conc32"])

    from llmdbenchmark.analysis import run_analysis

    reports = {}
    for suffix in treatments:
        results_dir = tmp_path / f"{experiment_ids[suffix]}_1"
        assert run_analysis("inference-perf", results_dir, None) is None
        reports[suffix] = yaml.safe_load(
            (
                results_dir
                / "benchmark_report_v0.2,_stage_0_lifecycle_metrics.json.yaml"
            ).read_text(encoding="utf-8")
        )

    for suffix in treatments:
        assert reports[suffix]["run"]["description"] == experiment_ids[suffix]
        assert reports[suffix]["run"]["eid"] == str(
            uuid.uuid5(uuid.NAMESPACE_URL, experiment_ids[suffix])
        )
    assert reports["conc32"]["run"]["eid"] != reports["conc64"]["run"]["eid"]


def test_driver_env_description_does_not_override_each_treatment(
    tmp_path, monkeypatch
) -> None:
    """Every envar the converters read has to be scoped per results directory.

    A scenario-wide LLMDBENCH_DESCRIPTION_TEXT in the driver's environment
    outranks the per-directory metadata, so leaving it unscoped gives every
    treatment of a sweep the same description -- exactly what scoping the
    experiment ID already prevents. The report library additionally prefixes
    each description with its own treatment.
    """
    treatments = {
        "conc32": ("inference-perf-conc32-1786024743-aaaaaa", "A SPECIFIC"),
        "conc64": ("inference-perf-conc64-1786024744-bbbbbb", "B SPECIFIC"),
    }
    for experiment_id, description in treatments.values():
        _setup_run(
            tmp_path / f"{experiment_id}_1",
            monkeypatch,
            experiment_id=experiment_id,
            description_text=description,
        )
    for envar in ("LLMDBENCH_MAGIC_ENVAR", "LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"):
        monkeypatch.delenv(envar, raising=False)
    monkeypatch.setenv("LLMDBENCH_DESCRIPTION_TEXT", "SCENARIO WIDE")

    from llmdbenchmark.analysis import run_analysis

    descriptions = {}
    for suffix, (experiment_id, description) in treatments.items():
        results_dir = tmp_path / f"{experiment_id}_1"
        assert run_analysis("inference-perf", results_dir, None) is None
        report = yaml.safe_load(
            (
                results_dir
                / "benchmark_report_v0.2,_stage_0_lifecycle_metrics.json.yaml"
            ).read_text(encoding="utf-8")
        )
        descriptions[suffix] = report["run"]["description"]
        assert descriptions[suffix] == f"{suffix}-{description}"
    assert descriptions["conc32"] != descriptions["conc64"]
    assert "SCENARIO WIDE" not in descriptions.values()
    assert os.environ["LLMDBENCH_DESCRIPTION_TEXT"] == "SCENARIO WIDE"


def test_failed_conversion_does_not_leak_the_results_dir(tmp_path, monkeypatch) -> None:
    """A raising conversion must still restore the envar.

    One driver process analyses many results dirs in sequence, so a leaked
    envar would pin every later report to this run's identity.
    """
    results_dir = tmp_path / f"{EXPERIMENT_ID}_1"
    _setup_run(results_dir, monkeypatch)
    for envar in ("LLMDBENCH_MAGIC_ENVAR", "LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"):
        monkeypatch.delenv(envar, raising=False)

    from llmdbenchmark import analysis

    monkeypatch.setattr(
        analysis,
        "_convert_to_benchmark_report",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError):
        analysis.run_analysis("inference-perf", results_dir, None)

    assert "LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR" not in os.environ
