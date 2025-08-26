"""
Filter results page
"""
from matplotlib import pyplot as plt
import streamlit as st
import db
import pandas as pd

def check_input():
    """
    Check all required input is there
    """
    scenario = st.session_state['scenario']
    if not scenario.model_name or not scenario.gpu_spec or not scenario.isl or not scenario.osl:
        return False
    return True

def display_basic_data():
    """
    Display info about data
    """
    user_scenario = st.session_state['scenario']
    st.info(f"""Benchmarking results will be filtered based on the following inputs:

- Model: `{user_scenario.model_name}`
- Precision: `{user_scenario.precision}`
- GPU Type: `{user_scenario.gpu_spec['name']}`
- GPU Available: {user_scenario.gpu_count_avail}
- ISL: {user_scenario.isl}
- OSL: {user_scenario.osl}
""")

def table(benchmark_data):
    """
    Display table of benchmark data
    """
    st.subheader("Optimal configurations")

    # Data cleaning
    df = benchmark_data[[
        'Name',
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
    ]].rename(columns={
        'Name': 'Replicas/Parallelism',
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
    edited_df = st.data_editor(
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

        st.markdown("##### llm-d configuration:")

        # Iterate over selected rows and fetch data from the original df
        for idx in selected_indices:
            row_data = benchmark_data.loc[idx]  # Drop checkbox col for clean data
            model_name = row_data['Model']
            dp = row_data['DP']
            tp = row_data['TP']

            col1, col2 = st.columns(2)
            col1.write("vLLM arguments")
            col1.code(f"""vllm serve {model_name} \\
--data-parallel-size {str(dp)} \\
--tensor-parallel-size {str(tp)} \\
"""
            )

            col2.write("Router config")
            col2.code("""apiVersion: inference.networking.x-k8s.io/v1alpha1
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

            st.write("---")
    else:
        st.info("Select one or more rows to see details below.")

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

    # TODO: what else?

# def misc(benchmark_data):



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

def pareto_plots(runs_selected):
    """
    Pareto plots
    """

    user_scenario = st.session_state['scenario']

    runs_filtered = runs_selected[
        (runs_selected.Mean_TTFT_ms <= user_scenario.ttft) &
        (runs_selected.Mean_TPOT_ms <= user_scenario.tpot) &
        (runs_selected.Total_Token_Throughput >= user_scenario.throughput)
    ]
    pareto_set = get_pareto_front(runs_selected)

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

    _, col, _ = st.columns([.5, 1, .5])
    with col:
        st.pyplot(fig)
    plt.show()

    return runs_pareto_front

if __name__ == "__main__":
    st.title("Parameter Sweep and Search")
    st.caption("Visualize benchmarking results.")


    if not check_input():
        st.warning("One or more inputs is missing in Home page: Model name, hardware specification, or workload")

    else:
        user_scenario =  st.session_state['scenario']

        # Filter benchmarking data
        df = db.read_benchmark_data()
        benchmark_data = df.loc[
            (df["Model"] == user_scenario.model_name) &
            (df["GPU"] == user_scenario.gpu_spec['name']) &
            (df["Num_GPUs"] <= user_scenario.gpu_count_avail) &
            (df["ISL"] == user_scenario.isl) &
            (df["OSL"] == user_scenario.osl)
        ]

        display_basic_data()

        if benchmark_data.empty:
            st.warning("The configuration selected returned no result.")
        else:
            select_slo(benchmark_data)

            # Plot configurations and get DataFrame with Pareto front configs.
            pareto_front = pareto_plots(benchmark_data)

            # Show table of optimal configs
            table(pareto_front)

            # miscellaneous output
            # misc(pareto_front)
