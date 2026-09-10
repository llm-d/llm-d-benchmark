"""model_serve_ref(): the single source of truth for what vLLM serves.

Semantics ("what this stack's default command would serve"), keyed on the
consuming stack -- NOT global config, since the plan renders every template
for every scenario and a standalone-enabled scenario must not perturb the
modelservice render:
- stack='standalone'                  -> model ID (HF hub resolution; see
                                         #1822 for why the staged PVC copy
                                         is a separate concern)
- stack='modelservice', uriProtocol hf -> HF id (huggingfaceId, then name)
- stack='modelservice', otherwise      -> <cacheBase>/<path> (staged copy)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment

_JINJA_DIR = Path(__file__).resolve().parent.parent / "config" / "templates" / "jinja"
_MACROS = (_JINJA_DIR / "_macros.j2").read_text(encoding="utf-8")


def _render_ref(values: dict[str, Any], call: str = "model_serve_ref()") -> str:
    env = Environment()
    template = env.from_string(_MACROS + "\n{{ " + call + " }}")
    return template.render(**values).strip().splitlines()[-1].strip()


_BASE = {
    "model": {
        "name": "Qwen/Qwen3-32B",
        "path": "models/Qwen/Qwen3-32B",
        "cacheBase": "/model-cache",
    },
}


def test_standalone_stack_serves_model_id() -> None:
    """The standalone ref is the ID regardless of global protocol config."""
    values = dict(_BASE)
    values["modelservice"] = {"uriProtocol": "pvc"}
    assert _render_ref(values, "model_serve_ref('standalone')") == "Qwen/Qwen3-32B"


def test_modelservice_ref_ignores_standalone_enabled() -> None:
    """A standalone-enabled scenario must not perturb the modelservice
    render -- the plan renders all templates for every scenario."""
    values = dict(_BASE)
    values["standalone"] = {"enabled": True}
    values["modelservice"] = {"uriProtocol": "pvc"}
    assert _render_ref(values) == "/model-cache/models/Qwen/Qwen3-32B"


def test_modelservice_hf_serves_huggingface_id() -> None:
    values = dict(_BASE)
    values["model"] = dict(_BASE["model"], huggingfaceId="Qwen/Qwen3-32B-FP8")
    values["modelservice"] = {"uriProtocol": "hf"}
    assert _render_ref(values) == "Qwen/Qwen3-32B-FP8"


def test_modelservice_hf_falls_back_to_model_name() -> None:
    values = dict(_BASE)
    values["modelservice"] = {"uriProtocol": "hf"}
    assert _render_ref(values) == "Qwen/Qwen3-32B"


def test_modelservice_pvc_serves_cache_path() -> None:
    values = dict(_BASE)
    values["modelservice"] = {"uriProtocol": "pvc"}
    assert _render_ref(values) == "/model-cache/models/Qwen/Qwen3-32B"


def test_serve_ref_env_emitted_for_both_stacks() -> None:
    """Long-term guard that both serving pod specs export MODEL_SERVE_REF
    (the render-diff harness proved the initial emission; this keeps it).
    modelservice pods get it via build_ms_env_vars (decode + prefill share
    the macro); standalone pods via their deployment template."""
    standalone = (_JINJA_DIR / "14_standalone-deployment_yaml.j2").read_text(
        encoding="utf-8"
    )
    assert "MODEL_SERVE_REF" in _MACROS
    assert "model_serve_ref('standalone')" in standalone


def test_default_commands_use_the_macro() -> None:
    """Both default-command branches must serve via model_serve_ref() so the
    env var and the defaults can never drift apart."""
    assert "vllm serve {{ model_serve_ref() }}" in _MACROS
    assert "vllm serve {{ model_serve_ref('standalone') }}" in _MACROS
