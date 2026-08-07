"""Render-pipeline integration tests for templates 27 and 27a.

Verifies that:
- A scenario with keda.scaledObjects renders 27_keda-scaledobjects.yaml
- authMode=none renders no TriggerAuthentication and no authenticationRef
- authMode=bearer-secret renders 27a_keda-triggerauthentication.yaml
  and adds authenticationRef to each prometheus trigger
- A scenario without keda.scaledObjects renders neither template
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import yaml

from llmdbenchmark.parser.cluster_resource_resolver import ClusterResourceResolver
from llmdbenchmark.parser.render_plans import RenderPlans
from llmdbenchmark.parser.version_resolver import VersionResolver

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = PROJECT_ROOT / "config" / "templates" / "jinja"
DEFAULTS = PROJECT_ROOT / "config" / "templates" / "values" / "defaults.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_yaml_content(path: Path) -> bool:
    """Return True iff the file exists and contains at least one non-comment line."""
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return True
    return False


def _find_yaml(stack_dir: Path, stem_prefix: str) -> Path | None:
    """Return the first *.yaml file whose name starts with *stem_prefix*."""
    for candidate in stack_dir.glob(f"{stem_prefix}*.yaml"):
        return candidate
    return None


def _render_with_overrides(
    tmp_path: Path, scenario_yaml_str: str, setup_overrides=None
):
    """Write scenario to a temp file and render it."""
    scenario_file = tmp_path / "scenario.yaml"
    scenario_file.write_text(scenario_yaml_str)
    logger = MagicMock()
    renderer = RenderPlans(
        template_dir=TEMPLATES,
        defaults_file=DEFAULTS,
        scenarios_file=scenario_file,
        output_dir=tmp_path / "out",
        logger=logger,
        setup_overrides=setup_overrides or {},
        version_resolver=VersionResolver(logger=logger, dry_run=True),
        cluster_resource_resolver=ClusterResourceResolver(logger=logger, dry_run=True),
    )
    return renderer.eval()


# ---------------------------------------------------------------------------
# Minimal scenario YAML builders
# ---------------------------------------------------------------------------

_SCENARIO_HEADER = """\
scenario:
  - name: test-keda
    model:
      name: test/model
      shortName: test-model
      path: models/test/model
      huggingfaceId: test/model
      size: 10Gi
      maxModelLen: 4096
      blockSize: 16
      gpuMemoryUtilization: 0.9
    modelservice:
      enabled: true
    standalone:
      enabled: false
    gateway:
      className: agentgateway
      externallyManaged: true
    prefill:
      enabled: false
      replicas: 0
    decode:
      replicas: 1
      parallelism:
        tensor: 1
        data: 1
        dataLocal: 1
        workers: 1
"""

_KEDA_NONE_AUTH = """\
    keda:
      prometheus:
        baseUrl: http://prometheus-operated.monitoring.svc.cluster.local
        port: 9090
        authMode: none
      scaledObjects:
        - name: decode-saturation
          targetRef:
            kind: Deployment
            name: ""
          minReplicas: 1
          maxReplicas: 5
          triggers:
            - type: prometheus
              name: kv-cache
              metricType: AverageValue
              query: |
                max(test_metric{namespace="test"})
              threshold: "0.7"
              activationThreshold: "0"
"""

_KEDA_BEARER_AUTH = """\
    keda:
      prometheus:
        baseUrl: http://prometheus-operated.monitoring.svc.cluster.local
        port: 9090
        authMode: bearer-secret
        secretName: keda-prom-secret  # pragma: allowlist secret
      scaledObjects:
        - name: decode-saturation
          targetRef:
            kind: Deployment
            name: ""
          minReplicas: 1
          maxReplicas: 5
          triggers:
            - type: prometheus
              name: kv-cache
              metricType: AverageValue
              query: |
                max(test_metric{namespace="test"})
              threshold: "0.7"
              activationThreshold: "0"
"""

_KEDA_NON_PROMETHEUS_TRIGGER = """\
    keda:
      prometheus:
        baseUrl: http://prometheus-operated.monitoring.svc.cluster.local
        port: 9090
        authMode: none
      scaledObjects:
        - name: cpu-scaler
          targetRef:
            kind: Deployment
            name: my-deploy
          minReplicas: 1
          maxReplicas: 5
          triggers:
            - type: cpu
              metricType: Utilization
              metadata:
                type: Utilization
                value: "50"
"""

_SCENARIO_NO_KEDA = _SCENARIO_HEADER  # no keda key at all


def _scenario_none_auth() -> str:
    return _SCENARIO_HEADER + _KEDA_NONE_AUTH


def _scenario_bearer_auth() -> str:
    return _SCENARIO_HEADER + _KEDA_BEARER_AUTH


def _scenario_non_prometheus_trigger() -> str:
    return _SCENARIO_HEADER + _KEDA_NON_PROMETHEUS_TRIGGER


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestScaledObjectsTemplate:
    """Template 27: generic ScaledObjects renderer."""

    def test_scaledobjects_rendered_with_keda_block(self, tmp_path: Path) -> None:
        """A scenario with keda.scaledObjects produces a non-empty template 27."""
        result = _render_with_overrides(tmp_path, _scenario_none_auth())
        assert len(result.rendered_paths) >= 1, "Expected at least one rendered stack"
        stack_dir = result.rendered_paths[0]
        so_file = _find_yaml(stack_dir, "27_keda-scaledobjects")
        assert so_file is not None, "27_keda-scaledobjects*.yaml not found in stack dir"
        assert _has_yaml_content(so_file), "27_keda-scaledobjects*.yaml is empty"

    def test_scaledobjects_contains_expected_resource(self, tmp_path: Path) -> None:
        """The rendered ScaledObject has the correct kind and name."""
        result = _render_with_overrides(tmp_path, _scenario_none_auth())
        stack_dir = result.rendered_paths[0]
        so_file = _find_yaml(stack_dir, "27_keda-scaledobjects")
        assert so_file is not None
        docs = list(yaml.safe_load_all(so_file.read_text(encoding="utf-8")))
        docs = [d for d in docs if d]  # drop None separators
        assert len(docs) >= 1
        assert docs[0]["kind"] == "ScaledObject"
        assert docs[0]["metadata"]["name"] == "decode-saturation"

    def test_no_keda_block_produces_empty_template_27(self, tmp_path: Path) -> None:
        """A scenario without keda.scaledObjects produces an empty template 27."""
        result = _render_with_overrides(tmp_path, _SCENARIO_NO_KEDA)
        assert len(result.rendered_paths) >= 1
        stack_dir = result.rendered_paths[0]
        so_file = _find_yaml(stack_dir, "27_keda-scaledobjects")
        # File may exist but must have no YAML content
        assert so_file is None or not _has_yaml_content(so_file), (
            "Template 27 should be empty when keda.scaledObjects is absent"
        )


class TestAuthModeNone:
    """authMode=none: no authenticationRef, no TriggerAuthentication."""

    def test_no_authentication_ref_in_triggers(self, tmp_path: Path) -> None:
        """authMode=none: no trigger should have an authenticationRef field."""
        result = _render_with_overrides(tmp_path, _scenario_none_auth())
        stack_dir = result.rendered_paths[0]
        so_file = _find_yaml(stack_dir, "27_keda-scaledobjects")
        assert so_file is not None
        docs = list(yaml.safe_load_all(so_file.read_text(encoding="utf-8")))
        docs = [d for d in docs if d]
        for doc in docs:
            for trigger in doc.get("spec", {}).get("triggers", []):
                assert "authenticationRef" not in trigger, (
                    f"Unexpected authenticationRef in trigger: {trigger}"
                )

    def test_non_prometheus_trigger_uses_metadata_block(self, tmp_path: Path) -> None:
        """A non-prometheus trigger renders trigger.metadata as-is, no serverAddress injected."""
        result = _render_with_overrides(tmp_path, _scenario_non_prometheus_trigger())
        stack_dir = result.rendered_paths[0]
        so_file = _find_yaml(stack_dir, "27_keda-scaledobjects")
        assert so_file is not None
        docs = [d for d in yaml.safe_load_all(so_file.read_text(encoding="utf-8")) if d]
        assert len(docs) >= 1
        trigger = docs[0]["spec"]["triggers"][0]
        assert trigger["type"] == "cpu"
        assert "serverAddress" not in trigger.get("metadata", {}), (
            "serverAddress must not be injected for non-prometheus triggers"
        )
        assert trigger["metadata"]["value"] == "50"
        assert "authenticationRef" not in trigger, (
            "authenticationRef must not appear on non-prometheus triggers"
        )

    def test_no_trigger_authentication_template(self, tmp_path: Path) -> None:
        """authMode=none: template 27a should be empty."""
        result = _render_with_overrides(tmp_path, _scenario_none_auth())
        stack_dir = result.rendered_paths[0]
        ta_file = _find_yaml(stack_dir, "27a_keda-triggerauthentication")
        assert ta_file is None or not _has_yaml_content(ta_file), (
            "Template 27a should be empty when authMode=none"
        )


class TestAuthModeBearerSecret:
    """authMode=bearer-secret: authenticationRef injected; template 27a rendered."""

    def test_authentication_ref_in_all_triggers(self, tmp_path: Path) -> None:
        """authMode=bearer-secret: every prometheus trigger has authenticationRef."""
        result = _render_with_overrides(tmp_path, _scenario_bearer_auth())
        stack_dir = result.rendered_paths[0]
        so_file = _find_yaml(stack_dir, "27_keda-scaledobjects")
        assert so_file is not None
        assert _has_yaml_content(so_file)
        docs = list(yaml.safe_load_all(so_file.read_text(encoding="utf-8")))
        docs = [d for d in docs if d]
        assert len(docs) >= 1
        for doc in docs:
            triggers = doc.get("spec", {}).get("triggers", [])
            assert triggers, "Expected at least one trigger in ScaledObject"
            for trigger in triggers:
                assert "authenticationRef" in trigger, (
                    f"authenticationRef missing from trigger: {trigger}"
                )
                assert trigger["authenticationRef"]["name"] == "keda-prometheus-auth"

    def test_trigger_authentication_rendered(self, tmp_path: Path) -> None:
        """authMode=bearer-secret: template 27a renders a TriggerAuthentication."""
        result = _render_with_overrides(tmp_path, _scenario_bearer_auth())
        stack_dir = result.rendered_paths[0]
        ta_file = _find_yaml(stack_dir, "27a_keda-triggerauthentication")
        assert ta_file is not None, "27a_keda-triggerauthentication*.yaml not found"
        assert _has_yaml_content(ta_file), "Template 27a is empty for bearer-secret"

    def test_trigger_authentication_secret_ref(self, tmp_path: Path) -> None:
        """authMode=bearer-secret: TriggerAuthentication references the secret name."""
        result = _render_with_overrides(tmp_path, _scenario_bearer_auth())
        stack_dir = result.rendered_paths[0]
        ta_file = _find_yaml(stack_dir, "27a_keda-triggerauthentication")
        assert ta_file is not None
        doc = yaml.safe_load(ta_file.read_text(encoding="utf-8"))
        assert doc is not None
        assert doc["kind"] == "TriggerAuthentication"
        refs = doc["spec"]["secretTargetRef"]
        assert len(refs) >= 1
        assert refs[0]["name"] == "keda-prom-secret"

    def test_no_keda_block_produces_empty_template_27a(self, tmp_path: Path) -> None:
        """A scenario without keda produces an empty template 27a."""
        result = _render_with_overrides(tmp_path, _SCENARIO_NO_KEDA)
        stack_dir = result.rendered_paths[0]
        ta_file = _find_yaml(stack_dir, "27a_keda-triggerauthentication")
        assert ta_file is None or not _has_yaml_content(ta_file), (
            "Template 27a should be empty when keda is absent"
        )
