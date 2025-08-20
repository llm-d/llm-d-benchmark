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
    if not scenario.model_name or not scenario.workload:
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
    st.subheader("Benchmark data")
    st.dataframe(benchmark_data, use_container_width=True)

def select_slo(benchmark_data):
    """
    Display widgets to select SLO requirements
    """

    user_scenario = st.session_state['scenario']

    st.subheader("Select SLO requirements")
    col1, col2, col3 = st.columns(3)
    user_scenario.ttft = col1.number_input("Max TTFT (ms)", min_value=0.00)
    user_scenario.tpot = col2.number_input("Max TPOT (ms)", min_value=0.00)
    user_scenario.throughput = col3.number_input("Min total tokens throughput (tokens/s)",
                                                 min_value=0,
                                                 step=1,
                                                 value=100,
                                                 max_value=1000000,
                                                 )

    # TODO: what else?

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

    st.warning("Some plots here, TODO")
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

    st.pyplot(fig)
    plt.show()
    st.write(user_scenario.ttft)

if __name__ == "__main__":
    st.title("Parameter Sweep and Search")
    st.caption("Visualize benchmarking results.")


    if not check_input():
        st.warning("One or more inputs is missing in Home page: Model name, ISL, OSL")

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
            pareto_plots(benchmark_data)
            table(benchmark_data)

            # table(df)