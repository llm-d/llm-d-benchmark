"""Tests for the DoE experiment parser and summary modules."""

from __future__ import annotations

import textwrap
import time
from pathlib import Path

import pytest
import yaml

from llmdbenchmark.experiment.parser import (
    ExperimentPlan,
    SetupTreatment,
    dotted_to_nested,
    parse_experiment,
)
from llmdbenchmark.experiment.summary import (
    ExperimentSummary,
    TreatmentResult,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = PROJECT_ROOT / "workload" / "experiments"


# ===========================================================================
# dotted_to_nested
# ===========================================================================


class TestDottedToNested:
    """Tests for the dotted_to_nested() utility function."""

    def test_single_dotted_key(self):
        assert dotted_to_nested({"a.b.c": 1}) == {"a": {"b": {"c": 1}}}

    def test_multiple_dotted_keys_same_prefix(self):
        result = dotted_to_nested({"a.b.c": 1, "a.b.d": 2})
        assert result == {"a": {"b": {"c": 1, "d": 2}}}

    def test_non_dotted_key(self):
        assert dotted_to_nested({"x": 3}) == {"x": 3}

    def test_mixed_dotted_and_non_dotted(self):
        result = dotted_to_nested({"a.b.c": 1, "a.b.d": 2, "x": 3})
        assert result == {"a": {"b": {"c": 1, "d": 2}}, "x": 3}

    def test_empty_dict(self):
        assert dotted_to_nested({}) == {}

    def test_single_level_key(self):
        assert dotted_to_nested({"key": "value"}) == {"key": "value"}

    def test_deep_nesting(self):
        result = dotted_to_nested({"a.b.c.d.e": 42})
        assert result == {"a": {"b": {"c": {"d": {"e": 42}}}}}

    def test_divergent_paths(self):
        """Keys that share a prefix but diverge at different levels."""
        result = dotted_to_nested({
            "model.maxModelLen": 16000,
            "model.blockSize": 64,
            "vllmCommon.flags.numCpuBlocks": 500,
        })
        assert result == {
            "model": {"maxModelLen": 16000, "blockSize": 64},
            "vllmCommon": {"flags": {"numCpuBlocks": 500}},
        }

    def test_preserves_value_types(self):
        result = dotted_to_nested({
            "a.int": 42,
            "a.float": 3.14,
            "a.str": "hello",
            "a.bool": True,
            "a.none": None,
            "a.list": [1, 2, 3],
        })
        assert result["a"]["int"] == 42
        assert result["a"]["float"] == 3.14
        assert result["a"]["str"] == "hello"
        assert result["a"]["bool"] is True
        assert result["a"]["none"] is None
        assert result["a"]["list"] == [1, 2, 3]

    def test_collision_scalar_then_nested(self):
        """Setting 'a.b' to a scalar then 'a.b.c' to a value should raise."""
        with pytest.raises(ValueError, match="Key conflict"):
            dotted_to_nested({"a.b": 1, "a.b.c": 2})

    def test_collision_nested_then_scalar(self):
        """Setting 'a.b.c' to a value then 'a.b' to a scalar should raise."""
        with pytest.raises(ValueError, match="Key conflict"):
            dotted_to_nested({"a.b.c": 2, "a.b": 1})


# ===========================================================================
# parse_experiment — with setup treatments
# ===========================================================================


class TestParseExperimentWithSetup:
    """Tests for parsing experiment files that include setup treatments."""

    @pytest.fixture
    def experiment_yaml(self, tmp_path: Path) -> Path:
        """Create a complete experiment YAML with setup + run treatments."""
        content = textwrap.dedent("""\
            experiment:
              name: test-experiment
              harness: inference-perf
              profile: shared_prefix_synthetic.yaml

            design:
              type: full_factorial
              setup:
                factors:
                  - name: numCpuBlocks
                    key: vllmCommon.flags.numCpuBlocks
                    levels: [500, 1000]
              run:
                factors:
                  - name: num_groups
                    key: data.shared_prefix.num_groups
                    levels: [40, 60]

            setup:
              constants:
                model.maxModelLen: 16000
                model.blockSize: 64
              treatments:
                - name: cpu-blocks-500
                  vllmCommon.flags.numCpuBlocks: 500
                - name: cpu-blocks-1000
                  vllmCommon.flags.numCpuBlocks: 1000

            treatments:
              - name: grp40
                data.shared_prefix.num_groups: 40
              - name: grp60
                data.shared_prefix.num_groups: 60
        """)
        p = tmp_path / "test-experiment.yaml"
        p.write_text(content)
        return p

    def test_parses_experiment_metadata(self, experiment_yaml: Path):
        plan = parse_experiment(experiment_yaml)
        assert plan.name == "test-experiment"
        assert plan.harness == "inference-perf"
        assert plan.profile == "shared_prefix_synthetic.yaml"

    def test_has_setup_phase(self, experiment_yaml: Path):
        plan = parse_experiment(experiment_yaml)
        assert plan.has_setup_phase is True

    def test_setup_treatment_count(self, experiment_yaml: Path):
        plan = parse_experiment(experiment_yaml)
        assert len(plan.setup_treatments) == 2

    def test_setup_treatment_names(self, experiment_yaml: Path):
        plan = parse_experiment(experiment_yaml)
        names = [t.name for t in plan.setup_treatments]
        assert names == ["cpu-blocks-500", "cpu-blocks-1000"]

    def test_setup_constants_merged_into_overrides(self, experiment_yaml: Path):
        plan = parse_experiment(experiment_yaml)
        for t in plan.setup_treatments:
            assert t.overrides["model"]["maxModelLen"] == 16000
            assert t.overrides["model"]["blockSize"] == 64

    def test_treatment_specific_overrides(self, experiment_yaml: Path):
        plan = parse_experiment(experiment_yaml)
        assert plan.setup_treatments[0].overrides["vllmCommon"]["flags"]["numCpuBlocks"] == 500
        assert plan.setup_treatments[1].overrides["vllmCommon"]["flags"]["numCpuBlocks"] == 1000

    def test_run_treatment_count(self, experiment_yaml: Path):
        plan = parse_experiment(experiment_yaml)
        assert plan.run_treatments_count == 2

    def test_total_matrix(self, experiment_yaml: Path):
        plan = parse_experiment(experiment_yaml)
        # 2 setup × 2 run = 4
        assert plan.total_matrix == 4

    def test_experiment_file_path(self, experiment_yaml: Path):
        plan = parse_experiment(experiment_yaml)
        assert plan.experiment_file == experiment_yaml.resolve()


class TestSetupConstantsOverrideOrder:
    """Verify that treatment-specific values override setup constants."""

    @pytest.fixture
    def yaml_with_override_conflict(self, tmp_path: Path) -> Path:
        """Setup where a treatment overrides a constant."""
        content = textwrap.dedent("""\
            experiment:
              name: override-test

            setup:
              constants:
                model.maxModelLen: 16000
                model.blockSize: 64
              treatments:
                - name: custom-model-len
                  model.maxModelLen: 32000
                  vllmCommon.flags.numCpuBlocks: 500
        """)
        p = tmp_path / "override-test.yaml"
        p.write_text(content)
        return p

    def test_treatment_overrides_constant(self, yaml_with_override_conflict: Path):
        plan = parse_experiment(yaml_with_override_conflict)
        t = plan.setup_treatments[0]
        assert t.overrides["model"]["maxModelLen"] == 32000
        assert t.overrides["model"]["blockSize"] == 64
        assert t.overrides["vllmCommon"]["flags"]["numCpuBlocks"] == 500


# ===========================================================================
# parse_experiment — without setup (backward compat)
# ===========================================================================


class TestParseExperimentWithoutSetup:
    """Tests for parsing run-only experiment files (no setup section)."""

    @pytest.fixture
    def run_only_yaml(self, tmp_path: Path) -> Path:
        """Create an experiment YAML without setup section."""
        content = textwrap.dedent("""\
            experiment:
              name: run-only-test
              harness: vllm-benchmark
              profile: random_concurrent.yaml

            treatments:
              - name: conc1
                max-concurrency: 1
                num-prompts: 10
              - name: conc8
                max-concurrency: 8
                num-prompts: 80
              - name: conc32
                max-concurrency: 32
                num-prompts: 320
        """)
        p = tmp_path / "run-only.yaml"
        p.write_text(content)
        return p

    def test_no_setup_phase(self, run_only_yaml: Path):
        plan = parse_experiment(run_only_yaml)
        assert plan.has_setup_phase is False

    def test_empty_setup_treatments(self, run_only_yaml: Path):
        plan = parse_experiment(run_only_yaml)
        assert plan.setup_treatments == []

    def test_run_treatment_count(self, run_only_yaml: Path):
        plan = parse_experiment(run_only_yaml)
        assert plan.run_treatments_count == 3

    def test_total_matrix_without_setup(self, run_only_yaml: Path):
        plan = parse_experiment(run_only_yaml)
        # max(0 setup, 1) × 3 run = 3
        assert plan.total_matrix == 3

    def test_metadata(self, run_only_yaml: Path):
        plan = parse_experiment(run_only_yaml)
        assert plan.name == "run-only-test"
        assert plan.harness == "vllm-benchmark"
        assert plan.profile == "random_concurrent.yaml"


# ===========================================================================
# parse_experiment — edge cases
# ===========================================================================


class TestParseExperimentEdgeCases:
    """Edge cases and error handling for parse_experiment."""

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError, match="not found"):
            parse_experiment(Path("/nonexistent/experiment.yaml"))

    def test_invalid_yaml_type(self, tmp_path: Path):
        """A YAML file that parses to a list instead of a dict."""
        p = tmp_path / "invalid.yaml"
        p.write_text("- item1\n- item2\n")
        with pytest.raises(ValueError, match="YAML mapping"):
            parse_experiment(p)

    def test_missing_experiment_name_uses_stem(self, tmp_path: Path):
        """When experiment.name is missing, file stem is used."""
        p = tmp_path / "my-cool-experiment.yaml"
        p.write_text("treatments:\n  - name: t1\n    key: value\n")
        plan = parse_experiment(p)
        assert plan.name == "my-cool-experiment"

    def test_missing_harness_and_profile(self, tmp_path: Path):
        """Harness and profile are None when not specified."""
        p = tmp_path / "minimal.yaml"
        p.write_text("experiment:\n  name: minimal\ntreatments:\n  - name: t1\n    k: v\n")
        plan = parse_experiment(p)
        assert plan.harness is None
        assert plan.profile is None

    def test_setup_without_treatments_key(self, tmp_path: Path):
        """Setup section without 'treatments' is ignored."""
        content = textwrap.dedent("""\
            experiment:
              name: no-setup-treatments
            setup:
              constants:
                model.maxModelLen: 16000
            treatments:
              - name: t1
                key: value
        """)
        p = tmp_path / "no-setup-treatments.yaml"
        p.write_text(content)
        plan = parse_experiment(p)
        assert plan.has_setup_phase is False
        assert plan.setup_treatments == []

    def test_empty_setup_treatments_list(self, tmp_path: Path):
        """Setup with empty treatments list."""
        content = textwrap.dedent("""\
            experiment:
              name: empty-setup
            setup:
              treatments: []
            treatments:
              - name: t1
                key: value
        """)
        p = tmp_path / "empty-setup.yaml"
        p.write_text(content)
        plan = parse_experiment(p)
        assert plan.has_setup_phase is False
        assert plan.setup_treatments == []

    def test_setup_treatment_without_name(self, tmp_path: Path):
        """Setup treatment missing name gets auto-generated name."""
        content = textwrap.dedent("""\
            experiment:
              name: no-name-treatment
            setup:
              treatments:
                - vllmCommon.flags.numCpuBlocks: 500
                - vllmCommon.flags.numCpuBlocks: 1000
        """)
        p = tmp_path / "no-name.yaml"
        p.write_text(content)
        plan = parse_experiment(p)
        assert plan.setup_treatments[0].name == "setup-0"
        assert plan.setup_treatments[1].name == "setup-1"

    def test_no_run_treatments(self, tmp_path: Path):
        """Experiment with setup but no run treatments."""
        content = textwrap.dedent("""\
            experiment:
              name: setup-only
            setup:
              treatments:
                - name: t1
                  key.sub: value
        """)
        p = tmp_path / "setup-only.yaml"
        p.write_text(content)
        plan = parse_experiment(p)
        assert plan.run_treatments_count == 0
        # total_matrix: max(1 setup, 1) × max(0 run, 1) = 1
        assert plan.total_matrix == 1

    def test_empty_treatments_list_returns_zero(self, tmp_path: Path):
        """An explicit empty treatments list should return 0, not fall through."""
        content = textwrap.dedent("""\
            experiment:
              name: empty-treatments
            treatments: []
            run:
              - name: should-not-count
                key: value
        """)
        p = tmp_path / "empty-treatments.yaml"
        p.write_text(content)
        plan = parse_experiment(p)
        assert plan.run_treatments_count == 0

    def test_setup_constants_without_constants_key(self, tmp_path: Path):
        """Setup section without constants key — treatments still parse."""
        content = textwrap.dedent("""\
            experiment:
              name: no-constants
            setup:
              treatments:
                - name: t1
                  model.maxModelLen: 16000
        """)
        p = tmp_path / "no-constants.yaml"
        p.write_text(content)
        plan = parse_experiment(p)
        assert len(plan.setup_treatments) == 1
        assert plan.setup_treatments[0].overrides == {
            "model": {"maxModelLen": 16000}
        }


# ===========================================================================
# parse_experiment — real experiment files
# ===========================================================================


class TestParseRealExperimentFiles:
    """Test parsing the actual experiment YAML files in the repo."""

    @pytest.fixture(params=[
        "tiered-prefix-cache.yaml",
        "precise-prefix-cache-aware.yaml",
        "inference-scheduling.yaml",
        "pd-disaggregation.yaml",
    ])
    def experiment_file(self, request) -> Path:
        path = EXPERIMENTS_DIR / request.param
        if not path.exists():
            pytest.skip(f"Experiment file not found: {path}")
        return path

    def test_parses_without_error(self, experiment_file: Path):
        plan = parse_experiment(experiment_file)
        assert isinstance(plan, ExperimentPlan)

    def test_has_experiment_name(self, experiment_file: Path):
        plan = parse_experiment(experiment_file)
        assert plan.name
        assert isinstance(plan.name, str)

    def test_has_harness(self, experiment_file: Path):
        plan = parse_experiment(experiment_file)
        assert plan.harness in ("inference-perf", "vllm-benchmark")

    def test_has_run_treatments(self, experiment_file: Path):
        plan = parse_experiment(experiment_file)
        assert plan.run_treatments_count > 0


class TestTieredPrefixCacheExperiment:
    """Detailed tests for the tiered-prefix-cache experiment."""

    @pytest.fixture
    def plan(self) -> ExperimentPlan:
        path = EXPERIMENTS_DIR / "tiered-prefix-cache.yaml"
        if not path.exists():
            pytest.skip("tiered-prefix-cache.yaml not found")
        return parse_experiment(path)

    def test_setup_treatments(self, plan: ExperimentPlan):
        assert plan.has_setup_phase is True
        assert len(plan.setup_treatments) == 4

    def test_setup_treatment_names(self, plan: ExperimentPlan):
        names = [t.name for t in plan.setup_treatments]
        assert names == [
            "cpu-blocks-500",
            "cpu-blocks-1000",
            "cpu-blocks-2000",
            "cpu-blocks-5000",
        ]

    def test_setup_constants_applied(self, plan: ExperimentPlan):
        for t in plan.setup_treatments:
            assert t.overrides["model"]["maxModelLen"] == 16000
            assert t.overrides["model"]["blockSize"] == 64

    def test_setup_treatment_overrides(self, plan: ExperimentPlan):
        expected_blocks = [500, 1000, 2000, 5000]
        for t, expected in zip(plan.setup_treatments, expected_blocks):
            assert t.overrides["vllmCommon"]["flags"]["numCpuBlocks"] == expected

    def test_run_treatment_count(self, plan: ExperimentPlan):
        assert plan.run_treatments_count == 6

    def test_total_matrix(self, plan: ExperimentPlan):
        # 4 setup × 6 run = 24
        assert plan.total_matrix == 24


class TestPrecisePrefixCacheAwareExperiment:
    """Detailed tests for the precise-prefix-cache-aware experiment."""

    @pytest.fixture
    def plan(self) -> ExperimentPlan:
        path = EXPERIMENTS_DIR / "precise-prefix-cache-aware.yaml"
        if not path.exists():
            pytest.skip("precise-prefix-cache-aware.yaml not found")
        return parse_experiment(path)

    def test_setup_treatments(self, plan: ExperimentPlan):
        assert plan.has_setup_phase is True
        assert len(plan.setup_treatments) == 3

    def test_setup_treatment_names(self, plan: ExperimentPlan):
        names = [t.name for t in plan.setup_treatments]
        assert names == [
            "routing-default",
            "routing-estimate",
            "routing-tracking",
        ]

    def test_run_treatment_count(self, plan: ExperimentPlan):
        assert plan.run_treatments_count == 6

    def test_total_matrix(self, plan: ExperimentPlan):
        # 3 setup × 6 run = 18
        assert plan.total_matrix == 18


class TestPdDisaggregationExperiment:
    """Detailed tests for the pd-disaggregation experiment."""

    @pytest.fixture
    def plan(self) -> ExperimentPlan:
        path = EXPERIMENTS_DIR / "pd-disaggregation.yaml"
        if not path.exists():
            pytest.skip("pd-disaggregation.yaml not found")
        return parse_experiment(path)

    def test_setup_treatments(self, plan: ExperimentPlan):
        assert plan.has_setup_phase is True
        assert len(plan.setup_treatments) == 9

    def test_has_modelservice_and_standalone(self, plan: ExperimentPlan):
        names = [t.name for t in plan.setup_treatments]
        ms_count = sum(1 for n in names if n.startswith("ms-"))
        sa_count = sum(1 for n in names if n.startswith("sa-"))
        assert ms_count == 6
        assert sa_count == 3

    def test_standalone_treatments_have_enabled_flag(self, plan: ExperimentPlan):
        for t in plan.setup_treatments:
            if t.name.startswith("sa-"):
                assert t.overrides.get("standalone", {}).get("enabled") is True

    def test_run_treatment_count(self, plan: ExperimentPlan):
        assert plan.run_treatments_count == 6

    def test_total_matrix(self, plan: ExperimentPlan):
        # 9 setup × 6 run = 54
        assert plan.total_matrix == 54


# ===========================================================================
# ExperimentPlan properties
# ===========================================================================


class TestExperimentPlanProperties:
    """Tests for ExperimentPlan computed properties."""

    def test_total_matrix_with_setup_and_run(self):
        plan = ExperimentPlan(
            name="test",
            harness=None,
            profile=None,
            setup_treatments=[
                SetupTreatment(name="s1"),
                SetupTreatment(name="s2"),
                SetupTreatment(name="s3"),
            ],
            run_treatments_count=6,
            experiment_file=Path("test.yaml"),
            has_setup_phase=True,
        )
        assert plan.total_matrix == 18  # 3 × 6

    def test_total_matrix_no_setup(self):
        plan = ExperimentPlan(
            name="test",
            harness=None,
            profile=None,
            setup_treatments=[],
            run_treatments_count=6,
            experiment_file=Path("test.yaml"),
            has_setup_phase=False,
        )
        # max(0, 1) × 6 = 6
        assert plan.total_matrix == 6

    def test_total_matrix_no_run(self):
        plan = ExperimentPlan(
            name="test",
            harness=None,
            profile=None,
            setup_treatments=[SetupTreatment(name="s1")],
            run_treatments_count=0,
            experiment_file=Path("test.yaml"),
            has_setup_phase=True,
        )
        # 1 × max(0, 1) = 1
        assert plan.total_matrix == 1


# ===========================================================================
# TreatmentResult
# ===========================================================================


class TestTreatmentResult:
    """Tests for TreatmentResult dataclass."""

    def test_default_status(self):
        r = TreatmentResult(setup_treatment="t1")
        assert r.status == "pending"

    def test_to_dict_success(self):
        r = TreatmentResult(
            setup_treatment="cpu-blocks-500",
            status="success",
            run_treatments_completed=6,
            run_treatments_total=6,
            workspace_dir="/tmp/workspace",
            duration_seconds=120.456,
        )
        d = r.to_dict()
        assert d["setup_treatment"] == "cpu-blocks-500"
        assert d["status"] == "success"
        assert d["run_treatments"] == "6/6"
        assert d["duration_seconds"] == 120.5
        assert d["workspace_dir"] == "/tmp/workspace"
        assert "error" not in d

    def test_to_dict_failure(self):
        r = TreatmentResult(
            setup_treatment="cpu-blocks-500",
            status="failed_standup",
            run_treatments_completed=0,
            run_treatments_total=6,
            error_message="Pod scheduling failed",
            duration_seconds=30.0,
        )
        d = r.to_dict()
        assert d["status"] == "failed_standup"
        assert d["run_treatments"] == "0/6"
        assert d["error"] == "Pod scheduling failed"

    def test_to_dict_no_workspace(self):
        r = TreatmentResult(setup_treatment="t1", status="failed_standup")
        d = r.to_dict()
        assert "workspace_dir" not in d


# ===========================================================================
# ExperimentSummary
# ===========================================================================


class TestExperimentSummary:
    """Tests for ExperimentSummary tracking and serialization."""

    @pytest.fixture
    def summary(self) -> ExperimentSummary:
        return ExperimentSummary(
            experiment_name="test-experiment",
            total_setup_treatments=4,
            total_run_treatments=6,
            start_time=time.time(),
        )

    def test_total_matrix(self, summary: ExperimentSummary):
        assert summary.total_matrix == 24  # 4 × 6

    def test_total_matrix_no_run(self):
        s = ExperimentSummary(
            experiment_name="test",
            total_setup_treatments=3,
            total_run_treatments=0,
        )
        assert s.total_matrix == 3  # 3 × max(0, 1) = 3

    def test_record_success(self, summary: ExperimentSummary):
        summary.record_success("cpu-blocks-500", 6, 6, "/tmp/ws", 120.0)
        assert len(summary.results) == 1
        assert summary.results[0].status == "success"
        assert summary.results[0].setup_treatment == "cpu-blocks-500"
        assert summary.results[0].run_treatments_completed == 6

    def test_record_failure(self, summary: ExperimentSummary):
        summary.record_failure(
            "cpu-blocks-1000", "standup", "Pod failed",
            run_completed=0, run_total=6,
        )
        assert len(summary.results) == 1
        assert summary.results[0].status == "failed_standup"
        assert summary.results[0].error_message == "Pod failed"

    def test_succeeded_count(self, summary: ExperimentSummary):
        summary.record_success("t1", 6, 6)
        summary.record_success("t2", 6, 6)
        summary.record_failure("t3", "run", "Error")
        assert summary.succeeded == 2

    def test_failed_count(self, summary: ExperimentSummary):
        summary.record_success("t1", 6, 6)
        summary.record_failure("t2", "standup", "Error")
        summary.record_failure("t3", "teardown", "Error")
        assert summary.failed == 2

    def test_to_dict(self, summary: ExperimentSummary):
        summary.record_success("t1", 6, 6, "/tmp/ws1", 100.0)
        summary.record_failure("t2", "run", "Error", 3, 6, "/tmp/ws2", 50.0)
        d = summary.to_dict()
        assert d["experiment"] == "test-experiment"
        assert d["total_setup_treatments"] == 4
        assert d["total_run_treatments"] == 6
        assert d["total_matrix"] == 24
        assert d["succeeded"] == 1
        assert d["failed"] == 1
        assert len(d["treatments"]) == 2
        assert isinstance(d["total_duration_seconds"], float)

    def test_write_yaml(self, summary: ExperimentSummary, tmp_path: Path):
        summary.record_success("t1", 6, 6, "/tmp/ws1", 100.0)
        summary.record_failure("t2", "standup", "Error", 0, 6)

        output = tmp_path / "experiment-summary.yaml"
        summary.write(output)

        assert output.exists()

        with open(output) as f:
            data = yaml.safe_load(f)

        assert data["experiment"] == "test-experiment"
        assert data["total_setup_treatments"] == 4
        assert data["total_run_treatments"] == 6
        assert data["succeeded"] == 1
        assert data["failed"] == 1
        assert len(data["treatments"]) == 2

    def test_write_creates_parent_dirs(self, summary: ExperimentSummary, tmp_path: Path):
        """write() creates parent directories if they don't exist."""
        output = tmp_path / "deep" / "nested" / "summary.yaml"
        summary.write(output)
        assert output.exists()

    def test_print_table(self, summary: ExperimentSummary):
        """print_table() runs without error (smoke test)."""
        summary.record_success("t1", 6, 6)
        summary.record_failure("t2", "standup", "Error")

        class MockLogger:
            def __init__(self):
                self.messages = []
            def log_info(self, msg):
                self.messages.append(msg)

        logger = MockLogger()
        summary.print_table(logger)
        assert len(logger.messages) > 0
        # Should contain the experiment name
        assert any("test-experiment" in m for m in logger.messages)
        # Should contain success and failure indicators
        assert any("t1" in m for m in logger.messages)
        assert any("t2" in m for m in logger.messages)

    def test_multiple_failure_phases(self, summary: ExperimentSummary):
        """Different failure phases produce distinct status strings."""
        summary.record_failure("t1", "standup", "Error1")
        summary.record_failure("t2", "run", "Error2")
        summary.record_failure("t3", "teardown", "Error3")
        statuses = [r.status for r in summary.results]
        assert statuses == ["failed_standup", "failed_run", "failed_teardown"]
        assert summary.failed == 3


# ===========================================================================
# SetupTreatment
# ===========================================================================


class TestSetupTreatment:
    """Tests for SetupTreatment dataclass."""

    def test_default_overrides(self):
        t = SetupTreatment(name="test")
        assert t.overrides == {}

    def test_with_overrides(self):
        overrides = {"model": {"maxModelLen": 16000}}
        t = SetupTreatment(name="test", overrides=overrides)
        assert t.overrides == overrides
