"""Regression tests for the sectioned scenario stack layout.

Issue #1064 defines four author-facing sections inside each stack:
``common``, ``standalone``, ``modelservice``, and ``fma``.  The renderer
normalizes those sections to the legacy flat effective config consumed by
resolvers and templates.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock

import yaml
import pytest

from llmdbenchmark.parser.render_plans import RenderPlans


def _renderer() -> RenderPlans:
    renderer = RenderPlans.__new__(RenderPlans)
    renderer.logger = MagicMock()
    return renderer


class TestExpandStackCommon:
    def test_common_fields_are_expanded_without_mutating_input(self) -> None:
        renderer = _renderer()
        values = {
            "modelservice": {"enabled": True},
            "common": {
                "model": {"name": "org/model"},
                "storage": {"modelPvc": {"size": "200Gi"}},
                "vllmCommon": {"inferencePort": 9000},
            },
        }
        before = deepcopy(values)

        result = renderer._expand_stack_common(values)

        assert result == {
            "modelservice": {"enabled": True},
            "model": {"name": "org/model"},
            "storage": {"modelPvc": {"size": "200Gi"}},
            "vllmCommon": {"inferencePort": 9000},
        }
        assert values == before

    def test_common_spelling_wins_over_legacy_flat_spelling(self) -> None:
        renderer = _renderer()
        result = renderer._expand_stack_common(
            {
                "model": {"name": "legacy", "maxModelLen": 4096},
                "common": {"model": {"name": "sectioned"}},
            }
        )

        assert result["model"] == {
            "name": "sectioned",
            "maxModelLen": 4096,
        }

    def test_non_mapping_common_is_rejected(self) -> None:
        renderer = _renderer()

        with pytest.raises(TypeError, match="'common' must be a mapping when present"):
            renderer._expand_stack_common({"common": "invalid"})


class TestSectionNormalization:
    def test_common_and_modelservice_sections_form_effective_config(self) -> None:
        renderer = _renderer()
        defaults = {
            # This is the modelservice chart's `common`, not stack.common.
            "common": {"chartDefault": True},
            "model": {"name": "default/model", "maxModelLen": 4096},
            "storage": {"modelPvc": {"size": "1Ti"}},
            "prefill": {"enabled": False, "replicas": 1},
            "decode": {"enabled": True, "replicas": 1},
            "modelservice": {"enabled": True, "uriProtocol": "pvc"},
        }
        stack = {
            "common": {
                "model": {"name": "org/model"},
                "storage": {"modelPvc": {"size": "200Gi"}},
            },
            "modelservice": {
                "enabled": True,
                "common": {"customCA": True},
                "prefill": {"enabled": True, "replicas": 2},
                "decode": {"replicas": 4},
                "inferenceExtension": {"enabled": True},
            },
            "standalone": {"enabled": False},
            "fma": {"enabled": False},
        }

        scenario_layer = renderer._expand_stack_common(stack)
        effective = renderer.deep_merge(defaults, scenario_layer)
        effective = renderer._hoist_modelservice_sections(effective)

        assert effective["model"] == {
            "name": "org/model",
            "maxModelLen": 4096,
        }
        assert effective["storage"]["modelPvc"]["size"] == "200Gi"
        assert effective["common"] == {
            "chartDefault": True,
            "customCA": True,
        }
        assert effective["prefill"] == {"enabled": True, "replicas": 2}
        assert effective["decode"] == {"enabled": True, "replicas": 4}
        assert effective["inferenceExtension"] == {"enabled": True}
        assert effective["modelservice"] == {
            "enabled": True,
            "uriProtocol": "pvc",
        }

    def test_sibling_summary_reads_model_from_common(self) -> None:
        renderer = _renderer()
        siblings = renderer._build_sibling_stacks(
            [
                {
                    "name": "pool-a",
                    "common": {"model": {"name": "org/model-a"}},
                    "standalone": {"enabled": True},
                },
                {
                    "name": "pool-b",
                    "common": {"model": {"name": "org/model-b"}},
                    "modelservice": {"enabled": True},
                },
            ]
        )

        assert siblings == [
            {"name": "pool-a", "modelName": "org/model-a", "standalone": True},
            {"name": "pool-b", "modelName": "org/model-b", "standalone": False},
        ]


def test_non_mapping_stack_common_fails_before_rendering(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults.yaml"
    scenario = tmp_path / "scenario.yaml"
    templates = tmp_path / "templates"
    defaults.write_text("{}\n", encoding="utf-8")
    scenario.write_text(
        yaml.safe_dump(
            {"scenario": [{"name": "bad-stack", "common": "not-a-mapping"}]}
        ),
        encoding="utf-8",
    )
    templates.mkdir()
    logger = MagicMock()

    result = RenderPlans(
        template_dir=templates,
        defaults_file=defaults,
        scenarios_file=scenario,
        output_dir=tmp_path / "out",
        logger=logger,
    ).eval()

    assert result.global_errors == ["Stack 1 'common' section must be a mapping"]


def test_sectioned_fma_example_renders_effective_config(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    output_dir = tmp_path / "out"

    result = RenderPlans(
        template_dir=project_root / "config/templates/jinja",
        defaults_file=project_root / "config/templates/values/defaults.yaml",
        scenarios_file=project_root / "config/scenarios/examples/fma.yaml",
        output_dir=output_dir,
        logger=MagicMock(),
    ).eval()

    assert not result.has_errors, result.to_dict()
    config_path = next(output_dir.rglob("config.yaml"))
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["model"]["name"] == "meta-llama/Llama-3.1-8B-Instruct"
    assert config["workDir"] == "~/data/fma"
    assert config["harness"]["name"] == "nop"
    assert config["modelservice"]["enabled"] is False
    assert config["standalone"]["enabled"] is False
    assert config["fma"]["enabled"] is True
