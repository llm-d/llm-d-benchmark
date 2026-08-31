"""Regression coverage for #1821: the FMA controllers Helm release must be
namespace-scoped, not model-keyed.

A model-keyed release name (the old ``<model_id_label>-fma-dp``) made every
model switch reinstall the controller from scratch, and was the root cause
of #1820 (teardown silently leaving a stray per-model release behind). The
release only stands up the model-agnostic dual-pods controller -- all model
wiring lives in the separately-applied CRs (24_fma-deployment.yaml.j2) -- so
one release should serve every model size in a namespace.
"""

from __future__ import annotations

import yaml
from jinja2 import Environment

from llmdbenchmark.parser.render_plans import RenderPlans

_TEMPLATE_PATH = "config/templates/jinja/26_helmfile-fma-controllers.yaml.j2"


def _render(model_id_label: str) -> dict:
    env = Environment(
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )
    env.filters["toyaml"] = RenderPlans._toyaml_filter
    with open(_TEMPLATE_PATH, encoding="utf-8") as fh:
        template = env.from_string(fh.read())
    values = {
        "model_id_label": model_id_label,
        "namespace": {"name": "bench"},
        "fma": {
            "enabled": True,
            "chart": {
                "url": "oci://ghcr.io/llm-d-incubation/llm-d-fast-model-actuation/charts/fma-controllers",
                "version": "0.6.5",
            },
            "image": {"repository": "example.com/fma", "tag": "v0.6.5"},
            "dualPod": {"sleeperLimit": 2, "debugAcceleratorMemory": False},
            "launcherPopulatorConfigurator": {
                "limitsCPU": 2,
                "limitsMemory": "2Gi",
                "requestsCPU": "100m",
                "requestsMemory": "128Mi",
            },
        },
    }
    out = template.render(**values)
    return yaml.safe_load(out)


class TestFmaControllerReleaseNaming:
    def test_release_name_is_namespace_scoped_not_model_keyed(self):
        rendered = _render("opt-125m-abc123")
        assert rendered["releases"][0]["name"] == "fma-controllers"

    def test_release_name_is_identical_across_different_models(self):
        """The whole point: switching models must not produce a new release
        name -- the same release should be reused/no-op'd by helmfile."""
        first = _render("qwen3-4b-aaa111")
        second = _render("qwen3-32b-bbb222")
        assert first["releases"][0]["name"] == second["releases"][0]["name"]
        assert first["releases"][0]["name"] == "fma-controllers"
