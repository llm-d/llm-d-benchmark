from typing import Any, Dict, List
from numpy import float64
from pandas import DataFrame
import streamlit as st
from streamlit.delta_generator import DeltaGenerator
import util

import src.config_explorer.explorer as xp
import src.config_explorer.plotting as xplotting


BENCHMARK_PATH_KEY = "benchmark_path"
BENCHMARK_DATA_KEY = "benchmark_data"
SELECTED_SCENARIO_KEY = "selected_scenario"

# ------- Scenario presets -------

PD_DISAGG = "PD Disaggregation"
INFERENCE_SCHEDULING = "Inference Scheduling"

scenarios_config_keys_mapping = {
    PD_DISAGG: {
        "description": "Compares inference performance of aggregate vs. prefill/decode disaggregate set up.",
        "columns": ['Model', 'GPU', 'ISL', 'OSL'],
        "config_keys":  [
            ['Replicas', 'TP'],
            ['P_Replicas', 'P_TP', 'D_Replicas', 'D_TP'],
        ],
        "col_seg_by": 'Directory_Base',
        "col_x": 'Max_Concurrency',
        "col_y": 'Thpt_per_GPU',
        "pareto": {
            "col_x": 'Thpt_per_User',
            "col_y": 'Thpt_per_GPU',
            "col_z": 'Max_Concurrency',
        }
    },

    INFERENCE_SCHEDULING: {
        "description": "Examines effects of inference scheduler scorer plugin weights.",
        "columns": ['Model', 'GPU', 'System_Prompt_Length', 'Question_Length', 'OSL_500', 'Groups', 'Prompts_Per_Group'],
        "config_keys":  ['KV_Cache_Scorer_Weight', 'Queue_Scorer_Weight', 'Prefix_Cache_Scorer_Weight', 'Prefix_Cache_Scorer_Mode'],
        "col_seg_by": 'Directory',
        "col_x": 'Max_QPS',
        "col_y": 'P90_TTFT_ms',
        "pareto": {
            "col_x": 'Total_Token_Throughput',
            "col_y": 'P90_TTFT_ms',
            "col_z": 'Max_QPS',
        }
    },

    "Custom": {
        "description": "Carve your own scenario",
        "columns": ['Model']
    }
}

preset_scenarios = {
    "Chatbot": {
        "description": "This application typically has high QPS, concurrency, and prefix hit rate, and favors low latency.",
        "input_len": 100,
        "output_len": 300,
        "system_prompt_length": 2048,
        "question_length": 100,
        "e2e_latency": 100,
        "throughput": 100,
        "ttft": 2000,
        "itl": 50,
        },
    "Document summarization": {
        "description": "This application maps to workload requests with high input length and short output length.",
        "input_len": 9999,
        "output_len": 1000,
        "max_qps": float64(5),
        "e2e_latency": 100000,
        "throughput": 100,
        "ttft": 10000,
        "itl": 100,
        },
    "Custom": {
        "description": "Design the workload patterns for your own custom application type.",
        "input_len": 300,
        "output_len": 1000,
        "e2e_latency": 200,
        "throughput": 200,
        "ttft": 1000,
        "itl": 50,
    }
}

def init_session_state():
    """
    Inits session state for data persistence
    """
    if BENCHMARK_DATA_KEY not in st.session_state:
        st.session_state[BENCHMARK_DATA_KEY] = xp.make_benchmark_runs_df()

@st.cache_data
def read_benchmark_path(benchmark_path: str) -> DataFrame:
    """
    Reads the data at the path
    """

    runs = xp.make_benchmark_runs_df()

    report_files = xp.get_benchmark_report_files(benchmark_path)
    for br_file in report_files:

        # Update session state data
        xp.add_benchmark_report_to_df(runs, br_file)

    return runs

def user_benchmark_path():
    """
    Obtains path to user data
    """

    benchmark_path = st.text_input("Enter absolute path to `llm-d` benchmark data",
                value="",
                # key=BENCHMARK_PATH_KEY,
                help="Navigate to the [llm-d community Google Drive](https://drive.google.com/drive/u/0/folders/1r2Z2Xp1L0KonUlvQHvEzed8AO9Xj8IPm) to download data.",
                )

    if st.button("Import data", type='primary'):
        # Populate the runs DataFrame with new path
        # benchmark_path = st.session_state[BENCHMARK_PATH_KEY]
        if benchmark_path != "":
            st.toast(f'Searching for benchmark report files within `{benchmark_path}`')

            try:
                st.session_state[BENCHMARK_DATA_KEY] = read_benchmark_path(benchmark_path)

                st.toast(f"Successfully imported {len(st.session_state[BENCHMARK_DATA_KEY])} report files. You may view the raw data below.", icon="🎉")
            except Exception:
                st.toast("File not found, please double check path.", icon='⚠️')

def filter_data_on_inputs(data: DataFrame, user_inputs: dict) -> DataFrame:
    """
    Filters data on inputs and SLOs
    """

    return data[
        (data['Model'] == user_inputs['model']) &
        (data['GPU'] == user_inputs['gpu_type']) &
        (data['Num_GPUs'] <= user_inputs['num_gpus']) &
        (data['ISL'] >= user_inputs['isl']) &
        (data['OSL'] >= user_inputs['osl'])
        # (data['Max_QPS'] <= user_inputs['max_qps'])
        ]

def inputs(tab: DeltaGenerator):
    """
    Inputs to the Visualizer
    """

    tab.subheader("Sweep input selection")
    tab.caption("Select initial filters on benchmarking data such as model and workload characteristics.")

    benchmark_data = st.session_state[BENCHMARK_DATA_KEY]
    scenario_to_return = {}

    if len(benchmark_data) == 0:
        tab.info("Import data above.")
        return None

    with tab.container(border=True):
        scenario_to_return['Model'] = st.selectbox(
            "Select a model",
            options=benchmark_data['Model'].unique()
            )

        scenario_to_return['GPU'] = st.selectbox(
            "Select an accelerator type",
            options=benchmark_data['GPU'].unique()
        )

        # selected_num_gpus = st.number_input(
        #     "Select max accelerator count",
        #     value=16,
        #     min_value=1
        #     )

    with tab.container(border=True):
        st.write("**Workload Profiles**")
        st.caption("Define the type of workload for the LLM. Based on the model and environment inputs, the available options are shown below.")

        # Show available combinations
        runs = benchmark_data[
            (benchmark_data["Model"] == scenario_to_return['Model']) &
            (benchmark_data["GPU"] == scenario_to_return['GPU'])
            # & (benchmark_data["Num_GPUs"] <= selected_num_gpus)
        ]
        # scenarios = xp.get_scenarios(runs, ['Model', "GPU", "Num_GPUs", "ISL_500", "OSL_500"])

        # with st.expander("Input/output sequence summary"):
        #     st.write("View the available input and output sequence length pairs in the benchmark data. \
        #              Sequence length within the same 500 tokens are binned together.")
        #     st.table(scenarios)

        selected_workload = st.radio("Select workload", options=preset_scenarios.keys())

        info = preset_scenarios[selected_workload]
        e2e_latency = info['e2e_latency']
        throughput = info['throughput']
        ttft = info['ttft']
        itl = info['itl']

        st.caption(info['description'])

        if selected_workload == "Chatbot":
            # Show scenario options for Chatbot application
            scenario_to_return['System_Prompt_Length'] = st.selectbox(
                "System prompt length",
                options=runs['System_Prompt_Length'].unique(),
                help="The number of tokens (words or characters) in the initial instructions given to a large language model"
                    )

            scenario_to_return['Question_Length'] = st.selectbox(
                "Question length",
                options=runs['Question_Length'].unique(),
                help="The user input part of the prompt as they interact with the chatbot. This is different from system prompt, which is the shared prefix of the prompt which is likely to be the same for different users and sessions."
                    )

            scenario_to_return['Groups'] = st.selectbox(
                "Number of groups",
                options=runs['Groups'].unique(),
                help="The number of shared prefix groups in the workload traffic"
                    )

            scenario_to_return['Prompts_Per_Group'] = st.selectbox(
                "Number of prompts per group",
                options=runs['Prompts_Per_Group'].unique(),
                help="The number of unique questions per group."
                    )

            scenario_to_return['OSL_500'] = st.selectbox(
                "Output sequence length",
                options=runs['OSL_500'].unique(),
                help="Number of tokens to generate for the output such that the output length is binned by the nearest 500."
                    )

        if selected_workload == "Document summarization":
            # Show scenario options for Document summary application

            st.caption("Exact matching is required for now. Click below to see the available combinations of ISL and OSL.")
            with st.expander("ISL to OSL"):
                temp = xp.get_scenarios(runs, ['ISL', 'OSL'])
                st.table(temp)

            scenario_to_return['ISL'] = st.selectbox(
                "Input sequence length",
                options=runs['ISL'].unique(),
                )

            scenario_to_return['OSL'] = st.selectbox(
                "Output sequence length",
                options=runs['OSL'].unique(),
                )

    # SLOs
    with tab.container(border=True):
        st.write("**Goals / SLOs**")
        st.caption("Define the desire constraints to reach for your application. Default values are suggested for the given application type. If you'd like to disregard a metric, set it to an extremely high or low value depending on the direction of the metric.")

        if selected_workload:
            scenario = preset_scenarios[selected_workload]

            col1, col2 = st.columns(2)
            throughput = col1.number_input("Throughput (total tokens/s)",
                                         value=scenario['throughput'],
                                         min_value=1,
                                         )
            e2e_latency = col2.number_input("P90 End-to-End latency",
                                value=scenario['e2e_latency'],
                                min_value=0,
                                )
            ttft = col1.number_input("P90 TTFT (ms)",
                        value=scenario['ttft'],
                        min_value=0,
                        )
            itl = col2.number_input("P90 ITL (ms)",
                        value=scenario['itl'],
                        min_value=0,
                        )

    data_to_return = {
        "scenario": scenario_to_return,
        "e2e_latency": e2e_latency,
        "ttft": ttft,
        "itl": itl,
        "throughput": throughput,
    }

    return data_to_return

def display_optimal_config_overview(container: DeltaGenerator,
                                    config_columns: List[str],
                                    original_benchmark_data: DataFrame,
                                    user_inputs: dict,
                                    user_selected_scenario: Dict[str, Any]
                                    ):
    """
    Displays the optimal configuration overview (Pareto charts)
    """

    container.subheader("Examine optimal configuration")

    # Define SLOs
    slos = [
        xp.SLO('P90_TTFT_ms', user_inputs['ttft']),
        xp.SLO('P90_ITL_ms', user_inputs['itl']),
        xp.SLO('Total_Token_Throughput', user_inputs['throughput']),
        xp.SLO('P90_E2EL_ms', user_inputs['e2e_latency']),
    ]

    # Columns for metrics of interest to optimize
    col_x = 'Mean_TTFT_ms'
    col_y = 'Thpt_per_GPU'

    # Select linear or log scales
    log_x = True
    log_y = False

    # Configuration columns of interest
    tradeoff_plot = xplotting.plot_pareto_tradeoff(
        runs_df=original_benchmark_data,
        scenario=user_selected_scenario,
        col_x=col_x,
        col_y=col_y,
        slos=slos,
        log_x=log_x,
        log_y=log_y
        )
    container.pyplot(tradeoff_plot)

    # Print tab1le of optimal configurations
    # Get scenario rows from all runs in dataset
    runs_scenario = xp.get_scenario_df(original_benchmark_data, user_selected_scenario)

    # Get just the rows that meet SLOs
    runs_meet_slo = xp.get_meet_slo_df(runs_scenario, slos)

    # Get rows on Pareto front
    runs_pareto_front = xp.get_pareto_front_df(runs_meet_slo, col_x, col_y, True)

    # Print the rows on Pareto front, showing just the columns of interest
    columns_of_interest = config_columns[:]
    columns_of_interest.append(col_x)
    columns_of_interest.append(col_y)

    # Display info
    container.info(f"Out of the {len(runs_meet_slo)} configurations that meet SLO requirements, {len(runs_pareto_front)} are optimal. Their configuration and performance metrics are shown below.")

    container.dataframe(runs_pareto_front[columns_of_interest], hide_index=True)

def outputs(tab: DeltaGenerator, user_inputs: dict):
    """
    Outputs to the Visualizer
    """

    tab.subheader("Sweep exploration")
    tab.caption("Visualize performance results that meet input selection.")
    original_benchmark_data = st.session_state[BENCHMARK_DATA_KEY]

    with tab.expander("Review raw data"):
        st.dataframe(original_benchmark_data)

    if len(original_benchmark_data) == 0:
        tab.info("Import data above.")
        return None

    selected_display_preset = tab.radio(
        "Select display presets",
        options=list(scenarios_config_keys_mapping.keys()),
        help="Scenario presents define a set of parameters to filter that showcase a certain feature or capability. For example, comparing throughput per user vs. throughput per GPU tradeoff for PD disaggregation scenarios."
        )

    if selected_display_preset:

        tab1, tab2 = tab.tabs(["📈 Performance overview", "🌟 Optimal configuration overview"])

        # Describe each tab
        tab1.info("View a summary of the data based on the selected preset. Each preset groups configurations define a specific scenario, helping to highlight its performance characteristics.")
        tab2.info("Given SLO requirements, filter for the best configurations of parallelism and replicas in aggregate and disaggregated setup.")

        scenario_preset = scenarios_config_keys_mapping[selected_display_preset]
        user_selected_scenario = user_inputs['scenario']
        pareto_config_columns = []

        if selected_display_preset == PD_DISAGG:
            pareto_config_columns = ['Replicas', 'TP', 'P_Replicas', 'P_TP', 'D_Replicas', 'D_TP']
            tab1.write("""The prefill/decode disaggregation scenario compares the effects of :blue[aggregate] inference vs. :blue[disaggregated] inference.""")

            tab1.subheader("Performance comparison (Throughput/GPU vs. Concurrency)")
            plot = xplotting.plot_scenario(
                runs_df=original_benchmark_data,
                scenario=user_selected_scenario,
                config_keys=scenario_preset['config_keys'],
                col_x=scenario_preset['col_x'],
                col_y=scenario_preset['col_y'],
                col_seg_by=scenario_preset['col_seg_by'],
            )
            tab1.pyplot(plot)

            tab1.divider()

            tab1.subheader("Performance tradeoff comparison")
            tradeoff_plot = xplotting.plot_scenario_tradeoff(
                runs_df=original_benchmark_data,
                scenario=user_selected_scenario,
                config_keys=scenario_preset['config_keys'],
                col_x=scenario_preset['pareto']['col_x'],
                col_y=scenario_preset['pareto']['col_y'],
                col_z=scenario_preset['pareto']['col_z'],
                col_seg_by=scenario_preset['col_seg_by'],
            )
            tab1.pyplot(tradeoff_plot)


        if selected_display_preset == INFERENCE_SCHEDULING:
            tab1.subheader("Performance comparison (QPS vs. TTFT)")
            plot = xplotting.plot_scenario(
                runs_df=original_benchmark_data,
                scenario=user_selected_scenario,
                config_keys=scenario_preset['config_keys'],
                col_x=scenario_preset['col_x'],
                col_y=scenario_preset['col_y'],
                col_seg_by=scenario_preset['col_seg_by'],
            )
            tab1.pyplot(plot)

            # Plot the tradeoff
            tab1.divider()

            tab1.subheader("Performance tradeoff comparison")
            tradeoff_plot = xplotting.plot_scenario_tradeoff(
                runs_df=original_benchmark_data,
                scenario=user_selected_scenario,
                config_keys=scenario_preset['config_keys'],
                col_x=scenario_preset['pareto']['col_x'],
                col_y=scenario_preset['pareto']['col_y'],
                col_z=scenario_preset['pareto']['col_z'],
                col_seg_by=scenario_preset['col_seg_by'],
            )
            tab1.pyplot(tradeoff_plot)

        if selected_display_preset == "Custom":
            tab1.warning("This feature is not yet available. To perform you own data exploration, see this [example Jupyter notebook](https://github.com/llm-d/llm-d-benchmark/blob/main/analysis/analysis.ipynb) for analysis using the `config_explorer` library.")

        display_optimal_config_overview(tab2, scenario_preset['config_keys'], original_benchmark_data, user_inputs, user_selected_scenario)

if __name__ == "__main__":
    # Set up streamlit config
    st.set_page_config(page_title="Configuration Explorer",
                       page_icon=None,
                       layout="wide",
                       initial_sidebar_state="expanded",
                       menu_items=None)
    st.title("Configuration Explorer")
    st.caption("This tool helps you find the most cost-effective, optimal configuration for serving models on llm-d based on hardware specification, workload characteristics, and SLO requirements.")

    init_session_state()

    # Display Sweep Explorer headings
    st.header("Configuration Sweep Explorer")
    st.caption("Explore, examine, and visualize existing benchmarking data for optimal `llm-d` configurations.")

    user_benchmark_path()
    col1, col2 = st.columns([0.3, 0.7], gap="large")
    col1_container = col1.container(height=1000, border=False)
    col2_container = col2.container(height=1000, border=False)
    user_inputs = inputs(col1_container)
    outputs(col2_container, user_inputs)
