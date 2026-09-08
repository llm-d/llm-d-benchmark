"""FMA controllers Helm release must be namespace-scoped, not model-keyed.

A model-keyed release name (the old ``<model_id_label>-fma-dp``) made every
model switch reinstall the controller from scratch, and was the root cause
of teardown silently leaving a stray per-model release behind. The
release only stands up the model-agnostic dual-pods controller -- all model
wiring lives in the separately-applied CRs (24_fma-deployment.yaml.j2) -- so
one release should serve every model size in a namespace.

The FMA controllers are now installed via the upstream ``install-fma.sh``
(see ``FMADeployStep._build_fma_install_command``) rather than a helmfile
template, so the release name is pinned through ``--chart-instance-name``.
This test asserts that flag is the fixed, model-agnostic ``fma-controllers``.
"""

from __future__ import annotations

import shlex

from llmdbenchmark.standup.steps.step_06_fma_deploy import FMADeployStep


def _plan_config(model_id_label: str) -> dict:
    return {
        "model_id_label": model_id_label,
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


def _chart_instance_name(model_id_label: str) -> str:
    command = FMADeployStep._build_fma_install_command(
        script_path="/tmp/install-fma.sh",
        plan_config=_plan_config(model_id_label),
        namespace="bench",
        non_admin=False,
    )
    tokens = shlex.split(command)
    idx = tokens.index("--chart-instance-name")
    return tokens[idx + 1]


class TestFmaControllerReleaseNaming:
    def test_release_name_is_namespace_scoped_not_model_keyed(self):
        assert _chart_instance_name("opt-125m-abc123") == "fma-controllers"

    def test_release_name_is_identical_across_different_models(self):
        """The whole point: switching models must not produce a new release
        name -- the same release should be reused/no-op'd by the installer."""
        first = _chart_instance_name("qwen3-4b-aaa111")
        second = _chart_instance_name("qwen3-32b-bbb222")
        assert first == second == "fma-controllers"
