"""Tests for kustomize guide-name helpers and FMA guide detection.

The llm-d FMA guides moved under one package: the base guide is
``guides/fast-model-actuation`` and the KEDA guide is nested at
``guides/fast-model-actuation/fast-model-actuation-keda``. FMA-specific
smoketest and teardown handling must recognise both, plus the legacy top-level
names still used by release branches up to release-0.10.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from llmdbenchmark.kustomize.guides import guide_basename, is_fma_guide
from llmdbenchmark.teardown.steps.step_01_uninstall_helm import UninstallHelmStep

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "guide_name, expected",
    [
        ("optimized-baseline", "optimized-baseline"),
        ("multimodal-serving/aggregation", "aggregation"),
        ("fast-model-actuation/fast-model-actuation-keda", "fast-model-actuation-keda"),
        ("fast-model-actuation/", "fast-model-actuation"),
        ("", ""),
    ],
)
def test_guide_basename(guide_name, expected):
    assert guide_basename(guide_name) == expected


@pytest.mark.parametrize(
    "guide_name",
    [
        "fast-model-actuation",
        "fast-model-actuation/fast-model-actuation-keda",
        # Legacy top-level names (release branches up to release-0.10).
        "fast-model-actuation-base",
        "fast-model-actuation-keda",
    ],
)
def test_is_fma_guide_matches_every_variant(guide_name):
    assert is_fma_guide(guide_name)


@pytest.mark.parametrize(
    "guide_name",
    [
        "",
        None,
        "optimized-baseline",
        "multimodal-serving/aggregation",
        "fast-model-actuationx",
        # A nested non-FMA guide inside the FMA package dir is not FMA.
        "fast-model-actuation/other",
    ],
)
def test_is_fma_guide_rejects_others(guide_name):
    assert not is_fma_guide(guide_name)


def _context_with_guide(tmp_path, guide_name):
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "config.yaml").write_text(
        yaml.safe_dump({"kustomize": {"guideName": guide_name}})
    )
    return SimpleNamespace(rendered_stacks=[stack])


@pytest.mark.parametrize(
    "guide_name, expected",
    [
        ("fast-model-actuation", "fast-model-actuation"),
        ("fast-model-actuation/fast-model-actuation-keda", "fast-model-actuation-keda"),
        ("fast-model-actuation-base", "fast-model-actuation-base"),
        ("optimized-baseline", ""),
    ],
)
def test_teardown_fma_guide_name_returns_label_value(tmp_path, guide_name, expected):
    # The returned name feeds the llm-d.ai/guide=<name> requester selector, so a
    # nested guide must resolve to its basename (the guide's label value).
    ctx = _context_with_guide(tmp_path, guide_name)
    assert UninstallHelmStep._fma_guide_name(ctx) == expected


@pytest.mark.parametrize(
    "scenario, guide_name",
    [
        ("fast-model-actuation", "fast-model-actuation"),
        (
            "fast-model-actuation-fast-model-actuation-keda",
            "fast-model-actuation/fast-model-actuation-keda",
        ),
    ],
)
def test_fma_scenarios_point_at_merged_guide_layout(scenario, guide_name):
    # CI derives the scenario file from the llm-d standup_scenario by replacing
    # "/" with "-", and sets guideName to the standup_scenario itself.
    scenario_file = REPO_ROOT / "config" / "scenarios" / "guides" / f"{scenario}.yaml"
    data = yaml.safe_load(scenario_file.read_text())
    assert data["scenario"][0]["kustomize"]["guideName"] == guide_name
    assert scenario == guide_name.replace("/", "-")

    spec = REPO_ROOT / "config" / "specification" / "guides" / f"{scenario}.yaml.j2"
    assert f"config/scenarios/guides/{scenario}.yaml" in spec.read_text()
