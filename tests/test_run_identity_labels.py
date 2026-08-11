"""Tests for per-treatment run identity in v0.2 / v0.2.1 reports.

Without a per-treatment ``run.description`` every treatment of a sweep falls
back to the model name, and consumers collapse the sweep into a single entry.
The generated label is ``<model> [<experiment id>]``; a submitter-supplied
description wins over it.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
import yaml

from llmdbenchmark.analysis.benchmark_report import native_to_br0_2_1
from llmdbenchmark.analysis.benchmark_report.native_to_br0_2 import (
    _get_harness_meta,
    import_inference_perf,
)

FIXTURE = Path(__file__).parent / "fixtures" / "inference_perf_lifecycle.yaml"

EXPERIMENT_ID = "inference-perf-conc32-1786024743-hipkpq"
# No treatment segment: <harness>-<timestamp>-<rand>.
UNSWEPT_EXPERIMENT_ID = "inference-perf-1786001414-kdonyb"

# The model every staged run reports. Only ever referenced through this name and
# _expect_label(), so the assertions never restate it.
MODEL = "Qwen/Qwen3-32B"


def _expect_label(experiment_id: str) -> str:
    """Build the run label prism should show for an experiment ID."""
    return f"{MODEL} [{experiment_id}]"


# The label is built the same way for every harness; these two stand in for a
# name the shortening list knows and one it does not.
HARNESSES = ["inference-perf", "aiperf"]


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


def test_in_pod_experiment_id_yields_run_label(tmp_path, monkeypatch) -> None:
    """The env var set in-pod drives the label and the eid."""
    results_file = _setup_run(tmp_path, monkeypatch)
    monkeypatch.setenv("LLMDBENCH_RUN_EXPERIMENT_ID", EXPERIMENT_ID)

    run = import_inference_perf(results_file).run

    assert run.description == _expect_label(EXPERIMENT_ID)
    assert run.eid == str(uuid.uuid5(uuid.NAMESPACE_URL, EXPERIMENT_ID))


@pytest.mark.parametrize("harness", HARNESSES)
def test_run_label_is_harness_agnostic(harness, tmp_path, monkeypatch) -> None:
    """The label carries the whole ID whatever harness produced it."""
    experiment_id = f"{harness}-conc32-1786024743-hipkpq"
    results_file = _setup_run(
        tmp_path, monkeypatch, harness_name=harness, experiment_id=experiment_id
    )

    run = import_inference_perf(results_file).run

    assert run.description == _expect_label(experiment_id)
    assert run.eid == str(uuid.uuid5(uuid.NAMESPACE_URL, experiment_id))


@pytest.mark.parametrize("harness", HARNESSES)
def test_multi_segment_treatment_is_kept_whole(harness, tmp_path, monkeypatch) -> None:
    """A compound treatment survives in the label, for every harness."""
    experiment_id = f"{harness}-grp40-splen8k-1773947901-i5e39v"
    results_file = _setup_run(
        tmp_path, monkeypatch, harness_name=harness, experiment_id=experiment_id
    )

    run = import_inference_perf(results_file).run

    assert run.description == _expect_label(experiment_id)
    assert "grp40-splen8k" in run.description


def test_post_hoc_experiment_id_read_from_run_metadata(tmp_path, monkeypatch) -> None:
    """Post-hoc conversion recovers the same identity from run_metadata.yaml."""
    results_file = _setup_run(tmp_path, monkeypatch, experiment_id=EXPERIMENT_ID)

    run = import_inference_perf(results_file).run

    assert run.description == _expect_label(EXPERIMENT_ID)
    assert run.eid == str(uuid.uuid5(uuid.NAMESPACE_URL, EXPERIMENT_ID))


def test_treatments_get_distinct_identity(tmp_path, monkeypatch) -> None:
    """The actual bug: two treatments must not share a label or an eid."""
    conc32 = import_inference_perf(
        _setup_run(tmp_path / "a", monkeypatch, experiment_id=EXPERIMENT_ID)
    ).run
    conc64 = import_inference_perf(
        _setup_run(
            tmp_path / "b",
            monkeypatch,
            experiment_id="inference-perf-conc64-1786036152-0vx72d",
        )
    ).run

    assert conc32.description != conc64.description
    assert conc32.eid != conc64.eid


def test_stages_of_one_run_share_an_eid(tmp_path, monkeypatch) -> None:
    """Multiple report files from one run stay grouped by eid."""
    results_file = _setup_run(tmp_path, monkeypatch, experiment_id=EXPERIMENT_ID)
    second_stage = tmp_path / "stage_0_session_lifecycle_metrics.json"
    second_stage.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    first = import_inference_perf(results_file).run
    second = import_inference_perf(str(second_stage)).run

    assert first.eid == second.eid
    assert first.uid != second.uid


def test_unswept_run_is_still_labelled(tmp_path, monkeypatch) -> None:
    """An ID encoding no treatment still yields a unique label."""
    results_file = _setup_run(
        tmp_path, monkeypatch, experiment_id=UNSWEPT_EXPERIMENT_ID
    )

    run = import_inference_perf(results_file).run

    assert run.description == _expect_label(UNSWEPT_EXPERIMENT_ID)


@pytest.mark.parametrize("harness", HARNESSES)
def test_missing_experiment_id_omits_identity_fields(
    harness, tmp_path, monkeypatch
) -> None:
    """With no experiment ID from any source the optional fields stay unset.

    The results dir basename is the last-resort source, so the dir is named
    after the harness alone: that shortens to the bare harness name, which
    identifies nothing, and must not become a label.
    """
    results_file = _setup_run(tmp_path / harness, monkeypatch, harness_name=harness)

    report = import_inference_perf(results_file)
    dumped = report.dump()["run"]

    assert "description" not in dumped
    assert "keywords" not in dumped
    assert "eid" not in dumped


@pytest.mark.parametrize("blank", ["  ", "\t", "\n"])
def test_blank_experiment_id_is_no_experiment_id(blank, tmp_path, monkeypatch) -> None:
    """Whitespace must not label a report '<model> [ ]' nor mint an eid.

    The dir is named after the harness so the results-dir fallback, which is
    correct to consult once the envar is rejected, also yields nothing.
    """
    results_file = _setup_run(tmp_path / "inference-perf", monkeypatch)
    monkeypatch.setenv("LLMDBENCH_RUN_EXPERIMENT_ID", blank)

    dumped = import_inference_perf(results_file).dump()["run"]

    assert "description" not in dumped
    assert "eid" not in dumped


def test_label_is_never_the_bare_model_name(tmp_path, monkeypatch) -> None:
    """A bare model name is identical across a sweep, and prism's resolveRunLabel
    deprioritizes a candidate equal to it."""
    results_file = _setup_run(tmp_path, monkeypatch, experiment_id=EXPERIMENT_ID)

    description = import_inference_perf(results_file).run.description

    assert description.lower() != MODEL.lower()
    assert description.startswith(MODEL)
    assert "conc32" in description


def test_keywords_are_left_to_the_submitter(tmp_path, monkeypatch) -> None:
    """SUBMISSION_POLICY.md reserves run.keywords for curated tags, so with no
    submitter value the key stays absent rather than becoming an empty list."""
    results_file = _setup_run(tmp_path, monkeypatch, experiment_id=EXPERIMENT_ID)

    dumped = import_inference_perf(results_file).dump()["run"]

    assert "description" in dumped
    assert "keywords" not in dumped


def test_submitter_description_wins_over_the_generated_label(
    tmp_path, monkeypatch
) -> None:
    """The schema documents run.description as submitter-provided."""
    results_file = _setup_run(
        tmp_path,
        monkeypatch,
        experiment_id=EXPERIMENT_ID,
        description_text="Sweep A: KV cache offload",
    )

    run = import_inference_perf(results_file).run

    assert run.description == "Sweep A: KV cache offload"
    # An override renames the run; it must not change what the run *is*.
    assert run.eid == str(uuid.uuid5(uuid.NAMESPACE_URL, EXPERIMENT_ID))


def test_description_envar_outranks_the_metadata_file(tmp_path, monkeypatch) -> None:
    """In-pod the envar is authoritative, matching every other harness value."""
    results_file = _setup_run(
        tmp_path,
        monkeypatch,
        experiment_id=EXPERIMENT_ID,
        description_text="from the file",
    )
    monkeypatch.setenv("LLMDBENCH_DESCRIPTION_TEXT", "from the envar")

    assert import_inference_perf(results_file).run.description == "from the envar"


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_blank_description_falls_back_to_the_generated_label(
    blank, tmp_path, monkeypatch
) -> None:
    """An unset knob reaches here as "", which must not become the label."""
    results_file = _setup_run(
        tmp_path, monkeypatch, experiment_id=EXPERIMENT_ID, description_text=blank
    )

    assert import_inference_perf(results_file).run.description == _expect_label(
        EXPERIMENT_ID
    )


def test_submitter_keywords_are_recorded(tmp_path, monkeypatch) -> None:
    """Comma-separated keywords arrive as a list, whitespace trimmed."""
    results_file = _setup_run(
        tmp_path,
        monkeypatch,
        experiment_id=EXPERIMENT_ID,
        description_keywords="kv-cache, offload ,p-d",
    )

    assert import_inference_perf(results_file).run.keywords == [
        "kv-cache",
        "offload",
        "p-d",
    ]


def test_a_description_survives_conversion_off_the_pod(tmp_path, monkeypatch) -> None:
    """The driver reports overwrite the in-pod ones and the driver sees no pod
    envars, so the description must reach it via run_metadata.yaml."""
    results_dir = tmp_path / f"{EXPERIMENT_ID}_1"
    results_file = _setup_run(
        results_dir,
        monkeypatch,
        description_text="Sweep A",
        experiment_id=EXPERIMENT_ID,
    )
    in_pod = import_inference_perf(results_file).run.description

    for envar in ("LLMDBENCH_MAGIC_ENVAR", "LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"):
        monkeypatch.delenv(envar, raising=False)
    delattr(_get_harness_meta, "_cache")
    monkeypatch.setenv("LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR", str(results_dir))

    assert in_pod == "Sweep A"
    assert import_inference_perf(results_file).run.description == in_pod


def test_model_name_is_read_from_the_environment(tmp_path, monkeypatch) -> None:
    """The in-pod envar supplies the model when run_metadata.yaml lacks it."""
    results_file = _setup_run(tmp_path, monkeypatch, model="")
    monkeypatch.setenv("LLMDBENCH_DEPLOY_CURRENT_MODEL", "meta-llama/Llama-3.1-8B")
    monkeypatch.setenv("LLMDBENCH_RUN_EXPERIMENT_ID", EXPERIMENT_ID)

    run = import_inference_perf(results_file).run

    assert run.description == f"meta-llama/Llama-3.1-8B [{EXPERIMENT_ID}]"


def test_label_without_a_model_falls_back_to_the_experiment_id(
    tmp_path, monkeypatch
) -> None:
    """With no model from any source the label is still per-treatment unique."""
    results_file = _setup_run(tmp_path, monkeypatch, model="")
    monkeypatch.setenv("LLMDBENCH_RUN_EXPERIMENT_ID", EXPERIMENT_ID)

    run = import_inference_perf(results_file).run

    assert run.description == EXPERIMENT_ID


def test_v0_2_1_carries_the_same_identity(tmp_path, monkeypatch) -> None:
    """v0.2.1 inherits the v0.2 identity fix rather than re-implementing it."""
    results_file = _setup_run(tmp_path, monkeypatch, experiment_id=EXPERIMENT_ID)

    report = native_to_br0_2_1.import_inference_perf(results_file)

    assert report.version == "0.2.1"
    assert report.run.description == _expect_label(EXPERIMENT_ID)
    assert report.run.eid == str(uuid.uuid5(uuid.NAMESPACE_URL, EXPERIMENT_ID))


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
    assert report["run"]["description"] == _expect_label(EXPERIMENT_ID)
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

    for suffix, model in treatments.items():
        assert (
            reports[suffix]["run"]["description"]
            == f"{model} [{experiment_ids[suffix]}]"
        )
        assert reports[suffix]["run"]["eid"] == str(
            uuid.uuid5(uuid.NAMESPACE_URL, experiment_ids[suffix])
        )
    assert reports["conc32"]["run"]["eid"] != reports["conc64"]["run"]["eid"]


def test_submitter_values_survive_without_an_experiment_id(
    tmp_path, monkeypatch
) -> None:
    """A submitter description needs no experiment ID to be correct.

    keywords have no source other than the submitter at all, so gating either on
    eid resolution discards the one thing the user asked for. eid itself does
    need an ID, so it stays absent.
    """
    results_file = _setup_run(
        tmp_path / "inference-perf",
        monkeypatch,
        description_text="Sweep A: KV cache offload",
        description_keywords="kv-cache, offload",
    )

    dumped = import_inference_perf(results_file).dump()["run"]

    assert dumped["description"] == "Sweep A: KV cache offload"
    assert dumped["keywords"] == ["kv-cache", "offload"]
    assert "eid" not in dumped


def test_driver_env_description_does_not_override_each_treatment(
    tmp_path, monkeypatch
) -> None:
    """Every envar the converters read has to be scoped per results directory.

    A scenario-wide LLMDBENCH_DESCRIPTION_TEXT in the driver's environment
    outranks the per-directory metadata, so leaving it unscoped gives every
    treatment of a sweep the same description -- exactly what scoping the
    experiment ID already prevents.
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

    for experiment_id, description in treatments.values():
        results_dir = tmp_path / f"{experiment_id}_1"
        assert run_analysis("inference-perf", results_dir, None) is None
        report = yaml.safe_load(
            (
                results_dir
                / "benchmark_report_v0.2,_stage_0_lifecycle_metrics.json.yaml"
            ).read_text(encoding="utf-8")
        )
        assert report["run"]["description"] == description
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
