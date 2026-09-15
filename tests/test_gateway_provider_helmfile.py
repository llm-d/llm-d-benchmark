"""Tests for the gateway provider helmfile (09_helmfile-gateway-provider.yaml.j2).

With the istio gateway, step 06 installs the llm-d-infra chart right after
this helmfile. That chart creates Istio resources (DestinationRule, Telemetry)
that Istio's validation webhook checks, so the istiod release has to wait
until istiod is ready. Without the wait, the webhook rejects those resources
while istiod is still starting.

The template is rendered through the real RenderPlans Jinja environment.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from llmdbenchmark.parser.render_plans import RenderPlans

TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "templates"
    / "jinja"
    / "09_helmfile-gateway-provider.yaml.j2"
)


@pytest.fixture(scope="module")
def template() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture
def renderer():
    """A RenderPlans wired only with what _render_template needs."""
    r = RenderPlans.__new__(RenderPlans)
    r.logger = MagicMock()
    r._jinja_env = None
    return r


def _istio_values() -> dict:
    """Minimal values dict mirroring defaults.yaml for the istio branch."""
    return {
        "standalone": {"enabled": False},
        "kustomize": {"enabled": False},
        "gateway": {"className": "istio", "providerNamespace": "istio-system"},
        "helmRepositories": {
            "istio": {"url": "https://istio-release.storage.googleapis.com/charts"}
        },
        "chartVersions": {"istioBase": "1.29.2", "istiod": "1.29.2"},
    }


def test_istiod_waits_until_ready(renderer, template):
    out = renderer._render_template(template, _istio_values())
    releases = {r["name"]: r for r in yaml.safe_load(out)["releases"]}
    assert releases["istiod"]["wait"] is True
    assert releases["istiod"]["timeout"] == 300
