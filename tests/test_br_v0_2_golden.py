"""Golden-output tests for the Benchmark Report v0.2 converters.

Each case runs one native-to-BR importer on a fixture from ``tests/fixtures/``
and compares the report to a committed YAML file in
``tests/fixtures/br_v0_2_golden/``. There is one golden per case, and both
converter modules must reproduce it: ``native_to_br0_2`` (what ``-b 0.2``
runs) and ``native_to_br0_2_1`` (what ``-b 0.2.1`` runs, now a re-export of
the former), for every importer each module exports.

These pin converter output, so a change to the consolidated module (#1922)
shows up as a reviewable golden diff instead of a silent change, and the
deprecated ``native_to_br0_2_1`` names are checked to stay in step.

Output is made deterministic by clearing the harness environment variables,
fixing ``uuid.uuid4`` (the envelope's initial ``run.uid``), pinning ``TZ`` to
UTC (vllm-benchmark and InferenceMAX render ``run.time.end`` in local time),
dropping the memoized run metadata, and sorting keys.

Regenerate after an intentional output change with:

    BR_UPDATE_GOLDEN=1 python -m pytest tests/test_br_v0_2_golden.py
"""

from __future__ import annotations

import importlib
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN_DIR = FIXTURES / "br_v0_2_golden"

# Environment variables the converters read to fill run, scenario and
# treatment details on a live harness pod.
HARNESS_ENV_PREFIXES = ("LLMDBENCH_", "KUBERNETES_", "POD_UID")

# Converter module for each requested report version.
MODULES = {
    "0_2": "llmd_benchmark_report.native_to_br0_2",
    "0_2_1": "llmd_benchmark_report.native_to_br0_2_1",
}

TREATMENT_ENV = {
    "LLMDBENCH_TREATMENT_NAME": "first",
    "LLMDBENCH_TREATMENT_GROUP": "combined",
    "LLMDBENCH_TREATMENT_CONCURRENT_WITH": "second,third",
}


@dataclass
class Case:
    name: str
    importer: str
    fixture: str
    versions: tuple[str, ...] = ("0_2", "0_2_1")
    kwargs: dict = field(default_factory=dict)
    env: dict = field(default_factory=dict)


CASES = [
    Case(
        "inference_perf",
        "import_inference_perf",
        "inference_perf_stage_lifecycle_metrics.json",
    ),
    Case(
        "inference_perf_treatment",
        "import_inference_perf",
        "inference_perf_stage_lifecycle_metrics.json",
        env=TREATMENT_ENV,
    ),
    Case(
        "inference_perf_multimodal",
        "import_inference_perf",
        "inference_perf_lifecycle.yaml",
    ),
    Case(
        "inference_perf_prompt_tokens_only",
        "import_inference_perf",
        "inference_perf_stage_prompt_tokens_only.json",
    ),
    Case(
        "inference_perf_session",
        "import_inference_perf_session",
        "inference_perf_stage_0_session_lifecycle_metrics.json",
    ),
    Case(
        "inference_perf_session_treatment",
        "import_inference_perf_session",
        "inference_perf_stage_0_session_lifecycle_metrics.json",
        env=TREATMENT_ENV,
    ),
    Case("guidellm_all", "import_guidellm_all", "guidellm_report_v2.json"),
    Case("vllm_benchmark", "import_vllm_benchmark", "vllm_benchmark_results.json"),
    Case("inferencemax", "import_inference_max", "inferencemax_results.json"),
    # Not exported by native_to_br0_2_1, so there is no 0.2.1 path to pin.
    Case("aiperf", "import_aiperf", "aiperf_results.json", versions=("0_2",)),
    Case(
        "eval_containers",
        "import_eval_containers",
        "eval_containers/task/result.json",
        versions=("0_2",),
    ),
]

PARAMS = [(case, version) for case in CASES for version in case.versions]


# Removes the run_metadata.yaml memo the v0.2 converter keeps on
# _get_harness_meta, so one case's metadata never leaks into the next.
def _drop_harness_meta_cache() -> None:
    from llmd_benchmark_report.native_to_br0_2 import _get_harness_meta

    if hasattr(_get_harness_meta, "_cache"):
        del _get_harness_meta._cache


# Sets TZ (or removes it when `value` is None) and applies it to the
# process, since time.tzset() is what makes local-time rendering see it.
def _apply_tz(value: str | None) -> None:
    if value is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = value
    time.tzset()


# Runs case.importer from the module for `version` on case.fixture with a
# clean harness environment plus case.env and TZ=UTC, and returns the report
# (or list of reports) as sorted-key YAML with run.uid fixed to the all-zero
# UUID. TZ is restored afterwards.
def _convert(case: Case, version: str, monkeypatch: pytest.MonkeyPatch) -> str:
    for name in list(os.environ):
        if name.startswith(HARNESS_ENV_PREFIXES):
            monkeypatch.delenv(name)
    for name, value in case.env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=0))

    importer = getattr(importlib.import_module(MODULES[version]), case.importer)
    previous_tz = os.environ.get("TZ")
    _apply_tz("UTC")
    _drop_harness_meta_cache()
    try:
        result = importer(str(FIXTURES / case.fixture), **case.kwargs)
    finally:
        _drop_harness_meta_cache()
        _apply_tz(previous_tz)

    if isinstance(result, list):
        data = [report.dump() for report in result]
    else:
        data = result.dump()
    return yaml.safe_dump(data, sort_keys=True)


# Each (case, version) pair, e.g. inference_perf at 0_2 and at 0_2_1, must
# reproduce tests/fixtures/br_v0_2_golden/inference_perf.yaml byte for byte.
@pytest.mark.parametrize(
    ("case", "version"),
    PARAMS,
    ids=[f"{case.name}-v{version}" for case, version in PARAMS],
)
def test_converter_output_matches_golden(
    case: Case, version: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _convert(case, version, monkeypatch)
    golden = GOLDEN_DIR / f"{case.name}.yaml"
    if os.environ.get("BR_UPDATE_GOLDEN"):
        GOLDEN_DIR.mkdir(exist_ok=True)
        golden.write_text(actual, encoding="utf-8")
    assert golden.exists(), (
        f"{golden.name} is missing; regenerate with BR_UPDATE_GOLDEN=1"
    )
    assert actual == golden.read_text(encoding="utf-8")


# Every file in br_v0_2_golden/ must belong to a case above, so a removed or
# renamed case cannot leave a stale golden behind.
def test_no_orphaned_goldens() -> None:
    expected = {f"{case.name}.yaml" for case in CASES}
    on_disk = {path.name for path in GOLDEN_DIR.glob("*.yaml")}
    assert on_disk <= expected, f"orphaned goldens: {sorted(on_disk - expected)}"
