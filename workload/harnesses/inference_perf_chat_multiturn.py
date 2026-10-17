from __future__ import annotations

import asyncio
import json
import os
from typing import Any


_PATCHED = False


def strip_thinking_content(content: str) -> str:
    start = content.find("<think>")
    if start < 0:
        end = content.find("</think>")
        if end >= 0:
            content = content[end + len("</think>") :]
            return content.replace("<think>", "").replace("</think>", "").lstrip()
        return content

    end = content.find("</think>", start + len("<think>"))
    if end < 0:
        return content[:start].rstrip()
    content = content[:start] + content[end + len("</think>") :]
    return content.replace("<think>", "").replace("</think>", "").lstrip()


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


def apply_patch() -> None:
    global _PATCHED
    if _PATCHED:
        return

    from pydantic import ConfigDict, Field

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

        @property
        def user_session(self):
            return LocalUserSession.get_instance(self.user_session_id)

        async def to_request_body(
            self, effective_model_name, max_tokens, ignore_eos, streaming
        ):
            self._session_context = await self.user_session.get_context(
                self.target_round
            )
            # Think time before a follow-up turn. inference-perf starts the
            # request clock after to_request_body returns and stops it after
            # process_response, so waiting here keeps it out of the latency.
            delay = _inter_turn_delay_seconds()
            if delay and self.target_round > 0:
                await asyncio.sleep(delay)
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
            self.user_session.update_context(self._session_context)
            return inference_info

        async def process_failure(
            self, response, config, tokenizer, exception, lora_adapter=None
        ):
            inference_info = InferenceInfo(
                request_metrics=RequestMetrics(text=Text(input_tokens=0))
            )
            self.update_inference_info(inference_info)
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
