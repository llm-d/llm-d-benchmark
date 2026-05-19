"""Ensure prometheus-adapter version pins stay aligned across tracked files."""

from __future__ import annotations

import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXPECTED_PIN = "prometheus-nats-exporter-2.23.0"


def _extract(pattern: str, text: str, label: str) -> str:
    match = re.search(pattern, text, flags=re.MULTILINE)
    assert match, f"Could not find {label} using pattern: {pattern}"
    return match.group(1)


def test_prometheus_adapter_pin_is_synced() -> None:
    defaults = (_REPO_ROOT / "config/templates/values/defaults.yaml").read_text(
        encoding="utf-8",
    )
    guide = (_REPO_ROOT / "config/scenarios/guides/workload-autoscaling.yaml").read_text(
        encoding="utf-8",
    )
    example = (_REPO_ROOT / "config/scenarios/examples/multi-model-wva.yaml").read_text(
        encoding="utf-8",
    )
    wva_doc = (_REPO_ROOT / "docs/workload-variant-autoscaler.md").read_text(
        encoding="utf-8",
    )
    upstream_doc = (_REPO_ROOT / "docs/upstream-versions.md").read_text(
        encoding="utf-8",
    )

    defaults_pin = _extract(
        r"^\s*prometheus_version:\s*&prometheus_version\s+([^\s]+)\s*$",
        defaults,
        "defaults prometheus_version",
    )
    guide_pin = _extract(
        r"^\s*prometheusAdapter:\s*([^\s]+)\s*$",
        guide,
        "guide chartVersions.prometheusAdapter",
    )
    example_pin = _extract(
        r"^\s*prometheusAdapter:\s*([^\s]+)\s*$",
        example,
        "example chartVersions.prometheusAdapter",
    )
    wva_doc_arch_pin = _extract(
        r"prometheus-adapter\s+([^\s,]+), in openshift-user-workload-monitoring",
        wva_doc,
        "WVA architecture prometheus-adapter version",
    )
    wva_doc_chart_pin = _extract(
        r"^\s*prometheusAdapter:\s*([^\s]+)\s+# bumped charts have broken external-metric rule format\s*$",
        wva_doc,
        "WVA chart pin section prometheusAdapter",
    )
    upstream_pin = _extract(
        r"\|\s*\*\*prometheusAdapter\*\*\s*\|\s*`([^`]+)`\s*\|",
        upstream_doc,
        "upstream versions table prometheusAdapter",
    )

    assert defaults_pin == _EXPECTED_PIN
    assert guide_pin == defaults_pin
    assert example_pin == defaults_pin
    assert wva_doc_arch_pin == defaults_pin
    assert wva_doc_chart_pin == defaults_pin
    assert upstream_pin == defaults_pin
