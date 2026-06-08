"""Tests for the 12_gaie-values.yaml.j2 inferenceExtension (EPP) value rendering.

Covers propagation of optional scheduling fields into the inferencepool
chart's `inferenceExtension` values. The chart's EPP pod spec honours
`tolerations` and `affinity`, so the template must emit each block when the
scenario sets one and omit it entirely when unset -- an empty block would
otherwise override the chart defaults while adding noise to the output.

`affinity` reuses the scenario-wide `affinity` convenience wrapper (the same
one decode/prefill/standalone consume): a flat `affinity.nodeSelector` map is
expanded into a required `nodeAffinity` term, gated on `affinity.enabled`.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from llmdbenchmark.parser.render_plans import RenderPlans

_REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = (
    _REPO_ROOT / "config" / "templates" / "jinja" / "12_router-values.yaml.j2"
)
DEFAULTS_PATH = _REPO_ROOT / "config" / "templates" / "values" / "defaults.yaml"

# Base values on the canonical defaults the template is rendered against in
# production. This keeps the test resilient to unrelated template churn (new
# keys gated behind defaults) -- we only overlay the fields under test.
_DEFAULTS = yaml.safe_load(DEFAULTS_PATH.read_text())


@pytest.fixture
def renderer():
    """Bare RenderPlans instance -- only the Jinja env/render path is exercised."""
    r = RenderPlans.__new__(RenderPlans)
    r._jinja_env = None
    return r


def _values(tolerations=None, affinity=None) -> dict:
    """Defaults overlaid with the scheduling fields under test."""
    values = copy.deepcopy(_DEFAULTS)
    # model_id_label is injected by the render pipeline, not defaults.yaml.
    values["model_id_label"] = "my-model"
    if tolerations is not None:
        values["inferenceExtension"]["tolerations"] = tolerations
    if affinity is not None:
        values["affinity"] = affinity
    return values


def _render(renderer, values: dict) -> dict:
    rendered = renderer._render_template(TEMPLATE_PATH.read_text(), values)
    return yaml.safe_load(rendered)


# def test_tolerations_are_propagated(renderer):
#    """A scenario-provided tolerations list reaches inferenceExtension.tolerations."""
#    tolerations = [
#        {
#            "key": "nvidia.com/gpu",
#            "operator": "Exists",
#            "effect": "NoSchedule",
#        }
#    ]
#    doc = _render(renderer, _values(tolerations=tolerations))
#    assert doc["inferenceExtension"]["tolerations"] == tolerations


# def test_tolerations_omitted_when_unset(renderer):
#    """No tolerations key -> no tolerations block (chart default applies)."""
#    doc = _render(renderer, _values(tolerations=None))
#    assert "tolerations" not in doc["inferenceExtension"]


# def test_empty_tolerations_omitted(renderer):
#    """An explicit empty list is falsy and must not emit an empty block."""
#    doc = _render(renderer, _values(tolerations=[]))
#    assert "tolerations" not in doc["inferenceExtension"]


# def test_affinity_nodeselector_expands_to_nodeaffinity(renderer):
#    """The shared affinity wrapper expands a nodeSelector map into nodeAffinity."""
#    affinity = {
#        "enabled": True,
#        "nodeSelector": {"nvidia.com/gpu.product": "NVIDIA-H100-80GB-HBM3"},
#    }
#    doc = _render(renderer, _values(affinity=affinity))
#    term = doc["inferenceExtension"]["affinity"]["nodeAffinity"][
#        "requiredDuringSchedulingIgnoredDuringExecution"
#    ]["nodeSelectorTerms"][0]["matchExpressions"][0]
#    assert term == {
#        "key": "nvidia.com/gpu.product",
#        "operator": "In",
#        "values": ["NVIDIA-H100-80GB-HBM3"],
#    }


# def test_affinity_passthrough_blocks_render_valid_yaml(renderer):
#    """podAffinity/podAntiAffinity are passed through and stay valid YAML.
#
#    Guards the first-line indentation of the toyaml passthrough -- an
#    un-indented first line silently corrupts the inferenceExtension mapping.
#    """
#    pod_anti = {
#        "preferredDuringSchedulingIgnoredDuringExecution": [
#            {
#                "weight": 100,
#                "podAffinityTerm": {"topologyKey": "kubernetes.io/hostname"},
#            }
#        ]
#    }
#    affinity = {
#        "enabled": True,
#        "nodeSelector": {"disktype": "ssd"},
#        "podAntiAffinity": pod_anti,
#    }
#    doc = _render(renderer, _values(affinity=affinity))
#    assert doc["inferenceExtension"]["affinity"]["podAntiAffinity"] == pod_anti


# def test_affinity_omitted_when_disabled(renderer):
#    """A present-but-disabled affinity wrapper must not emit a block."""
#    affinity = {"enabled": False, "nodeSelector": {"foo": "bar"}}
#    doc = _render(renderer, _values(affinity=affinity))
#    assert "affinity" not in doc["inferenceExtension"]


# def test_affinity_omitted_when_unset(renderer):
#    """No affinity wrapper -> no affinity block."""
#    doc = _render(renderer, _values(affinity=None))
#    assert "affinity" not in doc["inferenceExtension"]
