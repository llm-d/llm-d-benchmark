from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "workload"
    / "harnesses"
    / "inference_perf_chat_multiturn.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "inference_perf_chat_multiturn", MODULE_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_strip_thinking_content_removes_closed_think_block() -> None:
    module = _load_module()

    assert module.strip_thinking_content("<think>private</think>visible") == "visible"


def test_strip_thinking_content_drops_unclosed_think_block() -> None:
    module = _load_module()

    assert (
        module.strip_thinking_content("visible prefix <think>private")
        == "visible prefix"
    )


def test_extract_assistant_content_uses_visible_sse_content() -> None:
    module = _load_module()
    raw_response = """
data: {"choices":[{"delta":{"content":"<think>hidden"}}]}

data: {"choices":[{"delta":{"content":"</think>visible"}}]}

data: [DONE]
"""

    assert module.extract_assistant_content(raw_response) == "visible"


def test_chat_extra_body_requires_json_object(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    monkeypatch.setenv("LLMDBENCH_INFERENCE_PERF_CHAT_EXTRA_BODY", "[]")

    with pytest.raises(ValueError, match="must be a JSON object"):
        module._chat_extra_body()


def test_chat_extra_body_parses_json_object(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    monkeypatch.setenv(
        "LLMDBENCH_INFERENCE_PERF_CHAT_EXTRA_BODY",
        '{"temperature": 0, "seed": 42}',
    )

    assert module._chat_extra_body() == {"temperature": 0, "seed": 42}


def test_inter_turn_delay_defaults_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    monkeypatch.delenv(
        "LLMDBENCH_INFERENCE_PERF_INTER_TURN_DELAY_SECONDS", raising=False
    )

    assert module._inter_turn_delay_seconds() == 0.0


def test_inter_turn_delay_requires_non_negative_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    monkeypatch.setenv("LLMDBENCH_INFERENCE_PERF_INTER_TURN_DELAY_SECONDS", "-1")

    with pytest.raises(ValueError, match="must be non-negative"):
        module._inter_turn_delay_seconds()


def test_inter_turn_delay_parses_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    monkeypatch.setenv("LLMDBENCH_INFERENCE_PERF_INTER_TURN_DELAY_SECONDS", "2.5")

    assert module._inter_turn_delay_seconds() == 2.5


def test_apply_patch_updates_shared_prefix_load_lazy_data_when_inference_perf_is_installed() -> (
    None
):
    pytest.importorskip("inference_perf")
    module = _load_module()

    module.apply_patch()

    from inference_perf.datagen.shared_prefix_datagen import SharedPrefixDataGenerator

    assert (
        SharedPrefixDataGenerator.load_lazy_data.__name__
        == "load_lazy_data_with_chat_sessions"
    )
