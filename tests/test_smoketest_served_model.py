"""Tests for served-model detection used by the kustomize-mode smoketest.

In kustomize mode the model served by the deployment comes from the guide
manifests in the llm-d repo -- NOT from the (shared) benchmark scenario's
``model.name``. ``_served_model_from_args`` extracts the served model id
straight off the model server container's args so the smoketest asserts
against what is actually running.
"""

from __future__ import annotations

import pytest

from llmdbenchmark.smoketests.base import _served_model_from_args


@pytest.mark.parametrize(
    "args, expected",
    [
        # vLLM: `vllm serve <MODEL> --flags...`
        (["vllm", "serve", "Qwen/Qwen3-0.6B", "--port=8000"], "Qwen/Qwen3-0.6B"),
        # vLLM with command/args split (command holds `vllm serve`,
        # args start at the positional model) -- the XPU guide shape.
        (
            ["Qwen/Qwen3-0.6B", "--port=8000", "--dtype=float16"],
            "Qwen/Qwen3-0.6B",
        ),
        # SGLang `--model-path=<MODEL>`
        (["--model-path=Qwen/Qwen3-32B", "--tp=2"], "Qwen/Qwen3-32B"),
        # SGLang split form `--model-path <MODEL>`
        (["--model-path", "Qwen/Qwen3-32B"], "Qwen/Qwen3-32B"),
        # vLLM `--model=<MODEL>` form
        (["--model=meta-llama/Llama-3.1-8B"], "meta-llama/Llama-3.1-8B"),
        # No model token present
        (["--port=8000", "--dtype=float16"], None),
        ([], None),
    ],
)
def test_served_model_from_args(args, expected):
    assert _served_model_from_args(args) == expected
