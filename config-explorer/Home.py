"""
Main Page
"""

from matplotlib import pyplot as plt
import streamlit as st
import db
import util
import numpy as np
from src.config_explorer.functions import *

def init_session_state():
    """
    Inits session state for data persistence
    """

    if 'scenario' not in st.session_state:
        st.session_state['scenario'] = util.Scenario()
    if 'gpu_spec' not in st.session_state:
        st.session_state["gpu_spec"] = db.gpu_specs

def update_gpu_spec():
    st.session_state['scenario'].gpu_spec = st.session_state['gpu_spec'][st.session_state['selected_gpu_spec']]

def update_gpu_count_avail():
    st.session_state['scenario'].gpu_count_avail = st.session_state['selected_gpu_count_avail']

def update_isl():
    st.session_state['scenario'].isl = st.session_state['selected_isl']

def update_osl():
    st.session_state['scenario'].osl = st.session_state['selected_osl']

@st.dialog("Register a new accelerator")
def register_new_accelerator():
    """
    Dialog to register a new accelerator type
    """
    acc_name = st.text_input("Name", placeholder="NVIDIA-A100-40GB")
    acc_mem = st.number_input("Memory (GB)", min_value=1, step=1)

    if st.button("Register", use_container_width=True):
        if acc_name:
            st.session_state["gpu_spec"][acc_name] = {
                "name": acc_name,
                "memory": acc_mem
            }
            st.rerun()

def capacity_planner():
    """
    Get model inputs like model name, precision
    """

    st.subheader("Capacity Planner")
    st.caption("Determine if your model will fit on _n_ XXX GPU.")

    user_scenario = st.session_state['scenario']
    model_info = None

    # Model
    with st.container(border=True):
        st.write("**Model Specification**")
        selected_model = st.text_input("Model (Hugging Face format)",
                                       #value=user_scenario.model_name,
                                       value='RedHatAI/Llama-3.3-70B-Instruct-FP8-dynamic',
                                       placeholder="ibm-granite/granite-3.3-8b-instruct",
                                       )

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
                model_config = get_model_config_from_hf(selected_model)
                user_scenario.model_config = model_config
            except Exception as e:
                st.warning("Cannot access model config, see error below.")
                st.warning(e)
                return None

            user_scenario.model_name = selected_model
            total_params = model_total_params(model_info)
            precision_keys = model_precision_keys(model_info)
            gpu_memory_req = round(model_memory_req(model_info))

            # Display precision
            st.caption(f"Precision: {', '.join(precision_keys)}")

            # Record the first precision
            user_scenario.precision = precision_keys[0]

            st.caption(f"Total parameters: {total_params}")
            st.caption(f"GPU memory requirement: ~{gpu_memory_req} GB")

        else:
            return None

    # Hardware
    with st.container(border=True):
        st.write("**Hardware Specification**")

        col1, col2 = st.columns([0.7, 0.3])

        gpu_spec_options = list(st.session_state['gpu_spec'].keys())
        col1.selectbox("Accelerator",
                        key="selected_gpu_spec",
                        index=0,
                        options=gpu_spec_options,
                        on_change=update_gpu_spec,
                        )

        if st.session_state['selected_gpu_spec']:
            selected_gpu = st.session_state['selected_gpu_spec']
            user_scenario.gpu_spec = st.session_state['gpu_spec'][selected_gpu]
            gpu_memory = user_scenario.gpu_spec['memory']
            st.caption(f"GPU memory: {gpu_memory} GB")

            # Calculate the minimum number of GPUs required
            min_gpu_needed = min_gpu_req(model_info, gpu_memory)
            st.warning(f"Loading this model on the selected GPU requires at least `{min_gpu_needed}`")
            st.number_input("Number accelerators available",
                            key='selected_gpu_count_avail',
                            value=user_scenario.gpu_count_avail,
                            step=1,
                            on_change=update_gpu_count_avail,
                            min_value=0,
                            )

            if user_scenario.gpu_count_avail is None or user_scenario.gpu_count_avail < min_gpu_needed:
                st.error("Not enough GPU memory to load the model.")

        # Dialog for registering new accelerator data
        col2.info("Don't see your accelerator? Register a new one below")
        if col2.button("Register new accelerator", use_container_width=True):
            register_new_accelerator()

def kv_cache_estimator():
    """
    Estimate total memory needed for KV cache
    """

    user_scenario = st.session_state['scenario']
    model_info = user_scenario.model_info
    model_config = user_scenario.model_config

    st.subheader("KV Cache Estimator")
    st.caption("Estimate KV cache memory requirements for the selected model based on workload.")

    # Workload
    with st.container(border=True):
        st.write("**Workload Characteristics**")

        if model_info is None:
            st.warning("Model information not yet selected")
            return None
        if model_config is None:
            st.warning("Model config not available")
            return None

        col1, col2 = st.columns(2)
        col1.number_input("Input sequence length (prompt + context)",
                            value=1 if user_scenario.isl is None else user_scenario.isl,
                            min_value=1,
                            step=1,
                            key="selected_isl",
                            on_change=update_isl,
                            )

        col2.number_input("Output sequence length",
                            value=1 if user_scenario.osl is None else user_scenario.osl,
                            min_value=1,
                            step=1,
                            key="selected_osl",
                            on_change=update_osl,
                            )

        user_context_len = user_scenario.isl + user_scenario.osl
        max_model_ctx_len = max_context_len(model_config)
        if user_context_len > max_model_ctx_len:
            st.error(f"Input and output lengths exceed the max context length accepted by model ({max_model_ctx_len})")
            return None

        model_size = 0
        kv_cache = 0
        total = 0
        free = 0

        # Display GPU + KV pie chart
        if user_scenario.model_name:
            model_size = round(model_memory_req(model_info), 2)

        if user_scenario.isl and user_scenario.osl:
            kv_mem_req = kv_cache_req(model_info,
                                    model_config,
                                    context_len=user_scenario.isl + user_scenario.osl
                                      )
            kv_cache = round(kv_mem_req, 2)
            st.info(f"About ~{kv_cache} GB of KV cache is required.")
        if user_scenario.gpu_count_avail  is not None:
            total = user_scenario.gpu_count_avail * user_scenario.gpu_spec['memory']
            free = total - model_size - kv_cache

        if free < 0:
            st.warning(f'Memory usage exceeds available by {-free:.1f} GB')
            free = 0

        # Display chart iff model and cache size are selected
        if model_size > 0 and \
            kv_cache > -1 and \
            user_scenario.gpu_count_avail is not None and \
            user_scenario.gpu_count_avail >= min_gpu_req(model_info, user_scenario.gpu_spec['memory']):

            labels = ["Model", "KV Cache", "Free"]
            sizes = [model_size, kv_cache, free]
            colors = ["#ff9999", "#66b3ff", "#99ff99"]


            # Create donut chart
            fig, ax = plt.subplots(figsize=(4, 4))
            wedges, texts = ax.pie(
                sizes,
                colors=colors,
                startangle=90,               # Start at top
                wedgeprops=dict(width=0.4)   # <-- Makes it a donut
            )


            # Draw labels outside the chart with connection lines
            # `labeldistance` and `arrowprops` allow pointing labels
            kw = dict(arrowprops=dict(arrowstyle="-"),
                    zorder=0, va="center")

            for ii, pp in enumerate(wedges):
                ang = (pp.theta2 - pp.theta1)/2. + pp.theta1
                yy = np.sin(np.deg2rad(ang))
                xx = np.cos(np.deg2rad(ang))
                horizontalalignment = {-1: "right", 1: "left"}[int(np.sign(xx))]
                connectionstyle = f"angle,angleA=0,angleB={ang}"
                kw["arrowprops"].update({"connectionstyle": connectionstyle})
                ax.annotate(f"{labels[ii]} {sizes[ii]:.1f} GB", xy=(xx,yy), xytext=(1.3*np.sign(xx), 1.3*yy),
                            horizontalalignment=horizontalalignment, **kw, fontsize='14')

        # Add internal label
            ax.text(0, 0, f"Total\n{total} GB", ha="center", va="center",
                fontsize=16, fontweight="bold")


            # Equal aspect ratio ensures it's circular
            ax.axis("equal")
            plt.rcParams['axes.titley'] = 1.1
            ax.set_title('Memory Utilization', fontsize='20')

            # Render in Streamlit
            _, col, _ = st.columns([.5, 1, .5])
            with col:
                st.pyplot(fig)

if __name__ == '__main__':

    # Set up streamlit config
    st.set_page_config(page_title="Configuration Explorer",
                       page_icon=None,
                       layout="wide",
                       initial_sidebar_state="expanded",
                       menu_items=None)

    st.title("Configuration Explorer")
    st.caption("Finding the most cost-effective, optimal configuration for serving models on llm-d based on hardware specification, workload characteristics, and SLO requirements.")

    init_session_state()

    # Model spec input
    capacity_planner()

    # KV cache estimation
    kv_cache_estimator()