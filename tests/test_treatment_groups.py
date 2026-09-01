"""Tests for concurrent treatment groups.

Pins the contract that:

- A flat ``treatments:`` list still runs strictly sequentially, so every
  experiment file written before groups existed behaves identically.
- ``groups:`` batches its members for concurrent execution, and a
  single-member group is the sequential path.
- A malformed ``groups:`` block raises rather than silently degrading to
  sequential, which would report contention that was never measured.
- Each treatment gets its own pod label, so a wait scoped to one treatment
  cannot block on -- or be failed by -- a sibling's pods.
- Concurrent treatments really do overlap, one failing does not lose its
  sibling's result, and the shared experiment-ID list stays consistent.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.experiment.parser import (
    MAX_PARALLEL_TREATMENTS_CAP,
    groups_from_treatments,
    read_run_controls,
    read_treatment_groups,
)
from llmdbenchmark.run.steps.step_07_deploy_harness import (
    TREATMENT_LABEL,
    DeployHarnessStep,
    _TreatmentSpec,
)

# Kubernetes label-value grammar.
LABEL_VALUE = re.compile(r"[a-z0-9A-Z]([-_.a-z0-9A-Z]*[a-z0-9A-Z])?")

#: Real inference-perf stage output, so the converters run on valid input.
FIXTURE = (
    Path(__file__).parent / "fixtures" / "inference_perf_stage_lifecycle_metrics.json"
)

GROUPED = """\
max_parallel_treatments: 2
groups:
  - name: solo
    treatments:
      - name: alone
        profile: profile_a.yaml
  - name: mixed
    treatments:
      - name: first
        profile: profile_a.yaml
      - name: second
        profile: profile_b.yaml
        load.stages.0.concurrent_sessions: 32
"""


def session_results(tmp_path: Path) -> Path:
    """Minimum session-lifecycle input: the converter reads none of its fields."""
    path = tmp_path / "stage_0_session_lifecycle_metrics.json"
    path.write_text("{}", encoding="utf-8")
    return path


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "exp.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def make_context(**kwargs) -> ExecutionContext:
    context = ExecutionContext(plan_dir=Path("/tmp"), workspace=Path("/tmp"), **kwargs)
    context.logger = MagicMock()
    return context


def render_pod(**values) -> dict:
    """Render the harness pod template with `values` layered over a minimum."""
    import yaml

    template = Path("config/templates/jinja/20_harness_pod.yaml.j2").read_text(
        encoding="utf-8"
    )
    harness = {
        "podLabel": "llmdbench-harness-launcher",
        "namespace": "ns",
        "resources": {"cpu": 16, "memory": "1Gi"},
        "nodeSelector": {},
        "tolerations": [],
    }
    harness.update(values.pop("harness", {}))
    rendered = DeployHarnessStep._render_template(
        template[template.index("apiVersion:") : template.index("    env:")],
        {
            "pod_name": "p",
            "harness": harness,
            "namespace": {"name": "ns"},
            "images": {
                "benchmark": {
                    "repository": "r",
                    "tag": "t",
                    "pullPolicy": "IfNotPresent",
                }
            },
            **values,
        },
    )
    return yaml.safe_load(rendered)


def make_spec(
    name: str, group: str | None, index: int = 1, total: int = 1
) -> _TreatmentSpec:
    return _TreatmentSpec(
        treatment={"name": name, "group": group},
        index=index,
        total=total,
        group=group,
        cmd=None,
        plan_config={},
        harness_name="inference-perf",
        harness_ns="ns",
        deploy_namespace="ns",
        endpoint_url="http://endpoint",
        model_label="model",
        model_name="model",
        stack_type="llm-d",
        profile_name="profile_a.yaml",
        profile_mounts=[],
        results_dir_prefix="/requests",
        harness_executable="llm-d-benchmark.sh",
        template_content="",
        pod_label="llmdbench-harness-launcher",
        parallelism=1,
        timeout=60,
    )


class TestParsing:
    def test_groups_are_ordered_and_carry_profiles(self, tmp_path: Path) -> None:
        groups = read_treatment_groups(write(tmp_path, GROUPED))

        assert [g.name for g in groups] == ["solo", "mixed"]
        assert [g.is_concurrent for g in groups] == [False, True]
        assert [t["profile"] for t in groups[1].treatments] == [
            "profile_a.yaml",
            "profile_b.yaml",
        ]
        assert groups[1].treatments[1]["overrides"] == {
            "load.stages.0.concurrent_sessions": 32
        }

    def test_reads_empty_without_a_groups_block(self, tmp_path: Path) -> None:
        assert (
            read_treatment_groups(write(tmp_path, "treatments:\n  - name: a\n")) == []
        )
        assert read_treatment_groups(tmp_path / "absent.yaml") == []
        assert read_treatment_groups(None) == []

    def test_flat_list_becomes_single_member_groups(self) -> None:
        groups = groups_from_treatments([{"name": "a"}, {"name": "b"}])

        assert [g.name for g in groups] == ["a", "b"]
        assert not any(g.is_concurrent for g in groups)

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("groups: {}\n", "must be a non-empty list"),
            ("groups: []\n", "must be a non-empty list"),
            ("groups:\n  - [a, b]\n", "must be a mapping"),
            ("groups:\n  - name: g\n    treatments: []\n", "non-empty 'treatments'"),
            (
                "groups:\n"
                "  - name: g1\n    treatments: [{name: a}]\n"
                "  - name: g2\n    treatments: [{name: a}]\n",
                "appears in both group",
            ),
            (
                "groups:\n  - name: g\n    treatments:\n"
                "      - name: a\n        harness: guidellm\n",
                "sets 'harness'",
            ),
        ],
    )
    def test_malformed_groups_raise(
        self, tmp_path: Path, text: str, expected: str
    ) -> None:
        with pytest.raises(ValueError, match=expected):
            read_treatment_groups(write(tmp_path, text))

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("treatments:\n  - name: a\n", 1),  # unset: sequential
            ("max_parallel_treatments: 0\n", 1),
            ("max_parallel_treatments: 999\n", MAX_PARALLEL_TREATMENTS_CAP),
        ],
    )
    def test_parallelism_cap_is_clamped(
        self, tmp_path: Path, text: str, expected: int
    ) -> None:
        controls = read_run_controls(write(tmp_path, text))

        assert controls["max_parallel_treatments"] == expected


class TestBatching:
    def test_group_members_batch_together(self) -> None:
        specs = [
            make_spec("alone", "solo"),
            make_spec("first", "mixed"),
            make_spec("second", "mixed"),
        ]

        batches = DeployHarnessStep()._batch_specs(
            specs, make_context(max_parallel_treatments=2)
        )

        assert [[s.label for s in b] for b in batches] == [
            ["alone"],
            ["first", "second"],
        ]

    def test_ungrouped_treatments_stay_sequential(self) -> None:
        specs = [make_spec(n, None) for n in ("ctx8k", "ctx16k", "ctx32k")]

        batches = DeployHarnessStep()._batch_specs(specs, make_context())

        assert [len(b) for b in batches] == [1, 1, 1]

    def test_ungrouped_duplicate_names_stay_sequential(self) -> None:
        """A flat list may repeat a name; that must not imply a group."""
        specs = [make_spec("qps", None), make_spec("qps", None)]

        batches = DeployHarnessStep()._batch_specs(
            specs, make_context(max_parallel_treatments=2)
        )

        assert [len(b) for b in batches] == [1, 1]
        assert all(spec.siblings == () for batch in batches for spec in batch)

    def test_ungrouped_treatment_has_no_group(self) -> None:
        assert DeployHarnessStep()._treatment_group_name({"name": "solo"}) is None
        assert DeployHarnessStep()._treatment_group_name(None) is None
        assert (
            DeployHarnessStep()._treatment_group_name({"name": "a", "group": "g"})
            == "g"
        )

    def test_members_learn_their_siblings(self) -> None:
        specs = [make_spec("a", "mixed"), make_spec("b", "mixed")]

        batch = DeployHarnessStep()._batch_specs(
            specs, make_context(max_parallel_treatments=2)
        )[0]

        assert [s.siblings for s in batch] == [("b",), ("a",)]

    def test_group_larger_than_cap_warns(self) -> None:
        specs = [make_spec(n, "mixed") for n in ("a", "b", "c")]
        context = make_context(max_parallel_treatments=2)

        DeployHarnessStep()._batch_specs(specs, context)

        assert "only 2 run at a time" in context.logger.log_warning.call_args[0][0]


class TestPodLabel:
    def test_template_stamps_the_treatment_label(self) -> None:
        """The wait selector and the pod template must agree on the label key."""

        def labels(value: str | None) -> dict:
            extra = {} if value is None else {"treatment_label_value": value}
            return render_pod(**extra)["metadata"]["labels"]

        first, second, unset = labels("first-a"), labels("second-b"), labels(None)

        assert first[TREATMENT_LABEL] != second[TREATMENT_LABEL]
        assert unset[TREATMENT_LABEL] == "default"
        # Cleanup selects on app/function, so those must not move.
        for rendered in (first, second, unset):
            assert rendered["app"] == "llmdbench-harness-launcher"
            assert rendered["function"] == "load_generator"

    @pytest.mark.parametrize(
        "name",
        ["ctx8k", "ctx_8k", "Weka Traces!!", "--weird--", "", "a" * 80, "a" * 62],
    )
    def test_label_value_is_always_valid(self, name: str) -> None:
        value = DeployHarnessStep._treatment_label_value(name, "ab12cd")

        assert len(value) <= 63
        assert LABEL_VALUE.fullmatch(value)

    @pytest.mark.parametrize(
        "first_name, first_suffix, second_name, second_suffix",
        [
            # Distinct treatments.
            ("first", "aaaaaa", "second", "bbbbbb"),
            # A retry: a fresh label stops it waiting on the previous attempt.
            ("first", "aaaaaa", "first", "bbbbbb"),
            # Names that sanitize alike; only the suffix separates them.
            ("ctx_8k", "aaaaaa", "ctx-8k", "bbbbbb"),
            # Truncation must drop name characters, never the suffix.
            ("a" * 80, "aaaaaa", "a" * 80, "bbbbbb"),
            ("a" * 62, "aaaaaa", "a" * 62, "bbbbbb"),
        ],
    )
    def test_labels_are_unique(
        self, first_name, first_suffix, second_name, second_suffix
    ) -> None:
        assert DeployHarnessStep._treatment_label_value(
            first_name, first_suffix
        ) != DeployHarnessStep._treatment_label_value(second_name, second_suffix)


class TestConcurrency:
    def test_members_actually_overlap(self) -> None:
        step = DeployHarnessStep()
        context = make_context(max_parallel_treatments=2)
        batch = [make_spec("a", "mixed"), make_spec("b", "mixed")]
        inflight = peak = 0
        lock = threading.Lock()

        def occupy(spec, ctx):
            nonlocal inflight, peak
            with lock:
                inflight += 1
                peak = max(peak, inflight)
            time.sleep(0.2)
            with lock:
                inflight -= 1
            ctx.record_experiment_id(f"eid-{spec.label}")
            return True, [], 1

        step._run_treatment = occupy
        started = time.monotonic()
        results = step._run_batch_parallel(batch, context)
        elapsed = time.monotonic() - started

        assert peak == 2
        assert elapsed < 0.4
        assert results == [(True, [], 1), (True, [], 1)]
        assert sorted(context.experiment_ids) == ["eid-a", "eid-b"]

    def test_one_failure_keeps_its_sibling(self) -> None:
        step = DeployHarnessStep()
        batch = [make_spec("a", "mixed"), make_spec("b", "mixed")]

        def half_fail(spec, ctx):
            if spec.label == "a":
                raise RuntimeError("boom")
            return True, [], 3

        step._run_treatment = half_fail
        results = step._run_batch_parallel(
            batch, make_context(max_parallel_treatments=2)
        )

        assert results[0][0] is False
        assert "boom" in results[0][1][0]
        assert results[1] == (True, [], 3)

    def test_results_follow_submission_order(self) -> None:
        step = DeployHarnessStep()
        batch = [make_spec("slow", "mixed"), make_spec("fast", "mixed")]

        def staggered(spec, ctx):
            time.sleep(0.2 if spec.label == "slow" else 0.0)
            return True, [], 1 if spec.label == "slow" else 2

        step._run_treatment = staggered
        results = step._run_batch_parallel(
            batch, make_context(max_parallel_treatments=2)
        )

        assert [deployed for _, _, deployed in results] == [1, 2]


class TestCacheReset:
    @staticmethod
    def _reset_calls(monkeypatch, batch, **context_kwargs) -> tuple[MagicMock, Any]:
        reset = MagicMock()
        monkeypatch.setattr(
            "llmdbenchmark.run.steps.step_07_deploy_harness.reset_caches_pods", reset
        )
        context = make_context(**context_kwargs)
        DeployHarnessStep()._reset_caches_for_batch(batch, context)
        return reset, context

    def test_fires_once_for_a_whole_group(self, monkeypatch) -> None:
        batch = [make_spec("a", "mixed"), make_spec("b", "mixed")]

        reset, context = self._reset_calls(
            monkeypatch, batch, reset_caches=True, max_parallel_treatments=2
        )

        assert reset.call_count == 1
        assert "each start cold" in context.logger.log_warning.call_args[0][0]

    @pytest.mark.parametrize(
        "context_kwargs",
        [{"reset_caches": False}, {"reset_caches": True, "dry_run": True}],
    )
    def test_skipped(self, monkeypatch, context_kwargs) -> None:
        reset, _ = self._reset_calls(
            monkeypatch, [make_spec("a", "solo")], **context_kwargs
        )

        reset.assert_not_called()


class TestReportMetadata:
    @pytest.mark.parametrize(
        "grouping, expected",
        [
            (
                {
                    "treatment_name": "first",
                    "treatment_group": "mixed",
                    "concurrent_with": "second",
                },
                [
                    "export LLMDBENCH_TREATMENT_NAME=first",
                    "export LLMDBENCH_TREATMENT_GROUP=mixed",
                    "export LLMDBENCH_TREATMENT_CONCURRENT_WITH=second",
                ],
            ),
            # A sequential run exports nothing, so its report is unchanged.
            ({}, []),
        ],
    )
    def test_grouping_reaches_the_harness_environment(self, grouping, expected) -> None:
        command = DeployHarnessStep._build_harness_command(
            harness_executable="llm-d-benchmark.sh",
            profile_name="profile_a-first.yaml",
            harness_name="inference-perf",
            results_dir="/requests/e1_1",
            **grouping,
        )

        for export in expected:
            assert export in command
        if not expected:
            assert "LLMDBENCH_TREATMENT_" not in command

    @pytest.mark.parametrize(
        "name, group, siblings",
        [
            ("has space", "grp", "a,b"),
            ("semi;colon", "grp", "a,b"),
            ("quote'name", "grp", "o'brien,b"),
            ("$(subshell)", "grp", "`tick`"),
        ],
    )
    def test_metadata_survives_the_shell(
        self, name: str, group: str, siblings: str
    ) -> None:
        """Values reach the pod verbatim; a shell metacharacter must not run."""
        command = DeployHarnessStep._build_harness_command(
            harness_executable="llm-d-benchmark.sh",
            profile_name="p.yaml",
            harness_name="inference-perf",
            results_dir="/requests/e1_1",
            treatment_name=name,
            treatment_group=group,
            concurrent_with=siblings,
        )
        exports = "; ".join(p for p in command.split("; ") if "TREATMENT" in p)

        probe = subprocess.run(
            [
                "sh",
                "-c",
                f'{exports}; printf "%s\n%s\n%s" '
                f'"$LLMDBENCH_TREATMENT_NAME" "$LLMDBENCH_TREATMENT_GROUP" '
                f'"$LLMDBENCH_TREATMENT_CONCURRENT_WITH"',
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        assert probe.stdout.split("\n") == [name, group, siblings]

    def test_grouping_belongs_to_v0_2_1_only(self) -> None:
        """v0.2 forbids these fields, so a populator on the shared v0.2 path
        makes every report fail validation -- which is how it reached a cluster
        the first time."""
        from llmd_benchmark_report.schema_v0_2 import LoadMetadata as LoadMetadataV02
        from llmd_benchmark_report.schema_v0_2_1 import LoadMetadata

        grouping = {
            "treatment": "first",
            "treatment_group": "combined",
            "concurrent_with": ["second"],
        }

        accepted = LoadMetadata(**grouping)
        assert accepted.treatment == "first"
        assert accepted.concurrent_with == ["second"]

        with pytest.raises(ValidationError):
            LoadMetadataV02(**grouping)

    def test_v0_2_converter_never_emits_the_grouping(self, monkeypatch) -> None:
        """The v0.2 populator must stay clean of the v0.2.1-only fields."""
        import inspect

        from llmd_benchmark_report import native_to_br0_2

        source = inspect.getsource(native_to_br0_2._populate_load)

        assert "LLMDBENCH_TREATMENT" not in source

    @pytest.mark.parametrize(
        "environment, expected",
        [
            (
                {
                    "LLMDBENCH_TREATMENT_NAME": "first",
                    "LLMDBENCH_TREATMENT_GROUP": "combined",
                    "LLMDBENCH_TREATMENT_CONCURRENT_WITH": "second, third",
                },
                {
                    "treatment": "first",
                    "treatment_group": "combined",
                    "concurrent_with": ["second", "third"],
                },
            ),
            ({}, {}),
        ],
    )
    def test_v0_2_1_converter_reads_the_environment(
        self, monkeypatch, environment, expected
    ) -> None:
        for var in (
            "LLMDBENCH_TREATMENT_NAME",
            "LLMDBENCH_TREATMENT_GROUP",
            "LLMDBENCH_TREATMENT_CONCURRENT_WITH",
        ):
            monkeypatch.delenv(var, raising=False)
        for var, value in environment.items():
            monkeypatch.setenv(var, value)

        from llmd_benchmark_report import native_to_br0_2_1

        assert native_to_br0_2_1._treatment_metadata() == expected

    def test_session_reports_are_v0_2_1_and_carry_the_grouping(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Trace-replay workloads produce only a session report.

        The v0.2.1 module re-exported the v0.2 session converter, so ``-b 0.2.1``
        silently produced a v0.2 report that cannot hold the grouping at all.
        """
        monkeypatch.setenv("LLMDBENCH_TREATMENT_NAME", "first")
        monkeypatch.setenv("LLMDBENCH_TREATMENT_GROUP", "combined")
        monkeypatch.setenv("LLMDBENCH_TREATMENT_CONCURRENT_WITH", "second")

        from llmd_benchmark_report import native_to_br0_2, native_to_br0_2_1

        assert (
            native_to_br0_2_1.import_inference_perf_session
            is not native_to_br0_2.import_inference_perf_session
        )

        report = native_to_br0_2_1.import_inference_perf_session(
            str(session_results(tmp_path))
        )
        metadata = report.scenario.load.metadata

        assert report.version == "0.2.1"
        assert metadata.treatment == "first"
        assert metadata.treatment_group == "combined"
        assert metadata.concurrent_with == ["second"]

    def test_v0_2_session_reports_omit_the_grouping(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("LLMDBENCH_TREATMENT_NAME", "first")

        from llmd_benchmark_report import native_to_br0_2

        report = native_to_br0_2.import_inference_perf_session(
            str(session_results(tmp_path))
        )

        assert report.version == "0.2"
        assert report.scenario.load.metadata.model_dump().get("treatment") is None


class TestHarnessMemory:
    """A group of concurrent treatments needs more memory than one pod.

    The limit is raised without raising the request, so the ceiling can grow
    without shrinking the set of nodes a harness pod can schedule onto.
    """

    @staticmethod
    def _memory(resources: dict) -> tuple[str, str]:
        container = render_pod(
            harness={"resources": resources}, treatment_label_value="t-a1"
        )["spec"]["containers"][0]
        return (
            container["resources"]["requests"]["memory"],
            container["resources"]["limits"]["memory"],
        )

    def test_limit_exceeds_request_by_default(self) -> None:
        import yaml

        defaults = yaml.safe_load(
            Path("config/templates/values/defaults.yaml").read_text(encoding="utf-8")
        )

        request, limit = self._memory(defaults["harness"]["resources"])

        assert request != limit
        assert limit == defaults["harness"]["resources"]["memoryLimit"]

    @pytest.mark.parametrize(
        "resources, expected",
        [
            # Only `memory` set: keeps the pre-split behaviour.
            ({"cpu": 16, "memory": "8Gi"}, ("8Gi", "8Gi")),
            ({"cpu": 16, "memory": "4Gi", "memoryLimit": "16Gi"}, ("4Gi", "16Gi")),
        ],
    )
    def test_request_and_limit(self, resources, expected) -> None:
        assert self._memory(resources) == expected
