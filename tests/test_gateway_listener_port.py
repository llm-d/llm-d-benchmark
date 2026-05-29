"""Tests for the configurable Gateway listener port (gateway.listenerPort).

Covers 11_infra.yaml.j2, which renders the per-stack Gateway resource for
each supported gateway class:

* gke / agentgateway / istio (and any custom istio class) - emit a default
  HTTP listener on ``gateway.listenerPort`` when it is set, and omit the
  ``listeners`` block entirely when it is not.
* data-science-gateway-class - pins the listener to port 443 (HTTPS+TLS),
  so setting ``gateway.listenerPort`` is unsupported and must fail loudly.

The template is rendered through the real RenderPlans Jinja environment so
the ``raise`` global and custom filters are exercised exactly as in prod.
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
    / "11_infra.yaml.j2"
)

# Gateway classes that support a configurable listener port. "istio" and an
# arbitrary custom class both fall through to the default (Istio) branch.
LISTENER_PORT_CLASSES = ["gke", "agentgateway", "istio", "custom-istio-class"]


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


def _values(gw_class: str, listener_port: int | None = None) -> dict:
    """Minimal values dict mirroring defaults.yaml for the infra template."""
    gateway = {
        "className": gw_class,
        "logLevel": "error",
        "service": {"type": "NodePort"},
        "resources": {
            "limits": {"cpu": "16", "memory": "16Gi"},
            "requests": {"cpu": "4", "memory": "4Gi"},
        },
    }
    if listener_port is not None:
        gateway["listenerPort"] = listener_port
    return {
        "standalone": {"enabled": False},
        "kustomize": {"enabled": False},
        "gateway": gateway,
        "model_id_label": "model-1",
        "namespace": {"name": "llmdbench"},
    }


class TestListenerPortRendered:
    """Classes that honour gateway.listenerPort."""

    @pytest.mark.parametrize("gw_class", LISTENER_PORT_CLASSES)
    def test_port_set_emits_http_listener(self, renderer, template, gw_class):
        out = renderer._render_template(template, _values(gw_class, 8080))
        doc = yaml.safe_load(out)
        listeners = doc["gateway"]["listeners"]
        assert listeners == [
            {
                "name": "default",
                "port": 8080,
                "protocol": "HTTP",
                "allowedRoutes": {"namespaces": {"from": "All"}},
            }
        ]

    @pytest.mark.parametrize("gw_class", LISTENER_PORT_CLASSES)
    def test_port_unset_omits_listeners(self, renderer, template, gw_class):
        out = renderer._render_template(template, _values(gw_class, None))
        doc = yaml.safe_load(out)
        assert "listeners" not in doc["gateway"]

    @pytest.mark.parametrize("gw_class", LISTENER_PORT_CLASSES)
    def test_port_zero_is_treated_as_unset(self, renderer, template, gw_class):
        """A falsy port (0) must not emit a listener block."""
        out = renderer._render_template(template, _values(gw_class, 0))
        doc = yaml.safe_load(out)
        assert "listeners" not in doc["gateway"]


class TestDataScienceGatewayClass:
    """data-science-gateway-class pins the listener to 443 - port is rejected."""

    def test_default_keeps_fixed_443_listener(self, renderer, template):
        out = renderer._render_template(
            template, _values("data-science-gateway-class", None)
        )
        doc = yaml.safe_load(out)
        listener = doc["gateway"]["listeners"][0]
        assert listener["port"] == 443
        assert listener["protocol"] == "HTTPS"
        assert listener["tls"]["mode"] == "Terminate"

    def test_listener_port_raises(self, renderer, template):
        with pytest.raises(ValueError, match="data-science-gateway-class"):
            renderer._render_template(
                template, _values("data-science-gateway-class", 8080)
            )


class TestRaiseHelper:
    """The `raise` global that backs the data-science guard."""

    def test_raise_global_registered(self, renderer):
        env = renderer._get_jinja_env()
        assert "raise" in env.globals

    def test_raise_helper_raises_value_error(self):
        with pytest.raises(ValueError, match="boom"):
            RenderPlans._raise_helper("boom")
