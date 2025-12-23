import os
from typing import Dict, Optional, Tuple

from config_explorer.capacity_planner import get_model_config_from_hf, get_model_info_from_hf, get_text_config

from llm_optimizer.predefined.gpus import GPU_SPECS
from llm_optimizer.performance import PerformanceEstimationParams, PerformanceEstimationResult, run_performance_estimation

class GPURecommender:
    """Recommends optimal GPU for running LLM inference using BentoML's llm-optimizer roofline algorithm.

    Given a list of models and available GPUs, recommends the best GPU
    for each model based on synthetic performance estimates.
    """

    def __init__(
        self,
        model_id: str,
        input_len: int,
        output_len: int,
        max_gpus: int = 1,

        # Performance constraints
        max_ttft: Optional[float] = None,
        max_itl: Optional[float] = None,
        max_latency: Optional[float] = None,
    ):

        # Read HF Token
        hf_token = os.getenv("HF_TOKEN", None)
        self.input_len = input_len
        self.output_len = output_len
        self.model_id = model_id
        self.model_info = get_model_info_from_hf(model_id, hf_token)
        self.model_config = get_model_config_from_hf(model_id, hf_token)
        self.text_config = get_text_config(self.model_config)

        self.max_gpus = max_gpus

        # Keep track of performance bounds
        self.max_ttft = max_ttft
        self.max_itl = max_itl
        self.max_latency = max_latency

    def recommend_gpu(self, gpu_list: Optional[list] = None) -> Tuple[Dict[str, PerformanceEstimationResult], Dict[str, str]]:
        """
        Runs bento's recommendation engine

        Args:
            gpu_list: Optional list of GPU names to evaluate. If None, evaluates all GPUs in GPU_SPECS.
        """

        gpu_results = {}
        failed_gpus = {}

        # Use provided list or default to all GPUs
        gpus_to_evaluate = gpu_list if gpu_list else list(GPU_SPECS.keys())

        for gpu_name in gpus_to_evaluate:

            constraints = ""
            if self.max_ttft is not None:
                constraints += f"ttft:p95<={self.max_ttft}ms"
            if self.max_itl is not None:
                constraints += f"itl:p95<={self.max_itl}ms"
            if self.max_latency is not None:
                constraints += f"e2e_latency:p95<={self.max_latency}s"

            params = PerformanceEstimationParams(
                model=self.model_id,
                input_len=self.input_len,
                output_len=self.output_len,
                gpu=gpu_name,
                num_gpus=self.max_gpus,
                framework="vllm",
                target="throughput",
                constraints=constraints,
            )

            try:
                updated_params, result = run_performance_estimation(params)
                best_config = result.best_configs[0] if isinstance(result.best_configs, list) else result.best_configs
                gpu_results[gpu_name] = result
            except ValueError as e:
                msg = f"GPU {gpu_name} not suitable: {e}"
                failed_gpus[gpu_name] = msg
            except Exception as e:
                msg = f"Error estimating performance for GPU {gpu_name}: {e}"
                failed_gpus[gpu_name] = msg

        return gpu_results, failed_gpus