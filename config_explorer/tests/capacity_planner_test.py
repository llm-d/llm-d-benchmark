"""
Tests Capacity Planner functions
"""

import pytest
from src.config_explorer.capacity_planner import *

# ---- Constants ----
precision_types = ["fp32", "fp16", "fp8", "int4"]
small_model_id = "repo/small-model"
qwen_model = "Qwen/Qwen3-0.6B"
deepseek3 = "deepseek-ai/DeepSeek-V3.1"
gpt_oss = "openai/gpt-oss-20b"
redhat_qwen = "RedHatAI/Qwen3-8B-FP8-dynamic"

def test_get_model_info_and_config_from_hf():
    """
    Tests that model info can be retrieved without error for open-sourced models
    """

    model_info = get_model_info_from_hf(qwen_model)
    model_config = get_model_config_from_hf(qwen_model)

    assert hasattr(model_info, "id")
    assert hasattr(model_info, "safetensors")
    assert hasattr(model_config, "max_position_embeddings")

    # Try text config
    # For qwen, it's the same
    assert model_config.to_dict() == get_text_config(model_config).to_dict()

    # For mistral, it's different
    msitral = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
    model_config = get_model_config_from_hf(msitral)
    text_config = get_text_config(model_config)

    assert model_config.to_dict() != text_config.to_dict()

    # Try facebook model which is smaller
    facebook = "facebook/opt-125m"
    model_info = get_model_info_from_hf(facebook)
    model_config = get_model_config_from_hf(facebook)

    assert hasattr(model_info, "id")
    assert hasattr(model_info, "safetensors")
    assert hasattr(model_config, "max_position_embeddings")


def test_model_total_params():
    """
    Tests that model total params is fetched successfully
    """
    model_info = get_model_info_from_hf(qwen_model)

    # Num params from https://huggingface.co/Qwen/Qwen3-0.6B
    assert model_total_params(model_info) == 751632384

def test_precision_to_byte():
    """
    Tests that precision data type is converted to byte accurately
    """

    bytes_8 = ["F64", "I64", "INT64"]
    bytes_4 = ["F32", "I32", "INT32"]
    bytes_2 = ["F16", "BF16", "I16", "INT16"]
    bytes_1 = ["F8_E5M2", "F8_E4M3", "I8", "INT8", "U8"]
    bytes_half = ["FP4", "U4", "I4", "INT4"]
    boolean = ["BOOL"]

    for dtype in bytes_8:
        assert precision_to_byte(dtype) == 8

    for dtype in bytes_4:
        assert precision_to_byte(dtype) == 4

    for dtype in bytes_2:
        assert precision_to_byte(dtype) == 2

    for dtype in bytes_1:
        assert precision_to_byte(dtype) == 1

    for dtype in bytes_half:
        assert precision_to_byte(dtype) == 0.5

    for dtype in boolean:
        assert precision_to_byte(dtype) == 1

    # Special cases
    assert precision_to_byte("f64") == 8
    assert precision_to_byte("ff8_e5m2") == 1

def test_parameter_memory_req():
    """
    Tests parameter memory size is accurately calculated given precision
    """

    factor = 1024 ** 3
    params = [10, 1000, 10000, 100000]
    precisions = ["FP32", "FP16", "FP8", "INT4"]
    prec_to_byte = [4, 2, 1, 0.5]

    for param in params:
        for j, precision in enumerate(precisions):

            expected = param * prec_to_byte[j] / factor
            assert parameter_memory_req(param, precision) == expected

def test_model_memory_req():
    """
    Tests model memory can be correctly estimated
    """

    # GQA model
    model_info = get_model_info_from_hf(qwen_model)
    model_config = get_model_config_from_hf(qwen_model)
    assert model_memory_req(model_info, model_config) == 1.4000244140625

    # MLA model
    model_info = get_model_info_from_hf(deepseek3)
    model_config = get_model_config_from_hf(deepseek3)
    assert model_memory_req(model_info, model_config) == 641.2852922081947

    # MXFP4 model
    model_info = get_model_info_from_hf(gpt_oss)
    model_config = get_model_config_from_hf(gpt_oss)
    assert model_memory_req(model_info, model_config) == 13.111648678779602

    # No param info for facebook/opt-125m
    with pytest.raises(Exception):
        hf_model = "facebook/opt-125m"
        model_info = get_model_info_from_hf(hf_model)
        model_config = get_model_config_from_hf(hf_model)
        model_memory_req(model_info, model_config)


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
    model_info = get_model_info_from_hf(qwen_model)
    model_config = get_model_config_from_hf(qwen_model)

    # For context length = 0, kv cache req is 0
    actual_kv_cache_req = kv_cache_req(model_info, model_config, context_len=0)
    assert actual_kv_cache_req == 0

    # For context length = 10000
    actual_kv_cache_req = kv_cache_req(model_info, model_config, context_len=10000)
    rounded = round(actual_kv_cache_req, 5)
    assert rounded == 0.53406


def test_max_concurrent_req():
    """
    Tests that max concurrent request is estimated correctly given model and GPU spec
    """

    # This model does not take up 40GB GPU, so model size is negligible
    model_info = get_model_info_from_hf(qwen_model)
    model_config = get_model_config_from_hf(qwen_model)
    model_memory = model_memory_req(model_info, model_config)
    per_req_kv_cache_req = kv_cache_req(model_info, model_config, context_len=10000)

    for tp in range(1, 16):
        for pp in range(1, 16):
            for dp in range(1, 16):
                avail_gpu_count = tp * pp * dp
                gpu_mem = 40
                actual_max_concurrent_req = max_concurrent_requests(model_info,
                                                            model_config,
                                                            max_model_len=10000,
                                                            gpu_memory=gpu_mem,
                                                            gpu_mem_util=1,
                                                            tp=tp,
                                                            pp=pp,
                                                            dp=dp,
                                                            )


                expected = math.floor((avail_gpu_count * gpu_mem - model_memory * dp) / per_req_kv_cache_req)
                if expected < 0:
                    expected = 0

        assert actual_max_concurrent_req == expected

def test_find_possible_tp():
    """
    Tests the possible TP sizes are accurately calculated
    """

    model_config = get_model_config_from_hf(qwen_model)
    assert find_possible_tp(model_config) == [1, 2, 4, 8, 16]

    deepseek = "deepseek-ai/DeepSeek-R1"
    model_config = get_model_config_from_hf(deepseek)
    assert find_possible_tp(model_config) == [1, 2, 4, 8, 16, 32, 64, 128]

def test_gpus_required():
    """
    Tests GPU number required for parallelism is correctly calculated
    """

    for tp in range(1, 16):
        for pp in range(1, 16):
            for dp in range(1, 16):

                expected = tp * pp * dp
                assert expected == gpus_required(tp, pp, dp)

def test_allocatable_kv_cache_memory():
    """
    Tests allocatable kv cache memory is correctly calculated
    """

    model_info = get_model_info_from_hf(qwen_model)
    model_config = get_model_config_from_hf(qwen_model)
    model_memory = model_memory_req(model_info, model_config)

    gpu_memory = 40
    gpu_util = 1

    for tp in range(1, 16):
        for pp in range(1, 16):
            for dp in range(1, 16):

                # Expected
                gpu_count = tp * pp * dp
                expected = gpu_count * gpu_memory - model_memory * dp

                actual = allocatable_kv_cache_memory(
                    model_info,
                    model_config,
                    gpu_memory,
                    gpu_util,
                    tp,
                    pp,
                    dp
                )

                assert expected == actual

def test_is_moe():
    """
    Asserts that MOE models can be determined
    """

    moes = [
        "deepseek-ai/DeepSeek-R1",
        "deepseek-ai/DeepSeek-V3.1"
    ]

    non_moes = [
        qwen_model,
        "RedHatAI/Llama-3.3-70B-Instruct-FP8-dynamic"
    ]

    for model in moes:
        model_config = get_model_config_from_hf(model)
        assert is_moe(model_config) == True

    for model in non_moes:
        model_config = get_model_config_from_hf(model)
        assert is_moe(model_config) == False

def test_get_num_experts():
    """
    Tests that number of experts is fetched correctly
    """
    model_to_experts = {
        "deepseek-ai/DeepSeek-R1": 256,
        "deepseek-ai/DeepSeek-V3.1-Base": 256,
        "deepseek-ai/DeepSeek-V3.1": 256,
        "Qwen/Qwen3-235B-A22B-Thinking-2507": 128,
        "Qwen/Qwen3-235B-A22B-FP8": 128
    }

    for model, expected_experts in model_to_experts.items():
        model_config = get_model_config_from_hf(model)

        assert get_num_experts(model_config) == expected_experts

def test_experts_per_gpu():
    """
    Tests that experts per GPU is calculated correctly for MOE models
    """

    moe_models = {
        "deepseek-ai/DeepSeek-R1",
        "deepseek-ai/DeepSeek-V3.1-Base",
        "deepseek-ai/DeepSeek-V3.1",
        "Qwen/Qwen3-235B-A22B-Thinking-2507",
        "Qwen/Qwen3-235B-A22B-FP8"
    }

    for model in moe_models:
        model_config = get_model_config_from_hf(model)
        experts = get_num_experts(model_config)

        for tp in range(1, 16):
            for dp in range(1, 16):
                assert experts / (tp * dp) == experts_per_ep_group(model_config, tp, dp)

def test_head_dim_none():
    """
    Tests head dimension field for models that don't have them
    """
    mistral = "mistralai/Mixtral-8x7B-Instruct-v0.1"
    model_config = get_model_config_from_hf(mistral)
    model_info = get_model_info_from_hf(mistral)
    kv_cache_detail = KVCacheDetail(model_info, model_config)

    assert kv_cache_detail.head_dimension != None

def test_not_mla():
    """
    Verify MLA attentin check
    """
    qwen = "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
    model_config = get_model_config_from_hf(qwen)
    model_info = get_model_info_from_hf(qwen_model)
    kv_cache_detail = KVCacheDetail(model_info, model_config)
    assert kv_cache_detail.attention_type != AttentionType.MLA

def test_get_quant_method():
    """
    Tests getting quant method for models
    """

    model_to_quant_method = {
        gpt_oss: "mxfp4",
        redhat_qwen: "compressed-tensors",
        deepseek3: "fp8",
        qwen_model: "",
    }

    for model, expected in model_to_quant_method.items():
        model_config = get_model_config_from_hf(model)
        assert get_quant_method(model_config) == expected

def test_get_quant_bytes():
    """
    Tests that the byte requirement for the quant method can be fetched
    """

    model_to_quant_bytes = {
        gpt_oss: 4.25 / 8,      # mxfp4
        redhat_qwen: 1,         # num_bits: 8
        deepseek3: 1,           # fp8
    }

    for model, expected in model_to_quant_bytes.items():
        model_config = get_model_config_from_hf(model)
        assert get_quant_bytes(model_config) == expected

def test_inference_dtype():
    """
    Tests that inference dtype can be determined for quantized and unquantized models
    """

    model_to_dtype = {
        # quantized
        gpt_oss: "mxfp4",
        redhat_qwen: "bfloat16",
        "RedHatAI/Meta-Llama-3.1-8B-Instruct-FP8-dynamic": "bfloat16",

        # unquantized
        qwen_model: "bfloat16",
        deepseek3: "bfloat16",
    }

    for model, expceted in model_to_dtype.items():
        model_config = get_model_config_from_hf(model)
        assert inference_dtype(model_config) == expceted

