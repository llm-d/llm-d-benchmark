"""
Capacity planner provides functionality to estimate the minimum number of GPUs required for loading model and KV cache
"""

from dataclasses import dataclass
from enum import StrEnum
import math
from functools import reduce
import re
from typing import List
from huggingface_hub import HfApi, ModelInfo
from transformers import AutoConfig, AutoModel

class AttentionType(StrEnum):
    """
    AttentionType describe the attention mechanism used by the model
    """

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
        self.precision_in_bytes = precision_to_byte(self.kv_data_type)
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
        """
        # Calculate per token memory bytes depending on attention type
        if self.attention_type == AttentionType.MLA:
            self.per_token_memory_bytes = self.num_hidden_layers * (self.kv_lora_rank + self.qk_rope_head_dim) * self.precision_in_bytes
        else:
            self.num_attention_group = int(self.num_attention_heads / self.num_key_value_heads)
            self.per_token_memory_bytes = int(self.num_hidden_layers * 2 * self.head_dimension * (self.num_key_value_heads / self.num_attention_group) * self.precision_in_bytes)

        # Calculate kv cache size in bytes and in gb
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

def inference_dtype(model_config: AutoConfig) -> str:
    """
    Returns the inference KV cache data type used
    """

    dtype = None

    if hasattr(model_config, "dtype"):
        dtype = model_config.dtype

    if hasattr(model_config, "torch_dtype"):
        dtype = model_config.torch_dtype

    # It is possible that the model config sets this field to None
    if dtype is not None:
        return str(dtype)

    # At this point, it can be a quantized model, so use dtype in quantization_config
    if is_quantized(model_config):
        return get_quant_method(model_config)

    return ""

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

def gpus_required(tp: int=1, pp: int=1, dp: int=1) -> int:
    """
    Determines the number of GPUs required based on parallelism strategies
    """

    return tp * pp * dp

def per_gpu_model_memory_required(model_info: ModelInfo,
                                  model_config: AutoConfig,
                                  tp: int = 1,
                                  pp: int = 1) -> int:
    """
    Calculates model memory requirement for each GPU
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
                            ) -> float:
    gpu_count = tp * pp * dp
    available_memory = available_gpu_memory(gpu_memory, gpu_util) * gpu_count
    model_size = model_memory_req(model_info, model_config) * dp

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

# ---------------------- Utility helpers ----------------------
def bits_to_bytes(bits: int) -> int:
    """
    Convert number of bits to byte, assuming num bits is divisible
    """

    return int(bits / 8)

def bytes_to_gib(bytes: int) -> float:
    """
    Convert number of bytes to GiB
    """

    return bytes / (1024 ** 3)