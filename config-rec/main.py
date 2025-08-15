"""
A streamlit frontend for the Config Recommendation Tool
"""

import streamlit as st
import util
import db
import math

from dataclasses import dataclass

@dataclass
class Model:
    """Model stores info about a HuggingFace model"""
    name: str | None = None
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

    # GPU
    gpu_spec: dict | None = None

    def get_memory_req(self) -> int:
        """
        Returns memory requirement for the model
        """
        if "16" in self.precision:

            # M = param * 4 bytes_per_param / (32 / quantization) * 1.2
            # Assume 16-bit loading on GPU
            return (self.parameters * 2) / (32 / 16) * 1.2

        else:
            return -1

    def get_min_gpu_count(self) -> int:

        # https://blog.eleuther.ai/transformer-math/
        # https://ksingh7.medium.com/calculate-how-much-gpu-memory-you-need-to-serve-any-llm-67301a844f21
        model_memory = self.get_memory_req()
        if model_memory == -1:
            return model_memory

        gpu_memory_bytes =  self.gpu_spec['memory'] * 1e+9
        return math.ceil(model_memory / gpu_memory_bytes)

def inputs(col):
    """
    Inputs to the recommender
    """
    col.header("Inputs")

    user_model = Model()

    # Model
    with col.container(border=True):
        st.write("**Model Specification**")
        selected_model = st.text_input("Model (Hugging Face format)")

        if selected_model:
            info = util.get_model_info_from_hf(selected_model)
            user_model.name = selected_model
            user_model.parameters = info.safetensors.total

            # Precisions supported
            user_model.precision = st.selectbox("Select a precision",
                                              options=info.safetensors.parameters.keys())

            st.caption(f"Total parameters: {info.safetensors.parameters[user_model.precision]}")
            memory_req = round(user_model.get_memory_req() / 1e+9)
            st.caption(f"GPU memory requirement: {memory_req} GB")

    # Hardware
    with col.container(border=True):
        st.write("**Hardware Specification**")
        selected_gpu = st.selectbox("Accelerator", options=db.gpu_specs.keys())
        if selected_gpu:
            user_model.gpu_spec = db.gpu_specs[selected_gpu]
            st.caption(f"GPU memory: {user_model.gpu_spec['memory']} GB")

            # Calculate the minimum number of GPUs required
            min_gpu_req = user_model.get_min_gpu_count()

        user_model.tp = st.number_input("Number accelerators available", step=1, min_value=min_gpu_req)
        st.caption(f"Loading this model on the selected GPU in 16-bit mode requires a minimum of {min_gpu_req}, which does not yet account for KV cache.")

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
        user_model.block_size = st.select_slider("Block size", options=[1, 8, 16, 32, 64, 128], help="Size of a contiguous cache block in number of tokens. This is ignored on neuron devices and set to `--max-model-len`. On CUDA devices, only block sizes up to 32 are supported. On HPU devices, block size defaults to 128.")
        user_model.gpu_mem_utilization = st.number_input("GPU memory utilization",
                        value=0.9,
                        min_value=0.0,
                        max_value=1.0,
                        step=0.1,
                        help="""\
The fraction of GPU memory to be used for the model executor, which can range from 0 to 1. For example, a value of 0.5 would imply 50%% GPU memory utilization. If unspecified, will use the default value of 0.9. This is a per-instance limit, and only applies to the current vLLM instance. It does not matter if you have another vLLM instance running on the same GPU. For example, if you have two vLLM instances running on the same GPU, you can set the GPU memory utilization to 0.5 for each instance.""")
        user_model.enable_prefix_caching = st.checkbox("Enable prefix caching")

        # ------------------------------------------------------------------------------------------
        st.write("*vLLM Config*")
        user_model.pp = st.slider("Pipeline parallel size", step=1, min_value=1, max_value=10, help="partition model layers across GPUs")
        user_model.dp = st.slider("Data parallel size", step=1, min_value=1, max_value=10, help="partition input tensor across devices")
        user_model.tp = st.slider("Tensor parallelism", step=1, min_value=user_model.get_min_gpu_count(), max_value=10, help="partition model parameters across GPUs")

        # ------------------------------------------------------------------------------------------
        st.write("*Scheduler Config*")
        user_model.enable_chunked_prefill = st.checkbox("Enable chunked prefill")
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

    return user_model

def outputs(col, user_model: Model):
    """
    Determine the optimal configuration
    """
    col.header("Optimal Configuration")

    selected_model = user_model.name if user_model.name else "(no model selected)"
    if selected_model == 'gemma3:1b':
        col.warning("This model will not run on the specified hardware.")
    else:
        col.write(f"Based on your inputs, we recommend the following configuration to serve `{selected_model}`.")

        col.write("This suggested deployment will cost `$N/million input tokens`")
        col.markdown("""**Estimated SLO**

* Throughput: `XXX tokens/sec`
* TTFT: `YYY s`
* TPOT: `ZZZ s`
    """)

        vllm_command = f"""vllm serve {user_model.name} \\
        --block-size {str(user_model.block_size)}
        --pp {str(user_model.pp)} \\
        --dp {str(user_model.dp)}  \\
        --tp {str(user_model.tp)}"""

        if user_model.enable_prefix_caching:
            vllm_command += """ \\
        -enable-prefix-caching"""

        if user_model.enable_chunked_prefill:
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