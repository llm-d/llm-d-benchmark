"""
Main Page
"""

from matplotlib import pyplot as plt
import streamlit as st
import db
import util
import numpy as np
from src.config_explorer.capacity_planner import *
from huggingface_hub.errors import *

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
                user_scenario.model_config = model_config
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
                total_params = model_total_params(model_info)
                precision_keys = model_precision_keys(model_info)
                model_gpu_memory_req = round(model_memory_req(model_info))
            except Exception as e:
                st.warning(f"Cannot retrieve relevant information about the model, {e}")
                return None

            # Display first precision
            st.caption(f"Precision: {', '.join(precision_keys)}")
            st.caption(f"Total parameters: {total_params}")
            st.caption(f"GPU memory requirement: ~{model_gpu_memory_req} GB")

        else:
            return None

def hardware_specification():
    """
    Get hardware inputs like name and number of accelerators available
    """

    user_scenario = st.session_state[util.USER_SCENARIO_KEY]
    model_info = user_scenario.model_info
    model_config = user_scenario.model_config
    tp = user_scenario.tp_size
    pp = user_scenario.pp_size

    # Hardware
    with st.container(border=True):
        st.write("**Hardware Specification**")
        st.caption("Identify suitable accelerators for serving the model based on parallelism optimization and workload.")

        col1, col2 = st.columns([0.7, 0.3])

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
        col2.info("Don't see your accelerator? Register a new one below")
        if col2.button("Register new accelerator", use_container_width=True):
            register_new_accelerator()

        # For the selected GPU, show memory requirements
        if selected_gpu_name:
            gpu_memory = user_scenario.get_gpu_memory(db.gpu_specs)
            available_gpu_mem = available_gpu_memory(gpu_memory, user_scenario.gpu_mem_util)
            available_gpu_mem_rounded = round(available_gpu_mem, 2)
            st.caption(f"GPU memory: {gpu_memory} GB, available: {available_gpu_mem_rounded} GB")

            # Determine if GPU has enough memory
            per_gpu_model_memory_req = per_gpu_model_memory_required(model_info, tp, pp)
            per_request_kv_cache_memory = kv_cache_req(model_info,
                                    model_config,
                                    user_scenario.max_model_len,
                                    )
            max_request_kv_cache_memory = kv_cache_req(model_info,
                                    model_config,
                                    user_scenario.max_model_len,
                                    user_scenario.concurrency,
                                    )
            per_gpu_mem_required = per_gpu_memory_required(model_info,
                                      model_config,
                                      user_scenario.max_model_len,
                                      user_scenario.concurrency,
                                      tp,
                                      pp,
                                      )

            col1, col2 = st.columns(2)
            col1.info(f"""Memory breakdown per GPU:
- Model weights: ~{round(per_gpu_model_memory_req, 2)} GB
- KV cache per request: ~{round(per_request_kv_cache_memory, 2)} GB
- KV cache for max concurrent requests: ~{round(max_request_kv_cache_memory, 2)} GB
- Total: ~{round(per_gpu_mem_required, 2)} GB
""")

            # Hints for gpu memory requirement exceeding available
            if per_gpu_mem_required > available_gpu_mem:
                col2.error("""The accelerator selected does not have enough GPU memory. Here is what you can do:
- Select a GPU with higher memory
- Increase GPU utilization ratio
- Increase tensor parallelism or pipeline parallelism
- Decrease max model length
- Decrease max concurrency""")

            # Display vllm serve command for viable selection
            else:
                col2.success(f"""Great, the GPU you selected has enough memory to load the model and process the desired workload. You will need `{gpus_required(tp, pp, user_scenario.dp_size)}x{selected_gpu_name}`s for the selected scenario.
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

def parallelism_specification():
    """
    Parallelism strategies
    """
    user_scenario = st.session_state[util.USER_SCENARIO_KEY]
    model_config = user_scenario.model_config
    total_accelerators = user_scenario.gpu_count_avail
    gpu_memory = user_scenario.get_gpu_memory(db.gpu_specs)

    with st.container(border=True):
        st.write("**Parallelism Strategies**")
        st.caption("Parallelism optimization determines the number of GPUs required.")

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
            help = "Tensor parallel size is used as the EP size."

        col2.write("")
        col2.write("")
        enable_ep = col2.toggle("Enable expert parallelism",
                value=user_scenario.enable_ep,
                disabled=not is_moe_model,
                help=help,
                key=util.SELECTED_ENABLE_EP_KEY,
                on_change=util.update_scenario,
                args=[util.SELECTED_ENABLE_EP_KEY, "enable_ep"]
                )
        if enable_ep:
            ep_per_gpu = round(experts_per_gpu(model_config,
                                                tp=user_scenario.tp_size,
                                                pp=user_scenario.pp_size,
                                                dp=user_scenario.dp_size))
            col2.caption(f"There are a total of {get_num_experts(model_config)} experts for this model. \
                            There will be ~{ep_per_gpu} experts per GPU. \
                            Note that vLLM handles uneven splits of experts (see this [PR](https://github.com/vllm-project/vllm/pull/21497)).")

        st.info(f"GPUs required: `{gpus_required(tp_size, pp_size, dp_size)}`")


def workload_specification():
    """
    Estimate total memory needed for KV cache
    """

    user_scenario = st.session_state[util.USER_SCENARIO_KEY]
    model_info = user_scenario.model_info
    model_config = user_scenario.model_config
    total_gpu_avail = user_scenario.gpu_count_avail

    # Workload
    with st.container(border=True):
        st.write("**Workload Characteristics**")
        st.caption("Estimate KV cache memory requirements for the selected model based on workload.")

        if model_info is None:
            st.warning("Model information not yet selected")
            return None
        if model_config is None:
            st.warning("Model config not available, cannot estimate KV cache size")
            return None

        col1, col2 = st.columns(2)

        model_max_context_len = max_context_len(model_config)
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

# def memory_util_chart():
#     """
#     Show memory utilization chart
#     """

#     user_scenario = st.session_state[util.USER_SCENARIO_KEY]
#     model_info = user_scenario.model_info
#     model_config = user_scenario.model_config
#     min_gpu_required = min_gpu_req(model_info,
#                                    user_scenario.get_gpu_memory(db.gpu_specs),
#                                    gpu_mem_util=user_scenario.gpu_mem_util,
#                                    tp_size=user_scenario.tp_size,
#                                    dp_size=user_scenario.dp_size)

#     # Display GPU + KV pie chart for cluster
#     if user_scenario.can_show_mem_util_chart(min_gpu_required):
#         model_size = round(model_memory_req(model_info), 2)
#         kv_cache = 0
#         total = 0
#         free = 0

#         kv_cache = kv_cache_req(model_info,
#                                 model_config,
#                                 context_len=user_scenario.max_model_len,
#                                 batch_size=user_scenario.concurrency,
#                                 )
#         kv_cache = round(kv_cache, 2)
#         total = user_scenario.gpu_count_avail * user_scenario.get_gpu_memory(db.gpu_specs)
#         free = round(total - model_size - kv_cache, 2)

#         if free < 0:
#             st.warning(f'Memory usage exceeds available by {-free:.1f} GB')
#             free = 0
#             return None

#         # Display chart iff model and cache size are selected
#         labels = ["Model", "KV Cache", "Free"]
#         sizes = [model_size, kv_cache, free]
#         colors = ["#ff9999", "#66b3ff", "#99ff99"]

#         # Create donut chart
#         fig, ax = plt.subplots(figsize=(4, 4))
#         wedges, texts = ax.pie(
#             sizes,
#             colors=colors,
#             startangle=90,               # Start at top
#             wedgeprops=dict(width=0.4),   # <-- Makes it a donut,
#             labeldistance=1.1,   # Push labels outward
#             pctdistance=0.7,      # Adjust percentage position
#         )

#         # Add total as text in the center of the donut
#         ax.text(0, 0, f"Total\n{total} GB", ha="center", va="center", fontsize=12, fontweight="bold")

#         # Create a custom legend, including the total
#         legend_labels = [f"{labels[i]}: {sizes[i]} GB" for i in range(len(labels))]

#         # Position legend on the right
#         ax.legend(
#             wedges + [plt.Line2D([0], [0], color="#CCCCCC", lw=10)],  # Add fake handle for total
#             legend_labels,
#             title="Total GPU Memory Breakdown",
#             loc="center left",
#             bbox_to_anchor=(1, 0, 0.5, 1)
#         )

#         # Render in Streamlit
#         cols = st.columns([0.5, 1, 0.5])
#         with cols[1]:
#             st.pyplot(fig, bbox_inches="tight")


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