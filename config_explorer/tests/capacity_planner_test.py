"""
Tests Capacity Planner functions
"""

import math
import pytest
from src.config_explorer.capacity_planner import *

# ---- Constants ----
precision_types = ["fp32", "fp16", "fp8", "int4"]
small_model_id = "repo/small-model"
qwen_model = "Qwen/Qwen3-0.6B"
llama_model = "meta-llama/Llama-3.1-8B-Instruct"
deepseek3 = "deepseek-ai/DeepSeek-V3.1"
gpt_oss = "openai/gpt-oss-20b"
redhat_qwen = "RedHatAI/Qwen3-8B-FP8-dynamic"
redhat_nemotron = "redhatai/nvidia-nemotron-nano-9b-v2-fp8-dynamic"

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
    assert rounded == 1.06812


def test_max_concurrent_req():
    """
    Tests that max concurrent request is estimated correctly given model and GPU spec
    """

    model_info = get_model_info_from_hf(qwen_model)
    model_config = get_model_config_from_hf(qwen_model)
    model_memory = model_memory_req(model_info, model_config)
    max_model_len = 10000
    batch_size = 1
    gpu_mem = 40
    gpu_util = 1
    per_req_kv_cache_req = kv_cache_req(model_info, model_config, context_len=max_model_len)

    # Test a subset of parallelism configurations for reasonable test runtime
    test_configs = [
        (1, 1, 1), (2, 1, 1), (1, 2, 1), (1, 1, 2),
        (2, 2, 1), (4, 1, 1), (8, 1, 1), (4, 2, 2)
    ]

    for tp, pp, dp in test_configs:
        gpu_count = tp * pp * dp

        # Calculate allocatable KV cache memory using the implementation's logic
        allocatable_kv = allocatable_kv_cache_memory(
            model_info,
            model_config,
            gpu_mem,
            gpu_util,
            tp,
            pp,
            dp,
            max_model_len=max_model_len,
            batch_size=batch_size
        )

        # Calculate expected max concurrent requests
        if per_req_kv_cache_req == 0:
            expected = 0
        else:
            expected = max(0, math.floor(allocatable_kv / per_req_kv_cache_req))

        # Get actual max concurrent requests
        actual_max_concurrent_req = max_concurrent_requests(
            model_info,
            model_config,
            max_model_len=max_model_len,
            gpu_memory=gpu_mem,
            gpu_mem_util=gpu_util,
            batch_size=batch_size,
            tp=tp,
            pp=pp,
            dp=dp,
        )

        assert actual_max_concurrent_req == expected, \
            f"Failed for tp={tp}, pp={pp}, dp={dp}: expected {expected}, got {actual_max_concurrent_req}"


def test_total_kv_cache_blocks(monkeypatch):
    """
    Tests that total KV cache blocks are estimated correctly given model and GPU configuration.
    """

    known_model = "Qwen/Qwen2.5-0.5B"
    # Load lightweight GQA model for reproducibility
    model_info = get_model_info_from_hf(known_model)
    model_config = get_model_config_from_hf(known_model)

    # Reference parameters
    context_len = 32768
    gpu_mem = 80  # GB
    gpu_util = 0.9

    # Compute expected per-block memory
    kv_cache_detail = KVCacheDetail(model_info, model_config, context_len)
    estimated_per_token_memory = kv_cache_detail.per_token_memory_bytes

    ## per token memory
    num_layers = model_config.num_hidden_layers
    precision_in_bytes = precision_to_byte(inference_dtype(model_config))
    head_dimension = getattr(model_config, "head_dim", model_config.hidden_size / model_config.num_attention_heads)
    kv_heads = model_config.num_key_value_heads

    actual_per_token_memory = num_layers * 2 * head_dimension * kv_heads * precision_in_bytes

    assert estimated_per_token_memory == actual_per_token_memory

    # Mock allocatable_kv_cache_memory depending on tp, pp for know values of qwen
    def fake_allocatable_kv_cache_memory(model_info, model_config,
                                         gpu_memory, gpu_mem_util,
                                         tp, pp, dp,
                                         max_model_len=None, batch_size=1):
        if tp == 1:
            return 68.89 # observed in experiments
        elif tp == 2:
            return 68.09 # observed in experiments

    monkeypatch.setattr(
        "src.config_explorer.capacity_planner.allocatable_kv_cache_memory",
        fake_allocatable_kv_cache_memory
    )
    ## tp = 1
    actual_blocks = total_kv_cache_blocks(
        model_info=model_info,
        model_config=model_config,
        context_len=context_len,
        gpu_memory=gpu_mem,
        gpu_mem_util=gpu_util,
    )

    assert actual_blocks == 376231

    ## tp = 2
    actual_blocks = total_kv_cache_blocks(
        model_info=model_info,
        model_config=model_config,
        context_len=context_len,
        gpu_memory=gpu_mem,
        gpu_mem_util=gpu_util,
        tp = 2
    )

    assert actual_blocks == 743724

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
    # Import not needed since we're using 'from src.config_explorer.capacity_planner import *'
    # The functions are already available: estimate_vllm_activation_memory,
    # estimate_vllm_cuda_graph_memory, estimate_vllm_non_torch_memory

    model_info = get_model_info_from_hf(qwen_model)
    model_config = get_model_config_from_hf(qwen_model)
    model_memory = model_memory_req(model_info, model_config)

    gpu_memory = 40
    gpu_util = 1
    max_model_len = 2048
    batch_size = 1

    for tp in range(1, 16):
        for pp in range(1, 16):
            for dp in range(1, 16):

                # Expected calculation with new memory components
                gpu_count = tp * pp * dp
                available_memory = gpu_count * gpu_memory * gpu_util
                model_size = model_memory * dp

                # Calculate activation and overhead memory
                # Activation memory must be multiplied by dp since each
                # data parallel replica needs its own activation memory
                activation_memory = estimate_vllm_activation_memory(
                    model_config, max_model_len, batch_size, tp
                ) * dp
                cuda_graph_memory = estimate_vllm_cuda_graph_memory() * gpu_count
                non_torch_memory = estimate_vllm_non_torch_memory(tp) * gpu_count

                expected = max(0, available_memory - model_size - activation_memory -
                             cuda_graph_memory - non_torch_memory)

                actual = allocatable_kv_cache_memory(
                    model_info,
                    model_config,
                    gpu_memory,
                    gpu_util,
                    tp,
                    pp,
                    dp,
                    max_model_len=max_model_len,
                    batch_size=batch_size
                )

                assert abs(expected - actual) < 0.01, f"Expected {expected}, got {actual}"

def test_is_moe():
    """Asserts that MoE models can be determined"""

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
    """Tests that experts per GPU is calculated correctly for MoE models"""

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
    """Tests head dimension field for models that don't have them"""
    mistral = "mistralai/Mixtral-8x7B-Instruct-v0.1"
    model_config = get_model_config_from_hf(mistral)
    model_info = get_model_info_from_hf(mistral)
    kv_cache_detail = KVCacheDetail(model_info, model_config)

    assert kv_cache_detail.head_dimension != None

def test_not_mla():
    """Verify MLA attention check"""
    qwen = "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
    model_config = get_model_config_from_hf(qwen)
    model_info = get_model_info_from_hf(qwen_model)
    kv_cache_detail = KVCacheDetail(model_info, model_config)
    assert kv_cache_detail.attention_type != AttentionType.MLA

def test_get_quant_method():
    """Tests getting quant method for models"""

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
    """Tests that the byte requirement for the quant method can be fetched"""

    model_to_quant_bytes = {
        gpt_oss: 4.25 / 8,      # mxfp4
        redhat_qwen: 1,         # num_bits: 8
        deepseek3: 1,           # fp8
    }

    for model, expected in model_to_quant_bytes.items():
        model_config = get_model_config_from_hf(model)
        assert get_quant_bytes(model_config) == expected

def test_inference_dtype():
    """Tests that inference dtype can be determined for quantized and unquantized models"""

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

def test_inference_dtype_byte():
    """Tests that inference dtype byte can be determined for quantized and unquantized models"""

    model_to_dtype_byte = {
        # quantized
        gpt_oss: 4.25 / 8,
        redhat_qwen: 2,
        redhat_nemotron: 2,

        # unquantized
        qwen_model: 2,
        deepseek3: 2,
    }

    for model, expceted in model_to_dtype_byte.items():
        model_config = get_model_config_from_hf(model)
        assert inference_dtype_byte(model_config) == expceted

def test_estimate_vllm_non_torch_memory():
    """Tests that non-torch memory estimation returns TP-dependent values"""
    # TP=1: 0.15 GiB
    actual_tp1 = estimate_vllm_non_torch_memory(tp=1)
    expected_tp1 = 0.15
    assert actual_tp1 == expected_tp1, f"Expected {expected_tp1} GiB for TP=1, got {actual_tp1} GiB"
    assert isinstance(actual_tp1, float), "Should return a float"

    # TP>=2: 0.6 GiB
    actual_tp2 = estimate_vllm_non_torch_memory(tp=2)
    expected_tp2 = 0.6
    assert actual_tp2 == expected_tp2, f"Expected {expected_tp2} GiB for TP=2, got {actual_tp2} GiB"

    actual_tp4 = estimate_vllm_non_torch_memory(tp=4)
    assert actual_tp4 == expected_tp2, f"Expected {expected_tp2} GiB for TP=4, got {actual_tp4} GiB"

def test_estimate_vllm_cuda_graph_memory():
    """Tests that CUDA graph memory returns 0.0 (included in activation memory)"""
    expected = 0.0  # CUDA graph memory is included in activation profiling
    actual = estimate_vllm_cuda_graph_memory()
    assert actual == expected, f"Expected {expected} GiB, got {actual} GiB"
    assert isinstance(actual, float), "Should return a float"

def test_estimate_vllm_activation_memory_basic():
    """Tests activation memory estimation for basic scenarios"""
    model_config = get_model_config_from_hf(qwen_model)

    # Test basic case with seq_len=2048, batch_size=1, tp=1
    seq_len = 2048
    batch_size = 1
    tp = 1

    activation_mem = estimate_vllm_activation_memory(model_config, seq_len, batch_size, tp)

    # Should return a positive float
    assert isinstance(activation_mem, float), "Should return a float"
    assert activation_mem > 0, f"Activation memory should be positive, got {activation_mem}"

    # For a small model like Qwen 0.6B, activation memory should be reasonable (< 10 GB)
    assert activation_mem < 10.0, f"Activation memory seems too high: {activation_mem} GiB"

def test_estimate_vllm_activation_memory_zero_seq_len():
    """Tests that activation memory is zero for zero sequence length"""
    model_config = get_model_config_from_hf(qwen_model)

    activation_mem = estimate_vllm_activation_memory(model_config, seq_len=0, batch_size=1, tp=1)
    assert activation_mem == 0.0, f"Expected 0.0 for zero seq_len, got {activation_mem}"

def test_estimate_vllm_activation_memory_constant_with_tp():
    """Tests that activation memory does NOT scale with tensor parallelism (empirical behavior)"""
    model_config = get_model_config_from_hf(qwen_model)
    seq_len = 2048
    batch_size = 1

    # Get activation memory for different TP values
    mem_tp1 = estimate_vllm_activation_memory(model_config, seq_len, batch_size, tp=1)
    mem_tp2 = estimate_vllm_activation_memory(model_config, seq_len, batch_size, tp=2)
    mem_tp4 = estimate_vllm_activation_memory(model_config, seq_len, batch_size, tp=4)

    # Empirical observation: activation memory is constant regardless of TP
    # (Llama-70B TP=1 would have ~4.8 GiB, TP=2 shows 4.84 GiB per GPU)
    # The formula uses a constant base value that doesn't scale with TP
    assert mem_tp1 == mem_tp2, f"TP=1 memory ({mem_tp1}) should equal TP=2 memory ({mem_tp2})"
    assert mem_tp2 == mem_tp4, f"TP=2 memory ({mem_tp2}) should equal TP=4 memory ({mem_tp4})"

def test_estimate_vllm_activation_memory_scales_with_batch_size():
    """Tests that activation memory scales linearly with batch size"""
    model_config = get_model_config_from_hf(qwen_model)
    seq_len = 2048
    tp = 1

    mem_batch1 = estimate_vllm_activation_memory(model_config, seq_len, batch_size=1, tp=tp)
    mem_batch2 = estimate_vllm_activation_memory(model_config, seq_len, batch_size=2, tp=tp)
    mem_batch4 = estimate_vllm_activation_memory(model_config, seq_len, batch_size=4, tp=tp)

    # Should scale roughly linearly with batch size (within 20% due to safety margin and fixed buffers)
    assert mem_batch2 > mem_batch1, "Batch=2 memory should be > Batch=1 memory"
    assert mem_batch4 > mem_batch2, "Batch=4 memory should be > Batch=2 memory"

    # Check approximate linear scaling
    # Note: The ratio is close to but not exactly 2x/4x due to the 10% safety margin
    # applied to the total activation memory (see ACTIVATION_MEMORY_SAFETY_MARGIN).
    # This margin accounts for PyTorch memory fragmentation and is proportional to
    # the subtotal, so it slightly dampens the scaling ratio.
    ratio_2_to_1 = mem_batch2 / mem_batch1
    ratio_4_to_1 = mem_batch4 / mem_batch1

    # Should be between 1.5 and 2.5 for doubling, 3.0 and 4.5 for quadrupling
    assert 1.5 < ratio_2_to_1 < 2.5, f"Batch size scaling seems off: {ratio_2_to_1}"
    assert 3.0 < ratio_4_to_1 < 4.5, f"Batch size scaling seems off: {ratio_4_to_1}"

def test_estimate_vllm_activation_memory_scales_with_seq_len():
    """Tests that activation memory scales linearly with sequence length"""
    model_config = get_model_config_from_hf(qwen_model)
    batch_size = 1
    tp = 1

    mem_seq1024 = estimate_vllm_activation_memory(model_config, seq_len=1024, batch_size=batch_size, tp=tp)
    mem_seq2048 = estimate_vllm_activation_memory(model_config, seq_len=2048, batch_size=batch_size, tp=tp)
    mem_seq4096 = estimate_vllm_activation_memory(model_config, seq_len=4096, batch_size=batch_size, tp=tp)

    assert mem_seq2048 > mem_seq1024, "Longer sequence should use more memory"
    assert mem_seq4096 > mem_seq2048, "Longer sequence should use more memory"

    # Check approximate linear scaling
    # Note: The ratio is close to but not exactly 2x due to the 10% safety margin
    # applied to the total activation memory (see ACTIVATION_MEMORY_SAFETY_MARGIN).
    # This margin accounts for PyTorch memory fragmentation and is proportional to
    # the subtotal, so it slightly dampens the scaling ratio.
    ratio_2048_to_1024 = mem_seq2048 / mem_seq1024
    ratio_4096_to_2048 = mem_seq4096 / mem_seq2048

    # Should be between 1.8 and 2.2 (approximately 2x, with some slack for the safety margin)
    assert 1.8 < ratio_2048_to_1024 < 2.2, f"Sequence length scaling seems off: {ratio_2048_to_1024}"
    assert 1.8 < ratio_4096_to_2048 < 2.2, f"Sequence length scaling seems off: {ratio_4096_to_2048}"

def test_estimate_vllm_activation_memory_validation():
    """Tests that activation memory estimation validates parameters correctly"""
    model_config = get_model_config_from_hf(qwen_model)

    # Test invalid TP (zero and negative)
    with pytest.raises(ValueError, match="Tensor parallelism must be positive"):
        estimate_vllm_activation_memory(model_config, seq_len=2048, batch_size=1, tp=0)

    with pytest.raises(ValueError, match="Tensor parallelism must be positive"):
        estimate_vllm_activation_memory(model_config, seq_len=2048, batch_size=1, tp=-1)

    # Test negative sequence length
    with pytest.raises(ValueError, match="Sequence length cannot be negative"):
        estimate_vllm_activation_memory(model_config, seq_len=-1, batch_size=1, tp=1)

    # Test negative batch size
    with pytest.raises(ValueError, match="Batch size cannot be negative"):
        estimate_vllm_activation_memory(model_config, seq_len=2048, batch_size=-1, tp=1)

def test_estimate_vllm_activation_memory_formula():
    """Tests that activation memory calculation matches the empirical formula"""
    model_config = get_model_config_from_hf(qwen_model)
    seq_len = 2048
    batch_size = 2
    tp = 1

    # Get the actual result
    actual_mem_gib = estimate_vllm_activation_memory(model_config, seq_len, batch_size, tp)

    # Calculate expected value using the empirical formula
    # base_constant * (seq_len / REFERENCE_SEQ_LEN) * batch_size
    ACTIVATION_MEMORY_BASE_DENSE_GIB = 5.5  # Qwen is a dense model
    ACTIVATION_REFERENCE_SEQ_LEN = 16000

    seq_len_factor = seq_len / ACTIVATION_REFERENCE_SEQ_LEN
    batch_size_factor = batch_size
    expected_mem_gib = ACTIVATION_MEMORY_BASE_DENSE_GIB * seq_len_factor * batch_size_factor

    # Allow small floating point differences
    assert abs(actual_mem_gib - expected_mem_gib) < 0.001, \
        f"Formula mismatch: expected {expected_mem_gib} GiB, got {actual_mem_gib} GiB"

def test_estimate_vllm_activation_memory_empirical_validation():
    """Tests activation memory estimates against empirical vLLM measurements"""
    # Test parameters matching empirical data: seq_len=16000, batch_size=1, tp varies
    seq_len = 16000
    batch_size = 1

    # Test case 1: Qwen3-0.6B (dense, TP=1)
    # Empirical: 5.56 GiB, Expected with base 5.5: 5.5 GiB
    qwen_config = get_model_config_from_hf(qwen_model)
    qwen_activation = estimate_vllm_activation_memory(qwen_config, seq_len, batch_size, tp=1)
    assert 5.0 <= qwen_activation <= 6.0, \
        f"Qwen3-0.6B activation {qwen_activation} GiB outside expected range [5.0, 6.0] (empirical: 5.56)"

    # Test case 2: Llama-3.1-8B (dense, TP=1)
    # Empirical: 4.76 GiB, Expected with base 5.5: 5.5 GiB (conservative overestimate)
    llama_config = get_model_config_from_hf(llama_model)
    llama_activation = estimate_vllm_activation_memory(llama_config, seq_len, batch_size, tp=1)
    assert 4.5 <= llama_activation <= 6.0, \
        f"Llama-3.1-8B activation {llama_activation} GiB outside expected range [4.5, 6.0] (empirical: 4.76)"

    # Test case 3: TP=2 should give same result as TP=1 (empirical observation)
    # Llama-70B TP=2: 4.84 GiB (similar to TP=1 estimates)
    llama_activation_tp2 = estimate_vllm_activation_memory(llama_config, seq_len, batch_size, tp=2)
    assert llama_activation == llama_activation_tp2, \
        f"Activation memory should be constant with TP: TP=1 {llama_activation} vs TP=2 {llama_activation_tp2}"