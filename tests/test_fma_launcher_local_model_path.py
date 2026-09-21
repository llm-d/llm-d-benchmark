"""Regression coverage for #1822: FMA launcher should load the model from
the download job's staged local copy, not re-resolve the repo ID through
the HF hub cache.

``fma.launcher.loadModelFromLocalDir`` (opt-in, default off) switches the
InferenceServerConfig's ``--model`` argument between the repo ID
(``model.name``) and the staged local path
(``<fma.modelMountPath>/<model.path>``). The model name itself must never
change: it feeds the derived Helm release name elsewhere in the pipeline.
"""

from __future__ import annotations

import yaml
from jinja2 import Environment

from llmdbenchmark.parser.render_plans import RenderPlans

_TEMPLATE_PATH = "config/templates/jinja/24_fma-deployment.yaml.j2"


def _render(values: dict) -> list[dict]:
    env = Environment(
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )
    env.filters["toyaml"] = RenderPlans._toyaml_filter
    with open(_TEMPLATE_PATH, encoding="utf-8") as fh:
        template = env.from_string(fh.read())
    out = template.render(**values)
    return [yaml.safe_load(doc) for doc in out.split("\n---\n") if doc.strip()]


def _base_values(load_from_local_dir: bool) -> dict:
    return {
        "model_id_label": "opt-125m-abc123",
        "model": {
            "name": "facebook/opt-125m",
            "path": "models/facebook/opt-125m",
            "maxModelLen": 16384,
            "gpuMemoryUtilization": 0.95,
        },
        "namespace": {"name": "bench"},
        "labels": {"inferenceServing": "true"},
        "huggingface": {"enabled": False},
        "scenarioName": "test-scenario",
        "fma": {
            "enabled": True,
            "modelMountPath": "/model-cache",
            "modelPvcName": "model-pvc",
            "mountModelVolume": True,
            "launcher": {
                "loadModelFromLocalDir": load_from_local_dir,
                "maxInstances": 4,
                "image": {
                    "repository": "example.com/launcher",
                    "tag": "v0.6.5",
                    "pullPolicy": "IfNotPresent",
                },
                "podTemplate": {"metadata": {}},
                "customPreprocessCommands": [],
            },
            "launcherConfigurator": {"port": 8001},
            "requester": {
                "image": {"repository": "example.com/requester", "tag": "v0.6.5"},
                "probePort": 8080,
                "spiPort": 8081,
                "limitsGPU": 1,
                "limitsCPU": "1",
                "limitsMemory": "250Mi",
                "replicas": 0,
            },
        },
    }


def _options(docs: list[dict]) -> str:
    isc = next(d for d in docs if d and d.get("kind") == "InferenceServerConfig")
    return isc["spec"]["modelServerConfig"]["options"]


class TestFmaLauncherLocalModelPath:
    def test_default_off_uses_repo_id(self):
        docs = _render(_base_values(load_from_local_dir=False))
        assert "--model facebook/opt-125m " in _options(docs)

    def test_opt_in_uses_staged_local_path(self):
        docs = _render(_base_values(load_from_local_dir=True))
        assert "--model /model-cache/models/facebook/opt-125m " in _options(docs)

    def test_opt_in_does_not_change_model_name_used_for_release_naming(self):
        """model.name (which feeds the Helm release name elsewhere) must
        stay the repo ID regardless of the launcher's --model arg."""
        values = _base_values(load_from_local_dir=True)
        assert values["model"]["name"] == "facebook/opt-125m"

    def test_opt_in_pins_served_model_name_to_repo_id(self):
        """Without --served-model-name, vLLM advertises the model under its
        local PATH and the run phase's verify_model step fails."""
        docs = _render(_base_values(load_from_local_dir=True))
        assert "--served-model-name facebook/opt-125m" in _options(docs)


class TestFmaLauncherBaselineParity:
    """The launcher's prefix-caching flag inherits vllmCommon.flags.noPrefixCaching
    -- the key _macros.j2 emits the baseline's --no-enable-prefix-caching from --
    so an FMA arm cannot silently desync from the arm it is compared against."""

    def test_prefix_caching_on_by_default(self):
        assert "--no-enable-prefix-caching" not in _options(
            _render(_base_values(load_from_local_dir=False))
        )

    def test_prefix_caching_inherits_baseline_disable(self):
        values = _base_values(load_from_local_dir=False)
        values["vllmCommon"] = {"flags": {"noPrefixCaching": True}}
        assert "--no-enable-prefix-caching" in _options(_render(values))

    def test_logging_flags_inherit_when_launcher_keys_absent(self):
        """An absent launcher key is Jinja Undefined, not none -- it must still
        inherit rather than fall through and drop the flag."""
        values = _base_values(load_from_local_dir=False)
        values["vllmCommon"] = {
            "flags": {"disableLogRequests": True, "disableUvicornAccessLog": True}
        }
        options = _options(_render(values))
        assert "--no-enable-log-requests" in options
        assert "--disable-uvicorn-access-log" in options

    def test_explicit_false_overrides_inherited_true(self):
        """`| default(x, true)` would treat false as empty and wrongly inherit."""
        values = _base_values(load_from_local_dir=False)
        values["vllmCommon"] = {"flags": {"disableLogRequests": True}}
        values["fma"]["launcher"]["disableLogRequests"] = False
        assert "--no-enable-log-requests" not in _options(_render(values))

    def test_block_size_inherits_model_block_size(self):
        """model.blockSize is the same key the baseline feeds VLLM_BLOCK_SIZE."""
        values = _base_values(load_from_local_dir=False)
        values["model"]["blockSize"] = 64
        assert "--block-size 64" in _options(_render(values))
