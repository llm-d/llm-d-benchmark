import os
import pprint
import re
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import List

from huggingface_hub import ModelInfo
from transformers import AutoConfig

# Add project root to path for imports
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))

# ---------------- Import local packages ----------------
try:
    from functions import announce, environment_variable_to_dict, get_accelerator_nr, is_standalone_deployment
except ImportError as e:
    # Fallback for when dependencies are not available
    print(f"Warning: Could not import required modules: {e}")
    print("This script requires the llm-d environment to be properly set up.")
    print("Please run: ./setup/install_deps.sh")
    sys.exit(1)

try:
    from config_explorer.capacity_planner import gpus_required, get_model_info_from_hf, get_model_config_from_hf, get_text_config, find_possible_tp, max_context_len, available_gpu_memory, model_total_params, model_memory_req, allocatable_kv_cache_memory, kv_cache_req, max_concurrent_requests
    from huggingface_hub.errors import HfHubHTTPError
except ImportError as e:
    print(f"Could not import capacity planner package: {e}")
    sys.exit(1)


# ---------------- Data structure for validating vllm args ----------------
@dataclass
class ValidationParam:
    models: List[str]
    hf_token: str
    replicas: int
    gpu_type: str
    gpu_memory: int
    tp: int
    dp: int
    accelerator_nr: int
    requested_accelerator_nr: int
    gpu_memory_util: float
    max_model_len: int

# ---------------- Helpers ----------------

def announce_failed(msg: str, ignore_if_failed: bool):
    """
    Prints out failure message and exits execution if ignore_if_failed==False, otherwise continue
    """

    announce(f"❌ {msg}")
    if not ignore_if_failed:
        sys.exit(1)

def convert_accelerator_memory(gpu_name: str, accelerator_memory_param: str) -> int | None:
    """
    Try to guess the accelerator memory from its name
    """

    try:
        return int(accelerator_memory_param)
    except Exception as e:
        match = re.search(r"(\d+)\s*GB", gpu_name, re.IGNORECASE)
        result = None
        if match:
            result = int(match.group(1))
        else:
            # Some names might use just a number without GB (e.g., H100-80)
            match2 = re.search(r"-(\d+)\b", gpu_name)
            if match2:
                result = int(match2.group(1))

        if result is not None:
            announce(f"Determined GPU memory={result} from the accelerator's name: {gpu_name}. It may be incorrect, please set LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEMORY for accuracy.")

        # Could not guess
        return result

def validate_vllm_params(param: ValidationParam, ignore_if_failed: bool, type: str="common"):
    """
    Given a list of vLLM parameters, validate using capacity planner
    """

    env_var_prefix = "COMMON"
    if type != "common":
        env_var_prefix = f"MODELSERVICE_{type.upper()}"

    models_list = param.models
    hf_token = param.hf_token
    replicas = param.replicas
    gpu_memory = param.gpu_memory
    tp = param.tp
    dp = param.dp
    user_requested_gpu_count = param.requested_accelerator_nr
    max_model_len = param.max_model_len
    gpu_memory_util = param.gpu_memory_util

    # Sanity check on user inputs
    if gpu_memory is None:
        announce_failed("Cannot determine accelerator memory. Please set LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEMORY to enable Capacity Planner.", ignore_if_failed)

    per_replica_requirement = gpus_required(tp=tp, dp=dp)
    total_gpu_requirement = per_replica_requirement * replicas
    if total_gpu_requirement > user_requested_gpu_count:
        announce_failed(f"Accelerator requested is {user_requested_gpu_count} but it is not enough to stand up the model. Set LLMDBENCH_VLLM_{env_var_prefix}_ACCELERATOR_NR to TP x DP x replicas = {tp} x {dp} x {replicas} = {total_gpu_requirement}", ignore_if_failed)

    if total_gpu_requirement < user_requested_gpu_count:
        announce(f"⚠️ For each replica, model requires {total_gpu_requirement}, but you requested {user_requested_gpu_count} for the deployment. Note that some GPUs will be idle.")

    # Use capacity planner for further validation
    for model in models_list:
        announce(f"Validating vLLM parameters for each replica of {model}...")

        try:
            model_info = get_model_info_from_hf(model, hf_token)
            model_config = get_model_config_from_hf(model, hf_token)
            text_config = get_text_config(model_config)
        except Exception as e:
            e_str = str(e)
            if "gated" in e_str:
                announce_failed("Model is gated, please set LLMDBENCH_HF_TOKEN.")
            else:
                announce_failed(f"Could not obtain model info or config because: {e_str}")

        # Check if parallelism selections are valid
        valid_tp_values = find_possible_tp(text_config)
        if tp not in valid_tp_values:
            announce_failed(f"TP={tp} is invalid. Please select from these options ({valid_tp_values}) for {model}.", ignore_if_failed)

        # Check if model context length is valid, ignore_if_failed
        valid_max_context_len = 0
        try:
            valid_max_context_len = max_context_len(model_config)
        except Exception as e:
            announce_failed(f"Cannot determine the acceptable max model length from model config: {e}", ignore_if_failed)
        if max_model_len > valid_max_context_len:
            announce_failed(f"Max model length = {max_model_len} exceeds the acceptable for {model}. Set LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN to a value below or equal to {valid_max_context_len}", ignore_if_failed)

        # Display memory info
        announce("\n")
        announce("Collecting GPU information.......")
        avail_gpu_memory = available_gpu_memory(gpu_memory, gpu_memory_util)
        announce(f"ℹ️ GPU used for each replica: {per_replica_requirement} with {gpu_memory} GB of memory each, with {avail_gpu_memory} available.")
        announce(f"ℹ️ Total available GPU memory = {avail_gpu_memory * per_replica_requirement} GB")

        # # Calculate model memory requirement
        announce("\n")
        announce("Collecting model information.......")
        try:
            model_params = model_total_params(model_info)
            announce(f"ℹ️ {model} has a total of {model_params} parameters")

            model_mem_req = model_memory_req(model_info)
            announce(f"ℹ️ {model} requires {model_mem_req} GB of memory")

            # Estimate KV cache memory and max number of requests that can be served in worst case scenario
            announce("\n")
            announce("Estimating available KV cache.......")
            available_kv_cache = allocatable_kv_cache_memory(
                model_info, model_config,
                gpu_memory, gpu_memory_util,
                tp=tp, dp=dp,
            )

            if available_kv_cache < 0:
                announce_failed(f"There is not enough GPU memory to stand up model. Exceeds by {abs(available_kv_cache)} GB.", ignore_if_failed)

            announce(f"ℹ️ Allocatable memory for KV cache {available_kv_cache} GB")

            per_request_kv_cache_req = kv_cache_req(model_info, model_config, max_model_len)
            announce(f"ℹ️ KV cache memory for a request taking --max-model-len={max_model_len} requires {per_request_kv_cache_req} GB of memory")

            total_concurrent_reqs = max_concurrent_requests(
                model_info, model_config, max_model_len,
                gpu_memory, gpu_memory_util,
                tp=tp, dp=dp,
            )
            announce(f"ℹ️ The vLLM server can process up to {total_concurrent_reqs} number of requests at the same time, assuming the worst case scenario that each request takes --max-model-len")

        except Exception as e:
            announce_failed(f"Does not have enough information about model to estimate model memory or KV cache: {e}", ignore_if_failed)

def get_validation_param(ev: dict, type: str="common") -> ValidationParam:
    """
    Returns validation param from type: one of prefill, decode, or None (default=common)
    """

    prefix = "vllm_common"
    if type == "prefill" or type == "decode":
        prefix = f"vllm_modelservice_{type}"

    models_list = ev['deploy_model_list']
    models_list = [m.strip() for m in models_list.split(",")]
    gpu_type = ev['vllm_common_accelerator_resource']
    tp_size = int(ev[f'{prefix}_tensor_parallelism'])
    dp_size = int(ev[f'{prefix}_data_parallelism'])
    user_accelerator_nr = ev[f'{prefix}_accelerator_nr']

    validation_param = ValidationParam(
        models = models_list,
        hf_token = ev['hf_token'],
        replicas = int(ev[f'{prefix}_replicas']),
        gpu_type = gpu_type,
        gpu_memory = convert_accelerator_memory(gpu_type, ev['vllm_common_accelerator_memory']),
        tp = tp_size,
        dp = dp_size,
        accelerator_nr = user_accelerator_nr,
        requested_accelerator_nr = get_accelerator_nr(user_accelerator_nr, tp_size, dp_size),
        gpu_memory_util = float(ev[f'{prefix}_accelerator_mem_util']),
        max_model_len = int(ev['vllm_common_max_model_len']),
    )

    return validation_param

def validate_standalone_vllm_params(ev: dict, ignore_if_failed: bool):
    """
    Validates vllm standalone configuration
    """
    standalone_params = get_validation_param(ev)
    validate_vllm_params(standalone_params, ignore_if_failed)


def validate_modelservice_vllm_params(ev: dict, ignore_if_failed: bool):
    """
    Validates vllm modelservice configuration
    """
    prefill_params = get_validation_param(ev, type='prefill')
    decode_params = get_validation_param(ev, type='decode')

    announce("Validating prefill vLLM arguments...")
    validate_vllm_params(prefill_params, ignore_if_failed, type="prefill")

    announce("Validating decode vLLM arguments...")
    validate_vllm_params(decode_params, ignore_if_failed, type="decode")

def main():
    """Main function following the pattern from other Python steps"""

    # Set current step name for logging/tracking
    os.environ["LLMDBENCH_CURRENT_STEP"] = os.path.splitext(os.path.basename(__file__))[0]

    ev = {}
    environment_variable_to_dict(ev)

    if ev["control_dry_run"]:
        announce("DRY RUN enabled. No actual changes will be made.")

    # Capacity planning
    ignore_failed_validation = ev['ignore_failed_validation']
    msg = "Validating vLLM configuration against Capacity Planner... "
    if ignore_failed_validation:
        msg += "deployment will continue even if validation failed."
    else:
        msg += "deployment will halt if validation failed."
    announce(msg)

    if is_standalone_deployment(ev):
        announce("Deployment method is standalone")
        validate_standalone_vllm_params(ev, ignore_failed_validation)
    else:
        announce("Deployment method is modelservice, checking for prefill and decode deployments")
        validate_modelservice_vllm_params(ev, ignore_failed_validation)

if __name__ == "__main__":
    sys.exit(main())
