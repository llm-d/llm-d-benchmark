"""
Filter results page
"""
import streamlit as st
import db

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
    col1, col2 = st.columns(2)
    user_scenario.ttft = col1.number_input("TTFT (ms)", min_value=0.00)
    user_scenario.tpot = col2.number_input("TPOT (ms)", min_value=0.00)

    # TODO: what else?

def pareto_plots(benchmark_data):
    """
    Pareto plots
    """

    st.warning("Some plots here, TODO")

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
            (df["TP"] <= user_scenario.gpu_count_avail) &
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