"""Token-aware P/D autoscaling on a DisaggregatedSet (kustomize-deployed guide).

The DisaggregatedSet variant reuses the Deployment variant's triggers and HPA
behavior and only moves the scale target: each role's ScaledObject drives the
role's DisaggregatedSetRoleScaler, whose name is stable across rollouts.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

from llmdbenchmark.parser.cluster_resource_resolver import ClusterResourceResolver
from llmdbenchmark.parser.render_plans import RenderPlans
from llmdbenchmark.parser.version_resolver import VersionResolver
from llmdbenchmark.standup.steps.step_05_kustomize_deploy import KustomizeDeployStep
from llmdbenchmark.teardown.steps.step_01_uninstall_helm import UninstallHelmStep

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = PROJECT_ROOT / "config" / "templates" / "jinja"
DEFAULTS = PROJECT_ROOT / "config" / "templates" / "values" / "defaults.yaml"
GUIDES = PROJECT_ROOT / "config" / "scenarios" / "guides"

DEPLOY_GUIDE = "keda-epp-token-aware-pd-disaggregation"
DS_GUIDE = "keda-epp-token-aware-pd-disaggregation-disaggset"


def _render(tmp_path: Path, guide: str, overrides: dict | None = None) -> Path:
    logger = MagicMock()
    errors = RenderPlans(
        template_dir=TEMPLATES,
        defaults_file=DEFAULTS,
        scenarios_file=GUIDES / f"{guide}.yaml",
        output_dir=tmp_path / "out",
        logger=logger,
        setup_overrides=overrides or {},
        version_resolver=VersionResolver(logger=logger, dry_run=True),
        cluster_resource_resolver=ClusterResourceResolver(logger=logger, dry_run=True),
    ).eval()
    assert not errors.has_errors, errors.to_dict()
    [stack] = [p.parent for p in (tmp_path / "out").rglob("config.yaml")]
    return stack


def _scaled_objects(stack: Path) -> list[dict]:
    [rendered] = list(stack.glob("30_keda-scaledobject*.yaml"))
    docs = [d for d in yaml.safe_load_all(rendered.read_text()) if d]
    return [d for d in docs if d["kind"] == "ScaledObject"]


def test_each_role_targets_its_disaggregatedset_role_scaler(tmp_path):
    scaled_objects = _scaled_objects(_render(tmp_path, DS_GUIDE))
    assert [so["spec"]["scaleTargetRef"] for so in scaled_objects] == [
        {
            "apiVersion": "disaggregatedset.x-k8s.io/v1",
            "kind": "DisaggregatedSetRoleScaler",
            "name": f"pd-disagg-vllm-{role}",
        }
        for role in ("prefill", "decode")
    ]


def test_only_the_scale_target_differs_from_the_deployment_variant(tmp_path):
    deploy = _scaled_objects(_render(tmp_path / "deploy", DEPLOY_GUIDE))
    ds = _scaled_objects(_render(tmp_path / "ds", DS_GUIDE))
    assert len(deploy) == len(ds) == 2
    for so_deploy, so_ds in zip(deploy, ds):
        assert so_deploy["spec"]["scaleTargetRef"]["kind"] == "Deployment"
        for spec in (so_deploy["spec"], so_ds["spec"]):
            spec.pop("scaleTargetRef")
        assert so_ds["spec"] == so_deploy["spec"]


def test_dry_run_retargets_the_role_scaler_to_a_shadow_deployment(tmp_path):
    stack = _render(tmp_path, DS_GUIDE, {"eppKedaSaturation": {"dryRun": True}})
    for so in _scaled_objects(stack):
        ref = so["spec"]["scaleTargetRef"]
        assert ref["apiVersion"] == "apps/v1"
        assert ref["kind"] == "Deployment"
        assert ref["name"].endswith("-dryrun")
        real = so["metadata"]["annotations"]["llm-d.ai/autoscaling-dry-run-target"]
        assert real.startswith("DisaggregatedSetRoleScaler/pd-disagg-vllm-")


def test_scenario_hands_both_roles_to_keda(tmp_path):
    cfg = yaml.safe_load((_render(tmp_path, DS_GUIDE) / "config.yaml").read_text())
    assert cfg["kustomize"]["enabled"] and not cfg["modelservice"]["enabled"]
    ops = [
        op
        for entry in cfg["kustomize"]["patches"]
        for op in yaml.safe_load(entry["patch"])
        if op["path"].endswith("/scaling")
    ]
    assert ops == [
        {"op": "add", "path": f"/spec/roles/{i}/scaling", "value": {"mode": "External"}}
        for i in (0, 1)
    ]


@pytest.mark.parametrize(
    "entry, error",
    [
        (
            {
                "target": {"kind": "DisaggregatedSet"},
                "patch": "- op: remove\n  path: /a",
            },
            None,
        ),
        ({"patch": "kind: Deployment\nmetadata: {name: x}"}, None),
        (
            {"target": {"kind": "DisaggregatedSet"}, "patch": "kind: Deployment"},
            "JSON6902",
        ),
        ({"patch": "- op: remove\n  path: /a"}, "expected YAML mapping"),
    ],
)
def test_validate_kustomize_patches_accepts_json6902_only_with_a_target(entry, error):
    errors = RenderPlans._validate_kustomize_patches(
        {"kustomize": {"enabled": True, "patches": [entry]}}, "stack"
    )
    if error is None:
        assert errors == []
    else:
        assert len(errors) == 1 and error in errors[0]


def test_overlay_wrapper_keeps_a_patch_target(tmp_path):
    target = {"group": "disaggregatedset.x-k8s.io", "kind": "DisaggregatedSet"}
    ctx = SimpleNamespace(workspace=tmp_path, logger=MagicMock())
    overlay = KustomizeDeployStep._build_overlay_wrapper(
        ctx,
        tmp_path / "guide",
        patches_config=[
            {"target": target, "patch": "- op: remove\n  path: /a"},
            {"patch": "kind: Deployment"},
        ],
    )
    wrapper = yaml.safe_load((overlay / "kustomization.yaml").read_text())
    assert wrapper["patches"] == [
        {"path": "patch-00.yaml", "target": target},
        {"path": "patch-01.yaml"},
    ]


def test_teardown_runs_for_a_kustomize_stack_with_epp_keda(tmp_path):
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "kustomize": {"guideName": "pd-disaggregation"},
                "eppKedaSaturation": {"enabled": True},
            }
        )
    )
    ctx = SimpleNamespace(deployed_methods=["kustomize"], rendered_stacks=[stack])
    assert UninstallHelmStep().should_skip(ctx) is False

    (stack / "config.yaml").write_text(
        yaml.safe_dump({"kustomize": {"guideName": "pd-disaggregation"}})
    )
    assert UninstallHelmStep().should_skip(ctx) is True
