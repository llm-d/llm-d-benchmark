from __future__ import annotations

import asyncio
import importlib.util
import json
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


def test_strip_thinking_content_removes_all_think_blocks() -> None:
    module = _load_module()

    assert (
        module.strip_thinking_content(
            "<think>private one</think>first <think>private two</think>second"
        )
        == "first second"
    )
    assert (
        module.strip_thinking_content(
            "<think>private one</think>visible <think>unfinished"
        )
        == "visible"
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


def test_client_side_warmup_flag_requires_boolean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    monkeypatch.setenv("LLMDBENCH_INFERENCE_PERF_CLIENT_SIDE_WARMUP", "yes")

    with pytest.raises(ValueError, match="must be true or false"):
        module._client_side_warmup_enabled()


@pytest.mark.parametrize("status", [200, 503])
def test_warm_next_turn_posts_one_token_request_and_records_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: int
) -> None:
    from aiohttp import web

    module = _load_module()
    monkeypatch.setenv("LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR", str(tmp_path))

    async def run() -> tuple[dict, list]:
        received = []

        async def handler(request):
            received.append((request.path, await request.json()))
            return web.json_response({"choices": []}, status=status)

        app = web.Application()
        app.router.add_post("/v1/chat/completions", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        monkeypatch.setenv(
            "LLMDBENCH_HARNESS_STACK_ENDPOINT_URL", f"http://127.0.0.1:{port}"
        )
        try:
            await module._warm_next_turn(
                {
                    "model": "model",
                    "messages": [
                        {"role": "assistant", "content": "answer"},
                        {"role": "user", "content": "next"},
                    ],
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "max_tokens": 1200,
                    "min_tokens": 5,
                    "temperature": 0.0,
                },
                "session-0",
                1,
            )
        finally:
            await runner.cleanup()
        return received[0], json.loads(
            (tmp_path / "client_side_warmup.jsonl").read_text().strip()
        )

    (route, payload), record = asyncio.run(run())
    assert route == "/v1/chat/completions"
    assert payload == {
        "model": "model",
        "messages": [
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "next"},
        ],
        "stream": False,
        "max_tokens": 1,
        "temperature": 0.0,
    }
    assert record["status"] == status
    assert record["session_id"] == "session-0"
    assert record["chat_round"] == 1
    assert record["duration_seconds"] >= 0


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


def _install_inference_perf_stubs(monkeypatch: pytest.MonkeyPatch):
    """Just enough of inference_perf for apply_patch() to build a chat request."""
    import sys
    import types

    from pydantic import BaseModel

    class ChatMessage(BaseModel):
        role: str
        content: str

    class ChatCompletionAPIData(BaseModel):
        messages: list
        max_tokens: int = 0

        async def to_request_body(self, *_args):
            return {
                "model": _args[0],
                "messages": [message.model_dump() for message in self.messages],
                "max_tokens": self.max_tokens,
                "stream": _args[3],
            }

        async def process_response(self, *_args):
            return InferenceInfo()

    class InferenceInfo:
        def __init__(self, request_metrics=None):
            self.extra_info = {"raw_response": ""}

    class LocalUserSession:
        _instances: dict = {}

        def __init__(self):
            self.system_prompt = ""
            self.history = []
            self._current_round = 0

        @classmethod
        def get_instance(cls, session_id):
            return cls._instances.setdefault(session_id, cls())

        async def get_context(self, _round):
            self._current_round += 1
            return ""

        def update_context(self, _context):
            pass

    class SharedPrefixDataGenerator:
        def load_lazy_data(self, data):
            raise AssertionError("multi-turn chat should use the patched loader")

    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)
        return mod

    module("inference_perf")
    module(
        "inference_perf.apis",
        ChatCompletionAPIData=ChatCompletionAPIData,
        ChatMessage=ChatMessage,
        InferenceInfo=InferenceInfo,
    )
    module("inference_perf.apis.user_session", LocalUserSession=LocalUserSession)
    module("inference_perf.payloads", RequestMetrics=object, Text=lambda **_: None)
    module("inference_perf.datagen")
    module("inference_perf.datagen.synthetic")
    module(
        "inference_perf.datagen.synthetic.shared_prefix_datagen",
        SharedPrefixDataGenerator=SharedPrefixDataGenerator,
    )
    return SharedPrefixDataGenerator


def test_inter_turn_delay_stays_out_of_measured_request_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import time
    from types import SimpleNamespace

    delay = 0.3
    generator_cls = _install_inference_perf_stubs(monkeypatch)
    monkeypatch.setenv("LLMDBENCH_INFERENCE_PERF_INTER_TURN_DELAY_SECONDS", str(delay))
    module = _load_module()
    module.apply_patch()

    generator = SimpleNamespace(
        enable_multi_turn_chat=True,
        api_config=SimpleNamespace(type=SimpleNamespace(name="Chat")),
        prefix_multimodal=False,
        payload_multimodal=False,
        prompts=["question"],
        user_sessions=[SimpleNamespace(user_session_id="session-0")],
        flat_output_lens=[8],
    )

    async def turn(index: int) -> tuple[float, float]:
        data = generator_cls.load_lazy_data(
            generator, SimpleNamespace(data_index=index)
        )
        before = time.perf_counter()
        await data.to_request_body("model", 8, True, True)
        # inference-perf starts the request clock here and stops it after
        # process_response, so that span is the reported request latency.
        start = time.perf_counter()
        await data.process_response(None, None, None)
        return start - before, time.perf_counter() - start

    async def conversation():
        return [await turn(index) for index in range(2)]

    (first_wait, first_latency), (second_wait, second_latency) = asyncio.run(
        conversation()
    )

    assert first_latency < delay and second_latency < delay
    assert first_wait < delay  # the opening turn has no previous turn to wait on
    assert second_wait >= delay  # a follow-up turn still waits out the think time


@pytest.mark.parametrize("wait_before_next", [0.0, 0.06])
def test_warmup_starts_after_response_without_blocking_next_turn(
    monkeypatch: pytest.MonkeyPatch, wait_before_next: float
) -> None:
    import time
    from types import SimpleNamespace

    generator_cls = _install_inference_perf_stubs(monkeypatch)
    monkeypatch.setenv("LLMDBENCH_INFERENCE_PERF_CLIENT_SIDE_WARMUP", "true")
    monkeypatch.setenv("LLMDBENCH_INFERENCE_PERF_INTER_TURN_DELAY_SECONDS", "0.05")
    module = _load_module()
    monkeypatch.setattr(module, "extract_assistant_content", lambda _: "answer")
    warmups = []

    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_warmup(body, session_id, round_index):
        warmups.append((body, session_id, round_index, time.perf_counter()))
        started.set()
        await release.wait()

    monkeypatch.setattr(module, "_warm_next_turn", fake_warmup)
    module.apply_patch()
    generator = SimpleNamespace(
        enable_multi_turn_chat=True,
        api_config=SimpleNamespace(type=SimpleNamespace(name="Chat")),
        prefix_multimodal=False,
        payload_multimodal=False,
        prompts=["question"],
        user_sessions=[SimpleNamespace(user_session_id="session-1")],
        flat_output_lens=[8],
    )

    async def conversation():
        first = generator_cls.load_lazy_data(generator, SimpleNamespace(data_index=0))
        await first.to_request_body("model", 8, True, True)
        finished_at = time.perf_counter()
        await first.process_response(None, None, None)
        await asyncio.wait_for(started.wait(), 0.1)
        await asyncio.sleep(wait_before_next)
        second = generator_cls.load_lazy_data(generator, SimpleNamespace(data_index=1))
        next_body = await asyncio.wait_for(
            second.to_request_body("model", 8, True, True), 0.2
        )
        returned_at = time.perf_counter()
        assert not release.is_set()
        release.set()
        await asyncio.gather(*module._warmup_tasks)
        return next_body, finished_at, returned_at

    next_body, finished_at, returned_at = asyncio.run(conversation())
    assert len(warmups) == 1
    body, session_id, round_index, warmed_at = warmups[0]
    assert session_id == "session-1"
    assert round_index == 1
    assert [message["role"] for message in body["messages"]] == [
        "user",
        "assistant",
        "user",
    ]
    assert body["messages"][-2]["content"] == "answer"
    assert body["messages"][-1]["content"] == " "
    assert next_body["messages"][-1]["content"] == (
        "Continue the previous task. Answer the next step concisely."
    )
    assert returned_at - finished_at >= 0.05
    if wait_before_next:
        assert returned_at - finished_at < 0.10
    assert warmed_at - finished_at < 0.05
