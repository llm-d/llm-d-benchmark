"""
Utilities to fetch info from Hugging Face
"""
import math
from huggingface_hub import HfApi
from dataclasses import dataclass

PRECISIONS = ["FP32", "FP/BF16", "FP/INT8", "FP/INT4"]

@dataclass
class Scenario:
    """Scenario stores info about an user scenario"""
    model_name: str | None = None
    parameters: int | None = None
    precision: str | None = None    # ie: BF16
    tp: int = 1
    dp: int | None = None
    pp: int | None = None
    enable_prefix_caching: bool = False
    enable_chunked_prefill: bool = False
    block_size: int = 1
    gpu_mem_utilization: float = 0.9
    max_batched_tokens: int = 1
    gpu_count_avail: int | None = None
    workload: dict | None = None
    isl: int | None = None
    osl: int | None = None
    ttft: float | None = None
    tpot: float | None = None
    throughput: float | None = None

    # GPU
    gpu_spec: dict | None = None

    def get_memory_req(self) -> int:
        """
        Returns memory requirement for the model based on precision and model size
        """

        # memory = num_params * param_size  (BF/FP16 = 2 bytes per param) * 20% overhead
        if "32" in self.precision:
            return (self.parameters * 4) * 1.2

        if "16" in self.precision:
            return (self.parameters * 2) * 1.2

        if "8" in self.precision:
            return (self.parameters * 1) * 1.2

        if "4" in self.precision:
            return (self.parameters * 0.5) * 1.2
        else:
            return -1

    def get_gpu_mem_in_gb(self) -> int:
        """Round gpu memory req"""
        return round(self.get_memory_req() / 1e+9)

    def free_memory(self) -> int:
        """
        free = gpu_count_avail * GB - model size - KV cache
        """

        return self.gpu_count_avail * self.gpu_spec['memory'] - self.get_gpu_mem_in_gb()

    def get_min_gpu_count(self) -> int:

        # https://blog.eleuther.ai/transformer-math/
        # https://ksingh7.medium.com/calculate-how-much-gpu-memory-you-need-to-serve-any-llm-67301a844f21
        model_memory = self.get_memory_req()
        if model_memory == -1:
            return model_memory

        gpu_memory_bytes =  self.gpu_spec['memory'] * 1e+9
        return math.ceil(model_memory / gpu_memory_bytes)

    def get_kv_cache_req(self) -> int:
        """ Calculate and return KV cache memory requirement"""

        # TODO
        # Check out https://lmcache.ai/kv_cache_calculator.html
        # 1 token = ~ 1MB
        return (self.isl + self.osl) / 1000

def get_model_info_from_hf(model_name: str, hf_token: str | None = None):
    """
    Fetches model info from hf
    """
    api = HfApi(token=hf_token)
    try:
        return api.model_info(model_name)
    except Exception as e:
        return None

def get_model_parameters(model_name: str, hf_token: str | None = None):
    """
    Get model param count from HF
    """
    model_info = get_model_info_from_hf(model_name, hf_token)
    return model_info

def length_description_to_token(description: str) -> int:
    """
    Maps token length descriptor (Short, Medium, Long) to token integer
    """

    if description == "Short":
        return 100
    if description == "Medium":
        return 300
    return 600