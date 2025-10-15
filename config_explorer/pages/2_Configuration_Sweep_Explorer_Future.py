"""
Benchmarking sweep visualization page
"""
import itertools
import time
from matplotlib import pyplot as plt
import numpy as np
import plotly.express as px
import streamlit as st
from streamlit.delta_generator import DeltaGenerator
import db
import pandas as pd
import util
from src.config_explorer.capacity_planner import *

SIMULATE_BUTTON_KEY = 'simulate_button_key'

# --- Helpers ---
def parse_numeric_list(text: str, cast=int, fallback: List[int] | None = None) -> List[int]:
    """
    Parse a string like "50, 60 70;90" into a list of numbers (ints by default).
    Returns fallback if no valid values are found.
    """
    import re
    if not isinstance(text, str):
        return fallback or []
    tokens = [t for t in re.split(r"[,\s;]+", text.strip()) if t]
    values = []
    for t in tokens:
        try:
            # Support int-only by default but allow float cast if needed
            if cast is int:
                # Allow e.g. "50.0" to become 50
                values.append(int(float(t)))
            else:
                values.append(cast(t))
        except Exception:
            pass
    if not values:
        return fallback or []
    return values

def first_or_fallback(values: List[int], fallback: None | int=None) -> List[int]:
    """
    Return a single-value list: the first value if present, else [fallback].
    """
    if values:
        return [values[0]]
    if fallback is None:
        return []
    return [fallback]


def filter_parallelism(df, tp: List[int], dp: List[int], pp: List[int]):

    # If no PD
    no_pd_copy = df.copy()
    no_pd = no_pd_copy.loc[
        (df["Is_PD"] == False) &
        (df["DP"].isin(dp)) &
        (df["PP"].isin(pp)) &
        (df["TP"].isin(tp))
    ]

    # Prefill and decode parallelism cannot be more than the largest parallelism value in the list selected
    pd_copy = df.copy()
    yes_pd = pd_copy.loc[
        (df["Is_PD"] == True) &
        (df["P_DP"] <= dp[-1]) &
        (df['D_DP'] <= dp[-1]) &
        (df["P_TP"] <= tp[-1]) &
        (df['D_TP'] <= tp[-1]) &
        (df["P_PP"] <= pp[-1]) &
        (df['D_PP'] <= pp[-1])



        # (df["P_PP"] + df['D_PP'] <= pp[-1]) &
        # (df["P_TP"] + df['D_TP'] <= tp[-1])
    ]

    return pd.concat([no_pd, yes_pd])

def sidebar():
    with st.sidebar:
        st.button(
            "Filter performance results",
            use_container_width=True,
            type='primary',
            key=SIMULATE_BUTTON_KEY,
            )


def filter_numbers(numbers, threshold):
    """
    Returns a list of numbers less than or equal to the given threshold.

    Parameters:
    - numbers (list of int): The list of integers to filter.
    - threshold (int): The threshold value.

    Returns:
    - list of int: Filtered list with values <= threshold.
    """
    return [num for num in numbers if num <= threshold]


def sidebar():
    """
    Sidebar content
    """
    with st.sidebar:
        st.button(
            "Filter performance results",
            use_container_width=True,
            type='primary',
            key=SIMULATE_BUTTON_KEY,
            )

def table(tab: DeltaGenerator, benchmark_data):
    """
    Display table of benchmark data
    """

    # Data cleaning
    df = benchmark_data[[
        'Name',
        "Is_PD",
        'Concurrency',
        'Request_Throughput',
        'Output_Token_Throughput',
        'Total_Token_Throughput',
        'Mean_TTFT_ms',
        'Mean_TPOT_ms',
        'Mean_ITL_ms',
        'Mean_E2EL_ms',
        'Thpt_per_GPU',
        'Thpt_per_User',
        # 'block_size',
        # 'long_prefill_token_threshold',
        # 'enable_prefix_caching',
        # # 'max_num_batched_tokens',
        # 'gpu_memory_utilization',
    ]].rename(columns={
        'Name': 'Replicas/Parallelism',
        "Is_PD": "PD enabled",
        'Concurrency': 'Batch Size',
        'Request_Throughput': 'Request Thpt',
        'Output_Token_Throughput': 'Output Token Thpt',
        'Total_Token_Throughput': 'Total Token Thpt',
        'Mean_TTFT_ms': 'TTFT (ms)',
        'Mean_TPOT_ms': 'TPOT (ms)',
        'Mean_ITL_ms': 'ITL (ms)',
        'Mean_E2EL_ms': 'E2EL (ms)',
        'Thpt_per_GPU': 'Thpt/GPU (tok/s)',
        'Thpt_per_User': 'Thpt/User (tok/s)',
        # 'block_size': "Block size",
        # 'long_prefill_token_threshold': "Long prefill token threshold",
        # 'enable_prefix_caching': "Enable prefix caching",
        # # 'max_num_batched_tokens': "Max num batched tokens",
        # 'gpu_memory_utilization': "GPU memory utilization"
    })

    # Add a selection checkbox column
    df.insert(0, "Select", False)

    # Build column configuration dynamically
    column_config = {}
    for col in df.columns:
        if col == "Select":
            column_config[col] = st.column_config.CheckboxColumn(
                "Select", help="Check to select this row"
            )
        else:
            # Disable editing for all other columns automatically
            column_config[col] = st.column_config.TextColumn(col, disabled=True)

    # Render the data editor
    edited_df = tab.data_editor(
        df,
        column_config=column_config,
        use_container_width=True,
        num_rows="fixed",  # Prevent row adding/deleting
        hide_index=True    # Optional, makes UI cleaner
    )


    # Get selected rows
    selected_rows_df = edited_df[edited_df["Select"]]

    if not selected_rows_df.empty:
        # Get the indices of selected rows
        selected_indices = selected_rows_df.index.tolist()

        tab.markdown("##### vLLM configuration:")

        # Iterate over selected rows and fetch data from the original df
        for idx in selected_indices:
            col1, col2 = tab.columns(2)

            col1 = tab

            row_data = benchmark_data.loc[idx]  # Drop checkbox col for clean data
            model_name = row_data['Model']
            pd_enabled = row_data['Is_PD']

            # block_size = row_data['block_size']
            # long_prefill_token_threshold = row_data['long_prefill_token_threshold']
            # enable_prefix_caching = row_data['enable_prefix_caching']
            # max_num_batched_tokens = row_data['max_num_batched_tokens']
            # gpu_memory_utilization = row_data['gpu_memory_utilization']

            if not pd_enabled:
                col1.write("vLLM arguments (aggregate)")
                dp = row_data['DP']
                tp = row_data['TP']
                pp = row_data['PP']
                col1.code(f"""vllm serve {model_name} \\
--data-parallel-size {str(dp)} \\
--tensor-parallel-size {str(tp)} \\
--pipeline-parallel-size {str(pp)} \\
--enable-prefix-caching \\
--gpu-memory-utilization 0.9
"""
            )

            else:
                col1.write("vLLM arguments (disaggregate)")
                p_dp = row_data['P_DP']
                p_tp = row_data['P_TP']
                p_pp = row_data['P_PP']
                col1.write("*Prefill arguments*")
                col1.code(f"""vllm serve {model_name} \\
--data-parallel-size {str(p_dp)} \\
--tensor-parallel-size {str(p_tp)} \\
--pipeline-parallel-size {str(p_pp)} \\
--enable-prefix-caching \\
--gpu-memory-utilization 0.9
"""
            )

                d_dp = row_data['D_DP']
                d_tp = row_data['D_TP']
                d_pp = row_data['D_PP']
                col1.write("*Decode arguments*")
                col1.code(f"""vllm serve {model_name} \\
--data-parallel-size {str(d_dp)} \\
--tensor-parallel-size {str(d_tp)} \\
--pipeline-parallel-size {str(d_pp)} \\
--enable-prefix-caching \\
--gpu-memory-utilization 0.9
"""
            )



#             col2.write("Inference Scheduler config")
#             col2.code("""apiVersion: inference.networking.x-k8s.io/v1alpha1
# kind: EndpointPickerConfig
# plugins:
# - type: prefix-cache-meets_slor
#     parameters:
#     hashBlockSize: 5
#     maxPrefixBlocksToMatch: 256
#     lruCapacityPerServer: 31250
# - type: decode-filter
# - type: max-meets_slo-picker
# - type: single-profile-handler
# schedulingProfiles:
# - name: default
#     plugins:
#     - pluginRef: decode-filter
#     - pluginRef: max-meets_slo-picker
#     - pluginRef: prefix-cache-meets_slor
#     weight: 50
# """, language='yaml')

            tab.write("---")
    else:
        tab.info("Select one or more rows to see details below.")

def select_slo(benchmark_data):
    """
    Display widgets to select SLO requirements
    """

    user_scenario = st.session_state['scenario']

    st.subheader("Select SLO requirements")
    col1, col2, col3 = st.columns(3)
    user_scenario.ttft = col1.number_input("Max TTFT (ms)", min_value=0, value=5000, step=10)
    user_scenario.tpot = col2.number_input("Max TPOT (ms)", min_value=0, value=30, step=1)
    user_scenario.throughput = col3.number_input("Min total token throughput (tokens/s)",
                                                 min_value=0,
                                                 step=1,
                                                 value=5000,
                                                 max_value=1000000,
    )

def get_pareto_front(df: pd.DataFrame) -> set[int]:
    """Get indices of rows on Pareto front.

    Args:
        df (pandas.DataFrame): DataFrame to get Pareto front for.

    Returns:
        set[int]: Indices of DataFrame that are on Pareto front.
    """
    pareto_set = set(df.index.tolist())
    for ii, rowa in df.iterrows():
        is_pareto_front = df.index.isin(pareto_set)
        for jj, rowb in df[is_pareto_front].iterrows():
            if ii == jj:
                continue
            if rowa.Thpt_per_User > rowb.Thpt_per_User and rowa.Thpt_per_GPU > rowb.Thpt_per_GPU:
                # Index jj worse in all ways to index ii
                pareto_set.remove(jj)
    return pareto_set

def pareto_plots(tab: DeltaGenerator, runs_selected, ttft, itl, throughput):
    """
    Pareto plots
    """

    runs_filtered = runs_selected[
        (runs_selected.Mean_TTFT_ms <= ttft) &
        (runs_selected.Mean_ITL_ms <= itl) &
        (runs_selected.Total_Token_Throughput >= throughput)
    ]
    pareto_set = get_pareto_front(runs_filtered)

    # Runs that meet scenario selection, but fail SLOs
    runs_fails_slo = runs_selected[~runs_selected.index.isin(runs_filtered.index.tolist())]

    # Runs that meet SLOs, but are not on the Pareto front
    runs_filtered_not_front = runs_filtered[~runs_filtered.index.isin(pareto_set)]

    # Runs on the Pareto front
    runs_pareto_front = runs_filtered[runs_filtered.index.isin(pareto_set)]

    # Plot
    # Create a figure and plot all three lines on the SAME graph
    fig, ax = plt.subplots(figsize=(8, 5))

    ax.plot(runs_pareto_front.Thpt_per_User, runs_pareto_front.Thpt_per_GPU,
         marker='o', markersize=4,
         color='#FF00FF',
         linestyle='',
         label='Pareto front (optimal)'
        )

    ax.plot(runs_filtered_not_front.Thpt_per_User, runs_filtered_not_front.Thpt_per_GPU,
         marker='o', markersize=4,
         color='#000000',
         linestyle='',
         label='Meets SLOs but non-optimal'
        )

    ax.plot(runs_fails_slo.Thpt_per_User, runs_fails_slo.Thpt_per_GPU,
         marker='o', markersize=4,
         color='#CCCCCC',
         linestyle='',
         label='Fails SLOs'
        )

    ax.set_xlabel('Tok/s/User', fontsize='16')
    ax.set_ylabel('Tok/s/GPU', fontsize='16')
    ax.grid(True, linewidth=1, ls='--', color='gray')
    ax.axis([0, None, 0, None])
    ax.legend(bbox_to_anchor=(1.05, 1), loc=2, borderaxespad=0.)

    _, col, _ = tab.columns([.5, 1, .5])
    # with col:
    tab.pyplot(fig, use_container_width=True)
    plt.show()

    return runs_pareto_front

def inputs(tab: DeltaGenerator, data):
    """
    Inputs to the BLIS simulator
    """

    tab.subheader("Sweep input selection")
    tab.caption("Select initial filters on benchmarking data such as model and workload characteristics.")

    # Model
    models = [
        "meta-llama/Llama-3.1-70B-Instruct",
        "Qwen/Qwen2-7B",
        "Qwen/Qwen2-72B",
        "Qwen/Qwen2.5-14B",
        ]

    with tab.container(border=True):
        selected_model = st.selectbox("Select a model",
                    options=models
                    )


        if selected_model and selected_model != "":

            # TODO: RM this!!!
            hf_token = None

            if selected_model and selected_model != "":
                # Fetch model info
                try:
                    model_info = get_model_info_from_hf(selected_model)
                except Exception as e:
                    st.warning("Cannot access model information, see error below.")
                    st.warning(e)
                    return None

                # Fetch model config
                try:
                    model_config = get_model_config_from_hf(selected_model, hf_token=hf_token)
                except Exception as e:
                    e_str = str(e)
                    if "gated" in e_str:
                        st.warning("This is a gated model, please submit a HF token to view information")
                        hf_token = st.text_input("HF token")
                        if hf_token:
                            model_config = get_model_config_from_hf(selected_model, hf_token=hf_token)
                    else:
                        st.warning("Cannot access model config, see error below.")
                        st.warning(e)
                        return None

                try:
                    model_gpu_memory_req = round(model_memory_req(model_info, model_config), 2)
                except Exception as e:
                    st.warning(f"Cannot retrieve relevant information about the model, {e}. The Capacity Planner only has partial information and functionality.")
                    return None

            # # Display first precision
            # col1, col2 = st.columns(2)

            # col1.info(f"Size of model in memory: ~{model_gpu_memory_req} GB")
            # with col2.expander("See how model size is calculated below"):
            #     st.write("""Below shows how model memory is estimated. The number of parameters and precision are fetched from Hugging Face. Common data types include `BF16` (floating point 16-bit) and `F8_E4M3` (floating point 8-bit, 4 for exponents and 3 for mantissa). The total is then summed.""")

            #     data_types = []
            #     bytes_list = []
            #     params = []
            #     memory_req = []

            #     for d_type, param in model_info.safetensors.parameters.items():
            #         data_types.append(d_type)
            #         params.append(param)

            #         try:
            #             bytes_list.append(precision_to_byte(d_type))
            #         except Exception as e:
            #             st.warning(e)
            #             pass

            #         memory_req.append(parameter_memory_req(param, d_type))

            #     data = {
            #         "Data type": data_types,
            #         "Size in bytes": bytes_list,
            #         "Number of parameters": params,
            #         "Memory in GB (params x bytes)": memory_req,
            #     }
            #     st.dataframe(data, hide_index=True)

            #     st.write("In addition, vLLM [profiles memory](https://github.com/vllm-project/vllm/blob/dcf2f3ec067711ff69e5ab7478fca6ffb4f11daf/vllm/worker/worker.py#L229) by doing a forward pass with `--max-model-len` with dummy data to estimate the non-torch and torch activation peak memory consumption. This means the estimation of the model memory is actually an underestimation. Estimating intermediate memory footprint is currently work in progress.")

    # Scenario
    with tab.container(border=True):
        st.write("**Workload Profiles**")
        st.caption("Define the type of workload for the LLM. Select from a set of pre-defined workloads or tune each parameter based on your need.")

        preset_scenarios = {
            "Summarization": {
                "dataset": "shareGPT",
                "request_rate": 25,
                "input_len": 10000,
                "output_len": 1000,
                "prefix_hit_ratio": 30,
                "latency_p50": 10,
                "latency_p90": 100,
                "throughput": 100,
                "ttft": 2000,
                "itl": 50,
                },
            "Chatbot": {
                "dataset": "Summary Dataset",
                "request_rate": 5,
                "input_len": 2048,
                "output_len": 256,
                "prefix_hit_ratio": 5,
                "latency_p50": 100,
                "latency_p90": 1000,
                "throughput": 1000,
                "ttft": 2000,
                "itl": 500,
                },
            "Classification": {
                "dataset": "Text Dataset",
                "request_rate": 5,
                "input_len": 3096,
                "output_len": 32,
                "prefix_hit_ratio": 5,
                "latency_p50": 1,
                "latency_p90": 10,
                "throughput": 10,
                "ttft": 20,
                "itl": 5,
                },
            # "Custom": {
            #     "dataset": "Synthetic",
            #     "request_rate": 10,
            #     "input_len": 1000,
            #     "output_len": 1000,
            #     "prefix_hit_ratio": 5,
            #     "latency_p50": 10,
            #     "latency_p90": 100,
            #     "throughput": 100,
            #     "ttft": 200,
            #     "itl": 50,
            #     }
        }

        datasets = [
            "shareGPT",
            "Summary Dataset",
            "Text Dataset",
            "Synthetic",
            "Dataset 5",
            "Dataset 6",
        ]


        selected_workload = st.radio("Select workload",
                     options=preset_scenarios.keys())

        info = preset_scenarios[selected_workload]
        dataset = info['dataset']
        request_rate = info['request_rate']
        isl = info['input_len']
        osl = info['output_len']
        prefix_hit_ratio = info['prefix_hit_ratio']
        latency_p50 = info['latency_p50']
        latency_p90 = info['latency_p90']
        throughput = info['throughput']
        ttft = info['ttft']
        itl = info['itl']

        st.write(f"""
- Dataset: {dataset}
- Input length: {isl}
- Output length: {osl}
""")

        concurrency_options = benchmark_data["Concurrency"].unique()
        concurrency_options.sort()
        concurrency_selected = st.multiselect("Select concurrency (request rate)",
                                              options=concurrency_options,
                                              default=concurrency_options,
                       )

    # Environment & Hardware Section
    with tab.container(border=True):
        st.write("**Environment & Hardware**")

        # GPU Configuration
        gpu_type = st.selectbox("Accelerator Type", db.gpu_specs.keys())
        num_gpus = st.number_input(
            "Total number of GPUs (this will filter out parallelism combinations that are invalid)",
            value=16,
            min_value=1)

    # vLLM parameters
    with tab.container(border=True):
        st.write("**vLLM parameters**")
        st.caption("Select what vLLM engine arguments to filter.")

        # Keep consistent column proportions and vertical alignment to ensure clean rows.
        COL_SPEC = [1.2, 3.8]

        # -----------------------------
        # GPU Memory Utilization (%)
        # -----------------------------
        c1, c2 = st.columns(COL_SPEC, vertical_alignment="center")
        sweep_gpu_mem = st.checkbox(
            "GPU Memory Utilization (%)",
            value=True,
            help="Comma/space/semicolon-separated integers (1–100)."
        )


        default = [90]
        gpu_mem_text = st.multiselect(
            "Values to sweep (comma/space separated)",
            options=default,
            default=default,
            key="gpu_mem_text",
            disabled=not sweep_gpu_mem,
            label_visibility="collapsed",
        )
        gpu_memory_utilization_all = parse_numeric_list(
            gpu_mem_text, cast=int, fallback=default,
        )
        # Constrain to 1..100
        gpu_memory_utilization_all = [v for v in gpu_memory_utilization_all if 1 <= v <= 100]
        if sweep_gpu_mem and not gpu_memory_utilization_all:
            st.warning("Please provide at least one integer between 1 and 100.")
        # If not sweeping, select a single value (first or fallback)
        gpu_memory_utilization = (
            gpu_memory_utilization_all if sweep_gpu_mem
            else default
        )

        # # -----------------------------
        # # Block Size
        # # -----------------------------
        # c1, c2 = st.columns(COL_SPEC, vertical_alignment="center")
        # with c1:
        #     sweep_block_size = st.checkbox(
        #         "Block Size",
        #         value=True,
        #         help="Select one or more vLLM-acceptable block sizes (tokens per KV block)."
        #     )
        # ACCEPTABLE_BLOCK_SIZES = [8, 16, 32]  # Adjust if your vLLM build supports others
        # default_block_sizes = ACCEPTABLE_BLOCK_SIZES[:]  # all selected by default
        # with c2:
        #     block_size_selected = st.multiselect(
        #         "Block sizes",
        #         options=ACCEPTABLE_BLOCK_SIZES,
        #         default=default_block_sizes,
        #         key="block_sizes",
        #         disabled=not sweep_block_size,
        #         label_visibility="collapsed"
        #     )
        #     # If the user somehow clears selection while sweeping, restore defaults
        #     if sweep_block_size and not block_size_selected:
        #         st.info("No block sizes selected. Disable this parameter to sweep if that is what you intended, otherwise, use all acceptable block sizes by default.")
        #         block_size_selected = ACCEPTABLE_BLOCK_SIZES[:]
        #     block_size = (
        #         block_size_selected if sweep_block_size
        #         else [None]
        #     )

        # # -----------------------------
        # # Max Num Batched Tokens
        # # -----------------------------
        # c1, c2 = st.columns(COL_SPEC, vertical_alignment="center")
        # with c1:
        #     sweep_max_tokens = st.checkbox(
        #         "Max Num Batched Tokens",
        #         value=True,
        #         help="Comma/space/semicolon-separated integers."
        #     )
        # default_max_tokens_text = "256, 512, 1024, 2048"
        # with c2:
        #     max_tokens_text = st.text_input(
        #         "Values to sweep (comma/space separated)",
        #         value=default_max_tokens_text,
        #         key="max_tokens_text",
        #         disabled=not sweep_max_tokens,
        #         label_visibility="collapsed",
        #         help="Example: 256,512,1024,2048"
        #     )
        #     max_num_batched_tokens_all = parse_numeric_list(
        #         max_tokens_text, cast=int, fallback=[256, 512, 1024, 2048]
        #     )
        #     if sweep_max_tokens and not max_num_batched_tokens_all:
        #         st.warning("Please provide at least one integer for Max Num Batched Tokens.")
        #     max_num_batched_tokens = (
        #         max_num_batched_tokens_all if sweep_max_tokens
        #         else [None]
        #     )

        # # -----------------------------
        # # Long Prefill Token Threshold
        # # -----------------------------
        # c1, c2 = st.columns(COL_SPEC, vertical_alignment="center")
        # with c1:
        #     sweep_long_prefill = st.checkbox(
        #         "Long Prefill Token Threshold",
        #         value=True,
        #         help="Comma/space/semicolon-separated integers."
        #     )
        # default_long_prefill_text = "256, 512, 1024, 2048"
        # with c2:
        #     long_prefill_text = st.text_input(
        #         "Values to sweep (comma/space separated)",
        #         value=default_long_prefill_text,
        #         key="long_prefill_text",
        #         disabled=not sweep_long_prefill,
        #         label_visibility="collapsed",
        #         help="Example: 256,512,1024,2048"
        #     )
        #     long_prefill_token_threshold_all = parse_numeric_list(
        #         long_prefill_text, cast=int, fallback=[256, 512, 1024, 2048]
        #     )
        #     if sweep_long_prefill and not long_prefill_token_threshold_all:
        #         st.warning("Please provide at least one integer for Long Prefill Token Threshold.")
        #     long_prefill_token_threshold = (
        #         long_prefill_token_threshold_all if sweep_long_prefill
        #         else [0]
        #     )

        # -----------------------------
        # Enable Prefix Caching
        # -----------------------------
        sweep_prefix_cache = st.checkbox(
                "Enable Prefix Caching",
                value=True,
                help="Sweep over True/False to evaluate effect of prefix caching."
            )

        # Two multiselect values True/False, both selected by default
        prefix_options = [True, False]
        prefix_selected = st.multiselect(
            "Enable Prefix Caching values",
            options=prefix_options,
            default=[True],  # both selected by default
            key="prefix_caching_values",
            disabled=not sweep_prefix_cache,
            label_visibility="collapsed"
        )
        if sweep_prefix_cache and not prefix_selected:
            st.info("No selection made. Using both True and False by default.")
            prefix_selected = prefix_options[:]
        enable_prefix_caching = (
            prefix_selected if sweep_prefix_cache
            else [True]
        )

        # Parallelism selection

        st.divider()
        st.write("**Parallelism**")

        # -----------------------------
        # Tensor Parallelism (tp)
        # -----------------------------

        sweep_tp = st.checkbox(
            "Tensor Parallelism",
            value=True,
            help="Comma/space/semicolon-separated integers ≥ 1. Example: 1, 2, 4. TP=1 will always be considered.",
        )

        default = find_possible_tp(model_config)
        default = filter_numbers(default, num_gpus)

        tp = st.multiselect(
            "TP Values to sweep (comma/space separated)",
            options=default,
            default=default,
            key="tp_text",
            disabled=not sweep_tp,
            label_visibility="collapsed",
            help="Example: 1,2,4"
        )

        if not sweep_tp:
            tp = [1]

        # -----------------------------
        # Pipeline Parallelism (pp)
        # -----------------------------

        sweep_pp = st.checkbox(
            "Pipeline Parallelism",
            value=True,
            help="Comma/space/semicolon-separated integers ≥ 1. Example: 1, 2"
        )
        default = [1]

        pp = st.multiselect(
            "DP Values to sweep (comma/separated)",
            options=default,
            default=default,
            key="pp_text",
            disabled=not sweep_pp,
            label_visibility="collapsed",
            help="Example: 1,2"
        )

        if not sweep_pp:
            pp = [1]

        # -----------------------------
        # Data Parallelism (dp)
        # -----------------------------
        sweep_dp = st.checkbox(
            "Data Parallelism (replicas of model)",
            value=True,
            help="Comma/space/semicolon-separated integers ≥ 1. Example: 1, 2"
        )
        default_dp_text = [1]
        dp_text = st.multiselect(
            "Values to sweep (comma/space separated)",
            options=default_dp_text,
            default=default_dp_text,
            key="dp_text",
            disabled=not sweep_dp,
            label_visibility="collapsed",
            help="Example: 1,2"
        )
        dp_all = [v for v in parse_numeric_list(dp_text, cast=int, fallback=[1]) if v >= 1]
        if sweep_dp and not dp_all:
            st.warning("Please provide at least one integer ≥ 1 for Pipeline Parallelism (pp).")
        dp = dp_all if sweep_dp else first_or_fallback(dp_all, default_dp_text)

    # SLOs
    with tab.container(border=True):
        st.write("**Goals / SLOs**")
        st.caption("Define the desire constraints to reach for your application.")

        if selected_workload:
            scenario = preset_scenarios[selected_workload]
            disabled = selected_workload != "Custom"

            latency_col1, latency_col2 = st.columns(2)
            latency_p50 = latency_col1.number_input("E2E latency p50 (ms)",
                                          value=scenario['latency_p50'],
                                          min_value=0,
                                          )
            latency_p95 = latency_col2.number_input("E2E latency p95 (ms)",
                                value=scenario['latency_p90'],
                                min_value=0,
                                )

            ttft_col, itl_col = st.columns(2)
            ttft = ttft_col.number_input("TTFT (ms)",
                        value=scenario['ttft'],
                        min_value=0,
                        )
            itl = itl_col.number_input("ITL (ms)",
                        value=scenario['itl'],
                        min_value=0,
                        )

            throughput = st.number_input("Throughput (token/s)",
                                         value=scenario['throughput'],
                                         min_value=1,
                                         )



    data_to_return = {
        "model": selected_model,
        "gpu_type": gpu_type,
        "num_gpus": num_gpus,
        "tp": tp,
        "dp": dp,
        "pp": pp,
        "isl": isl,
        "osl": osl,
        'concurrency': concurrency_selected,
        "gpu_memory_utilization": gpu_memory_utilization,
        # "max_num_batched_tokens": max_num_batched_tokens,
        # "block_size": block_size,
        # "long_prefill_token_threshold": long_prefill_token_threshold,
        "enable_prefix_caching": enable_prefix_caching,
        "latency_p50": latency_p50,
        "latency_p95": latency_p95,
        "throughput": throughput,
        "ttft": ttft,
        "itl": itl,
        "throughput": throughput,

        # If value to sweep
        # "sweep_gpu_memory_utilization": sweep_gpu_mem,
        # "sweep_block_size": sweep_block_size,
        # "sweep_max_num_batched_tokens": sweep_max_tokens,
        # "sweep_long_prefill_token_threshold": sweep_long_prefill,
        # "sweep_enable_prefix_caching": sweep_prefix_cache,
    }

    return data_to_return

def output(tab, user_input: dict, original_benchmark_data):
    """
    Visualize output
    """
    tab.subheader("Sweep exploration")
    tab.caption("Visualize performance results that meet input selection.")

    with tab.expander("See all data (without filter)"):
        st.dataframe(original_benchmark_data)

    model_name = user_input['model']
    gpu_type = user_input['gpu_type']
    num_gpus = user_input['num_gpus']
    tp_selected = user_input['tp']
    dp_selected = user_input['dp']
    pp_selected = user_input['pp']
    isl = user_input['isl']
    osl = user_input['osl']
    concurrencies = user_input['concurrency']
    gpu_memory_utilization = user_input['gpu_memory_utilization']
    # max_num_batched_tokens = user_input['max_num_batched_tokens']
    # block_size = user_input['block_size']
    # long_prefill_token_threshold = user_input['long_prefill_token_threshold']
    enable_prefix_caching = user_input['enable_prefix_caching']
    latency_p50 = user_input['latency_p50']
    latency_p95 = user_input['latency_p95']
    throughput = user_input['throughput']
    ttft = user_input['ttft']
    itl = user_input['itl']
    throughput = user_input['throughput']

    # Plot configurations and get DataFrame with Pareto front configs.
    # Filter benchmarking data
    df = db.read_benchmark_data()
    benchmark_data = df.loc[
        (df["Model"] == model_name) &
        (df["GPU"] == gpu_type) &
        (df["Num_GPUs"] <= num_gpus) &
        (df["ISL"] == isl ) &
        (df["OSL"] == osl ) &
        (df['Concurrency'].isin(concurrencies))
    ]
    benchmark_data_copy = benchmark_data.copy()

    if benchmark_data.empty:
        tab.warning("The inputs selected returned no results. Try loosening your SLO requirements or use the experimental feature: BLIS estimator.")
        st.caption("The BLIS estimator will estimate performance (TTFT, ITL, latency, etc.) from the selected input without the need for GPUs.")
        if tab.button("Run BLIS estimator"):
            with tab:
                with st.spinner("Estimating performance... should take just a few seconds..."):
                    time.sleep(3)

                # Totally mocked
                df['Model'] = "Qwen/Qwen2-7B"
                benchmark_data = df.loc[
                (df["Model"] == model_name) &
                (df["GPU"] == gpu_type) &
                (df["Num_GPUs"] <= num_gpus) &
                (df["ISL"] == isl ) &
                (df["OSL"] == osl ) &
                (df['Concurrency'].isin(concurrencies))
                ]
                benchmark_data_copy = benchmark_data.copy()

    df1 = filter_parallelism(
        benchmark_data,
        tp_selected,
        dp_selected,
        pp_selected,
    )

    # # Combo graph
    # sweep_combos = list(itertools.product(
    #     gpu_memory_utilization,
    #     block_size,
    #     max_num_batched_tokens,
    #     long_prefill_token_threshold,
    #     enable_prefix_caching
    # ))

    # np.random.seed(42)

    # new_columns = {
    #     "gpu_memory_utilization": gpu_memory_utilization,
    #     # "max_num_batched_tokens": max_num_batched_tokens,
    #     # "block_size": block_size,
    #     # "long_prefill_token_threshold": long_prefill_token_threshold,
    #     "enable_prefix_caching": enable_prefix_caching,
    # }

    # New cols
    df1['gpu_memory_utilization'] = gpu_memory_utilization[0]
    df1['enable_prefix_caching'] = enable_prefix_caching[0]

    # for col, options in new_columns.items():
    #     df1[col] = np.random.choice(options, size=len(df1))

    # meets_slo = fraction of conditions met
    df1["meets_slo"] = (
        (df1["Total_Token_Throughput"] >= throughput) &
        (df1["Mean_TTFT_ms"] <= ttft) &
        (df1["Mean_ITL_ms"] <= itl)
    ).astype(int)


    # # Build dimension list dynamically - some may not be selected
    agg_dimensions = ["Replicas", "TP", "PP", "DP", "gpu_memory_utilization", "enable_prefix_caching"]
    disagg_dimensions = ["P_Replicas", "P_TP", "P_PP", "P_DP", "D_Replicas", "D_TP", "D_PP", "D_DP", "gpu_memory_utilization", "enable_prefix_caching"]
    # if user_input["sweep_gpu_memory_utilization"]:
    #     agg_dimensions.append("gpu_memory_utilization")
    #     disagg_dimensions.append("gpu_memory_utilization")
    # if user_input["sweep_block_size"]:
    #     agg_dimensions.append("block_size")
    #     disagg_dimensions.append("block_size")
    # if user_input["sweep_max_num_batched_tokens"]:
    #     agg_dimensions.append("max_num_batched_tokens")
    #     disagg_dimensions.append("max_num_batched_tokens")
    # if user_input["sweep_long_prefill_token_threshold"]:
    #     agg_dimensions.append("long_prefill_token_threshold")
    #     disagg_dimensions.append("long_prefill_token_threshold")
    # if user_input["sweep_enable_prefix_caching"]:
    #     agg_dimensions.append("enable_prefix_caching")
    #     disagg_dimensions.append("enable_prefix_caching")


    tab.subheader("Aggregate setting")
    aggregate = df1.loc[(df["Is_PD"] == False)]
    agg_count = (aggregate["meets_slo"] == 1).sum()
    tab.info(f"There are {agg_count} aggregate configurations that meet SLO requirements.")
    fig = px.parallel_categories(
        aggregate,
        dimensions=agg_dimensions,
        color="meets_slo",
        color_continuous_scale=[(0, "red"), (1, "green")],
        labels={col: col for col in df.columns},
        range_color=[0,1],
    )

    if agg_count > 0: tab.plotly_chart(fig, use_container_width=True)

    tab.subheader("P/D disaggregate setting")
    disaggregate = df1.loc[(df["Is_PD"] == True)]
    disagg_count = (disaggregate["meets_slo"] == 1).sum()
    tab.info(f"There are {disagg_count} disaggregate configurations that meet SLO requirements.")
    fig = px.parallel_categories(
        disaggregate,
        dimensions=disagg_dimensions,
        color="meets_slo",
        color_continuous_scale=[(0, "red"), (1, "green")],
        labels={col: col for col in df.columns},
        range_color=[0,1],
    )
    if disagg_count > 0: tab.plotly_chart(fig, use_container_width=True)

    tab.subheader("Optimal configurations")
    pareto_front = pareto_plots(tab, benchmark_data_copy, ttft, itl, throughput)

    tab.info(f"Of the {agg_count + disagg_count} that meet SLO requirements, {len(pareto_front)} are optimal because they maximize tokens/s/User or tokens/s/GPU.")
    table(tab, pareto_front)

if __name__ == "__main__":
    # Set up streamlit config
    st.set_page_config(page_title="Configuration Explorer",
                       page_icon=None,
                       layout="wide",
                       initial_sidebar_state="expanded",
                       menu_items=None)
    st.title("Configuration Explorer")
    st.caption("This tool helps you find the most cost-effective, optimal configuration for serving models on llm-d based on hardware specification, workload characteristics, and SLO requirements.")

    util.init_session_state()

    # Display Sweep Explorer headings
    st.header("Configuration Sweep Explorer (Future)")
    st.caption("Explore, exmaine, and visualize existing benchmarking data for optimal `llm-d` configurations.")

    benchmark_data = db.read_benchmark_data()
    col1, col2 = st.columns([0.3, 0.7], gap="large")
    col1_container = col1.container(height=1000, border=False)
    col2_container = col2.container(height=1000, border=False)
    user_inputs = inputs(col1_container, benchmark_data)
    output(col2_container, user_inputs, benchmark_data)
