"""Tests for #1891: prefer inference-perf's native BR0.2 partial over the
hand-derived aggregate mapping when a sibling partial is present.

inference-perf >= v0.7.0 (kubernetes-sigs/inference-perf#461) writes
``inference-perf.partial.stage_<N>.yaml`` next to each
``stage_<N>_lifecycle_metrics.json``, carrying ``results.request_performance
.aggregate`` and ``run.{uid,eid,time}`` computed from the same request
lifecycle metrics ``import_inference_perf`` otherwise re-derives by hand
(~700 lines in ``_build_inference_perf_aggregate_native``). These tests pin:

- the partial's aggregate is used verbatim instead of the native derivation,
  and agrees field-for-field with what the native derivation produces on the
  same underlying data;
- the partial's run.uid/eid/time win over the envelope's placeholders;
- older harness images with no partial keep working via the native fallback;
- partial lookup is stage-scoped (stage 0's partial never leaks into stage 1).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from llmd_benchmark_report.native_to_br0_2 import import_inference_perf

FIXTURES = Path(__file__).parent / "fixtures"
NATIVE_FIXTURE = FIXTURES / "inference_perf_stage_lifecycle_metrics.json"


def _write_stage(tmp_path: Path, stage: int) -> Path:
    """Copy the native lifecycle fixture into tmp_path as stage_<N>_lifecycle_metrics.json."""
    dest = tmp_path / f"stage_{stage}_lifecycle_metrics.json"
    shutil.copyfile(NATIVE_FIXTURE, dest)
    return dest


def _write_partial(tmp_path: Path, stage: int, partial: dict) -> Path:
    dest = tmp_path / f"inference-perf.partial.stage_{stage}.yaml"
    dest.write_text(yaml.safe_dump(partial, sort_keys=False), encoding="utf-8")
    return dest


def _partial_for(aggregate: dict, *, uid: str, eid: str | None = None) -> dict:
    run: dict = {
        "uid": uid,
        "time": {
            "start": "2026-05-20T14:30:12.123+00:00",
            "end": "2026-05-20T14:30:17.456+00:00",
            "duration": "PT5.333S",
        },
    }
    if eid is not None:
        run["eid"] = eid
    return {
        "version": "0.2.1",
        "run": run,
        "results": {"request_performance": {"aggregate": aggregate}},
    }


class TestPartialPreferredOverNativeDerivation:
    def test_no_partial_falls_back_to_native_derivation(self, tmp_path: Path) -> None:
        """Older harness images (no partial file) keep working unchanged."""
        results_file = _write_stage(tmp_path, 0)

        report = import_inference_perf(str(results_file))

        aggregate = report.dump()["results"]["request_performance"]["aggregate"]
        assert aggregate["requests"]["total"] == 79

    def test_partial_aggregate_agrees_with_native_derivation(
        self, tmp_path: Path
    ) -> None:
        """The partial-sourced and natively-derived reports must agree
        field-for-field on the same underlying run -- the whole point of the
        partial is that it's computed from the same request lifecycle data."""
        results_file = _write_stage(tmp_path, 0)
        native_aggregate = import_inference_perf(str(results_file)).dump()["results"][
            "request_performance"
        ]["aggregate"]

        _write_partial(
            tmp_path,
            0,
            _partial_for(native_aggregate, uid="inference-perf-stage-0-aaaa1111"),
        )
        partial_aggregate = import_inference_perf(str(results_file)).dump()["results"][
            "request_performance"
        ]["aggregate"]

        assert partial_aggregate == native_aggregate

    def test_partial_aggregate_is_actually_used_not_silently_ignored(
        self, tmp_path: Path
    ) -> None:
        """A distinct partial aggregate must show up verbatim in the report --
        proving the partial path runs instead of the native derivation, not
        just alongside it."""
        results_file = _write_stage(tmp_path, 0)
        distinct_aggregate = {
            "requests": {"total": 12345, "failures": 0},
        }
        _write_partial(
            tmp_path,
            0,
            _partial_for(distinct_aggregate, uid="inference-perf-stage-0-bbbb2222"),
        )

        report = import_inference_perf(str(results_file))

        aggregate = report.dump()["results"]["request_performance"]["aggregate"]
        assert aggregate["requests"]["total"] == 12345
        # The native fixture's own count (79) must not leak through.
        assert aggregate["requests"]["total"] != 79

    def test_partial_run_identity_wins_over_envelope_placeholders(
        self, tmp_path: Path
    ) -> None:
        results_file = _write_stage(tmp_path, 0)
        _write_partial(
            tmp_path,
            0,
            _partial_for(
                {"requests": {"total": 1, "failures": 0}},
                uid="inference-perf-stage-0-cccc3333",
                eid="inference-perf-experiment-dddd4444",
            ),
        )

        report = import_inference_perf(str(results_file))

        assert report.run.uid == "inference-perf-stage-0-cccc3333"
        assert report.run.eid == "inference-perf-experiment-dddd4444"
        assert report.run.time.duration == "PT5.333S"

    def test_partial_lookup_is_scoped_to_its_own_stage(self, tmp_path: Path) -> None:
        """Stage 0's partial must never leak into stage 1's report."""
        stage0_file = _write_stage(tmp_path, 0)
        stage1_file = _write_stage(tmp_path, 1)
        _write_partial(
            tmp_path,
            0,
            _partial_for(
                {"requests": {"total": 111, "failures": 0}},
                uid="inference-perf-stage-0-eeee5555",
            ),
        )
        # No partial written for stage 1: it must fall back to native.

        report0 = import_inference_perf(str(stage0_file))
        report1 = import_inference_perf(str(stage1_file))

        assert (
            report0.dump()["results"]["request_performance"]["aggregate"]["requests"][
                "total"
            ]
            == 111
        )
        assert (
            report1.dump()["results"]["request_performance"]["aggregate"]["requests"][
                "total"
            ]
            == 79
        )
        assert report1.run.uid != "inference-perf-stage-0-eeee5555"

    def test_malformed_partial_falls_back_to_native_derivation(
        self, tmp_path: Path
    ) -> None:
        """A partial that fails to parse must not crash the conversion --
        fall back to the native derivation, matching the no-partial case."""
        results_file = _write_stage(tmp_path, 0)
        partial_path = tmp_path / "inference-perf.partial.stage_0.yaml"
        partial_path.write_text("not: valid: yaml: [", encoding="utf-8")

        report = import_inference_perf(str(results_file))

        aggregate = report.dump()["results"]["request_performance"]["aggregate"]
        assert aggregate["requests"]["total"] == 79
