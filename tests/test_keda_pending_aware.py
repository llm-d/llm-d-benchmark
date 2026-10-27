"""Pending-aware scaling (eppKedaSaturation.scaledObject.pendingAware).

A dynamic stabilization window rendered by 30_keda-scaledobject for any
eppKedaSaturation guide. Verifies:

- it is off by default: a guide without the flag renders as before;
- on, each ScaledObject has one trigger that wraps the guide's own triggers,
  scoped to its own role, with R substituted and a 0 s scale-up window;
- the guide's scale-up policies and scale-down behavior are kept;
- on without servingCapacity fails the render with a clear message.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from llmdbenchmark.parser.cluster_resource_resolver import ClusterResourceResolver
from llmdbenchmark.parser.render_plans import RenderPlans
from llmdbenchmark.parser.version_resolver import VersionResolver

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = PROJECT_ROOT / "config" / "templates" / "jinja"
DEFAULTS = PROJECT_ROOT / "config" / "templates" / "values" / "defaults.yaml"
GUIDES = PROJECT_ROOT / "config" / "scenarios" / "guides"

# Every scenario that drives autoscaling through eppKedaSaturation, and its roles.
KEDA_GUIDES = {
    "epp-keda-saturation": ["decode"],
    "keda-epp-token-aware": ["decode"],
    "keda-epp-inflight": ["decode"],
    "keda-epp-token-aware-pd-disaggregation": ["prefill", "decode"],
}


def _render(tmp_path: Path, guide: str, scaled_object: dict | None):
    logger = MagicMock()
    overrides = (
        {}
        if scaled_object is None
        else {"eppKedaSaturation": {"scaledObject": scaled_object}}
    )
    errors = RenderPlans(
        template_dir=TEMPLATES,
        defaults_file=DEFAULTS,
        scenarios_file=GUIDES / f"{guide}.yaml",
        output_dir=tmp_path / "out",
        logger=logger,
        setup_overrides=overrides,
        version_resolver=VersionResolver(logger=logger, dry_run=True),
        cluster_resource_resolver=ClusterResourceResolver(logger=logger, dry_run=True),
    ).eval()
    rendered = list((tmp_path / "out").rglob("30_keda-scaledobject*.yaml"))
    docs = (
        [d for d in yaml.safe_load_all(rendered[0].read_text()) if d]
        if rendered
        else []
    )
    return errors, [d for d in docs if d.get("kind") == "ScaledObject"]


@pytest.mark.parametrize("guide", KEDA_GUIDES)
def test_off_by_default(tmp_path, guide):
    errors, base = _render(tmp_path / "a", guide, None)
    _, off = _render(tmp_path / "b", guide, {"pendingAware": False})
    assert not errors.has_errors, errors.to_dict()
    assert off == base
    assert all("pending-aware-replicas" not in str(so) for so in base)


@pytest.mark.parametrize("guide, roles", KEDA_GUIDES.items())
def test_on_wraps_the_guides_triggers_per_role(tmp_path, guide, roles):
    _, base = _render(tmp_path / "a", guide, None)
    errors, on = _render(
        tmp_path / "b", guide, {"pendingAware": True, "servingCapacity": 1.8}
    )
    assert not errors.has_errors, errors.to_dict()
    assert len(on) == len(base) == len(roles)
    for so, guide_so, role in zip(on, base, roles):
        [trigger] = so["spec"]["triggers"]
        query = trigger["metadata"]["query"]
        assert trigger["name"] == "pending-aware-replicas"
        assert (trigger["metricType"], trigger["metadata"]["threshold"]) == (
            "AverageValue",
            "1",
        )
        assert "${" not in query and "/ 1.8)" in query
        assert f'label_llm_d_ai_role="{role}"' in query
        for t in guide_so["spec"]["triggers"]:
            assert f"/ {t['metadata']['threshold']})" in query
        behavior = so["spec"]["advanced"]["horizontalPodAutoscalerConfig"]["behavior"]
        guide_behavior = guide_so["spec"]["advanced"]["horizontalPodAutoscalerConfig"][
            "behavior"
        ]
        assert behavior["scaleUp"]["stabilizationWindowSeconds"] == 0
        assert behavior["scaleUp"]["policies"] == guide_behavior["scaleUp"]["policies"]
        assert behavior["scaleDown"] == guide_behavior["scaleDown"]
        # everything else about the ScaledObject is the guide's
        for key in (
            "scaleTargetRef",
            "minReplicaCount",
            "maxReplicaCount",
            "pollingInterval",
        ):
            assert so["spec"][key] == guide_so["spec"][key]


def test_on_without_serving_capacity_names_the_fix(tmp_path):
    errors, _ = _render(tmp_path, "epp-keda-saturation", {"pendingAware": True})
    assert errors.has_errors
    assert "make calibrate-serving-capacity" in str(errors.to_dict())
