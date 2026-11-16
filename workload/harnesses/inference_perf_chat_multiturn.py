from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import aiohttp
import yaml


_PATCHED = False
logger = logging.getLogger(__name__)
_warmup_tasks: set[asyncio.Task[None]] = set()


def strip_thinking_content(content: str) -> str:
    if "<think>" not in content:
        end = content.find("</think>")
        if end >= 0:
            return content[end + len("</think>") :].lstrip()
        return content

    visible = []
    while (start := content.find("<think>")) >= 0:
        visible.append(content[:start])
        content = content[start + len("<think>") :]
        end = content.find("</think>")
        if end < 0:
            return "".join(visible).rstrip()
        content = content[end + len("</think>") :]
    return ("".join(visible) + content).lstrip()


def extract_assistant_content(raw_response: str | None) -> str:
    if not raw_response:
        return ""

    chunks: list[str] = []
    for line in raw_response.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for choice in data.get("choices", []):
            delta = choice.get("delta") or {}
            message = choice.get("message") or {}
            content = delta.get("content") or message.get("content")
            if content:
                chunks.append(content)
    return strip_thinking_content("".join(chunks))


def _shared_prefix_module():
    try:
        from inference_perf.datagen.synthetic import shared_prefix_datagen

        return shared_prefix_datagen
    except ModuleNotFoundError:
        from inference_perf.datagen import shared_prefix_datagen

        return shared_prefix_datagen


def _api_type_chat(api_type: Any) -> bool:
    return getattr(api_type, "name", None) == "Chat" or str(api_type).endswith("Chat")


def _chat_extra_body() -> dict[str, Any]:
    raw = os.environ.get("LLMDBENCH_INFERENCE_PERF_CHAT_EXTRA_BODY")
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(
            "LLMDBENCH_INFERENCE_PERF_CHAT_EXTRA_BODY must be a JSON object"
        )
    return data


def _inter_turn_delay_seconds() -> float:
    raw = os.environ.get("LLMDBENCH_INFERENCE_PERF_INTER_TURN_DELAY_SECONDS")
    if not raw:
        return 0.0
    delay = float(raw)
    if delay < 0:
        raise ValueError(
            "LLMDBENCH_INFERENCE_PERF_INTER_TURN_DELAY_SECONDS must be non-negative"
        )
    return delay


def _client_side_warmup_enabled() -> bool:
    value = os.environ.get("LLMDBENCH_INFERENCE_PERF_CLIENT_SIDE_WARMUP", "false")
    if value not in ("true", "false"):
        raise ValueError(
            "LLMDBENCH_INFERENCE_PERF_CLIENT_SIDE_WARMUP must be true or false"
        )
    return value == "true"


def _schedule_warmup(body: dict[str, Any], session_id: str, round_index: int) -> None:
    task = asyncio.create_task(_warm_next_turn(body, session_id, round_index))
    _warmup_tasks.add(task)

    def finished(completed: asyncio.Task[None]) -> None:
        _warmup_tasks.discard(completed)
        if not completed.cancelled() and (error := completed.exception()) is not None:
            logger.error(
                "Client-side warm-up failed (session %s, turn %s)",
                session_id,
                round_index,
                exc_info=(type(error), error, error.__traceback__),
            )

    task.add_done_callback(finished)


async def _warm_next_turn(
    body: dict[str, Any], session_id: str, round_index: int
) -> None:
    warmup_body = {
        **body,
        "stream": False,
        "max_tokens": 1,
    }
    warmup_body.pop("stream_options", None)
    warmup_body.pop("min_tokens", None)
    warmup_body.pop("max_completion_tokens", None)
    with Path(os.environ["LLMDBENCH_INFERENCE_PERF_CONFIG_FILE"]).open(
        encoding="utf-8"
    ) as config_file:
        config = yaml.safe_load(config_file)
    endpoint = config["server"]["base_url"].rstrip("/")
    headers = {"Content-Type": "application/json"}
    if api_key := config["server"].get("api_key"):
        headers["Authorization"] = f"Bearer {api_key}"
    api_config = config["api"]
    for key, value in (api_config.get("headers") or {}).items():
        for existing in list(headers):
            if existing.lower() == key.lower():
                del headers[existing]
        headers[key] = value
    if session_header := api_config.get("session_id_header_key"):
        headers[session_header] = session_id
    start = time.perf_counter()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as client:
        try:
            async with client.post(
                f"{endpoint}/v1/chat/completions",
                json=warmup_body,
                headers=headers,
            ) as response:
                await response.read()
                status = response.status
                if status != 200:
                    logger.error(
                        "Client-side warm-up failed: HTTP %s (session %s, turn %s)",
                        status,
                        session_id,
                        round_index,
                    )
        except (aiohttp.ClientError, TimeoutError) as exc:
            status = None
            logger.error(
                "Client-side warm-up failed (session %s, turn %s): %s",
                session_id,
                round_index,
                exc,
            )
        elapsed = time.perf_counter() - start

    results = Path(os.environ["LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"])
    with (results / "client_side_warmup.jsonl").open("a", encoding="utf-8") as output:
        output.write(
            json.dumps(
                {
                    "session_id": session_id,
                    "chat_round": round_index,
                    "status": status,
                    "duration_seconds": elapsed,
                }
            )
            + "\n"
        )


def apply_patch() -> None:
    global _PATCHED
    if _PATCHED:
        return

    from pydantic import ConfigDict, Field, PrivateAttr

    try:
        from inference_perf.apis import (
            ChatCompletionAPIData,
            ChatMessage,
            InferenceInfo,
        )
    except ImportError:
        from inference_perf.apis.base import InferenceInfo
        from inference_perf.apis.chat import ChatCompletionAPIData, ChatMessage

    from inference_perf.apis.user_session import LocalUserSession
    from inference_perf.payloads import RequestMetrics, Text

    shared_prefix_datagen = _shared_prefix_module()
    SharedPrefixDataGenerator = shared_prefix_datagen.SharedPrefixDataGenerator
    original_load_lazy_data = SharedPrefixDataGenerator.load_lazy_data

    class UserSessionChatCompletionAPIData(ChatCompletionAPIData):
        model_config = ConfigDict(arbitrary_types_allowed=True)
        user_session_id: str = Field(exclude=True)
        target_round: int
        _request_body: dict[str, Any] = PrivateAttr(default_factory=dict)

        @property
        def user_session(self):
            return LocalUserSession.get_instance(self.user_session_id)

        async def to_request_body(
            self, effective_model_name, max_tokens, ignore_eos, streaming
        ):
            self._session_context = await self.user_session.get_context(
                self.target_round
            )
            messages = []
            if self.user_session.system_prompt:
                messages.append(
                    ChatMessage(role="system", content=self.user_session.system_prompt)
                )
            messages.extend(self.user_session.history)
            messages.extend(self.messages)
            self.messages = messages
            body = await super().to_request_body(
                effective_model_name, max_tokens, ignore_eos, streaming
            )
            body.update(_chat_extra_body())
            self._request_body = body
            # inference-perf starts the request clock after to_request_body returns.
            delay = _inter_turn_delay_seconds()
            if delay and self.target_round > 0:
                completed_at = getattr(
                    self.user_session, "_last_turn_completed_at", None
                )
                if completed_at is None:
                    raise RuntimeError("Previous chat turn has no completion time")
                await asyncio.sleep(
                    max(0.0, completed_at + delay - asyncio.get_running_loop().time())
                )
            return body

        def update_inference_info(self, inference_info) -> None:
            inference_info.extra_info["user_session"] = self.user_session_id
            inference_info.extra_info["chat_round"] = self.user_session._current_round

        async def process_response(
            self, response, config, tokenizer, lora_adapter=None
        ):
            inference_info = await super().process_response(
                response, config, tokenizer, lora_adapter
            )
            self.update_inference_info(inference_info)
            assistant_content = extract_assistant_content(
                inference_info.extra_info.get("raw_response")
            )
            self.user_session.history.extend(self.messages[-1:])
            if assistant_content:
                self.user_session.history.append(
                    ChatMessage(role="assistant", content=assistant_content)
                )
            if assistant_content and _client_side_warmup_enabled():
                warmup_body = {
                    **self._request_body,
                    "messages": [
                        *self._request_body["messages"],
                        {"role": "assistant", "content": assistant_content},
                        {"role": "user", "content": " "},
                    ],
                }
                _schedule_warmup(
                    warmup_body, self.user_session_id, self.user_session._current_round
                )
            self.user_session._last_turn_completed_at = (
                asyncio.get_running_loop().time()
            )
            self.user_session.update_context(self._session_context)
            return inference_info

        async def process_failure(
            self, response, config, tokenizer, exception, lora_adapter=None
        ):
            inference_info = InferenceInfo(
                request_metrics=RequestMetrics(text=Text(input_tokens=0))
            )
            self.update_inference_info(inference_info)
            self.user_session._last_turn_completed_at = (
                asyncio.get_running_loop().time()
            )
            self.user_session.update_context(self._session_context)
            return inference_info

    def load_lazy_data_with_chat_sessions(self, data):
        if not self.enable_multi_turn_chat or not _api_type_chat(self.api_config.type):
            return original_load_lazy_data(self, data)
        if self.prefix_multimodal or self.payload_multimodal:
            return original_load_lazy_data(self, data)

        prompt_index = data.data_index % len(self.prompts)
        user_id = data.data_index % len(self.user_sessions)
        round_index = data.data_index // len(self.user_sessions)
        prompt = self.prompts[prompt_index]

        initial_prompt_suffix = os.environ.get(
            "LLMDBENCH_SPEC_PREFILL_INITIAL_PROMPT_SUFFIX"
        )
        if round_index == 0 and initial_prompt_suffix:
            prompt = f"{prompt}\n\n{initial_prompt_suffix}"
        elif round_index > 0:
            prompt = os.environ.get(
                "LLMDBENCH_SPEC_PREFILL_FOLLOWUP_PROMPT",
                "Continue the previous task. Answer the next step concisely.",
            )

        return UserSessionChatCompletionAPIData(
            messages=[ChatMessage(role="user", content=prompt)],
            max_tokens=self.flat_output_lens[prompt_index],
            user_session_id=self.user_sessions[user_id].user_session_id,
            target_round=round_index,
        )

    SharedPrefixDataGenerator.load_lazy_data = load_lazy_data_with_chat_sessions
    _PATCHED = True
