import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path


current_file = Path(__file__).resolve()
workspace_root = current_file.parents[2]
setup_dir = current_file.parents[1]
config_explorer_src = workspace_root / "config_explorer" / "src"
sys.path.insert(0, str(config_explorer_src))
sys.path.insert(1, str(setup_dir))
sys.path.insert(2, str(workspace_root))

try:
    from huggingface_hub import ModelInfo
    from huggingface_hub.errors import GatedRepoError, HfHubHTTPError
    from transformers import AutoConfig
except ModuleNotFoundError as e:
    print(f"❌ ERROR: Required dependency not installed: {e}")
    print("Please install the required dependencies:")
    print(f"  pip install -r {workspace_root / 'config_explorer' / 'requirements.txt'}")
    sys.exit(1)

# Import config_explorer module
try:
    from config_explorer.capacity_planner import (
        KVCacheDetail,
        allocatable_kv_cache_memory,
        available_gpu_memory,
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
except ModuleNotFoundError as e:
    print(f"❌ ERROR: Failed to import config_explorer module: {e}")
    print(f"\nTry: pip install -r {workspace_root / 'config_explorer' / 'requirements.txt'}")
    sys.exit(1)
except Exception as e:
    print(f"❌ ERROR: An unexpected error occurred while importing config_explorer: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)


# ---------------- Import local packages ----------------
try:
    from functions import (
        announce,
        environment_variable_to_dict,
        get_accelerator_nr,
        get_accelerator_type,
        is_standalone_deployment,
    )
except ImportError as e:
    # Fallback for when dependencies are not available
    print(f"❌ ERROR: Could not import required modules: {e}")
    print("This script requires the llm-d environment to be properly set up.")
    print("Please run: ./setup/install_deps.sh")
    sys.exit(1)

# ---------------- Data structure for validating vllm args ----------------
COMMON = "COMMON"
PREFILL = "PREFILL"
DECODE = "DECODE"


@dataclass
class ValidationParam:
    models: list[str]
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


def convert_accelerator_memory(gpu_name: str, accelerator_memory_param: str) -> int:
    """
    Try to guess the accelerator memory from its name
    """

    try:
        return int(accelerator_memory_param)
    except ValueError:
        # String is not an integer
        pass

    result = 0

    if gpu_name == "auto":
        announce(
            "⚠️ Accelerator (LLMDBENCH_VLLM_COMMON_AFFINITY) type is set to be automatically detected, but requires connecting to kube client. The affinity check is invoked at a later step. To exercise the capacity planner, set LLMDBENCH_COMMON_ACCELERATOR_MEMORY. Otherwise, capacity planner will use 0 as the GPU memory."
        )

    match = re.search(r"(\d+)\s*GB", gpu_name, re.IGNORECASE)
    if match:
        result = int(match.group(1))
    else:
        # Some names might use just a number without GB (e.g., H100-80)
        match2 = re.search(r"-(\d+)\b", gpu_name)
        if match2:
            result = int(match2.group(1))

    if result > 0:
        announce(
            f"Determined GPU memory={result} from the accelerator's name: {gpu_name}. It may be incorrect, please set LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEMORY for accuracy."
        )

    return result


def get_model_info(model_name: str, hf_token: str, ignore_if_failed: bool) -> ModelInfo | None:
    """
    Obtains model info from HF
    """

    try:
        return get_model_info_from_hf(model_name, hf_token)

    except GatedRepoError:
        announce_failed(
            "Model is gated and the token provided via LLMDBENCH_HF_TOKEN does not, work. Please double check.",
            ignore_if_failed,
        )
    except HfHubHTTPError as hf_exp:
        announce_failed(
            f"Error reaching Hugging Face API: Is LLMDBENCH_HF_TOKEN correctly set? {hf_exp}", ignore_if_failed
        )
    except Exception as e:
        announce_failed(f"Cannot retrieve ModelInfo: {e}", ignore_if_failed)

    return None


def get_model_config_and_text_config(
    model_name: str, hf_token: str, ignore_if_failed: bool
) -> tuple[AutoConfig | None, AutoConfig | None]:
    """
    Obtains model config and text config from HF
    """

    try:
        config = get_model_config_from_hf(model_name, hf_token)
        return config, get_text_config(config)

    except GatedRepoError:
        announce_failed(
            "Model is gated and the token provided via LLMDBENCH_HF_TOKEN does not work. Please double check.",
            ignore_if_failed,
        )
    except HfHubHTTPError as hf_exp:
        announce_failed(
            f"Error reaching Hugging Face API. Is LLMDBENCH_HF_TOKEN correctly set? {hf_exp}", ignore_if_failed
        )
    except Exception as e:
        announce_failed(f"Cannot retrieve model config: {e}", ignore_if_failed)

    return None, None


def validate_vllm_params(param: ValidationParam, ignore_if_failed: bool, type: str = COMMON):
    """
    Given a list of vLLM parameters, validate using capacity planner
    """

    env_var_prefix = COMMON
    if type != COMMON:
        env_var_prefix = f"MODELSERVICE_{type}"

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
        announce_failed(
            "Cannot determine accelerator memory. Please set LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEMORY to enable Capacity Planner.",
            ignore_if_failed,
        )

    per_replica_requirement = gpus_required(tp=tp, dp=dp)
    if replicas == 0:
        per_replica_requirement = 0
    total_gpu_requirement = per_replica_requirement

    if total_gpu_requirement > user_requested_gpu_count:
        announce_failed(
            f"Accelerator requested is {user_requested_gpu_count} but it is not enough to stand up the model. Set LLMDBENCH_VLLM_{env_var_prefix}_ACCELERATOR_NR to TP x DP = {tp} x {dp} = {total_gpu_requirement}",
            ignore_if_failed,
        )

    if total_gpu_requirement < user_requested_gpu_count:
        announce(
            f"⚠️ For each replica, model requires {total_gpu_requirement}, but you requested {user_requested_gpu_count} for the deployment. Note that some GPUs will be idle."
        )

    # Use capacity planner for further validation
    for model in models_list:
        model_info = get_model_info(model, hf_token, ignore_if_failed)
        model_config, text_config = get_model_config_and_text_config(model, hf_token, ignore_if_failed)

        if model_config is not None:
            # Check if parallelism selections are valid
            try:
                valid_tp_values = find_possible_tp(text_config)
                if tp not in valid_tp_values:
                    announce_failed(
                        f"TP={tp} is invalid. Please select from these options ({valid_tp_values}) for {model}.",
                        ignore_if_failed,
                    )
            except AttributeError as e:
                # Error: config['num_attention_heads'] not in config
                announce_failed(
                    f"Cannot obtain data on the number of attention heads, cannot find valid tp values: {e}",
                    ignore_if_failed,
                )

            # Check if model context length is valid
            valid_max_context_len = 0
            try:
                # Error: config['max_positional_embeddings'] not in config
                valid_max_context_len = max_context_len(model_config)
            except AttributeError as e:
                announce_failed(f"Cannot obtain data on the max context length for model: {e}", ignore_if_failed)

            if max_model_len > valid_max_context_len:
                announce_failed(
                    f"Max model length = {max_model_len} exceeds the acceptable for {model}. Set LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN to a value below or equal to {valid_max_context_len}",
                    ignore_if_failed,
                )
        else:
            announce_failed("Model config on parameter shape not available.", ignore_if_failed)

        # Display memory info
        announce("👉 Collecting GPU information....")
        avail_gpu_memory = available_gpu_memory(gpu_memory, gpu_memory_util)
        announce(
            f"ℹ️ {gpu_memory} GB of memory per GPU, with {gpu_memory} GB x {gpu_memory_util} (gpu_memory_utilization) = {avail_gpu_memory} GB available to use."
        )
        announce(
            f"ℹ️ Each model replica requires {per_replica_requirement} GPUs, total available GPU memory = {avail_gpu_memory * per_replica_requirement} GB."
        )

        # # Calculate model memory requirement
        announce("👉 Collecting model information....")
        if model_info is not None:
            try:
                model_params = model_total_params(model_info)
                announce(f"ℹ️ {model} has a total of {model_params} parameters")

                model_mem_req = model_memory_req(model_info)
                announce(f"ℹ️ {model} requires {model_mem_req} GB of memory")

                # Estimate KV cache memory and max number of requests that can be served in worst case scenario
                announce("👉 Estimating available KV cache....")
                available_kv_cache = allocatable_kv_cache_memory(
                    model_info,
                    model_config,
                    gpu_memory,
                    gpu_memory_util,
                    tp=tp,
                    dp=dp,
                )

                if available_kv_cache < 0:
                    announce_failed(
                        f"There is not enough GPU memory to stand up model. Exceeds by {abs(available_kv_cache)} GB.",
                        ignore_if_failed,
                    )

                announce(f"ℹ️ Allocatable memory for KV cache {available_kv_cache} GB")

                kv_details = KVCacheDetail(model_info, model_config, max_model_len, batch_size=1)
                announce(
                    f"ℹ️ KV cache memory for a request taking --max-model-len={max_model_len} requires {kv_details.per_request_kv_cache_gb} GB of memory"
                )

                total_concurrent_reqs = max_concurrent_requests(
                    model_info,
                    model_config,
                    max_model_len,
                    gpu_memory,
                    gpu_memory_util,
                    tp=tp,
                    dp=dp,
                )
                announce(
                    f"ℹ️ The vLLM server can process up to {total_concurrent_reqs} number of requests at the same time, assuming the worst case scenario that each request takes --max-model-len"
                )

            except AttributeError as e:
                # Model might not have safetensors data on parameters
                announce_failed(
                    f"Does not have enough information about model to estimate model memory or KV cache: {e}",
                    ignore_if_failed,
                )
        else:
            announce_failed("Model info on model's architecture not available.", ignore_if_failed)


def get_validation_param(ev: dict, type: str = COMMON) -> ValidationParam:
    """
    Returns validation param from type: one of prefill, decode, or None (default=common)
    """

    prefix = f"vllm_{COMMON}"
    if type == PREFILL or type == DECODE:
        prefix = f"vllm_modelservice_{type}"
    prefix = prefix.lower()

    models_list = ev["deploy_model_list"]
    models_list = [m.strip() for m in models_list.split(",")]
    replicas = ev[f"{prefix}_replicas"] or 0
    replicas = int(replicas)
    gpu_type = get_accelerator_type(ev)
    tp_size = int(ev[f"{prefix}_tensor_parallelism"])
    dp_size = int(ev[f"{prefix}_data_parallelism"])
    user_accelerator_nr = ev[f"{prefix}_accelerator_nr"]

    hf_token = ev["hf_token"]
    if hf_token == "":
        hf_token = None

    validation_param = ValidationParam(
        models=models_list,
        hf_token=hf_token,
        replicas=replicas,
        gpu_type=gpu_type,
        gpu_memory=convert_accelerator_memory(gpu_type, ev["vllm_common_accelerator_memory"]),
        tp=tp_size,
        dp=dp_size,
        accelerator_nr=user_accelerator_nr,
        requested_accelerator_nr=get_accelerator_nr(user_accelerator_nr, tp_size, dp_size),
        gpu_memory_util=float(ev[f"{prefix}_accelerator_mem_util"]),
        max_model_len=int(ev["vllm_common_max_model_len"]),
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
    prefill_params = get_validation_param(ev, type=PREFILL)
    decode_params = get_validation_param(ev, type=DECODE)

    announce(f"Validating prefill vLLM arguments for {prefill_params.models} ...")
    validate_vllm_params(prefill_params, ignore_if_failed, type=PREFILL)

    announce(f"Validating decode vLLM arguments for {decode_params.models} ...")
    validate_vllm_params(decode_params, ignore_if_failed, type=DECODE)


def main():
    """Main function following the pattern from other Python steps"""

    # Set current step name for logging/tracking
    os.environ["LLMDBENCH_CURRENT_STEP"] = os.path.splitext(os.path.basename(__file__))[0]

    ev = {}
    environment_variable_to_dict(ev)

    if ev["control_dry_run"]:
        announce("DRY RUN enabled. No actual changes will be made.")

    # Capacity planning
    ignore_failed_validation = ev["ignore_failed_validation"]
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
