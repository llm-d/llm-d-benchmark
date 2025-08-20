"""
Main Page
"""

from matplotlib import pyplot as plt
import streamlit as st
import db
import util
import pandas as pd
import plotly.express as px
import numpy as np

def init_session_state():
    """
    Inits session state for data persistence
    """

    if 'scenario' not in st.session_state:
        st.session_state['scenario'] = util.Scenario()
    if 'gpu_spec' not in st.session_state:
        st.session_state["gpu_spec"] = db.gpu_specs

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

    # Model
    with st.container(border=True):
        st.write("**Model Specification**")
        selected_model = st.text_input("Model (Hugging Face format)",
                                       value=user_scenario.model_name,
                                       placeholder="ibm-granite/granite-3.3-8b-instruct",
                                       )

        if selected_model and selected_model != "":

            info = util.get_model_info_from_hf(selected_model)
            if info is None:
                st.warning("Model not found on Hugging Face. Please verify model name is in repo/modelID format.")
                return None

            user_scenario.model_name = selected_model
            user_scenario.parameters = info.safetensors.total

            # Precisions supported
            selected_precision_index = util.PRECISIONS.index(user_scenario.precision) if user_scenario.precision else 0
            user_scenario.precision = st.selectbox("Select a precision",
                                                index=selected_precision_index,
                                                options=util.PRECISIONS,
                                                )

            st.caption(f"Total parameters: {user_scenario.parameters}")
            st.caption(f"GPU memory requirement: ~{user_scenario.get_gpu_mem_in_gb()} GB")

        else:
            return None

    # Hardware
    with st.container(border=True):
        st.write("**Hardware Specification**")

        col1, col2 = st.columns([0.7, 0.3])

        gpu_spec_options = list(st.session_state['gpu_spec'].keys())
        gpu_spec_accelerator_index = gpu_spec_options.index(user_scenario.gpu_spec['name']) if user_scenario.gpu_spec else 0
        selected_gpu = col1.selectbox("Accelerator",
                                      index=gpu_spec_accelerator_index,
                                      options=gpu_spec_options,
                                      )
        if selected_gpu:
            user_scenario.gpu_spec = st.session_state['gpu_spec'][selected_gpu]
            st.caption(f"GPU memory: {user_scenario.gpu_spec['memory']} GB")

            # Calculate the minimum number of GPUs required
            min_gpu_req = user_scenario.get_min_gpu_count()

        default_value = min_gpu_req if user_scenario.gpu_count_avail is None else user_scenario.gpu_count_avail
        user_scenario.gpu_count_avail = st.number_input("Number accelerators available",
                                        value=default_value,
                                        step=1,
                                        min_value=min_gpu_req)

        # Dialog for registering new accelerator data
        col2.info("Don't see your accelerator? Register a new one below")
        if col2.button("Register new accelerator", use_container_width=True):
            register_new_accelerator()

        st.success(f"Loading this model on the selected GPU in {user_scenario.precision} mode requires a minimum of {min_gpu_req}, which does not yet account for KV cache.")

def kv_cache_estimator():
    """
    Estimate total memory needed for KV cache
    """

    user_scenario = st.session_state['scenario']

    st.subheader("KV Cache Estimator")
    st.caption("Estimate KV cache memory requirements for the selected model based on workload.")

    # Workload
    with st.container(border=True):
        st.write("**Workload Characteristics**")

        workload_list = list(db.workload.keys())
        scenario_workload_index = 0 if user_scenario.workload is None else workload_list.index(user_scenario.workload['name'])

        selected_workload = st.selectbox("Workload",
                                        index=scenario_workload_index,
                                         options=workload_list,
        )
        if selected_workload:
            user_scenario.workload = db.workload[selected_workload]

            isl_str = user_scenario.workload['itl']
            osl_str = user_scenario.workload['otl']
            isl = util.length_description_to_token(isl_str)
            osl = util.length_description_to_token(osl_str)

            st.caption(f"""This workload uses the XXX dataset. This workload is primarily for YYY purposes.
* Input Sequence Length (ISL): {isl_str} / ~{isl}
* Output Sequence Length (OSL): {osl_str} / ~{osl}
            """)

            col1, col2 = st.columns(2)
            user_scenario.isl = col1.number_input("Input sequence length (prompt + context)",
                                                value=isl if user_scenario.isl is None else user_scenario.isl,
                                                min_value=1,
                                                step=1,
                                                )

            user_scenario.osl = col2.number_input("Output sequence length",
                                                value=osl if user_scenario.osl is None else user_scenario.osl,
                                                min_value=1,
                                                step=1,
                                                )


            #st.info(f"Estimated KV cache size requirement: ~{user_scenario.get_kv_cache_req()} GB")


        model_size = 0
        kv_cache = 0
        free = 0

        # Display GPU + KV pie chart
        if user_scenario.model_name:
            model_size = user_scenario.get_gpu_mem_in_gb()
        if user_scenario.workload:
            kv_cache = user_scenario.get_kv_cache_req()
        if model_size > 0 and kv_cache > 0:
            free = user_scenario.free_memory()

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
        ax.text(0, 0, f"Total\n{user_scenario.gpu_count_avail * user_scenario.gpu_spec['memory']} GB", ha="center", va="center",
            fontsize=16, fontweight="bold")


        # Equal aspect ratio ensures it's circular
        ax.axis("equal")
        plt.rcParams['axes.titley'] = 1.1
        ax.set_title('Memory Utilization', fontsize='20')

        # Render in Streamlit
        col1, col2, _ = st.columns([.5, 1, .5])
        with col2:
            st.pyplot(fig)
        #st.pyplot(fig, use_container_width=False)

if __name__ == '__main__':

    # Set up streamlit config
    st.set_page_config(page_title="Configuration Recommendation Tool",
                       page_icon=None,
                       layout="wide",
                       initial_sidebar_state="expanded",
                       menu_items=None)

    st.title("Configuration Recommendation")
    st.caption("Finding the most cost-effective, optimal configuration for serving models on llm-d based on hardware specification, workload characteristics, and SLO requirements.")

    init_session_state()

    # Model spec input
    capacity_planner()

    # KV cache estimation
    kv_cache_estimator()