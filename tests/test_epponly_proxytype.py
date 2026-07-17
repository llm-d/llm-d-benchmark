"""Tests for router.proxy.proxyType rendering on the epponly path.

epponly (the standalone router topology) used to only ever render
Envoy sidecar settings, there was no way to pick agentgateway as the
proxy even though the chart supports it. This checks that setting
inferenceExtension.proxyType actually shows up in the rendered values,
and that picking envoy or leaving it unset behaves the same as before.
"""

from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from llmdbenchmark.parser.render_plans import RenderPlans

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = (
    REPO_ROOT / "config" / "templates" / "jinja" / "12_router-values.yaml.j2"
)
DEFAULTS_PATH = REPO_ROOT / "config" / "templates" / "values" / "defaults.yaml"


@pytest.fixture
def renderer():
    """Bypass __init__, we only need _render_template + the jinja filters."""
    logger = MagicMock()
    r = RenderPlans.__new__(RenderPlans)
    r.logger = logger
    r._jinja_env = None
    return r


@pytest.fixture
def defaults():
    with open(DEFAULTS_PATH) as f:
        return yaml.safe_load(f)


def scenario_values(
    defaults: dict,
    gateway_class: str = "epponly",
    extra_inference_extension: dict | None = None,
) -> dict:
    """Real defaults.yaml plus the bits a scenario would normally override."""
    values = copy.deepcopy(defaults)
    values["standalone"] = {"enabled": False}
    values["kustomize"] = {"enabled": False}
    values["gateway"]["className"] = gateway_class
    if extra_inference_extension:
        values["inferenceExtension"].update(extra_inference_extension)
    return values


def render(renderer, values: dict) -> dict:
    rendered = renderer._render_template(TEMPLATE_PATH.read_text(), values)
    return yaml.safe_load(rendered)


class TestProxyTypeRendering:
    def test_no_proxytype_set_keeps_old_behavior(self, renderer, defaults):
        """Nothing set at all -> no proxy block, same as before this fix."""
        out = render(renderer, scenario_values(defaults))
        assert "proxy" not in out["router"]

    def test_proxytype_agentgateway_is_rendered(self, renderer, defaults):
        out = render(
            renderer,
            scenario_values(
                defaults, extra_inference_extension={"proxyType": "agentgateway"}
            ),
        )
        assert out["router"]["proxy"]["proxyType"] == "agentgateway"

    def test_proxytype_agentgateway_auto_sets_secure_serving_false(
        self, renderer, defaults
    ):
        """chart fails to render if secure-serving isn't false for agentgateway."""
        out = render(
            renderer,
            scenario_values(
                defaults, extra_inference_extension={"proxyType": "agentgateway"}
            ),
        )
        assert out["router"]["epp"]["flags"]["secure-serving"] == "false"

    def test_proxytype_envoy_explicit_still_works(self, renderer, defaults):
        out = render(
            renderer,
            scenario_values(defaults, extra_inference_extension={"proxyType": "envoy"}),
        )
        assert out["router"]["proxy"]["proxyType"] == "envoy"
        # envoy doesn't need secure-serving forced off
        assert "secure-serving" not in out["router"]["epp"]["flags"]

    def test_envoy_sidecar_args_still_work_alongside_proxytype(
        self, renderer, defaults
    ):
        """existing envoySidecar.args/resources scenarios shouldn't break."""
        out = render(
            renderer,
            scenario_values(
                defaults,
                extra_inference_extension={
                    "proxyType": "envoy",
                    "envoySidecar": {"args": ["--foo", "bar"]},
                },
            ),
        )
        assert out["router"]["proxy"]["proxyType"] == "envoy"
        assert out["router"]["proxy"]["args"] == ["--foo", "bar"]

    def test_non_epponly_ignores_proxytype(self, renderer, defaults):
        """proxyType is an epponly-only knob, gateway-based topologies don't use it."""
        out = render(
            renderer,
            scenario_values(
                defaults,
                gateway_class="istio",
                extra_inference_extension={"proxyType": "agentgateway"},
            ),
        )
        assert "proxy" not in out["router"]
