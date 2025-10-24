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

scenarios_mapping = {
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

    if len(benchmark_data) == 0:
        tab.info("Import data above.")
        return None

    with tab.container(border=True):
        selected_model = st.selectbox(
            "Select a model",
            options=benchmark_data['Model'].unique()
            )

        selected_gpu = st.selectbox(
            "Select an accelerator type",
            options=benchmark_data['GPU'].unique()
        )

        selected_num_gpus = st.number_input(
            "Select max accelerator count",
            value=16,
            min_value=1
            )


    with tab.container(border=True):
        st.write("**Workload Profiles**")
        st.caption("Define the type of workload for the LLM. Based on the model and environment inputs, the available options are shown below.")

        # Show available combinations
        runs = benchmark_data[
            (benchmark_data["Model"] == selected_model) &
            (benchmark_data["GPU"] == selected_gpu) &
            (benchmark_data["Num_GPUs"] <= selected_num_gpus)
        ]
        scenarios = xp.get_scenarios(runs, ['Model', "GPU", "Num_GPUs", "ISL_500", "OSL_500", "Max_QPS"])

        with st.expander("Input/output sequence summary"):
            st.write("View the available input and output sequence length pairs in the benchmark data. \
                     Sequence length within the same 500 tokens are binned together.")
            st.table(scenarios)

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
                "e2e_latency": 1000,
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

        selected_workload = st.radio("Select workload", options=preset_scenarios.keys())

        info = preset_scenarios[selected_workload]
        isl = info['input_len']
        osl = info['output_len']
        e2e_latency = info['e2e_latency']
        throughput = info['throughput']
        ttft = info['ttft']
        itl = info['itl']
        extra = {}

        st.caption(info['description'])
        selected_isl_range = st.number_input(
            "Input sequence length",
            value=isl,
            min_value=1,
            max_value=max(runs['ISL'].max(), isl),
            )

        selected_osl_range = st.number_input(
            "Output sequence length",
            value=osl,
            min_value=1,
            max_value=max(runs['OSL'].max(), osl),
            )

    # SLOs
    with tab.container(border=True):
        st.write("**Goals / SLOs**")
        st.caption("Define the desire constraints to reach for your application. Default values are suggested for the given application type.")

        if selected_workload:
            scenario = preset_scenarios[selected_workload]

            col1, col2 = st.columns(2)
            st.caption("Note that all the metrics here refer to the p90 value.")
            throughput = col1.number_input("Throughput (token/s)",
                                         value=scenario['throughput'],
                                         min_value=1,
                                         )
            e2e_latency = col2.number_input("End-to-End latency",
                                value=scenario['e2e_latency'],
                                min_value=0,
                                )
            ttft = col1.number_input("TTFT (ms)",
                        value=scenario['ttft'],
                        min_value=0,
                        )
            itl = col2.number_input("ITL (ms)",
                        value=scenario['itl'],
                        min_value=0,
                        )

    data_to_return = {
        "model": selected_model,
        "gpu_type": selected_gpu,
        "num_gpus": selected_num_gpus,
        "isl": selected_isl_range,
        "osl": selected_osl_range,
        "e2e_latency": e2e_latency,
        "ttft": ttft,
        "itl": itl,
        "throughput": throughput,
        "extra": extra
    }

    return data_to_return


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
        options=list(scenarios_mapping.keys()),
        help="Scenario presents define a set of parameters to filter that showcase a certain feature or capability. For example, comparing throughput per user vs. throughput per GPU tradeoff for PD disaggregation scenarios."
        )

    if selected_display_preset:
        scenario_preset = scenarios_mapping[selected_display_preset]
        filtered_data = filter_data_on_inputs(original_benchmark_data, user_inputs)
        scenario_columns = scenario_preset['columns']
        scenarios = xp.get_scenarios(filtered_data, scenario_columns)

        if selected_display_preset == PD_DISAGG:
            tab.write("""The prefill/decode disaggregation scenario compares the effects of :blue[aggregate] inference vs. :blue[disaggregated] inference.""")

            tab.dataframe(scenarios, hide_index=False)

            selected_index = tab.selectbox("Select a row to compare the details of the runs in that group.",
                                            options=[i for i in range(len(scenarios))]
                                            )

            if selected_index is not None:

                tab.subheader("Performance comparison (Throughput/GPU vs. Concurrency)")
                plot = xplotting.plot_scenario(
                    runs_df=original_benchmark_data,
                    scenario=scenarios[selected_index],
                    config_keys=scenario_preset['config_keys'],
                    col_x=scenario_preset['col_x'],
                    col_y=scenario_preset['col_y'],
                    col_seg_by=scenario_preset['col_seg_by'],
                )
                tab.pyplot(plot)

                tab.divider()

                tab.subheader("Performance tradeoff comparison")
                tradeoff_plot = xplotting.plot_scenario_tradeoff(
                    runs_df=original_benchmark_data,
                    scenario=scenarios[selected_index],
                    config_keys=scenario_preset['config_keys'],
                    col_x=scenario_preset['pareto']['col_x'],
                    col_y=scenario_preset['pareto']['col_y'],
                    col_z=scenario_preset['pareto']['col_z'],
                    col_seg_by=scenario_preset['col_seg_by'],
                )
                tab.pyplot(tradeoff_plot)

                tab.divider()
                tab.subheader("Examine optimal configuration")

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
                config_columns = ['Replicas', 'TP', 'P_Replicas', 'P_TP', 'D_Replicas', 'D_TP']
                tradeoff_plot = xplotting.plot_pareto_tradeoff(
                    runs_df=filtered_data,
                    scenario=scenarios[selected_index],
                    col_x=col_x,
                    col_y=col_y,
                    slos=slos,
                    log_x=log_x,
                    log_y=log_y
                    )
                tab.pyplot(tradeoff_plot)

                # Print table of optimal configurations
                # Get scenario rows from all runs in dataset
                runs_scenario = xp.get_scenario_df(filtered_data, scenarios[selected_index])

                # Get just the rows that meet SLOs
                runs_meet_slo = xp.get_meet_slo_df(runs_scenario, slos)

                # Get rows on Pareto front
                runs_pareto_front = xp.get_pareto_front_df(runs_meet_slo, col_x, col_y, True)

                # Print the rows on Pareto front, showing just the columns of interest
                config_columns = ['Replicas', 'TP', 'P_Replicas', 'P_TP', 'D_Replicas', 'D_TP']
                columns_of_interest = config_columns[:]
                columns_of_interest.append(col_x)
                columns_of_interest.append(col_y)
                tab.dataframe(runs_pareto_front[columns_of_interest])


        if selected_display_preset == INFERENCE_SCHEDULING:

            tab.write("""The inference scheduling scenario compares the effects of the inference scheduler plugins. This scenario can be explored by examining:

- :blue[System prompt length]: the number of tokens (words or characters) in the initial instructions given to a large language model
- :blue[Question length]: the user input part of the prompt as they interact with the chatbot. This is different from system prompt, which is the shared prefix of the prompt which is likely to be the same for different users and sessions
- :blue[Number of groups]: the number of shared prefix groups in the workload traffic
- :blue[Number of prompts per group]: the number of unique questions per group

                     """)
            tab.dataframe(scenarios, hide_index=False)

            selected_index = tab.selectbox("Select a row to compare the details of the runs in that group.",
                                            options=[i for i in range(len(scenarios))]
                                            )

            if selected_index is not None:

                tab.subheader("Performance comparison (QPS vs. TTFT)")
                plot = xplotting.plot_scenario(
                    runs_df=original_benchmark_data,
                    scenario=scenarios[selected_index],
                    config_keys=scenario_preset['config_keys'],
                    col_x=scenario_preset['col_x'],
                    col_y=scenario_preset['col_y'],
                    col_seg_by=scenario_preset['col_seg_by'],
                )
                tab.pyplot(plot)

                # Plot the tradeoff
                tab.divider()

                tab.subheader("Performance tradeoff comparison")
                tradeoff_plot = xplotting.plot_scenario_tradeoff(
                    runs_df=original_benchmark_data,
                    scenario=scenarios[selected_index],
                    config_keys=scenario_preset['config_keys'],
                    col_x=scenario_preset['pareto']['col_x'],
                    col_y=scenario_preset['pareto']['col_y'],
                    col_z=scenario_preset['pareto']['col_z'],
                    col_seg_by=scenario_preset['col_seg_by'],
                )
                tab.pyplot(tradeoff_plot)

        if selected_display_preset == "Custom":
            tab.warning("This feature is not yet available. To perform you own data exploration, see this [example Jupyter notebook](https://github.com/llm-d/llm-d-benchmark/blob/main/analysis/analysis.ipynb) for analysis using the `config_explorer` library.")

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
