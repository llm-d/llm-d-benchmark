"""Tests for the gateway provider helmfile template."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import yaml

from llmdbenchmark.parser.render_plans import RenderPlans


def _istio_values() -> dict[str, Any]:
    return {
        "standalone": {"enabled": False},
        "kustomize": {"enabled": False},
        "gateway": {"className": "istio", "providerNamespace": "istio-system"},
        "helmRepositories": {
            "istio": {"url": "https://istio-release.storage.googleapis.com/charts"}
        },
        "chartVersions": {"istioBase": "1.29.2", "istiod": "1.29.2"},
    }


def test_istiod_release_waits_until_ready() -> None:
    template_path = (
        Path(__file__).resolve().parent.parent
        / "config"
        / "templates"
        / "jinja"
        / "09_helmfile-gateway-provider.yaml.j2"
    )
    renderer = RenderPlans.__new__(RenderPlans)
    renderer.logger = MagicMock()
    renderer._jinja_env = None
    rendered = renderer._render_template(
        template_path.read_text(encoding="utf-8"), _istio_values()
    )
    releases = {
        release["name"]: release for release in yaml.safe_load(rendered)["releases"]
    }

    assert releases["istiod"]["wait"] is True
    assert releases["istiod"]["timeout"] == 300
