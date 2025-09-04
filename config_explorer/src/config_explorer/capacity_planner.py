"""
Capacity planner provides functionality to estimate the minimum number of GPUs required for loading model and KV cache
"""

import math
from typing import List
from huggingface_hub import HfApi, ModelInfo
from transformers import AutoConfig

# Model
def get_model_info_from_hf(model_name: str, hf_token: str | None = None):
    """
    Fetches model info from HF, does not handle error
    """
    api = HfApi(token=hf_token)
    return api.model_info(model_name)

def model_total_params(model_info: ModelInfo) -> int:
    """
    Returns the total parameters of the model
    """
    return model_info.safetensors.total

def model_precision_keys(model_info: ModelInfo) -> List[str]:
    """
    Returns a list of the precisions for the model weights
    """
    return list(model_info.safetensors.parameters.keys())

def precision_bytes(model_info: ModelInfo) -> int:
    """
    Returns the byte requirement for a parameter for the highest precision of the model
    """
    precisions = model_precision_keys(model_info)

    for precision in precisions:
        if "32" in precision:
            return 4
        if "16" in precision:
            return 2
        if "8" in precision:
            return 1
        if "4" in precision:
            return 0.5

    # Return 4 as default
    return 4

def model_memory_req(model_info: ModelInfo) -> int:
    """
    Calculates the GPU memory required for loading the model
    """
    model_params = model_total_params(model_info)
    model_precision_bytes = precision_bytes(model_info)

    # num_params * bytes * 20% overhead
    # Then convert to GB
    return ((model_params * model_precision_bytes) * 1.2) / (1024 ** 3)

# GPU and KV cache
def min_gpu_req(model_info: ModelInfo, gpu_memory: int) -> int:
    """
    Calculates the minimum GPU count needed for the model
    """

    model_memory_gb = model_memory_req(model_info)
    return math.ceil(model_memory_gb / gpu_memory)

def get_model_config_from_hf(model_name: str, hf_token: str=None) -> AutoConfig:
    """
    Returns LLM model config
    """

    model_config = AutoConfig.from_pretrained(
        model_name,
        trust_remote_code=True,
        token=hf_token or None,
    )

    # For LLMs
    if hasattr(model_config, "text_config"):
        model_config = model_config.text_config

    return model_config


def kv_cache_req(model_info: ModelInfo,
                 model_config: AutoConfig,
                 context_len: int,
                 batch_size: int = 1,
                 ) -> int:
    """
    Calculates the KV cache GPU memory requirement for the model
    """

    precision_in_bytes = precision_bytes(model_info)
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

def max_context_len(model_config: AutoConfig) -> int:
    """
    Returns the max context length accepted by model
    """
    return model_config.max_position_embeddings

def max_concurrent_req(model_info: ModelInfo,
                        model_config: AutoConfig,
                        max_model_len: int,
                        available_gpu_count: int,
                        gpu_memory: int,
                    ) -> int:
    """
    Calculates the max number of concurrent requests the model can serve with the specified GPUs available
    """

    model_memory = model_memory_req(model_info)
    per_request_kv_cache = kv_cache_req(model_info,
                                        model_config,
                                        max_model_len,
                                        )

    total_gpu_memory = available_gpu_count * gpu_memory
    allocatable_kv_cache_size = total_gpu_memory - model_memory

    # If < 0, return 0
    return max(0, math.floor(allocatable_kv_cache_size / per_request_kv_cache))