"""
Tests Capacity Planner functions
"""

from huggingface_hub import ModelInfo
from transformers import AutoConfig
from src.config_explorer.capacity_planner import *

# ---- Constants ----

precision_types = ["fp32", "fp16", "fp8", "int4"]
small_model_id = "repo/small-model"

def test_model_memory_req():
    """
    Tests that model memory is correct calculated for the precision type
    """
    for precision_type in precision_types:

        # Start with 5 million parameters
        parameter = 5000000

        # Go up to 500 billion parameters
        # 500 billion = 500,000,000,000
        for _ in range(5):
            model_info = ModelInfo(
                id=small_model_id,
                safetensors={
                    "parameters": {
                        precision_type: parameter,
                    },
                    "total": parameter
                }
            )

            actual_model_memory = model_memory_req(model_info)
            expected_model_memory = 0
            match precision_type:
                case "fp32":
                    expected_model_memory = (parameter * 4 * 1.2) / (1024 ** 3)
                case "fp16":
                    expected_model_memory = (parameter * 2 * 1.2) / (1024 ** 3)
                case "fp8":
                    expected_model_memory = (parameter * 1 * 1.2) / (1024 ** 3)
                case "int4":
                    expected_model_memory = (parameter * 0.5 * 1.2) / (1024 ** 3)

            assert actual_model_memory == expected_model_memory
            parameter *= 10

def test_gpu_min_req():
    """
    Tests that the minimum GPU required is correctly calculated
    """
    # Start with 5 million parameters
    parameter = 5000000

    # Go up to 500 billion parameters
    # 500 billion = 500,000,000,000
    for _ in range(5):
        model_info = ModelInfo(
            id=small_model_id,
            safetensors={
                "parameters": {
                    precision_types[0]: parameter,
                },
                "total": parameter
            }
        )

        # Start with gpu_memory=10, increment by 10 each time, up to 200
        for gpu_memory in range(10, 200, 10):
            model_memory = (parameter * 4 * 1.2) / (1024 ** 3)
            actual_min_gpu_req = min_gpu_req(model_info, gpu_memory)
            expected_min_gpu_req = math.ceil(model_memory / gpu_memory)
            assert actual_min_gpu_req == expected_min_gpu_req

def test_kv_cache_req():
    """
    Tests KV cache is estimated correctly
    """

    # Assert deepseek is calculated correctly for context length of 10000
    deepseek_mlas = {
        "deepseek-ai/DeepSeek-V3": 0.65446,
        "deepseek-ai/DeepSeek-V2": 0.64373,
        "deepseek-ai/DeepSeek-V2-Chat": 0.64373,
        "deepseek-ai/DeepSeek-R1": 0.65446,
        "deepseek-ai/DeepSeek-R1-Zero": 0.65446,
    }

    for deepseek, actual_kv_cache in deepseek_mlas.items():
        model_info = get_model_info_from_hf(deepseek)
        model_config = get_model_config_from_hf(deepseek)

        # For context length = 0, kv cache req is 0
        actual_kv_cache_req = kv_cache_req(model_info, model_config, context_len=0)
        assert actual_kv_cache_req == 0

        # For context length = 10000
        actual_kv_cache_req = kv_cache_req(model_info, model_config, context_len=10000)
        rounded = round(actual_kv_cache_req, 5)
        assert rounded == actual_kv_cache


    # Assert other models
    qwen3 = "Qwen/Qwen3-0.6B"
    model_info = get_model_info_from_hf(qwen3)
    model_config = get_model_config_from_hf(qwen3)

    # For context length = 0, kv cache req is 0
    actual_kv_cache_req = kv_cache_req(model_info, model_config, context_len=0)
    assert actual_kv_cache_req == 0

    # For context length = 10000
    actual_kv_cache_req = kv_cache_req(model_info, model_config, context_len=10000)
    rounded = round(actual_kv_cache_req, 5)
    assert rounded == 1.06812


def test_max_concurrent_req():
    """
    Tests that max concurrent request is estimated correctly given model and GPU spec
    """

    # This model does not take up 40GB GPU, so model size is negligible
    qwen3 = "Qwen/Qwen3-0.6B"
    model_info = get_model_info_from_hf(qwen3)
    model_config = get_model_config_from_hf(qwen3)
    model_memory = model_memory_req(model_info)
    per_req_kv_cache_req = kv_cache_req(model_info, model_config, context_len=10000)

    for avail_gpu_count in range(0, 10):
        gpu_mem = 40
        actual_max_concurrent_req = max_concurrent_req(model_info,
                                                       model_config,
                                                       max_model_len=10000,
                                                       available_gpu_count=avail_gpu_count,
                                                       gpu_memory=gpu_mem
                                                       )

        expected = math.floor((avail_gpu_count * gpu_mem - model_memory) / per_req_kv_cache_req)
        if expected < 0:
            expected = 0

        assert actual_max_concurrent_req == expected
