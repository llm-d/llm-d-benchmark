import os
import sys
from pathlib import Path

# Add project root to path for imports
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))

try:
    from functions import announce, environment_variable_to_dict
except ImportError as e:
    # Fallback for when dependencies are not available
    print(f"Warning: Could not import required modules: {e}")
    print("This script requires the llm-d environment to be properly set up.")
    print("Please run: ./setup/install_deps.sh")
    sys.exit(1)

try:
    from config_explorer.capacity_planner import *
except ImportError as e:
    print(f"Could not import capacity planner package: {e}")
    sys.exit(1)

def convert_accelerator_memory(gpu_name: str, accelerator_memory_param: str) -> int | None:
    """
    Try to guess the accelerator memory from its name
    """

    try:
        return int(accelerator_memory_param)
    except Exception:
        print("here")
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

def announce_failed_validation(msg: str):
    """
    Announce messages for failed validation
    """
    announce(f"❌ {msg}")

def validate_vllm_params(ev: dict):
    """
    Validates vllm standalone configuration
    """

    models = ev['deploy_model_list']
    models = [m.strip() for m in models.split(",")]
    hf_token = ev['hf_token']
    replicas = int(ev['vllm_common_replicas'])
    gpu_type = ev['vllm_common_accelerator_resource']
    gpu_memory = convert_accelerator_memory(gpu_type, ev['vllm_common_accelerator_memory'])
    user_requested_gpu_count = int(ev['vllm_common_accelerator_nr'])
    tp = int(ev['vllm_common_tensor_parallelism'])
    dp = int(ev['vllm_common_data_parallelism'])
    gpu_memory_util = float(ev['vllm_common_accelerator_mem_util'])
    max_model_len = int(ev['vllm_common_max_model_len'])

    # Sanity check on user inputs
    if gpu_memory is None:
        announce_failed_validation("Cannot determine accelerator memory. Please set LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEMORY to enable Capacity Planner.")
        sys.exit(1)

    per_replica_requirement = gpus_required(tp=tp, dp=dp)
    total_gpu_requirement = per_replica_requirement * replicas
    if total_gpu_requirement > user_requested_gpu_count:
        announce_failed_validation(f"Accelerator requested is {user_requested_gpu_count} but it is not enough to stand up the model. Set LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR to TP x DP x replicas = {tp} x {dp} x {replicas} = {total_gpu_requirement}")
        sys.exit(1)
    if total_gpu_requirement < user_requested_gpu_count:
        announce(f"⚠️ For each replica, model requires {total_gpu_requirement}, but you requested {user_requested_gpu_count} for the deployment. Note that some GPUs will be idle.")

    # Use capacity planner for further validation
    for model in models:
        announce(f"Validating vLLM parameters for each replica of {model}...")

        try:
            model_info = get_model_info_from_hf(model, hf_token)
            model_config = get_model_config_from_hf(model, hf_token)
            text_config = get_text_config(model_config)
        except Exception:
            announce_failed_validation("Model is gated, please set LLMDBENCH_HF_TOKEN.")
            sys.exit(1)

        # Check if parallelism selections are valid
        valid_tp_values = find_possible_tp(text_config)
        if tp not in valid_tp_values:
            announce_failed_validation(f"TP={tp} is invalid. Please select from these options ({valid_tp_values}) for {model}.")
            sys.exit(1)

        # Check if model context length is valid
        valid_max_context_len = max_context_len(model_config)
        if max_model_len > valid_max_context_len:
            announce_failed_validation(f"Max model length = {max_model_len} exceeds the acceptable for {model}. Set LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN to a value below or equal to {valid_max_context_len}")
            sys.exit(1)

        # Display memory info
        announce("\n")
        announce("Collecting GPU information.......")
        avail_gpu_memory = available_gpu_memory(gpu_memory, gpu_memory_util)
        announce(f"ℹ️ GPU used for each replica: {per_replica_requirement} with {gpu_memory} GB of memory each, with {avail_gpu_memory} available.")
        announce(f"ℹ️ Total available GPU memory = {avail_gpu_memory * per_replica_requirement} GB")

        # # Calculate model memory requirement
        announce("\n")
        announce("Collecting model information.......")
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
            announce_failed_validation(f"There is not enough GPU memory to stand up model. Exceeds by {abs(available_kv_cache)} GB.")
            sys.exit(1)
        announce(f"ℹ️ Allocatable memory for KV cache {available_kv_cache} GB")

        per_request_kv_cache_req = kv_cache_req(model_info, model_config, max_model_len)
        announce(f"ℹ️ KV cache memory for a request taking --max-model-len={max_model_len} requires {per_request_kv_cache_req} GB of memory")

        total_concurrent_reqs = max_concurrent_requests(
            model_info, model_config, max_model_len,
            gpu_memory, gpu_memory_util,
            tp=tp, dp=dp,
        )
        announce(f"ℹ️ The vLLM server can process up to {total_concurrent_reqs} number of requests at the same time, assuming the worst case scenario that each request takes --max-model-len")


def main():
    """Main function following the pattern from other Python steps"""

    # Set current step name for logging/tracking
    os.environ["LLMDBENCH_CURRENT_STEP"] = os.path.splitext(os.path.basename(__file__))[0]

    ev = {}
    environment_variable_to_dict(ev)

    if ev["control_dry_run"]:
        announce("DRY RUN enabled. No actual changes will be made.")

    # Capacity planning
    skip_validation = ev['skip_validation']
    if not skip_validation:
        announce("Validating vLLM configuration against Capacity Planner...")
        validate_vllm_params(ev)
    else:
        announce("Skipping vLLM configuration against Capacity Planner...")

if __name__ == "__main__":
    sys.exit(main())
