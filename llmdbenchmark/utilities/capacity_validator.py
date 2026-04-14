"""Capacity planner validation for vLLM deployments against GPU and model constraints."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, TYPE_CHECKING

from planner.capacity_planner import (
    KVCacheDetail,
    allocatable_kv_cache_memory,
    available_gpu_memory,
    estimate_vllm_activation_memory,
    estimate_vllm_cuda_graph_memory,
    estimate_vllm_non_torch_memory,
    find_possible_tp,
    get_model_config_from_hf,
    get_model_info_from_hf,
    get_text_config,
    gpus_required,
    max_concurrent_requests,
    max_context_len,
    model_memory_req,
    model_total_params,
)

if TYPE_CHECKING:
    from transformers import AutoConfig


class _Logger(Protocol):
    """Minimal logger interface compatible with both project and stdlib loggers."""

    def log_info(self, msg: str, **kwargs: Any) -> None: ...

    def log_warning(self, msg: str, **kwargs: Any) -> None: ...


class _StdlibLoggerAdapter:
    """Wrap a standard :class:`logging.Logger` to match :class:`_Logger`."""

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger

    def log_info(self, msg: str, **_kw: Any) -> None:
        self._log.info(msg)

    def log_warning(self, msg: str, **_kw: Any) -> None:
        self._log.warning(msg)


def _ensure_logger(logger: Any) -> _Logger:
    """Return a _Logger-compatible object, wrapping stdlib loggers if needed."""
    if hasattr(logger, "log_info") and hasattr(logger, "log_warning"):
        return logger  # type: ignore[return-value]
    return _StdlibLoggerAdapter(logger)


@dataclass
class ValidationParams:
    """Parameters for a single deployment method's capacity validation."""

    models: list[str]
    hf_token: str | None
    replicas: int
    gpu_memory: int  # GPU memory in GB; 0 = unknown (skip GPU memory checks)
    tp: int
    pp: int
    dp: int
    accelerator_nr: int  # User-requested GPUs per pod
    gpu_memory_util: float
    max_model_len: int
    ignore_failures: bool = False
    label: str = ""  # e.g. "standalone", "decode", "prefill"


def _get_model_config(
    model_name: str,
    hf_token: str | None,
    logger: _Logger,
    ignore_failures: bool = False,
) -> "AutoConfig | None":
    """Fetch model config from HuggingFace, with error handling."""
    tag = "WARNING" if ignore_failures else "ERROR"
    try:
        return get_model_config_from_hf(model_name, hf_token)
    except Exception as exc:
        logger.log_warning(
            f"{tag}: Cannot retrieve model config for {model_name}: {exc}"
        )
        return None


def _convert_accelerator_memory(gpu_name: str, raw_value: str) -> int:
    """Determine GPU memory in GB from an explicit value or GPU product name. Returns 0 if unknown."""
    try:
        return int(raw_value)
    except (ValueError, TypeError):
        pass

    if not gpu_name:
        return 0

    match = re.search(r"(\d+)\s*GB", gpu_name, re.IGNORECASE)
    if match:
        return int(match.group(1))

    match2 = re.search(r"-(\d+)\b", gpu_name)
    if match2:
        return int(match2.group(1))

    return 0


def validate_vllm_params(
    params: ValidationParams,
    logger: _Logger,
) -> list[str]:
    """Validate vLLM parameters against the capacity planner. Returns diagnostic messages."""
    tag = "WARNING" if params.ignore_failures else "ERROR"
    prefix = f"[{params.label}] " if params.label else ""
    messages: list[str] = []

    def msg(text: str) -> None:
        full = f"{prefix}{tag}: {text}"
        messages.append(full)
        logger.log_warning(full)

    def info(text: str) -> None:
        full = f"{prefix}{text}"
        messages.append(full)
        logger.log_info(full)

    per_replica_gpus = gpus_required(tp=params.tp, pp=params.pp, dp=params.dp)
    if params.replicas == 0:
        per_replica_gpus = 0

    if per_replica_gpus > params.accelerator_nr:
        msg(
            f"Accelerator requested is {params.accelerator_nr} but "
            f"TP x PP x DP = {params.tp} x {params.pp} x {params.dp} "
            f"= {per_replica_gpus} GPUs are required per replica"
        )

    if 0 < per_replica_gpus < params.accelerator_nr:
        msg(
            f"Each replica requires {per_replica_gpus} GPUs, but "
            f"{params.accelerator_nr} requested per pod. "
            f"Some GPUs will be idle."
        )

    skip_gpu_tests = False
    if params.gpu_memory is None or params.gpu_memory == 0:
        info(
            "Cannot determine accelerator memory. "
            "Set accelerator.memory in your config to enable "
            "GPU memory validation (KV cache estimation). "
            "Skipping GPU memory checks."
        )
        skip_gpu_tests = True

    for model in params.models:
        model_config = _get_model_config(
            model, params.hf_token, logger, params.ignore_failures
        )
        text_config = None
        if model_config is not None:
            text_config = get_text_config(model_config)

        if model_config is not None:
            try:
                valid_tp = find_possible_tp(text_config)
                if params.tp not in valid_tp:
                    msg(
                        f"TP={params.tp} is invalid for {model}. "
                        f"Valid values: {valid_tp}"
                    )
            except AttributeError:
                msg(
                    f"Cannot determine valid TP values for {model} "
                    "(num_attention_heads not available)"
                )

            valid_max_ctx = 0
            try:
                valid_max_ctx = max_context_len(model_config)
            except AttributeError as exc:
                msg(f"Cannot determine max context length for {model}: {exc}")

            if valid_max_ctx and params.max_model_len > valid_max_ctx:
                msg(
                    f"maxModelLen={params.max_model_len} exceeds "
                    f"model limit of {valid_max_ctx} for {model}"
                )
        else:
            msg("Model config on parameter shape not available.")

        if not skip_gpu_tests:
            avail_mem = available_gpu_memory(params.gpu_memory, params.gpu_memory_util)
            info(
                f"{params.gpu_memory} GB per GPU, "
                f"{params.gpu_memory} x {params.gpu_memory_util} "
                f"(gpu_memory_utilization) = {avail_mem:.1f} GB available"
            )
            info(
                f"Each replica requires {per_replica_gpus} GPUs, "
                f"total available GPU memory = "
                f"{avail_mem * per_replica_gpus:.1f} GB"
            )

        if model_config is not None:
            try:
                total_params = model_total_params(model, params.hf_token)
                info(f"{model} has {total_params:,} parameters")

                model_mem = model_memory_req(model, model_config, params.hf_token)
                info(f"{model} requires {model_mem:.2f} GB of memory")

                if not skip_gpu_tests:
                    activation_mem = estimate_vllm_activation_memory(
                        model_config, tp=params.tp
                    )
                    cuda_graph_mem = estimate_vllm_cuda_graph_memory()
                    non_torch_mem = estimate_vllm_non_torch_memory(params.tp)
                    total_intermediate = activation_mem + cuda_graph_mem + non_torch_mem

                    info(f"Peak activation memory per GPU: " f"{activation_mem:.2f} GB")
                    info(f"Non-torch memory per GPU: " f"{non_torch_mem:.2f} GB")
                    info(
                        f"Total intermediate memory per GPU: "
                        f"{total_intermediate:.2f} GB"
                    )

                if not skip_gpu_tests:
                    avail_kv = allocatable_kv_cache_memory(
                        model,
                        model_config,
                        params.gpu_memory,
                        params.gpu_memory_util,
                        tp=params.tp,
                        pp=params.pp,
                        dp=params.dp,
                        max_model_len=params.max_model_len,
                        batch_size=1,
                        hf_token=params.hf_token,
                    )

                    kv_details = KVCacheDetail(
                        model, model_config, params.max_model_len, batch_size=1
                    )
                    per_req_kv = kv_details.per_request_kv_cache_gb

                    if avail_kv < 0:
                        msg(
                            "DEPLOYMENT WILL FAIL: Insufficient GPU memory "
                            "to load model."
                        )
                        msg(
                            f"Model requires {abs(avail_kv):.2f} GB MORE "
                            "memory than available after loading weights "
                            "and activation memory."
                        )
                        _log_config_suggestions(msg, params)

                    elif avail_kv < per_req_kv:
                        msg(
                            "DEPLOYMENT WILL FAIL: Model loads but cannot "
                            "serve any requests."
                        )
                        msg(
                            f"Available KV cache: {avail_kv:.2f} GB, "
                            f"required per request "
                            f"(max_model_len={params.max_model_len}): "
                            f"{per_req_kv:.2f} GB"
                        )
                        _log_config_suggestions(msg, params)

                    else:
                        info(f"Allocatable KV cache memory: " f"{avail_kv:.2f} GB")
                        info(
                            f"Per-request KV cache "
                            f"(max_model_len={params.max_model_len}): "
                            f"{per_req_kv:.2f} GB"
                        )

                        total_concurrent = max_concurrent_requests(
                            model,
                            model_config,
                            params.max_model_len,
                            params.gpu_memory,
                            params.gpu_memory_util,
                            batch_size=1,
                            tp=params.tp,
                            pp=params.pp,
                            dp=params.dp,
                            hf_token=params.hf_token,
                        )
                        info(
                            f"Max concurrent requests (worst case, "
                            f"each at max_model_len): {total_concurrent}"
                        )

            except (AttributeError, Exception) as exc:
                msg(f"Cannot estimate model memory or KV cache for " f"{model}: {exc}")
        else:
            msg("Model architecture info not available -- skipping memory checks.")

    return messages


def _log_config_suggestions(msg_fn, params: ValidationParams) -> None:
    """Log configuration suggestions when deployment will fail."""
    msg_fn(f"  Current config:")
    msg_fn(f"    GPU memory per device: {params.gpu_memory} GB")
    msg_fn(f"    GPU memory utilization: {params.gpu_memory_util}")
    msg_fn(f"    maxModelLen: {params.max_model_len}")
    msg_fn(f"    TP: {params.tp}, PP: {params.pp}, DP: {params.dp}")
    msg_fn(f"  Possible solutions:")
    msg_fn(f"    1. Reduce maxModelLen (currently {params.max_model_len})")
    msg_fn(f"    2. Increase tensor parallelism to use more GPUs")
    msg_fn(f"    3. Use GPUs with more memory")
    msg_fn(
        f"    4. Increase gpu_memory_utilization "
        f"(currently {params.gpu_memory_util}, may cause OOM)"
    )


def _extract_params(
    plan_config: dict,
    method: str,
    ignore_failures: bool,
) -> ValidationParams | None:
    """Extract ValidationParams for a deployment method from the plan config."""
    method_config = plan_config.get(method, {})

    replicas = int(method_config.get("replicas", 0))
    if replicas == 0:
        return None
    if method_config.get("enabled") is False:
        return None

    model_config = plan_config.get("model", {})
    model_name = model_config.get("huggingfaceId") or model_config.get("name", "")
    if not model_name:
        return None
    models = [m.strip() for m in model_name.split(",") if m.strip()]

    hf_section = plan_config.get("huggingface", {})
    hf_token = hf_section.get("token") or os.environ.get("HF_TOKEN") or None
    if hf_token in ("", "REPLACE_TOKEN"):
        hf_token = None

    parallelism = method_config.get("parallelism", {})
    tp = int(parallelism.get("tensor", 1))
    dp = int(parallelism.get("data", 1))
    pp = 1  # Pipeline parallelism not yet exposed per-method

    accel_section = plan_config.get("accelerator", {})
    method_accel_count = method_config.get("accelerator", {}).get("count")
    global_accel_count = accel_section.get("count")

    accelerator_nr = int(
        method_config.get(
            "acceleratorNr",
            method_accel_count
            or global_accel_count
            or tp * pp * dp,
        )
    )
    accel_type = method_config.get("acceleratorType", {}).get(
        "labelValue", ""
    ) or accel_section.get("type", "")
    gpu_memory = _convert_accelerator_memory(
        accel_type,
        str(accel_section.get("memory", "")),
    )

    gpu_memory_util = float(model_config["gpuMemoryUtilization"])

    max_model_len = int(model_config["maxModelLen"])

    return ValidationParams(
        models=models,
        hf_token=hf_token,
        replicas=replicas,
        gpu_memory=gpu_memory,
        tp=tp,
        pp=pp,
        dp=dp,
        accelerator_nr=accelerator_nr,
        gpu_memory_util=gpu_memory_util,
        max_model_len=max_model_len,
        ignore_failures=ignore_failures,
        label=method,
    )


def run_capacity_planner(
    plan_config: dict,
    logger: Any,
    ignore_failures: bool = False,
) -> list[str]:
    """Run capacity planner validation for all active deployment methods."""
    log = _ensure_logger(logger)
    all_messages: list[str] = []

    if ignore_failures:
        log.log_info(
            "Validating vLLM configuration against Capacity Planner "
            "(deployment will continue even if validation fails)"
        )
    else:
        log.log_info(
            "Validating vLLM configuration against Capacity Planner "
            "(deployment will halt if validation fails)"
        )

    standalone = plan_config.get("standalone", {})
    is_standalone = (
        standalone.get("enabled", False) and int(standalone.get("replicas", 0)) > 0
    )

    if is_standalone:
        log.log_info("Deployment method is standalone")
        params = _extract_params(plan_config, "standalone", ignore_failures)
        if params:
            all_messages.extend(validate_vllm_params(params, log))
    else:
        log.log_info(
            "Deployment method is modelservice -- "
            "checking decode and prefill configurations"
        )

        for method in ("decode", "prefill"):
            params = _extract_params(plan_config, method, ignore_failures)
            if params:
                log.log_info(
                    f"Validating {method} vLLM arguments for " f"{params.models} ..."
                )
                all_messages.extend(validate_vllm_params(params, log))
            else:
                log.log_info(f"{method} is disabled or has 0 replicas -- skipping")

    return all_messages
