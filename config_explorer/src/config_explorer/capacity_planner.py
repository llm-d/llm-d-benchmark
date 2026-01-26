"""
Capacity planner for LLM inference memory estimation.

This module implements memory estimation formulas for LLM inference with vLLM:
- Model weight memory requirements
- KV cache memory for different attention mechanisms (MHA, GQA, MQA, MLA)
- Activation memory during forward pass
- CUDA graph and system overhead

Calculates minimum GPU requirements based on model architecture, parallelism
configuration, and workload characteristics.
"""

from dataclasses import dataclass
from enum import StrEnum
import math
from functools import reduce
import re
from typing import List
from huggingface_hub import HfApi
from huggingface_hub.hf_api import ModelInfo

import contextlib
import io
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    from transformers import AutoConfig, AutoModel

# Memory Overhead Constants (in GiB)
# Empirically validated against vLLM on H100 GPUs with seq_len=16000, batch_size=1
# Source: empirical-test/analysis-results.md
# Test environment: H100 (79.18 GiB), vLLM with FlashAttention, max_model_len=16000
ACTIVATION_MEMORY_BASE_DENSE_GIB = 5.5  # Dense models: Qwen3-0.6B (5.56), Llama-8B (4.76), Llama-70B/TP2 (4.84)
ACTIVATION_MEMORY_BASE_MOE_GIB = 8.0    # MoE models: GPT-OSS-20B (7.38)
ACTIVATION_REFERENCE_SEQ_LEN = 16000    # Reference sequence length for empirical measurements
VLLM_NON_TORCH_MEMORY_TP1_GIB = 0.15    # TP=1: empirical range 0.13-0.14 GiB
VLLM_NON_TORCH_MEMORY_TPN_GIB = 0.6     # TP≥2: empirical 0.55 GiB (TP=2)
# Note: CUDA graph memory is included in activation memory profiling, not a separate constant

# Computational Constants
BYTES_PER_GIB = 1024 ** 3
FP16_BF16_BYTES = 2  # Computational dtype for most inference workloads
HIGH_PRECISION_THRESHOLD_BYTES = 2  # Distinguish quantized vs full-precision
DEFAULT_KV_CACHE_DTYPE_BYTES = 1  # FP8 KV cache default

class AttentionType(StrEnum):
    """Attention mechanism types supported by the capacity planner."""
    MLA = "Multi-head latent attention"
    MHA = "Multi-head attention"
    GQA = "Grouped-query attention"
    MQA = "Multi-query attention"

@dataclass
class KVCacheDetail:
    # Required inputs from model config
    model: str
    attention_type: AttentionType
    kv_data_type: str
    precision_in_bytes: int
    num_hidden_layers: int
    hidden_size: int
    num_attention_heads: int
    num_key_value_heads: int
    head_dimension: int
    model_architecture: str

    # Derived outputs from input
    num_attention_group: int
    per_token_memory_bytes: int
    per_request_kv_cache_bytes: int
    per_request_kv_cache_gb: float          # Single request kv cache
    kv_cache_size_gb: float                 # Batch size kv cache

    # Workload inputs
    context_len: int = 1
    batch_size: int = 1

    # Required inputs for MLA attention models
    kv_lora_rank: int | None = None
    qk_rope_head_dim: int | None = None

    def __init__(self, model_info: ModelInfo, model_config: AutoConfig, context_len: int=1, batch_size: int=1):
        """
        KVCacheDetail stores information that are relevant to calculating KV cache memory requirement
        """
        self.model = model_info.id
        self.kv_data_type = inference_dtype(model_config)
        self.precision_in_bytes = inference_dtype_byte(model_config)
        self.model_architecture = model_config.architectures[0]

        # kv_data_type is stored at the model_config level, so need to fetch text_config afterward
        model_config = get_text_config(model_config)

        self.num_hidden_layers = model_config.num_hidden_layers
        self.hidden_size = model_config.hidden_size
        self.num_attention_heads = model_config.num_attention_heads
        self.num_key_value_heads = model_config.num_key_value_heads
        self.head_dimension = getattr(model_config,"head_dim", None)
        if self.head_dimension is None:
            self.head_dimension = int(self.hidden_size / self.num_attention_heads)
        # Determine attention type
        if use_mla(self.model_architecture):
            self.attention_type = AttentionType.MLA
            self.kv_lora_rank = model_config.kv_lora_rank
            self.qk_rope_head_dim = model_config.qk_rope_head_dim
        else:
            if self.num_key_value_heads == 1:
                self.attention_type = AttentionType.MQA

            elif self.num_key_value_heads == self.num_attention_heads:
                self.attention_type = AttentionType.MHA

            else:
                # At this point, 1 < num_key_value_heads < num_attention_heads
                # For example, 8 KV heads with 32 attention heads, so 4 attention heads share the same KV matrices
                self.attention_type = AttentionType.GQA

        # Calculate kv cache size in bytes and in gb
        self.set_context_len(context_len)
        self.set_batch_size(batch_size)

    def set_context_len(self, context_len: int):
        """
        Sets context length and recalculates memory requirement
        """
        self.context_len = context_len
        self.__recalculate()

    def set_batch_size(self, batch_size: int):
        """
        Sets batch size and recalculates memory requirement
        """
        self.batch_size = batch_size
        self.__recalculate()

    def __recalculate(self):
        """"
        Recalculates per token memory, kv cache size in bytes, and in GB

        KV Cache Memory Formulas:
        - Standard Attention (MHA, GQA, MQA):
          num_layers * 2 * num_kv_heads * head_dim * precision_bytes
          Factor of 2 for separate K and V caches

        - Multi-head Latent Attention (MLA):
          num_layers * (kv_lora_rank + qk_rope_head_dim) * precision_bytes
          Uses compressed KV representation (DeepSeek-V2/V3)

        Attention Types:
        - MHA: num_kv_heads == num_attention_heads (all heads have dedicated K,V)
        - GQA: 1 < num_kv_heads < num_attention_heads (multiple Q heads share K,V)
        - MQA: num_kv_heads == 1 (single K,V pair shared across all Q heads)
        - MLA: Compressed KV with low-rank projection
        """
        if self.attention_type == AttentionType.MLA:
            self.per_token_memory_bytes = self.num_hidden_layers * (self.kv_lora_rank + self.qk_rope_head_dim) * self.precision_in_bytes
        else:
            self.num_attention_group = int(self.num_attention_heads / self.num_key_value_heads)
            self.per_token_memory_bytes = int(self.num_hidden_layers * 2 * self.head_dimension * self.num_key_value_heads * self.precision_in_bytes)

        self.per_request_kv_cache_bytes = self.per_token_memory_bytes * self.context_len
        self.per_request_kv_cache_gb = bytes_to_gib(self.per_request_kv_cache_bytes)
        self.kv_cache_size_gb = self.per_request_kv_cache_gb * self.batch_size

# Model
def get_model_info_from_hf(model_name: str, hf_token: str | None = None) -> ModelInfo:
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

def get_quantization_config(model_config: AutoConfig) -> dict:
    """
    Returns the quantization config
    """

    return model_config.quantization_config

def is_quantized(model_config: AutoConfig) -> bool:
    """
    Returns True if model is quantized
    """

    return hasattr(model_config, 'quantization_config')

def model_total_params(model_info: ModelInfo) -> int:
    """
    Returns the total parameters of the model
    """
    return model_info.safetensors.total

def max_context_len(model_config: AutoConfig) -> int:
    """
    Returns the max context length accepted by model
    """
    model_config = get_text_config(model_config)
    return model_config.max_position_embeddings

def estimate_vllm_non_torch_memory(tp: int = 1) -> float:
    """
    Estimate non-torch memory (CUDA runtime, Python interpreter) in GiB.

    Non-torch memory increases with TP due to NCCL/communication overhead.

    Args:
        tp: Tensor parallelism degree

    Returns:
        Non-torch memory in GiB per GPU
    """
    return VLLM_NON_TORCH_MEMORY_TP1_GIB if tp == 1 else VLLM_NON_TORCH_MEMORY_TPN_GIB

def estimate_vllm_cuda_graph_memory() -> float:
    """
    CUDA graph memory overhead per GPU in GiB.

    Note: Empirical measurements show CUDA graph memory is included in the
    activation memory profiling (range: -0.45 to +0.39 GiB as separate measurement).
    Returning 0.0 to avoid double-counting.

    Returns:
        0.0 (CUDA graph memory already included in activation estimate)
    """
    return 0.0

def estimate_vllm_activation_memory(config: AutoConfig,
                                   seq_len: int,
                                   batch_size: int = 1,
                                   tp: int = 1) -> float:
    """
    Estimate peak activation memory for vLLM inference in GiB.

    Uses empirically-calibrated base constants that capture vLLM's actual memory
    allocation during profiling, then scales linearly with sequence length and batch size.

    The base constants are derived from vLLM profiling measurements which include:
    - Hidden states and layer buffers
    - FlashAttention workspace
    - FFN intermediate activations
    - Multiple concurrent prefill requests during warmup
    - CUDA graph compilation overhead
    - PyTorch memory allocator fragmentation

    Empirical validation (seq_len=16000, batch_size=1):
    - Dense models: 4.76-5.56 GiB (Qwen3-0.6B, Llama-8B, Llama-70B with TP=2)
    - MoE models: 7.38 GiB (GPT-OSS-20B with 32 experts)

    Source: empirical-test/analysis-results.md

    Args:
        config: Model configuration
        seq_len: Sequence length
        batch_size: Batch size
        tp: Tensor parallelism degree (note: empirical data shows activation
            memory does NOT scale inversely with TP)

    Returns:
        float: Estimated peak activation memory in GiB

    Raises:
        ValueError: If tp <= 0 or seq_len < 0 or batch_size < 0
    """
    if tp <= 0:
        raise ValueError(f"Tensor parallelism must be positive, got tp={tp}")
    if seq_len < 0:
        raise ValueError(f"Sequence length cannot be negative, got seq_len={seq_len}")
    if batch_size < 0:
        raise ValueError(f"Batch size cannot be negative, got batch_size={batch_size}")

    if seq_len == 0:
        return 0.0

    # Select base constant based on model type (at reference seq_len=16000, batch=1)
    if is_moe(config):
        base_constant_gib = ACTIVATION_MEMORY_BASE_MOE_GIB
    else:
        base_constant_gib = ACTIVATION_MEMORY_BASE_DENSE_GIB

    # Scale linearly with sequence length and batch size
    # All major activation components (hidden states, attention workspace, FFN intermediates)
    # are proportional to seq_len and batch_size
    seq_len_factor = seq_len / ACTIVATION_REFERENCE_SEQ_LEN
    batch_size_factor = batch_size

    # TP scaling: Empirical data shows activation memory does NOT scale inversely with TP
    # (Llama-70B TP=1 would have ~4.8 GiB, TP=2 shows 4.84 GiB per GPU)
    # This suggests vLLM's per-GPU allocation doesn't simply divide by TP

    return base_constant_gib * seq_len_factor * batch_size_factor

def precision_to_byte(precision: str) -> float:
    """
    Returns the byte requirement the data type
    """

    precision = precision.strip().lower()

    mapping = {
        # Floating point
        "f64": 8,
        "f32": 4,
        "f16": 2,
        "bf16": 2,
        "f8_e5m2": 1,
        "f8_e4m3": 1,
        "fp4": 0.5,

        # Integers
        "i64": 8,
        "int64": 8,
        "i32": 4,
        "int32": 4,
        "i16": 2,
        "int16": 2,
        "i8": 1,
        "int8": 1,
        "u8": 1,
        "u4": 0.5,
        "i4": 0.5,
        "int4": 0.5,

        # Boolean
        "bool": 1,  # stored as byte per element

        # Special data types
        # gpt-oss: https://cdn.openai.com/pdf/419b6906-9da6-406c-a19d-1bb078ac7637/oai_gpt-oss_model_card.pdf
        # 4.25 bits per param
        "mxfp4": 4.25 / 8,
    }

    if precision in mapping:
        return float(mapping[precision])
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
    return bytes_to_gib(parameter * precision_byte)

def parameter_precision_memory_req(parameter: int, precision_in_byte: int) -> float:
    """
    Calculates the memory requirement (in GiB) for the number of parameters for the specified precision in bytes.
    """

    return bytes_to_gib(parameter * precision_in_byte)

def get_quant_method(model_config: AutoConfig) -> str:
    """
    Tries to determine the quant method used in quantization_config
    """

    if is_quantized(model_config):
        quantization_config = get_quantization_config(model_config)

        if "quant_method" in quantization_config:
            return quantization_config['quant_method']

    return ""

def get_quant_bytes(model_config: AutoConfig) -> float:
    """
    Returns the number of bytes specified by quant_method
    """

    quant_config = get_quantization_config(model_config)
    quant_method = get_quant_method(model_config)
    if quant_method != "":
        try:
            return precision_to_byte(quant_method)

        # Quant method not convertible like "compressed-tensors"
        # Example: https://huggingface.co/RedHatAI/Qwen3-8B-FP8-dynamic/blob/main/config.json
        except ValueError:

            # Sometimes bits are given
            if "bits" in quant_config:
                return float(bits_to_bytes(quant_config['bits']))

            # Sometimes bits are nested in config groups
            if 'config_groups' in quant_config:
                if 'group_0' in quant_config['config_groups']:
                    if 'weights' in quant_config['config_groups']['group_0']:
                        num_bits = quant_config['config_groups']['group_0']['weights']['num_bits']
                        return float(bits_to_bytes(num_bits))
    # Not quantized
    else:
        return 0.0


def model_memory_req(model_info: ModelInfo, model_config: AutoConfig) -> float:
    """
    Calculates the GPU memory (in GiB) required for loading the model
    """

    model_params = model_info.safetensors.parameters
    memory = 0

    # Check if model is quantized
    quantization_byte = None
    if is_quantized(model_config):
        quantization_byte = get_quant_bytes(model_config)

    for precision, num_params in model_params.items():
        precision_in_byte = precision_to_byte(precision)

        # IF FP16 or FP32, keep it as so
        if precision_in_byte >= 2:
            memory += parameter_memory_req(num_params, precision)
        else:
            # Otherwise, check if model is quantized, and use that as the precision
            if quantization_byte is not None:
                memory += parameter_precision_memory_req(num_params, quantization_byte)
            else:
                memory += parameter_memory_req(num_params, precision)

    return memory

def _extract_dtype_from_config(model_config: AutoConfig) -> str | None:
    """
    Extract dtype from model config, checking common attribute names.

    Returns:
        Dtype string if found, None otherwise
    """
    for attr in ["torch_dtype", "dtype"]:
        if hasattr(model_config, attr):
            dtype = getattr(model_config, attr)
            if dtype is not None:
                return str(dtype)
    return None

def inference_dtype(model_config: AutoConfig) -> str:
    """
    Returns the inference KV cache data type used.

    Checks model config dtype attributes first, falls back to quantization
    method if available, returns empty string if neither found.
    """
    dtype = _extract_dtype_from_config(model_config)
    if dtype is not None:
        return dtype

    if is_quantized(model_config):
        return get_quant_method(model_config)

    return ""

def inference_dtype_byte(model_config: AutoConfig) -> float:
    """
    Returns the precision for the inference KV cache data type in bytes.

    For standard dtypes (fp32, bf16, etc.), converts directly.
    For compressed formats (compressed-tensors), extracts from quantization config.
    Falls back to FP8 (1 byte) as default.
    """
    native_kv_dtype = inference_dtype(model_config)

    try:
        return precision_to_byte(native_kv_dtype)
    except ValueError:
        # Cannot determine from dtype string (e.g., "compressed-tensors")
        if is_quantized(model_config):
            return get_quant_bytes(model_config)

        return DEFAULT_KV_CACHE_DTYPE_BYTES

def use_mla(model_architecture: str) -> bool:
    """
    Returns true for models that use MLA attention
    """

    deepseek_mla_models = [
        "DeepseekV3ForCausalLM",
        "DeepseekV2ForCausalLM",
    ]

    return any(deepseek in model_architecture for deepseek in deepseek_mla_models)

def kv_cache_req(model_info: ModelInfo,
                    model_config: AutoConfig,
                    context_len: int,
                    batch_size: int = 1,
                    ) -> float:
    """
    Calculates the KV cache requirement in GiB
    """

    return KVCacheDetail(model_info, model_config, context_len, batch_size).kv_cache_size_gb

def total_kv_cache_blocks(model_info: ModelInfo,
                    model_config: AutoConfig,
                    context_len: int,
                    gpu_memory: int,
                    gpu_mem_util: float=0.9,
                    batch_size: int = 1,
                    block_size: int = 16,
                    tp: int=1,
                    pp: int=1,
                    dp: int=1,
                    ) -> int:
    """
    Calculate total number of KV cache blocks that can fit in GPU memory.

    Implements vLLM's block-based memory management. KV cache is divided into
    fixed-size blocks (default 16 tokens) for dynamic allocation and efficient
    memory sharing across requests.
    """
    kv_cache_detail = KVCacheDetail(model_info, model_config, context_len, batch_size)
    per_token_memory = kv_cache_detail.per_token_memory_bytes / (tp * pp)
    per_block_memory = per_token_memory * block_size

    kv_cache_allocatable = allocatable_kv_cache_memory(
        model_info, model_config,
        gpu_memory, gpu_mem_util,
        tp, pp, dp,
        max_model_len=context_len,
        batch_size=batch_size
    )

    total_kv_blocks = gib_to_bytes(kv_cache_allocatable) // per_block_memory
    return total_kv_blocks

def max_concurrent_requests(model_info: ModelInfo,
                        model_config: AutoConfig,
                        max_model_len: int,
                        gpu_memory: int,
                        gpu_mem_util: float=0.9,
                        batch_size: int=1,
                        tp: int=1,
                        pp: int=1,
                        dp: int=1,
                    ) -> int:
    """
    Calculate maximum number of concurrent requests that can be served.

    Args:
        model_info: HuggingFace model info
        model_config: Model configuration
        max_model_len: Maximum sequence length per request
        gpu_memory: GPU memory per device in GiB
        gpu_mem_util: GPU memory utilization factor
        batch_size: Batch size for activation memory estimation
        tp: Tensor parallelism degree
        pp: Pipeline parallelism degree
        dp: Data parallelism degree

    Returns:
        int: Maximum number of concurrent requests
    """
    # Find allocatable memory for KV cache
    kv_cache_allocatable = allocatable_kv_cache_memory(
        model_info, model_config,
        gpu_memory, gpu_mem_util,
        tp, pp, dp,
        max_model_len=max_model_len,
        batch_size=batch_size
    )

    # Find kv cache requirement for one request of max-model-len
    per_request_kv_cache_req = kv_cache_req(model_info, model_config, max_model_len)
    # MEDIUM FIX: Check if allocatable_kv is non-positive to prevent division by zero
    if per_request_kv_cache_req == 0 or kv_cache_allocatable <= 0:
        return 0
    return max(0, math.floor(kv_cache_allocatable / per_request_kv_cache_req))

def find_possible_tp(model_config: AutoConfig) -> List[int]:
    """
    Find possible tensor parallelism values for the model.

    TP must be a divisor of num_attention_heads to ensure each TP rank has
    an integer number of heads. For example, 32 heads supports TP ∈ {1,2,4,8,16,32}.
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

def gpus_required(tp: int=1, pp: int=1, dp: int=1) -> int:
    """
    Determines the number of GPUs required based on parallelism strategies
    """

    return tp * pp * dp

def per_gpu_model_memory_required(model_info: ModelInfo,
                                  model_config: AutoConfig,
                                  tp: int = 1,
                                  pp: int = 1) -> float:
    """
    Calculate model memory requirement per GPU.

    With parallelism: TP shards layers horizontally, PP distributes layers vertically.
    Memory per GPU = Total_model_memory / (TP × PP)
    """
    model_memory = model_memory_req(model_info, model_config)
    return model_memory / (tp * pp)

def allocatable_kv_cache_memory(model_info: ModelInfo,
                            model_config: AutoConfig,
                            gpu_memory: int,
                            gpu_util: float = 0.9,
                            tp: int = 1,
                            pp: int = 1,
                            dp: int = 1,
                            max_model_len: int | None = None,
                            batch_size: int = 1,
                            ) -> float:
    """
    Calculate allocatable memory for KV cache after accounting for model weights,
    activation memory, CUDA graphs, and system overhead.

    Memory Formula:
    Available = (GPU_memory × utilization × num_GPUs)
              - (Model_weights × DP)
              - (Activation_memory × DP)
              - CUDA_graph_overhead
              - Non_torch_overhead

    Args:
        model_info: HuggingFace model info
        model_config: Model configuration
        gpu_memory: GPU memory per device in GiB
        gpu_util: GPU memory utilization factor (default 0.9)
        tp: Tensor parallelism degree
        pp: Pipeline parallelism degree
        dp: Data parallelism degree
        max_model_len: Maximum sequence length (defaults to model's max_position_embeddings)
        batch_size: Batch size for activation memory estimation

    Returns:
        float: Available memory for KV cache in GiB
    """
    gpu_count = tp * pp * dp
    available_memory = available_gpu_memory(gpu_memory, gpu_util) * gpu_count
    model_size = model_memory_req(model_info, model_config) * dp

    if max_model_len is None:
        try:
            max_model_len = max_context_len(model_config)
        except AttributeError:
            max_model_len = 2048

    # Each data parallel replica needs its own activation memory
    activation_memory = estimate_vllm_activation_memory(
        model_config,
        seq_len=max_model_len,
        batch_size=batch_size,
        tp=tp
    ) * dp

    # CUDA graph memory is included in activation memory profiling
    cuda_graph_memory = estimate_vllm_cuda_graph_memory() * gpu_count  # Returns 0.0

    # Non-torch memory scales with TP due to NCCL/communication overhead
    non_torch_memory = estimate_vllm_non_torch_memory(tp) * gpu_count

    total_consumed = model_size + activation_memory + cuda_graph_memory + non_torch_memory

    return max(0, available_memory - total_consumed)

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
    Calculate number of experts per GPU for MoE models.

    Expert Parallelism distributes expert FFN layers across GPUs.
    EP size = TP × DP, and experts are evenly sharded across the EP group.
    Each GPU stores (total_experts / EP_size) expert parameters.
    """
    num_experts = get_num_experts(model_config)
    ep_size = get_ep_size(tp, dp)
    if num_experts is None:
        return 0
    return num_experts / ep_size

# ---------------------- Utility helpers ----------------------
def bits_to_bytes(bits: int) -> int:
    """
    Convert number of bits to byte, assuming num bits is divisible
    """

    return int(bits / 8)

def bytes_to_gib(num_bytes: int) -> float:
    """
    Convert bytes to gibibytes (GiB)
    """
    return num_bytes / BYTES_PER_GIB

def gib_to_bytes(gib: float) -> float:
    """
    Convert gibibytes (GiB) to bytes
    """
    return gib * BYTES_PER_GIB
