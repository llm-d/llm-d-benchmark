"""
Capacity planner provides functionality to estimate the minimum number of GPUs required for loading model and KV cache
"""

import math
from functools import reduce
import re
from typing import List
from huggingface_hub import HfApi, ModelInfo
from transformers import AutoConfig, AutoModel

# Model
def get_model_info_from_hf(model_name: str, hf_token: str | None = None):
    """
    Fetches model info from HF, does not handle error
    """
    api = HfApi(token=hf_token)
    model_info = api.model_info(model_name)
    return model_info

def get_model_config_from_hf(model_name: str, hf_token: str=None) -> AutoConfig:
    """
    Returns LLM model config
    """

    model_config = AutoConfig.from_pretrained(
        model_name,
        trust_remote_code=True,
        token=hf_token or None,
    )

    return model_config

def get_text_config(model_config: AutoConfig) -> dict:
    """
    Returns text config (for LLMs)

    Some models nest LLM architecture inside 'text_config', some don't
    Compare https://huggingface.co/Qwen/Qwen3-0.6B/blob/main/config.json with https://huggingface.co/mistralai/Mistral-Small-3.2-24B-Instruct-2506/blob/main/config.json
    """

    if hasattr(model_config, "text_config"):
        model_config = model_config.text_config

    return model_config

def model_total_params(model_info: ModelInfo) -> int:
    """
    Returns the total parameters of the model
    """
    return model_info.safetensors.total

def max_context_len(model_config: AutoConfig) -> int:
    """
    Returns the max context length accepted by model
    """
    return model_config.max_position_embeddings

def __estimate_vllm_non_torch_memory() -> int:
    """
    Estimate non-torch memory consumption.
    Dummy function for now.
    """

    return 1

def __estimate_vllm_peak_memory(config: AutoConfig,
                              seq_len: int,
                              batch_size=1,
                              include_hidden=True):
    """
    Estimate peak activation memory for vLLM inference in bytes without running PyTorch.
    """
    num_layers = config.num_hidden_layers
    hidden_size = config.hidden_size
    num_heads = config.num_attention_heads
    head_dim = hidden_size // num_heads
    dtype_bytes = precision_to_byte(str(config.torch_dtype))

    # KV cache
    kv_bytes = 2 * num_layers * batch_size * num_heads * head_dim * seq_len * dtype_bytes

    # Hidden states
    hidden_bytes = batch_size * seq_len * hidden_size * dtype_bytes if include_hidden else 0

    total_bytes = kv_bytes + hidden_bytes
    return total_bytes

def precision_to_byte(precision: str) -> int:
    """
    Returns the byte requirement for a parameter for the highest precision of the model
    """

    mapping = {
        # Floating point
        "F64": 8,
        "F32": 4,
        "F16": 2,
        "BF16": 2,
        "F8_E5M2": 1,
        "F8_E4M3": 1,
        "FP4": 0.5,

        # Integers
        "I64": 8,
        "INT64": 8,
        "I32": 4,
        "INT32": 4,
        "I16": 2,
        "INT16": 2,
        "I8": 1,
        "INT8": 1,
        "U8": 1,
        "U4": 0.5,
        "I4": 0.5,
        "INT4": 0.5,

        # Boolean
        "BOOL": 1,  # stored as byte per element
    }

    if precision in mapping:
        return mapping[precision]
    else:
        # Try to infer the precision from the first whole number
        match = re.search(r"\d+", precision)
        if match:
            bits = int(match.group(0))
            if bits % 8 == 0:
                return bits // 8

    raise ValueError("Unsupported precision type.")

def parameter_memory_req(parameter: int, precision: str) -> float:
    """
    Calculates the memory requirement (in GiB) for the number of parameters for the specified precision
    """

    precision_byte = precision_to_byte(precision)
    return parameter * precision_byte / (1024 ** 3)

def model_memory_req(model_info: ModelInfo) -> float:
    """
    Calculates the GPU memory (in GiB) required for loading the model
    """

    model_params = model_info.safetensors.parameters
    memory = 0
    for precision, num_params in model_params.items():
        memory += parameter_memory_req(num_params, precision)

    return memory

def inference_dtype(model_config: AutoConfig) -> str:
    """
    Returns the inference KV cache data type used
    """

    if hasattr(model_config, "dtype"):
        return str(model_config.dtype)

    return str(model_config.torch_dtype)

def kv_cache_req(model_info: ModelInfo,
                 model_config: AutoConfig,
                 context_len: int,
                 batch_size: int = 1,
                 ) -> int:
    """
    Calculates the KV cache GPU memory requirement for the model based on context length and batch size
    """

    precision_in_bytes = precision_to_byte(inference_dtype(model_config))
    deepseek_mla_models = [
        "DeepSeek-V3",
        "DeepSeek-V2",
        "DeepSeek-R1",
    ]

    per_token_memory = 0

    # DeepSeek MLA attention, all other models use MHA, GQA, or MQA
    mla = any(deepseek in model_info.id for deepseek in deepseek_mla_models)

    try:
        num_layers = model_config.num_hidden_layers
        if mla:
            kv_lora_rank = model_config.kv_lora_rank
            qk_rope_head_dim = model_config.qk_rope_head_dim
            per_token_memory = num_layers * (kv_lora_rank + qk_rope_head_dim) * precision_in_bytes
        else:
            head_dimension = getattr(model_config, "head_dim", model_config.hidden_size / model_config.num_attention_heads)
            kv_heads = model_config.num_key_value_heads
            per_token_memory = num_layers * 2 * head_dimension * kv_heads * precision_in_bytes
    except Exception as e:
        print(e)
        return 0

    kv_cache_size = per_token_memory * context_len * batch_size
    kv_cache_size_gb =  kv_cache_size / (1024 ** 3)
    return kv_cache_size_gb

def max_concurrent_requests(model_info: ModelInfo,
                        model_config: AutoConfig,
                        max_model_len: int,
                        gpu_memory: int,
                        gpu_mem_util: float=0.9,
                        tp: int=1,
                        pp: int=1,
                        dp: int=1,
                    ) -> int:

    # Find allocatable memory for KV cache
    kv_cache_allocatable = allocatable_kv_cache_memory(
        model_info, model_config,
        gpu_memory, gpu_mem_util,
        tp, pp, dp
    )

    # Find kv cache requirement for one request of max-model-len
    per_request_kv_cache_req = kv_cache_req(model_info, model_config, max_model_len)
    if per_request_kv_cache_req == 0:
        return 0
    return max(0, math.floor(kv_cache_allocatable / per_request_kv_cache_req))

def find_possible_tp(model_config: AutoConfig) -> List[int]:
    """
    Finds possible values for tp for the given model
    """

    model_config = get_text_config(model_config)

    num_attention_heads = model_config.num_attention_heads

    factors = set(reduce(
        list.__add__,
        ([i, num_attention_heads // i] for i in range(1, int(num_attention_heads**0.5) + 1) if num_attention_heads % i == 0)))

    factors = list(factors)
    factors.sort()
    return factors

def available_gpu_memory(memory: int, gpu_utilization: float=0.9) -> float:
    """
    Returns the available GPU memory
    """

    return memory * gpu_utilization

def gpus_required(tp: int, pp: int, dp: int) -> int:
    """
    Determines the number of GPUs required based on parallelism strategies
    """

    return tp * pp * dp

def per_gpu_model_memory_required(model_info: ModelInfo, tp: int = 1, pp: int = 1) -> int:
    """
    Calculates model memory requirement for each GPU
    """

    model_memory = model_memory_req(model_info)
    return model_memory / (tp * pp)

def allocatable_kv_cache_memory(model_info: ModelInfo,
                            model_config: AutoConfig,
                            gpu_memory: int,
                            gpu_util: float = 0.9,
                            tp: int = 1,
                            pp: int = 1,
                            dp: int = 1,
                            ) -> float:
    gpu_count = tp * pp * dp
    available_memory = available_gpu_memory(gpu_memory, gpu_util) * gpu_count
    model_size = model_memory_req(model_info) * dp

    # TODO: non torch memory
    # TOOD: peak activation memory

    return available_memory - model_size

def is_moe(model_config: AutoConfig) -> bool:
    """
    Returns true if model is MoE
    """
    indicators = [
        "n_routed_experts",
        "n_shared_experts",
        "num_experts",
        "num_experts_per_tok",
    ]
    for indicator in indicators:
        if hasattr(model_config, indicator):
            return True
    return False

def get_num_experts(model_config: AutoConfig) -> int | None:
    """
    Returns the number of experts or None for non-MoE models
    """

    if hasattr(model_config, "n_routed_experts"):
        return model_config.n_routed_experts
    if hasattr(model_config, "num_experts"):
        return model_config.num_experts
    return None

def get_ep_size(tp_size: int, dp_size: int) -> int:
    """
    Returns EP size
    """
    return tp_size * dp_size

def experts_per_ep_group(model_config: AutoConfig,
                   tp: int=1,
                   dp: int=1,
                   ) -> float:
    """
    Calculates the number of experts to handle on each GPU
    """

    num_experts = get_num_experts(model_config)
    ep_size = get_ep_size(tp, dp)
    if num_experts is None:
        return 0
    return num_experts / ep_size