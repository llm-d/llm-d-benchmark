"""Tests for --per-band-traffic band discovery and treatment generation.

Pins the contract that:

- Band structure is read from the rendered stack's EPP plugins config and
  is ``None`` when the flow-control feature gate is off, so the run fails
  loudly instead of launching class-less traffic.
- One treatment is generated per objective that selects a band; objectives
  that select no band are skipped with a warning, and bands with no
  objective warn (their traffic cannot be targeted).
- The default band gets a header-less instance only when it names an
  explicit priority that nothing else covers.
- The objective header override is applied last so it always wins, and
  ``-o`` base overrides plus per-band overrides compose underneath it.
- ``apply_overrides(create_missing_parents=True)`` attaches the header to
  a base profile that has no ``api.headers`` block, and a header that
  cannot land is a hard step error, not a warning.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from llmdbenchmark.experiment.bands import (
    BAND_GROUP_NAME,
    DEFAULT_TREATMENT_NAME,
    OBJECTIVE_HEADER_KEY,
    build_band_treatments,
    extract_flow_control,
    find_endpoint_picker_config,
    load_per_band_overrides,
    pair_bands_with_objectives,
)
from llmdbenchmark.utilities.profile_renderer import apply_overrides

_STEP_PATH = (
    Path(__file__).resolve().parent.parent
    / "llmdbenchmark"
    / "run"
    / "steps"
    / "step_05_render_profiles.py"
)
_spec = importlib.util.spec_from_file_location(
    "step_05_render_profiles_bands", _STEP_PATH
)
_module = importlib.util.module_from_spec(_spec)
sys.modules["step_05_render_profiles_bands"] = _module
_spec.loader.exec_module(_module)
RenderProfilesStep = _module.RenderProfilesStep

#: EndpointPickerConfig in the shape the turn-priority-fairness guide
#: renders into ``pluginsCustomConfig`` (a YAML string value): flow control
#: on, batch band at -1, interactive band at 0.
PICKER_YAML = """\
apiVersion: llm-d.ai/v1alpha1
kind: EndpointPickerConfig
featureGates:
- flowControl
plugins: []
flowControl:
  priorityBands:
  - priority: -1
    defaultRequestTTL: "9000s"
  - priority: 0
"""


def _plan_config(picker_yaml: str | None = PICKER_YAML) -> dict:
    modelservice: dict = {"router": {"epp": {}}}
    if picker_yaml is not None:
        modelservice["router"]["epp"]["pluginsCustomConfig"] = {
            "guide-plugins.yaml": picker_yaml
        }
    return {"modelservice": modelservice, "namespace": {"name": "bench"}}


class _Logger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def log_info(self, msg: str, **_: Any) -> None:
        self.messages.append(msg)

    def log_warning(self, msg: str, **_: Any) -> None:
        self.messages.append(f"WARN: {msg}")


@dataclass
class _Context:
    workspace: Path
    base_dir: Path
    harness_name: str | None = "inference-perf"
    harness_profile: str | None = None
    workload_file_path: str | None = None
    deployed_endpoints: dict[str, str] = field(default_factory=dict)
    model_name: str | None = None
    dataset_url: str | None = None
    dry_run: bool = False
    harness_debug: bool = False
    experiment_treatments_file: str | None = None
    profile_overrides: str | None = None
    experiment_treatments: list[dict] | None = None
    per_band_traffic: bool = True
    per_band_overrides_file: str | None = None
    namespace: str | None = "bench"
    cmd: Any = None
    max_parallel_treatments: int = 1
    max_parallel_treatments_explicit: bool = False
    treatment_groups: list[Any] = field(default_factory=list)
    logger: _Logger = field(default_factory=_Logger)

    def workload_profiles_dir(self) -> Path:
        path = self.workspace / "workload" / "profiles"
        path.mkdir(parents=True, exist_ok=True)
        return path


def _write_stack(tmp_path: Path, plan_config: dict) -> Path:
    stack_path = tmp_path / "stack"
    stack_path.mkdir()
    (stack_path / "config.yaml").write_text(
        yaml.safe_dump(plan_config), encoding="utf-8"
    )
    return stack_path


def _workload_file(tmp_path: Path) -> Path:
    workload_file = tmp_path / "base.yaml.in"
    workload_file.write_text(
        "load:\n  stages:\n  - rate: 1\napi:\n  type: completion\n",
        encoding="utf-8",
    )
    return workload_file


# ---------------------------------------------------------------- finder --


def test_find_endpoint_picker_config_parses_string_values() -> None:
    picker = find_endpoint_picker_config(_plan_config())
    assert picker is not None
    assert picker["kind"] == "EndpointPickerConfig"
    assert "flowControl" in picker


def test_find_endpoint_picker_config_reads_rendered_top_level_router() -> None:
    """Rendered config.yaml normalizes modelservice.router to a top-level
    router key; discovery must find the picker there too."""
    plan = {
        "router": {"epp": {"pluginsCustomConfig": {"p.yaml": PICKER_YAML}}},
        "namespace": {"name": "bench"},
    }
    picker = find_endpoint_picker_config(plan)
    assert picker is not None
    assert picker["kind"] == "EndpointPickerConfig"


def test_find_endpoint_picker_config_returns_none_without_epp_block() -> None:
    assert find_endpoint_picker_config(_plan_config(picker_yaml=None)) is None
    assert find_endpoint_picker_config({}) is None
    assert find_endpoint_picker_config(None) is None


def test_find_endpoint_picker_config_accepts_dict_values() -> None:
    plan = _plan_config()
    plan["modelservice"]["router"]["epp"]["pluginsCustomConfig"] = {
        "p.yaml": yaml.safe_load(PICKER_YAML)
    }
    assert find_endpoint_picker_config(plan) is not None


# --------------------------------------------------------------- extract --


def test_extract_flow_control_reads_bands() -> None:
    bands = extract_flow_control(yaml.safe_load(PICKER_YAML))
    assert bands is not None
    assert bands.priorities == [-1, 0]
    assert bands.default_priority is None
    assert not bands.has_default_band


def test_extract_flow_control_reads_default_band_priority() -> None:
    picker = yaml.safe_load(PICKER_YAML)
    picker["flowControl"]["defaultPriorityBand"] = {
        "priority": 40,
        "maxRequests": 100,
    }
    bands = extract_flow_control(picker)
    assert bands is not None
    assert bands.default_priority == 40
    assert bands.has_default_band


def test_extract_flow_control_is_none_when_gate_off() -> None:
    picker = yaml.safe_load(PICKER_YAML)
    picker["featureGates"] = []
    assert extract_flow_control(picker) is None


def test_extract_flow_control_is_none_without_bands() -> None:
    picker = yaml.safe_load(PICKER_YAML)
    picker["flowControl"]["priorityBands"] = []
    assert extract_flow_control(picker) is None


# --------------------------------------------------------------- pairing --


def test_objective_selecting_a_band_gets_an_instance() -> None:
    bands = extract_flow_control(yaml.safe_load(PICKER_YAML))
    instances, warnings = pair_bands_with_objectives(
        bands, [("agentic-sessions", -1), ("interactive", 0)]
    )
    assert not warnings
    assert [(i.objective_name, i.band_priority) for i in instances] == [
        ("agentic-sessions", -1),
        ("interactive", 0),
    ]


def test_objective_matching_no_band_is_skipped_with_warning() -> None:
    bands = extract_flow_control(yaml.safe_load(PICKER_YAML))
    instances, warnings = pair_bands_with_objectives(bands, [("odd", 7)])
    assert [i.objective_name for i in instances] == []
    assert len(warnings) == 3  # the odd objective and both untargetable bands
    assert any("'odd'" in w for w in warnings)
    assert any("priority -1" in w for w in warnings)


def test_default_band_gets_headerless_instance_when_uncovered() -> None:
    picker = yaml.safe_load(PICKER_YAML)
    picker["flowControl"]["defaultPriorityBand"] = {"priority": 40}
    bands = extract_flow_control(picker)
    instances, _ = pair_bands_with_objectives(bands, [("premium", -1)])
    default = [i for i in instances if i.objective_name is None]
    assert len(default) == 1
    assert default[0].treatment_name == DEFAULT_TREATMENT_NAME
    assert default[0].band_priority == 40


def test_default_band_instance_suppressed_when_objective_covers_it() -> None:
    picker = yaml.safe_load(PICKER_YAML)
    picker["flowControl"]["defaultPriorityBand"] = {"priority": 40}
    bands = extract_flow_control(picker)
    instances, _ = pair_bands_with_objectives(bands, [("standard", 40)])
    assert [i.objective_name for i in instances] == ["standard"]


def test_objectives_only_mode_takes_every_objective() -> None:
    instances, warnings = pair_bands_with_objectives(
        None, [("premium", 80), ("besteffort", 0)]
    )
    assert not warnings
    assert [i.objective_name for i in instances] == ["premium", "besteffort"]


def test_unsafe_objective_name_is_sanitized_for_label_use() -> None:
    instances, _ = pair_bands_with_objectives(None, [("a b/c", 0)])
    assert instances[0].treatment_name == "a-b-c"


# ------------------------------------------------------------- treatments --


def test_band_treatments_carry_header_last_and_band_marker() -> None:
    instances, _ = pair_bands_with_objectives(
        None, [("premium", 80), ("besteffort", 0)]
    )
    treatments, errors = build_band_treatments(
        instances,
        {"load.stages.0.rate": 5},
        {"besteffort": {"load.stages.0.rate": 1}},
    )
    assert not errors
    assert [t["name"] for t in treatments] == ["premium", "besteffort"]
    assert all(t["group"] == BAND_GROUP_NAME and t["band"] for t in treatments)
    assert list(treatments[0]["overrides"])[-1] == OBJECTIVE_HEADER_KEY
    assert treatments[0]["overrides"][OBJECTIVE_HEADER_KEY] == "premium"
    # Base override shared, per-band override scoped, header never overridden.
    assert treatments[0]["overrides"]["load.stages.0.rate"] == 5
    assert treatments[1]["overrides"]["load.stages.0.rate"] == 1
    assert treatments[1]["overrides"][OBJECTIVE_HEADER_KEY] == "besteffort"


def test_headerless_default_treatment_has_no_header_override() -> None:
    picker = yaml.safe_load(PICKER_YAML)
    picker["flowControl"]["defaultPriorityBand"] = {"priority": 40}
    bands = extract_flow_control(picker)
    instances, _ = pair_bands_with_objectives(bands, [])
    instances = [i for i in instances if i.treatment_name == DEFAULT_TREATMENT_NAME]
    treatments, _ = build_band_treatments(instances, None, None)
    assert OBJECTIVE_HEADER_KEY not in treatments[0]["overrides"]


def test_unknown_per_band_key_is_an_error() -> None:
    instances, _ = pair_bands_with_objectives(None, [("premium", 80)])
    treatments, errors = build_band_treatments(
        instances, None, {"nosuchclass": {"load.stages.0.rate": 1}}
    )
    assert not treatments
    assert len(errors) == 1
    assert "nosuchclass" in errors[0]


# ----------------------------------------------------------- overrides io --


def test_load_per_band_overrides_reads_yaml(tmp_path: Path) -> None:
    file = tmp_path / "bands.yaml"
    file.write_text("premium:\n  load.stages.0.rate: 5\n", encoding="utf-8")
    assert load_per_band_overrides(str(file)) == {"premium": {"load.stages.0.rate": 5}}
    assert load_per_band_overrides(None) is None


def test_load_per_band_overrides_rejects_malformed(tmp_path: Path) -> None:
    file = tmp_path / "bands.yaml"
    file.write_text("premium: 5\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must map treatment names"):
        load_per_band_overrides(str(file))


def test_load_per_band_overrides_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        load_per_band_overrides(str(tmp_path / "missing.yaml"))


# ------------------------------------------------- apply_overrides growth --


def test_apply_overrides_creates_missing_parents_for_header() -> None:
    profile = "load:\n  stages:\n  - rate: 1\n"
    overridden, unmatched = apply_overrides(
        profile, {OBJECTIVE_HEADER_KEY: "premium"}, create_missing_parents=True
    )
    assert unmatched == []
    assert (
        yaml.safe_load(overridden)["api"]["headers"]["x-llm-d-inference-objective"]
        == "premium"
    )


def test_apply_overrides_keeps_default_unmatched_behaviour() -> None:
    profile = "load:\n  stages:\n  - rate: 1\n"
    overridden, unmatched = apply_overrides(profile, {OBJECTIVE_HEADER_KEY: "premium"})
    assert unmatched == [OBJECTIVE_HEADER_KEY]
    assert yaml.safe_load(overridden).get("api") is None


def test_apply_overrides_never_grows_lists() -> None:
    profile = "load:\n  stages:\n  - rate: 1\n"
    overridden, unmatched = apply_overrides(
        profile,
        {"load.stages.1.rate": 2},
        create_missing_parents=True,
    )
    assert unmatched == ["load.stages.1.rate"]


# ------------------------------------------------------------ step level --


def test_step_generates_band_treatments(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        _module,
        "list_inference_objectives",
        lambda cmd, ns: [("agentic-sessions", -1), ("interactive", 0)],
    )
    context = _Context(
        workspace=tmp_path / "workspace",
        base_dir=tmp_path / "base",
        workload_file_path=str(_workload_file(tmp_path)),
        profile_overrides="load.stages.0.rate=2",
    )
    stack = _write_stack(tmp_path, _plan_config())

    result = RenderProfilesStep().execute(context, stack)

    assert result.success, result.errors
    out_dir = context.workload_profiles_dir() / "inference-perf"
    premium = yaml.safe_load(
        (out_dir / "base-agentic-sessions.yaml").read_text(encoding="utf-8")
    )
    interactive = yaml.safe_load(
        (out_dir / "base-interactive.yaml").read_text(encoding="utf-8")
    )
    assert premium["api"]["headers"]["x-llm-d-inference-objective"] == (
        "agentic-sessions"
    )
    assert interactive["api"]["headers"]["x-llm-d-inference-objective"] == (
        "interactive"
    )
    # The -o base override shaped both instances.
    assert premium["load"]["stages"][0]["rate"] == 2
    assert interactive["load"]["stages"][0]["rate"] == 2
    # One concurrent group, parallelism raised to the instance count.
    assert len(context.treatment_groups) == 1
    assert context.treatment_groups[0].name == BAND_GROUP_NAME
    assert context.max_parallel_treatments == 2


def test_step_respects_explicit_parallelism_cap(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(
        _module,
        "list_inference_objectives",
        lambda cmd, ns: [("a", -1), ("b", 0)],
    )
    context = _Context(
        workspace=tmp_path / "workspace",
        base_dir=tmp_path / "base",
        workload_file_path=str(_workload_file(tmp_path)),
        max_parallel_treatments=1,
        max_parallel_treatments_explicit=True,
    )
    stack = _write_stack(tmp_path, _plan_config())

    result = RenderProfilesStep().execute(context, stack)

    assert result.success, result.errors
    assert context.max_parallel_treatments == 1


def test_step_fails_when_flow_control_is_off(tmp_path: Path) -> None:
    picker = yaml.safe_load(PICKER_YAML)
    picker["featureGates"] = []
    context = _Context(
        workspace=tmp_path / "workspace",
        base_dir=tmp_path / "base",
        workload_file_path=str(_workload_file(tmp_path)),
    )
    stack = _write_stack(tmp_path, _plan_config(picker_yaml=yaml.safe_dump(picker)))

    result = RenderProfilesStep().execute(context, stack)

    assert not result.success
    assert any("flow control is not enabled" in e for e in result.errors)


def test_step_fails_when_no_objectives_exist(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(_module, "list_inference_objectives", lambda cmd, ns: [])
    context = _Context(
        workspace=tmp_path / "workspace",
        base_dir=tmp_path / "base",
        workload_file_path=str(_workload_file(tmp_path)),
    )
    stack = _write_stack(tmp_path, _plan_config())

    result = RenderProfilesStep().execute(context, stack)

    assert not result.success
    assert any("no band instances" in e for e in result.errors)


def test_step_hard_fails_when_header_cannot_land(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(
        _module,
        "list_inference_objectives",
        lambda cmd, ns: [("agentic-sessions", -1)],
    )
    # api is a scalar here, so the header path cannot be created.
    workload_file = tmp_path / "scalar.yaml.in"
    workload_file.write_text("api: completion\n", encoding="utf-8")
    context = _Context(
        workspace=tmp_path / "workspace",
        base_dir=tmp_path / "base",
        workload_file_path=str(workload_file),
    )
    stack = _write_stack(tmp_path, _plan_config())

    result = RenderProfilesStep().execute(context, stack)

    assert not result.success
    assert any("objective header override" in e for e in result.errors)


def test_step_applies_per_band_overrides(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        _module,
        "list_inference_objectives",
        lambda cmd, ns: [("agentic-sessions", -1), ("interactive", 0)],
    )
    bands_file = tmp_path / "bands.yaml"
    bands_file.write_text("interactive:\n  load.stages.0.rate: 9\n", encoding="utf-8")
    context = _Context(
        workspace=tmp_path / "workspace",
        base_dir=tmp_path / "base",
        workload_file_path=str(_workload_file(tmp_path)),
        per_band_overrides_file=str(bands_file),
    )
    stack = _write_stack(tmp_path, _plan_config())

    result = RenderProfilesStep().execute(context, stack)

    assert result.success, result.errors
    out_dir = context.workload_profiles_dir() / "inference-perf"
    interactive = yaml.safe_load(
        (out_dir / "base-interactive.yaml").read_text(encoding="utf-8")
    )
    agentic = yaml.safe_load(
        (out_dir / "base-agentic-sessions.yaml").read_text(encoding="utf-8")
    )
    assert interactive["load"]["stages"][0]["rate"] == 9
    assert agentic["load"]["stages"][0]["rate"] == 1
