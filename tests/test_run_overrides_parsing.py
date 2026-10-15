"""Tests for -o/--overrides parsing in the run phase.

Pins the contract that profile overrides are split and coerced the same way
as --set scenario overrides: commas inside quotes or brackets belong to the
value, and values are parsed as YAML so a map or list override (e.g.
``api.headers={...}``) reaches the workload profile as a real mapping
instead of a string.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_STEP_PATH = (
    Path(__file__).resolve().parent.parent
    / "llmdbenchmark"
    / "run"
    / "steps"
    / "step_05_render_profiles.py"
)
_spec = importlib.util.spec_from_file_location(
    "step_05_render_profiles_isolated", _STEP_PATH
)
_module = importlib.util.module_from_spec(_spec)
sys.modules["step_05_render_profiles_isolated"] = _module
_spec.loader.exec_module(_module)
RenderProfilesStep = _module.RenderProfilesStep


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def log_info(self, msg: str, **_: Any) -> None:
        pass

    def log_warning(self, msg: str, **_: Any) -> None:
        self.warnings.append(msg)


@dataclass
class _Context:
    profile_overrides: str | None = None
    experiment_treatments_file: str | None = None
    logger: _Logger = field(default_factory=_Logger)


def _overrides(raw: str) -> dict:
    treatments = RenderProfilesStep()._resolve_treatments(  # pylint: disable=protected-access
        _Context(profile_overrides=raw), None
    )
    assert len(treatments) == 1
    assert treatments[0]["name"] == "override"
    return treatments[0]["overrides"]


class TestOverridesParsing:
    def test_map_value_becomes_a_dict(self):
        overrides = _overrides(
            'api.headers={"x-llm-d-inference-objective": "agentic-sessions"}'
        )
        assert overrides == {
            "api.headers": {"x-llm-d-inference-objective": "agentic-sessions"}
        }

    def test_commas_inside_braces_do_not_split_pairs(self):
        overrides = _overrides(
            'api.headers={"a": "b", "c": "d"},load.request_timeout=9000'
        )
        assert overrides == {
            "api.headers": {"a": "b", "c": "d"},
            "load.request_timeout": 9000,
        }

    def test_list_value_becomes_a_list(self):
        assert _overrides("data.some_list=[1, 2]") == {"data.some_list": [1, 2]}

    def test_scalars_keep_their_types(self):
        overrides = _overrides(
            "load.stages.0.timeout=300,server.model_name=some:name,api.streaming=true"
        )
        assert overrides == {
            "load.stages.0.timeout": 300,
            "server.model_name": "some:name",
            "api.streaming": True,
        }

    def test_pair_without_equals_is_skipped(self):
        assert _overrides("not-a-pair,api.streaming=true") == {"api.streaming": True}

    def test_surprising_coercion_logs_a_warning(self):
        context = _Context(profile_overrides="load.stages.0.timeout=1:30")
        treatments = RenderProfilesStep()._resolve_treatments(  # pylint: disable=protected-access
            context, None
        )
        assert treatments[0]["overrides"]["load.stages.0.timeout"] == 90
        assert len(context.logger.warnings) == 1

    def test_map_override_lands_in_the_rendered_profile(self):
        from llmdbenchmark.utilities.profile_renderer import apply_overrides

        overrides = _overrides(
            'api.headers={"x-llm-d-inference-objective": "agentic-sessions"}'
        )
        content, unmatched = apply_overrides("api:\n  type: chat\n", overrides)
        assert unmatched == []
        assert yaml.safe_load(content)["api"]["headers"] == {
            "x-llm-d-inference-objective": "agentic-sessions"
        }
