"""
A streamlit frontend for the Config Recommendation Tool
"""

import streamlit as st


def inputs(col):
    """
    Inputs to the recommender
    """
    col.header("Inputs")

    user_inputs = {}

    # Model
    with col.container(border=True):
        st.write("**Model Specification**")
        selected_model = st.selectbox("Model", options=[
            "deepseek-r1:1.5b", "gemma3:1b",
            "qwen3:4b", "qwen2.5:0.5b",
            "llama3.1:8b", "llama3.2:1b",
            "granite3.3:2b", "granite3.3:8b"],
            index=None,
        )

        if selected_model:
            st.caption("This model is....")

    # Hardware
    with col.container(border=True):
        st.write("**Hardware Specification**")
        st.selectbox("Accelerator", options=[
            "NVIDIA-A100",
            "NVIDIA-H100",
            "NVIDIA-L40S",
            "AMD-MI300X"
        ])

        st.number_input("Number accelerators available", step=1, min_value=1)

    # Router
    with col.container(border=True):
        st.write("**Router Specification**")
        st.checkbox("Prefix aware")
        st.checkbox("KV cache aware")
        st.text_input("Enter Inference Scheduler config")


    # vLLM Config
    with col.container(border=True):
        st.write("**vLLM Configuration**")

        # ------------------------------------------------------------------------------------------
        st.write("*Cache Config*")
        selected_block_size = st.select_slider("Block size", options=[1, 8, 16, 32, 64, 128], help="Size of a contiguous cache block in number of tokens. This is ignored on neuron devices and set to `--max-model-len`. On CUDA devices, only block sizes up to 32 are supported. On HPU devices, block size defaults to 128.")
        st.number_input("GPU memory utilization",
                        value=0.9,
                        min_value=0.0,
                        max_value=1.0,
                        step=0.1,
                        help="""\
The fraction of GPU memory to be used for the model executor, which can range from 0 to 1. For example, a value of 0.5 would imply 50%% GPU memory utilization. If unspecified, will use the default value of 0.9. This is a per-instance limit, and only applies to the current vLLM instance. It does not matter if you have another vLLM instance running on the same GPU. For example, if you have two vLLM instances running on the same GPU, you can set the GPU memory utilization to 0.5 for each instance.""")
        enable_prefix_caching = st.checkbox("Enable prefix caching")

        # ------------------------------------------------------------------------------------------
        st.write("*vLLM Config*")
        pp_size = st.slider("Pipeline parallel size", step=1, min_value=1, max_value=10, help="partition model layers across GPUs")
        dp_size = st.slider("Data parallel size", step=1, min_value=1, max_value=10, help="partition input tensor across devices")
        tp_size = st.slider("Tensor parallelism", step=1, min_value=1, max_value=10, help="partition model parameters across GPUs")

        # ------------------------------------------------------------------------------------------
        st.write("*Scheduler Config*")
        enable_chunked_prefill = st.checkbox("Enable chunked prefill")
        st.number_input("Max num batched tokens", min_value=1, max_value=2048, step=1)

    # Workload
    with col.container(border=True):
        st.write("**Workload Characteristics**")
        selected_workload = st.selectbox("Workload", options=[
            "Interactive Chat",
            "Text classification",
            "Image classification",
            "Summarization/Information Retrieval",
            "Text Generation",
            "Translation",
            "Code Completion",
            "Code Generation"
        ])

        st.caption("""This workload uses the XXX dataset. This workload is primarily for YYY purposes.
* Input Sequence Length (ISL): Medium
* Output Sequence Length (OSL): High
                """)

        with col.container(border=True):
            st.write("**SLO Requirements**")
            st.number_input("Min throughput (otuput tokens/sec)", min_value=1, step=1)
            st.slider("Max latency (TTFT)", min_value=1.0, max_value=1000.0, step=0.01)
            st.slider("Max latency (TPOT)", min_value=1.0, max_value=1000.0, step=0.01)

    # Populate user input
    user_inputs['model'] = selected_model
    user_inputs['block_size'] = selected_block_size
    user_inputs['pp_size'] = pp_size
    user_inputs['dp_size'] = dp_size
    user_inputs['tp_size'] = dp_size
    user_inputs['enable_prefix_caching'] = enable_prefix_caching
    user_inputs['enable_chunked_prefill'] = enable_chunked_prefill

    return user_inputs

def outputs(col, user_inputs):
    """
    Determine the optimal configuration
    """
    col.header("Optimal Configuration")

    selected_model = user_inputs['model'] if user_inputs['model'] else "(no model selected)"
    if selected_model == 'gemma3:1b':
        col.warning("This model will not run on the specified hardware.")
    else:
        col.write(f"Based on your inputs, we recommend the following configuration to serve `{selected_model}`.")

        col.write("This deployment will cost `$N/million input tokens`")
        col.markdown("""**Estimated SLO**
    * Throughput: `XXX tokens/sec`
    * TTFT: `YYY s`
    * TPOT: `ZZZ s`
    """)

        vllm_command = f"""vllm serve {selected_model} \\
        --block-size {str(user_inputs['block_size'])}
        --pp {str(user_inputs['pp_size'])} \\
        --dp {str(user_inputs['dp_size'])}  \\
        --tp {str(user_inputs['tp_size'])}"""

        if user_inputs['enable_prefix_caching']:
            vllm_command += """ \\
        -enable-prefix-caching"""

        if user_inputs['enable_chunked_prefill']:
            vllm_command += """ \\
        -enable_chunked_prefill"""

        col.code(vllm_command)

        col.write("**Routing configuration**")
        col.code("""apiVersion: inference.networking.x-k8s.io/v1alpha1
    kind: EndpointPickerConfig
    plugins:
    - type: prefix-cache-scorer
        parameters:
        hashBlockSize: 5
        maxPrefixBlocksToMatch: 256
        lruCapacityPerServer: 31250
    - type: decode-filter
    - type: max-score-picker
    - type: single-profile-handler
    schedulingProfiles:
    - name: default
        plugins:
        - pluginRef: decode-filter
        - pluginRef: max-score-picker
        - pluginRef: prefix-cache-scorer
        weight: 50
    """, language='yaml')

        with col.container(border=True):
            st.write("**Configuration Matrix**")
            data = {
                "DP": [1, 2, 3, 4, 5, 6],
                "PP": [1, 2, 3, 4, 5, 6],
                "TP": [1, 2, 3, 4, 5, 6],
                "BlockSize": [1, 8, 16, 32, 64, 128],
                "Cost ($/million tokens)": [1.15, 1.23, 1.43, 4.65, 5.23, 2.32],
                "TTFT": [14.34, 21.34, 5.54, 3.34, 1.23, 1.43],
                "TPOT": [14.34, 21.34, 5.54, 3.34, 1.23, 1.43]
            }
            st.dataframe(data)

if __name__ == '__main__':

    # Set up streamlit config
    st.set_page_config(page_title="Configuration Recommendation Tool",
                       page_icon=None,
                       layout="wide",
                       initial_sidebar_state="expanded",
                       menu_items=None)

    st.title("Configuration Recommendation")
    st.caption("Finding the most cost-effective, optimal configuration for serving models on llm-d based on hardware specification, workload characteristics, and SLO requirements.")


    # Input
    col1, col2 = st.columns(2)
    user_inputs = inputs(col1)

    outputs(col2, user_inputs)