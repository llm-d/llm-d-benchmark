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
        max_gpus_per_type: Optional[Dict[str, int]] = None,
        gpu_list: Optional[list] = None,

        # Performance constraints
        max_ttft: Optional[float] = None,
        max_itl: Optional[float] = None,
        max_latency: Optional[float] = None,
    ):
        """
        Initialize GPU Recommender.

        Args:
            model_id: HuggingFace model ID
            input_len: Input sequence length
            output_len: Output sequence length
            max_gpus: Default maximum number of GPUs to use (applies to all GPU types unless overridden)
            max_gpus_per_type: Optional dict mapping GPU names to their specific max_gpus limit.
                              Example: {"H100": 8, "A100": 4, "L40": 2}
                              If a GPU is not in this dict, it will use the default max_gpus value.
            gpu_list: Optional list of GPU names to evaluate. If None, evaluates all GPUs in GPU_SPECS.
            max_ttft: Maximum time to first token constraint (ms)
            max_itl: Maximum inter-token latency constraint (ms)
            max_latency: Maximum end-to-end latency constraint (s)
        """

        # Read HF Token
        hf_token = os.getenv("HF_TOKEN", None)
        self.input_len = input_len
        self.output_len = output_len
        self.model_id = model_id
        self.model_info = get_model_info_from_hf(model_id, hf_token)
        self.model_config = get_model_config_from_hf(model_id, hf_token)
        self.text_config = get_text_config(self.model_config)

        self.max_gpus = max_gpus
        self.max_gpus_per_type = max_gpus_per_type or {}
        self.gpu_list = gpu_list if gpu_list else list(GPU_SPECS.keys())

        # Keep track of performance bounds
        self.max_ttft = max_ttft
        self.max_itl = max_itl
        self.max_latency = max_latency

        # Store results after recommendation
        self.gpu_results: Optional[Dict[str, PerformanceEstimationResult]] = None
        self.failed_gpus: Optional[Dict[str, str]] = None

    def get_gpu_results(self) -> Tuple[Dict[str, PerformanceEstimationResult], Dict[str, str]]:
        """
        Runs bento's recommendation engine
        """

        gpu_results = {}
        failed_gpus = {}

        # Use the gpu_list from instance attribute
        for gpu_name in self.gpu_list:
            # Use GPU-specific max_gpus if configured, otherwise use default
            num_gpus = self.max_gpus_per_type.get(gpu_name, self.max_gpus)

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
                num_gpus=num_gpus,
                framework="vllm",
                target="throughput",
                constraints=constraints,
            )

            try:
                _, result = run_performance_estimation(params)

                # check that best_config exists (if not, it means estimation failed due to constraints)
                _ = result.best_configs[0] if isinstance(result.best_configs, list) else result.best_configs
                gpu_results[gpu_name] = result
            except ValueError as e:
                msg = f"GPU {gpu_name} not suitable: {e}"
                failed_gpus[gpu_name] = msg
            except Exception as e:
                msg = f"Error estimating performance for GPU {gpu_name}: {e}"
                failed_gpus[gpu_name] = msg

        # Store results in instance variables
        self.gpu_results = gpu_results
        self.failed_gpus = failed_gpus

        return gpu_results, failed_gpus

    def get_gpu_with_highest_throughput(self) -> Optional[Tuple[str, float]]:
        """
        Get the GPU with the highest throughput from results.

        Returns:
            Tuple of (gpu_name, throughput_value) or None if no valid data
        """
        if not self.gpu_results:
            self.get_gpu_results()

        best_gpu = None
        best_throughput = -float('inf')

        for gpu_name, result in self.gpu_results.items():
            if hasattr(result, 'best_configs') and result.best_configs:
                best_latency_result = result.best_configs.get('best_latency') if isinstance(result.best_configs, dict) else None

                if best_latency_result and hasattr(best_latency_result, 'output_throughput_tps') and best_latency_result.output_throughput_tps is not None:
                    throughput = best_latency_result.output_throughput_tps
                    if throughput > best_throughput:
                        best_throughput = throughput
                        best_gpu = gpu_name

        return (best_gpu, best_throughput) if best_gpu else None

    def get_gpu_with_lowest_ttft(self) -> Optional[Tuple[str, float]]:
        """
        Get the GPU with the lowest Time to First Token (TTFT) from results.

        Returns:
            Tuple of (gpu_name, ttft_value) or None if no valid data
        """
        if not self.gpu_results:
            self.get_gpu_results()

        best_gpu = None
        best_ttft = float('inf')

        for gpu_name, result in self.gpu_results.items():
            if hasattr(result, 'best_configs') and result.best_configs:
                best_latency_result = result.best_configs.get('best_latency') if isinstance(result.best_configs, dict) else None

                if best_latency_result and hasattr(best_latency_result, 'ttft_ms') and best_latency_result.ttft_ms is not None:
                    ttft = best_latency_result.ttft_ms
                    if ttft < best_ttft:
                        best_ttft = ttft
                        best_gpu = gpu_name

        return (best_gpu, best_ttft) if best_gpu else None

    def get_gpu_with_lowest_itl(self) -> Optional[Tuple[str, float]]:
        """
        Get the GPU with the lowest Inter-Token Latency (ITL) from results.

        Returns:
            Tuple of (gpu_name, itl_value) or None if no valid data
        """
        if not self.gpu_results:
            self.get_gpu_results()

        best_gpu = None
        best_itl = float('inf')

        for gpu_name, result in self.gpu_results.items():
            if hasattr(result, 'best_configs') and result.best_configs:
                best_latency_result = result.best_configs.get('best_latency') if isinstance(result.best_configs, dict) else None

                if best_latency_result and hasattr(best_latency_result, 'itl_ms') and best_latency_result.itl_ms is not None:
                    itl = best_latency_result.itl_ms
                    if itl < best_itl:
                        best_itl = itl
                        best_gpu = gpu_name

        return (best_gpu, best_itl) if best_gpu else None

    def get_gpu_with_lowest_e2e_latency(self) -> Optional[Tuple[str, float]]:
        """
        Get the GPU with the lowest End-to-End (E2E) latency from results.

        Returns:
            Tuple of (gpu_name, e2e_latency_value) or None if no valid data
        """
        if not self.gpu_results:
            self.get_gpu_results()

        best_gpu = None
        best_e2e = float('inf')

        for gpu_name, result in self.gpu_results.items():
            if hasattr(result, 'best_configs') and result.best_configs:
                best_latency_result = result.best_configs.get('best_latency') if isinstance(result.best_configs, dict) else None

                if best_latency_result and hasattr(best_latency_result, 'e2e_latency_s') and best_latency_result.e2e_latency_s is not None:
                    e2e = best_latency_result.e2e_latency_s
                    if e2e < best_e2e:
                        best_e2e = e2e
                        best_gpu = gpu_name

        return (best_gpu, best_e2e) if best_gpu else None

    def get_performance_summary(self, verbose: bool = False) -> dict:
        """
        Get a comprehensive performance summary for all GPUs.

        Args:
            verbose: If True, include concurrency analysis for each GPU

        Returns:
            Dictionary with structured performance data for all GPUs
        """
        if not self.gpu_results:
            self.get_gpu_results()

        summary = {
            "estimated_best_performance": {},
            "gpu_results": {},
        }

        # Get best performance recommendations
        best_throughput = self.get_gpu_with_highest_throughput()
        if best_throughput:
            summary["estimated_best_performance"]["highest_throughput"] = {
                "gpu": best_throughput[0],
                "throughput_tps": round(best_throughput[1], 2)
            }

        best_ttft = self.get_gpu_with_lowest_ttft()
        if best_ttft:
            summary["estimated_best_performance"]["lowest_ttft"] = {
                "gpu": best_ttft[0],
                "ttft_ms": round(best_ttft[1], 2)
            }

        best_itl = self.get_gpu_with_lowest_itl()
        if best_itl:
            summary["estimated_best_performance"]["lowest_itl"] = {
                "gpu": best_itl[0],
                "itl_ms": round(best_itl[1], 2)
            }

        best_e2e = self.get_gpu_with_lowest_e2e_latency()
        if best_e2e:
            summary["estimated_best_performance"]["lowest_e2e_latency"] = {
                "gpu": best_e2e[0],
                "e2e_latency_s": round(best_e2e[1], 4)
            }

        # Add detailed results for each GPU conforming to llm-optimizer structure
        for gpu_name, gpu_result in self.gpu_results.items():
            if hasattr(gpu_result, 'best_configs') and gpu_result.best_configs:
                gpu_data = {}

                # Extract best_latency config (concurrency = 1)
                best_latency = gpu_result.best_configs.get('best_latency') if isinstance(gpu_result.best_configs, dict) else None
                if best_latency:
                    gpu_data["best_latency"] = {
                        "optimal_concurrency": 1,
                        "throughput_tps": round(best_latency.output_throughput_tps, 2) if best_latency.output_throughput_tps else None,
                        "ttft_ms": round(best_latency.ttft_ms, 2) if best_latency.ttft_ms else None,
                        "itl_ms": round(best_latency.itl_ms, 2) if best_latency.itl_ms else None,
                        "e2e_latency_s": round(best_latency.e2e_latency_s, 4) if best_latency.e2e_latency_s else None,
                        "prefill_is_memory_bound": best_latency.prefill_is_memory_bound if hasattr(best_latency, 'prefill_is_memory_bound') else None,
                        "decode_is_memory_bound": best_latency.decode_is_memory_bound if hasattr(best_latency, 'decode_is_memory_bound') else None,
                    }

                # Extract best_throughput config (optimal concurrency)
                best_throughput_config = gpu_result.best_configs.get('best_output_throughput') if isinstance(gpu_result.best_configs, dict) else None
                if best_throughput_config:
                    gpu_data["best_output_throughput"] = {
                        "optimal_concurrency": best_throughput_config.concurrency if hasattr(best_throughput_config, 'concurrency') else None,
                        "throughput_tps": round(best_throughput_config.output_throughput_tps, 2) if best_throughput_config.output_throughput_tps else None,
                        "ttft_ms": round(best_throughput_config.ttft_ms, 2) if best_throughput_config.ttft_ms else None,
                        "itl_ms": round(best_throughput_config.itl_ms, 2) if best_throughput_config.itl_ms else None,
                        "e2e_latency_s": round(best_throughput_config.e2e_latency_s, 4) if best_throughput_config.e2e_latency_s else None,
                        "prefill_is_memory_bound": best_throughput_config.prefill_is_memory_bound if hasattr(best_throughput_config, 'prefill_is_memory_bound') else None,
                        "decode_is_memory_bound": best_throughput_config.decode_is_memory_bound if hasattr(best_throughput_config, 'decode_is_memory_bound') else None,
                    }

                # Add concurrency analysis if verbose
                if verbose and hasattr(gpu_result, 'concurrency_analysis') and gpu_result.concurrency_analysis:
                    gpu_data["concurrency_analysis"] = []
                    for conc_result in gpu_result.concurrency_analysis:
                        gpu_data["concurrency_analysis"].append({
                            "optimal_concurrency": conc_result.concurrency if hasattr(conc_result, 'concurrency') else None,
                            "throughput_tps": round(conc_result.output_throughput_tps, 2) if conc_result.output_throughput_tps else None,
                            "ttft_ms": round(conc_result.ttft_ms, 2) if conc_result.ttft_ms else None,
                            "itl_ms": round(conc_result.itl_ms, 2) if conc_result.itl_ms else None,
                            "e2e_latency_s": round(conc_result.e2e_latency_s, 4) if conc_result.e2e_latency_s else None,
                        })

                # Add GPU memory info
                if best_latency:
                    if hasattr(best_latency, 'total_memory_gb'):
                        gpu_data["total_memory_gb"] = best_latency.total_memory_gb
                    if hasattr(best_latency, 'model_memory_gb'):
                        gpu_data["model_memory_gb"] = round(best_latency.model_memory_gb, 2)
                    if hasattr(best_latency, 'kv_cache_memory_gb'):
                        gpu_data["kv_cache_memory_gb"] = round(best_latency.kv_cache_memory_gb, 2)

                summary["gpu_results"][gpu_name] = gpu_data

        return summary