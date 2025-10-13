"""
Main Page
"""

from matplotlib import pyplot as plt
import streamlit as st
import db
import util
from src.config_explorer.capacity_planner import *
from decimal import Decimal

def update_gpu_spec():
    """
    Update user selected GPU spec in session state
    """
    st.session_state['scenario'].gpu_spec = st.session_state['gpu_spec'][st.session_state['selected_gpu_spec']]

@st.dialog("Register a new accelerator")
def register_new_accelerator():
    """
    Dialog to register a new accelerator type
    """
    acc_name = st.text_input("Name", placeholder="NVIDIA-A100-40GB")
    acc_mem = st.number_input("Memory (GB)", min_value=1, step=1)

    if st.button("Register", use_container_width=True):
        if acc_name:

            db.gpu_specs[acc_name] = {
                "name": acc_name,
                "memory": acc_mem
            }
            st.rerun()

def get_model_size_df(model_info: ModelInfo, model_config: AutoConfig) -> dict:
    """
    Returns dataframe for displaying how model size is calculated
    """

    data_types = []
    quantized_data_types = []
    bytes_list = []
    params = []
    memory_req = []

    quant_method = ""
    quant_method_byte = 0

    try:
        quant_method = get_quant_method(model_config)
        quant_method_byte = get_quant_bytes(model_config)
    except AttributeError:
        # Model doesn't contain quant config
        pass

    for d_type, param in model_info.safetensors.parameters.items():
        param_bytes = 0
        try:
            param_bytes = precision_to_byte(d_type)
        except Exception:
            pass

        # Update info
        data_types.append(d_type)
        params.append(param)

        if param_bytes >= 2 or quant_method == "":
            quantized_data_types.append(d_type)
            bytes_list.append(param_bytes)
            memory_req.append(parameter_memory_req(param, d_type))
        else:
            quantized_data_types.append(quant_method)
            bytes_list.append(quant_method_byte)
            memory_req.append(parameter_precision_memory_req(param, quant_method_byte))

    data = {
        "Data type": data_types,
        "Quantized data type": quantized_data_types,
        "Size in bytes": bytes_list,
        "Number of parameters": params,
        "Memory in GB (params x bytes)": memory_req,
    }

    if quant_method == "":
        del data["Quantized data type"]

    return data

def model_specification():
    """
    Get model inputs like model name, precision
    """

    user_scenario = st.session_state[util.USER_SCENARIO_KEY]
    model_info = None

    # Model
    with st.container(border=True):
        st.write("**Model Specification**")

        selected_model = st.text_input("Model (Hugging Face format)",
                                        value=user_scenario.get_model_name(),
                                        key=util.SELECTED_MODEL_KEY,
                                        on_change=util.on_update_model_name,
                                       )
        hf_token = None

        if selected_model and selected_model != "":
            # Fetch model info
            try:
                model_info = get_model_info_from_hf(selected_model)
                user_scenario.model_info = model_info
            except Exception as e:
                st.warning("Cannot access model information, see error below.")
                st.warning(e)
                return None

            # Fetch model config
            try:
                model_config = get_model_config_from_hf(selected_model, hf_token=hf_token)
                text_config = get_text_config(model_config)
                user_scenario.model_config = model_config
                user_scenario.text_config = text_config
            except Exception as e:
                e_str = str(e)
                if "gated" in e_str:
                    st.warning("This is a gated model, please submit a HF token to view information")
                    hf_token = st.text_input("HF token")
                    if hf_token:
                        model_config = get_model_config_from_hf(selected_model, hf_token=hf_token)
                        user_scenario.model_config = model_config
                else:
                    st.warning("Cannot access model config, see error below.")
                    st.warning(e)
                    return None

            try:
                model_gpu_memory_req = util.pretty_round(model_memory_req(model_info, model_config))
            except Exception as e:
                st.warning(f"Cannot retrieve relevant information about the model, {e}. The Capacity Planner only has partial information and functionality.")
                return None

            # Display model memory calculation
            col1, col2 = st.columns(2)

            col1.info(f"Size of model in memory: ~{model_gpu_memory_req} GB")
            with col2.expander("See how model size is calculated below"):
                st.write("""Below shows how model memory is estimated. The number of parameters and precision are fetched from Hugging Face. Common data types include `BF16` (floating point 16-bit) and `F8_E4M3` (floating point 8-bit, 4 for exponents and 3 for mantissa). The total is then summed.""")

                if is_quantized(model_config):
                    quant_method = get_quant_method(model_config)
                    st.write(f"This model contains a quantization config. The quantization method is: `{quant_method}`")

                data = get_model_size_df(model_info, model_config)
                st.dataframe(data, hide_index=True)

                st.write("In addition, vLLM [profiles memory](https://github.com/vllm-project/vllm/blob/dcf2f3ec067711ff69e5ab7478fca6ffb4f11daf/vllm/worker/worker.py#L229) by doing a forward pass with `--max-model-len` with dummy data to estimate the non-torch and torch activation peak memory consumption. This means the estimation of the model memory is actually an underestimation. Estimating intermediate memory footprint is currently work in progress.")

        else:
            return None

def parallelism_specification():
    """
    Parallelism configuration
    """
    user_scenario = st.session_state[util.USER_SCENARIO_KEY]
    model_config = user_scenario.text_config

    with st.container(border=True):
        st.write("**Parallelism Configuration**")
        st.caption("Parallelism determines the number of GPUs required.")

        if model_config is None:
            st.warning("Model config not found.")
            return None

        # Display some useful info
        col1, col2 = st.columns(2)
        possible_tp_sizes = find_possible_tp(model_config)
        tp_size = col1.selectbox("Tensor parallel size (shard model weights across GPUs)",
                                    options=possible_tp_sizes,
                                    index=possible_tp_sizes.index(user_scenario.tp_size),
                                    key=util.SELECTED_TP_SIZE_KEY,
                                    help=f"Must be divisible by the number of attention heads (`{model_config.num_attention_heads}` for this model)",
                                    on_change=util.on_update_parallelism,
                                    args=[util.SELECTED_TP_SIZE_KEY, "tp_size"]
                                    )
        pp_size = col2.number_input("Pipeline parallel size (shard layers across GPUs)",
                                    min_value=1,
                                    max_value=model_config.num_hidden_layers,
                                    key=util.SELECTED_PP_SIZE_KEY,
                                    value=user_scenario.pp_size,
                                    help=f"This number is capped by the number of hidden layers (`{model_config.num_hidden_layers}` for this model). \
                                    Also, vLLM handles uneven splits, see the [documentation](https://docs.vllm.ai/en/latest/api/vllm/distributed/index.html#vllm.distributed.get_pp_indices)",
                                    on_change=util.on_update_parallelism,
                                    args=[util.SELECTED_PP_SIZE_KEY, "pp_size"]
                                    )
        dp_size = col1.number_input("Data parallel size (replicas of model)",
                        min_value=1,
                        key=util.SELECTED_DP_SIZE_KEY,
                                    value=user_scenario.dp_size,
                        on_change=util.on_update_parallelism,
                        args=[util.SELECTED_DP_SIZE_KEY, "dp_size"]
                        )

        # Enable EP
        is_moe_model = is_moe(model_config)
        help = "EP is not available as an option for non-MoE models."
        if is_moe_model:
            help = """Instead of the traditional single feed forward layer in transformers, mixture of expert (MoE) models exercise a parallel feed-forward neural-network layers where a number of selected \"experts\" are activated for each token ([citation](https://nvidia.github.io/TensorRT-LLM/advanced/expert-parallelism.html)).


Tensor parallelism splits expert weights across GPUs. Expert parallelism splits incoming token's hidden state across GPUs. In vLLM, enabling data parallelism on MoE models essentially achieves the latter purpose.
"""

        enable_ep = col2.toggle("Enable expert parallelism",
                value=user_scenario.enable_ep,
                disabled=not is_moe_model,
                help=help,
                key=util.SELECTED_ENABLE_EP_KEY,
                on_change=util.update_scenario,
                args=[util.SELECTED_ENABLE_EP_KEY, "enable_ep"]
                )
        if enable_ep:
            total_experts = get_num_experts(model_config)
            ep_size = get_ep_size(tp_size, dp_size)
            experts_per_ep = experts_per_ep_group(model_config, tp_size, dp_size)
            experts_per_ep_str = round(experts_per_ep)

            col2.info(f"""Total number of experts: {total_experts}

`EP size = (TP x DP) = {ep_size}`, meaning each group will get `{total_experts} / {ep_size} = {experts_per_ep_str}` experts per group.
""")
            if experts_per_ep < 1:
                col2.warning("Since some EP groups will get 0 expert, this is an under-utilization of GPU resources. We recommend decreasing TP or DP for better use of your accelerators.")

            if not Decimal(experts_per_ep) % 1 == 0:
                col2.caption("The total number of experts is not divisible by EP size you selected. However, vLLM handles uneven split of experts (see this [PR](https://github.com/vllm-project/vllm/pull/21497)), so some EP groups will have fewer experts than others.")


        st.info(f"GPUs required (`TP x PP x DP`): `{gpus_required(tp_size, pp_size, dp_size)}`")


def workload_specification():
    """
    Estimate total memory needed for KV cache
    """

    user_scenario = st.session_state[util.USER_SCENARIO_KEY]
    model_info = user_scenario.model_info
    model_config = user_scenario.model_config
    text_config = user_scenario.text_config

    # Workload
    with st.container(border=True):
        st.write("**Workload Characteristics**")
        if model_config is None:
            st.warning("Model config not found.")
            return None

        if model_info is None:
            st.warning("Model information not yet selected")
            return None
        if model_config is None:
            st.warning("Model config not available, cannot estimate KV cache size.")
            return None


        st.caption(f"Estimate KV cache memory requirements for the selected model based on workload. Note that the model uses data type of `{inference_dtype(model_config)}` for KV cache during inference.")

        col1, col2 = st.columns(2)

        model_max_context_len = max_context_len(text_config)
        col1.number_input(
            f"Max model len (max model context length is: {model_max_context_len})",
            min_value=1,
            max_value=model_max_context_len,
            value=user_scenario.max_model_len,
            key=util.SELECTED_MAX_MODEL_LEN_KEY,
            on_change=util.on_update_max_model_len,
            )
        col1.caption("Maximum model length for the model: how many tokens (input + output) the model can process. \
Higher max model length means fewer concurrent requests can be served, \
                because for the same GPU memory available for KV cache, \
                each request requires more memory allocation. \
")

        col2.number_input("Input the max number of concurrent requests to process",
            min_value=0,
            step=1,
            key=util.SELECTED_CONCURRENCY_KEY,
            value=user_scenario.concurrency,
            on_change=util.update_scenario,
            args=[util.SELECTED_CONCURRENCY_KEY, "concurrency"]
            )

        try:
            max_concurrent_requests_num = max_concurrent_requests(
                model_info,
                model_config,
                user_scenario.max_model_len,
                gpu_memory=user_scenario.get_gpu_memory(db.gpu_specs),
                gpu_mem_util=user_scenario.gpu_mem_util,
                tp=user_scenario.tp_size,
                pp=user_scenario.pp_size,
                dp=user_scenario.dp_size,
            )

        except Exception:
            col2.warning("Model does not have safetensors data available, cannot estimate KV cache memory requirement.")
            return None

        try:
            kv_details = KVCacheDetail(
                model_info,
                model_config,
                user_scenario.max_model_len,
                user_scenario.concurrency,
            )
        except AttributeError as e:
            col2.warning(f"There is not enough information to estimate KV cache requirement per request: {e}")
            return None

        col2.info(f"Assuming the worst case scenario, such that every request contains `--max-model-len` tokens, each request takes {util.pretty_round(kv_details.per_request_kv_cache_gb)} GB for KV cache, which means the maximum concurrent requests that can be processed is {max_concurrent_requests_num}.")

        # Display details on how KV cache is estimated
        with st.expander("See how KV cache is calculated below"):
            st.write(f"""First, the per-token memory requirement is estimated given the following inputs:
- KV cache data type: `{kv_details.kv_data_type}` = {kv_details.precision_in_bytes} bytes in memory
- Hidden layers: {model_config.num_hidden_layers}

This model uses _{kv_details.attention_type}_. The relevant parameters are:
""")
            if kv_details.attention_type == AttentionType.MLA:
                st.write(f"""- KV lora rank: {kv_details.kv_lora_rank}
- QK rope head dimension: {kv_details.qk_rope_head_dim}""")

                st.code(f"""
Per-token memory = layers x (kv_lora_rank + qk_rope_head_dim) x precision_in_bytes
                 = {kv_details.num_hidden_layers} x ({kv_details.kv_lora_rank} + {kv_details.qk_rope_head_dim}) x {kv_details.precision_in_bytes}
                 = {kv_details.per_token_memory_bytes} bytes
""")
            else:
                st.write(f"""- Head dimension: {kv_details.head_dimension}
- Attention heads: {kv_details.num_attention_heads}
- KV heads: {kv_details.num_key_value_heads}
- Number of attention groups: {kv_details.num_attention_group}
""")

                st.code(f"""
Per-token memory = layers x 2 (two for K and V matrices) x head_dimension x (kv_heads / num_attention_groups) x precision_in_bytes
                 = {kv_details.num_hidden_layers} x 2 x {kv_details.head_dimension} x ({kv_details.num_attention_heads} / {kv_details.num_key_value_heads}) x {kv_details.precision_in_bytes}
                 = {kv_details.per_token_memory_bytes} bytes
""")

            st.write(f"""Finally, the per-token-memory is then multiplied by the context length (max-model-len) and batch size (concurrency).
- Number of tokens (context length): {user_scenario.max_model_len}
- Concurrency: {user_scenario.concurrency}
""")
            st.code(f"""
KV cache per request = per_token_memory x context_len x batch_size
                     = {kv_details.per_token_memory_bytes} x {user_scenario.max_model_len} x {user_scenario.concurrency}
                     = {kv_details.per_request_kv_cache_bytes} bytes
                     = {kv_details.per_request_kv_cache_bytes} / (1024 ^ 3)
                     = {kv_details.per_request_kv_cache_gb} GB
""")

            st.code(f"""
KV cache for max concurrency = kv_cache_per_request x concurrency
                             = {kv_details.per_request_kv_cache_gb} GB x {user_scenario.concurrency}
                             = {kv_details.kv_cache_size_gb} GB
""")



def hardware_specification():
    """
    Get hardware inputs like name and number of accelerators available
    """

    user_scenario = st.session_state[util.USER_SCENARIO_KEY]
    model_info = user_scenario.model_info
    model_config = user_scenario.model_config
    text_config = user_scenario.text_config

    concurrency = user_scenario.concurrency
    tp = user_scenario.tp_size
    pp = user_scenario.pp_size
    dp = user_scenario.dp_size

    # Hardware
    with st.container(border=True):
        st.write("**Hardware Specification**")
        st.caption("Identify suitable accelerators for serving the model based on parallelism optimization and workload.")

        if model_config is None:
            st.warning("Model config not found.")
            return None

        col1, col2 = st.columns([0.6, 0.4])

        index = 0
        if user_scenario.gpu_name in db.gpu_specs.keys():
            index = list(db.gpu_specs.keys()).index(user_scenario.gpu_name)

        col1.number_input("GPU utilization ratio",
                key=util.SELECTED_GPU_MEMORY_UTIL_KEY,
                value=user_scenario.gpu_mem_util,
                min_value=0.0,
                step=0.01,
                on_change=util.update_scenario,
                args=[util.SELECTED_GPU_MEMORY_UTIL_KEY, "gpu_mem_util"]
                )

        # Select GPU type
        selected_gpu_name = col1.selectbox("Accelerator",
                                key=util.SELECTED_GPU_NAME_KEY,
                                index=index,
                                options=db.gpu_specs,
                                on_change=util.update_scenario,
                                args=[util.SELECTED_GPU_NAME_KEY, "gpu_name"],
                                )

        # Dialog for registering new accelerator data
        col2.write("\n\nDon't see your accelerator? Register a new one below")
        if col2.button("Register new accelerator", use_container_width=True):
            register_new_accelerator()

        # For the selected GPU, show memory requirements
        if selected_gpu_name:

            # Get info
            gpu_memory = user_scenario.get_gpu_memory(db.gpu_specs)
            available_gpu_count = gpus_required(tp, pp, dp)
            available_gpu_mem = available_gpu_memory(gpu_memory, user_scenario.gpu_mem_util)

            try:
                model_size = model_memory_req(model_info, model_config)
            except Exception:
                st.warning("Model does not have safetensor data available, cannot estimate model memory.")
                return None

            model_size_per_gpu = per_gpu_model_memory_required(model_info, model_config, tp, pp)
            allocatable_kv_cache = allocatable_kv_cache_memory(model_info,
                                                    model_config,
                                                    gpu_memory,
                                                    user_scenario.gpu_mem_util,
                                                    tp,
                                                    pp,
                                                    dp,
                                                    )

            kv_details = KVCacheDetail(model_info, model_config,
                                    user_scenario.max_model_len,
                                    user_scenario.concurrency,
                                    )
            per_request_kv_cache_memory = kv_details.per_request_kv_cache_gb
            all_request_kv_cache_memory = kv_details.kv_cache_size_gb

            # Compute more info for pretty print
            total_memory = gpu_memory * available_gpu_count
            total_available_gpu_mem = available_gpu_mem * available_gpu_count
            reserved = total_memory - total_available_gpu_mem
            total_model_size = model_size * dp
            kv_cache_available_per_gpu = available_gpu_mem - model_size_per_gpu
            free = total_available_gpu_mem - total_model_size - all_request_kv_cache_memory

            st.caption(f"GPU memory: {gpu_memory} GB, available: {util.pretty_round(available_gpu_mem)} GB")

            # Determine if GPU has enough memory
            col1, col2 = st.columns([0.6, 0.4])

            col1.info(f"""Memory breakdown per GPU:
- Model weights: {util.pretty_round(model_size_per_gpu)} GB
- Free memory available for KV cache: {util.pretty_round(kv_cache_available_per_gpu)} GB
""")

            memory_util_chart(col1)

            with col1.expander("Total memory breakdown"):
                st.markdown(f"""
- Total memory: {gpu_memory * available_gpu_count} GB
- Reserved: {util.pretty_round(reserved)} GB
- Total memory available: {available_gpu_mem * available_gpu_count} GB
- Single model weights: {util.pretty_round(model_size)} GB
- Total model weights (for data parallelism): {util.pretty_round(total_model_size)} GB
- Allocatable KV cache memory: {util.pretty_round(allocatable_kv_cache)} GB
- KV cache per request: {util.pretty_round(per_request_kv_cache_memory)} GB
- KV cache for max concurrent requests: {util.pretty_round(all_request_kv_cache_memory)} GB
- Model + Max request KV cache: {util.pretty_round(total_model_size + all_request_kv_cache_memory)} GB
- Free: {util.pretty_round(free)} GB
    """)

            # Hints if gpu memory requirement exceeds available

            # if per_gpu_mem_required > available_gpu_mem:
            if free < 0:
                col2.error("""The accelerator selected does not have enough GPU memory. Here is what you can do:
- Select a GPU with higher memory
- Increase GPU utilization ratio
- Increase tensor parallelism or pipeline parallelism
- Decrease max model length
- Decrease max concurrency""")

            # Display vllm serve command for viable selection
            else:
                col2.success(f"""The overall configuration has enough memory to load the model and process the desired workload. You will need `{gpus_required(tp, pp, user_scenario.dp_size)}x{selected_gpu_name}`s for the selected scenario. Below is the general vLLM serve command.
""")
                vllm_serve_cmd = f"""vllm serve {user_scenario.model_name} \\
    --max-model-len {user_scenario.max_model_len} \\
    --gpu-memory-utilization {user_scenario.gpu_mem_util} \\
    --tensor-parallel-size {tp} \\
    --pipeline-parallel-size {pp} \\
    --data-parallel-size {user_scenario.dp_size}"""
                if user_scenario.enable_ep:
                    vllm_serve_cmd += f""" \\
    --enable-expert-parallel
        """
                col2.code(vllm_serve_cmd)

def memory_util_chart(st_context):
    """
    Show memory utilization chart
    """

    user_scenario = st.session_state[util.USER_SCENARIO_KEY]
    model_info = user_scenario.model_info
    model_config = user_scenario.model_config
    text_config = user_scenario.text_config
    gpu_memory = user_scenario.get_gpu_memory(db.gpu_specs)
    gpu_memory_util = user_scenario.gpu_mem_util
    concurrency = user_scenario.concurrency
    tp = user_scenario.tp_size
    pp = user_scenario.pp_size
    dp = user_scenario.dp_size

    # Display GPU + KV pie chart
    total_memory = gpus_required(tp, pp, dp) * gpu_memory
    available = gpus_required(tp, pp, dp) * available_gpu_memory(gpu_memory, gpu_memory_util)
    reserved = total_memory - available
    model_size = model_memory_req(model_info, model_config) * dp
    max_concurrency_kv_cache = kv_cache_req(model_info, model_config, user_scenario.max_model_len, concurrency)
    free = available - model_size - max_concurrency_kv_cache

    if free < 0:
        st.warning(f"Memory exceeds available by {abs(util.pretty_round(free))} GB.")
        return None

    # Display chart iff model and cache size are selected
    labels = ["Model", "KV Cache", "Free", "Reserved"]
    sizes = [util.pretty_round(model_size), util.pretty_round(max_concurrency_kv_cache), util.pretty_round(free), util.pretty_round(reserved)]
    colors = ["#ff9999", "#66b3ff", "#99ff99", "#808080"]

    # Create donut chart
    fig, ax = plt.subplots(figsize=(4, 4))
    wedges, texts = ax.pie(
        sizes,
        colors=colors,
        startangle=90,               # Start at top
        wedgeprops=dict(width=0.4),   # <-- Makes it a donut,
        labeldistance=1.1,   # Push labels outward
        pctdistance=0.7,      # Adjust percentage position
    )

    # Add total as text in the center of the donut
    ax.text(0, 0, f"Total\n{util.pretty_round(total_memory)} GB", ha="center", va="center", fontsize=12, fontweight="bold")

    # Create a custom legend, including the total
    legend_labels = [f"{labels[i]}: {sizes[i]} GB" for i in range(len(labels))]

    # Position legend on the right
    ax.legend(
        wedges + [plt.Line2D([0], [0], color="#CCCCCC", lw=10)],  # Add fake handle for total
        legend_labels,
        title="Total Storage Breakdown",
        loc="center left",
        bbox_to_anchor=(1, 0, 0.5, 1)
    )

    # Render in Streamlit
    _, col, _ = st_context.columns([.5, 1, .5])
    with col:
        st.pyplot(fig, bbox_inches="tight")

if __name__ == '__main__':

    # Set up streamlit config
    st.set_page_config(page_title="Configuration Explorer",
                       page_icon=None,
                       layout="wide",
                       initial_sidebar_state="expanded",
                       menu_items=None)

    st.title("Configuration Explorer")
    st.caption("This tool helps you find the most cost-effective, optimal configuration for serving models on llm-d based on hardware specification, workload characteristics, and SLO requirements.")

    util.init_session_state()

    # Display Capacity Planner headings
    st.subheader("Capacity Planner")
    st.caption("Determine how many GPUs you need to fit your model and how many requests can be served at once depending on request patterns.")

    # Get user inputs and show outputs
    model_specification()
    parallelism_specification()
    workload_specification()
    hardware_specification()