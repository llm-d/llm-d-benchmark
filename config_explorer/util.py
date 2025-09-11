"""
Streamlit frontend utilities
"""
import streamlit as st
from huggingface_hub import ModelInfo
from transformers import AutoConfig
from dataclasses import dataclass
from src.config_explorer.capacity_planner import *

# Session state variables pertaining to user selected values
USER_SCENARIO_KEY = "scenario"
SELECTED_MODEL_KEY = "selected_model"
SELECTED_GPU_NAME_KEY = "selected_gpu_name"
SELECTED_GPU_COUNT_AVAIL_KEY = "selected_gpu_count_avail"
SELECTED_GPU_PER_NODE_KEY = "selected_gpu_per_node"
SELECTED_NODE_COUNT_KEY = "selected_node_count"
SELECTED_MAX_MODEL_LEN_KEY = "selected_max_model_len"
SELECTED_CONCURRENCY_KEY = "selected_concurrency"

## Parallelism strategy keys
SELECTED_PP_SIZE_KEY = "selected_pp_size"
SELECTED_DP_SIZE_KEY = "selected_dp_size"

@dataclass
class Scenario:
    """Scenario stores info about an user scenario in Streamlit"""
    model_name: str = 'RedHatAI/Llama-3.3-70B-Instruct-FP8-dynamic'
    model_info: ModelInfo | None = None
    model_config: AutoConfig | None = None
    max_model_len: int | None = None
    concurrency: int | None = None

    # GPU
    gpu_name: str ='NVIDIA-H100-80GB-HBM3'
    gpu_count_avail: int | None = None
    gpu_per_node: int | None = None
    node_count: int = 1

    # Parallelism
    pp_size: int = 1
    dp_size: int = 1

    def get_model_name(self) -> str:
        if not self.model_name:
            self.model_name = 'RedHatAI/Llama-3.3-70B-Instruct-FP8-dynamic'
        return self.model_name

    def get_gpu_spec(self, gpu_specs_db: dict) -> dict:
        return gpu_specs_db[self.gpu_name]

    def get_gpu_memory(self, gpu_specs_db: dict) -> int:
        return self.get_gpu_spec(gpu_specs_db)['memory']

    def can_show_mem_util_chart(self, min_gpu_req: int):
        if self.model_name and self.model_info and self.model_config and \
            self.max_model_len and self.concurrency and \
                self.gpu_name and self.get_total_accelerators() and \
                    self.get_total_accelerators() >= min_gpu_req:
            return True
        return False

    def get_total_accelerators(self) -> int:
        if not self.gpu_per_node:
            return 0
        return self.gpu_per_node * self.node_count

def init_session_state():
    """
    Inits session state for data persistence
    """

    if USER_SCENARIO_KEY not in st.session_state:
        st.session_state[USER_SCENARIO_KEY] = Scenario()

def update_scenario(session_state_key: str, scenario_attr: str):
    """
    Update session state value and scenario
    """
    st.session_state[USER_SCENARIO_KEY].__setattr__(scenario_attr, st.session_state[session_state_key])

def on_update_gpu_count():
    """
    Reset concurrency to none
    """
    scenario = st.session_state[USER_SCENARIO_KEY]
    scenario.gpu_count_avail = st.session_state[SELECTED_GPU_COUNT_AVAIL_KEY]
    scenario.concurrency = None

def on_update_gpu_per_node():
    """
    Reset concurrency to none
    """
    scenario = st.session_state[USER_SCENARIO_KEY]
    scenario.gpu_per_node = st.session_state[SELECTED_GPU_PER_NODE_KEY]
    scenario.concurrency = None

def on_update_node_count():
    """
    Reset concurrency to none
    """
    scenario = st.session_state[USER_SCENARIO_KEY]
    scenario.node_count = st.session_state[SELECTED_NODE_COUNT_KEY]
    scenario.concurrency = None

def on_update_model_name():
    """
    Reset model name
    """
    scenario = st.session_state[USER_SCENARIO_KEY]
    scenario.model_name = st.session_state[SELECTED_MODEL_KEY]
    scenario.max_model_len = None
    scenario.concurrency = None

def on_update_max_model_len():
    """
    Reset max model length
    """
    scenario = st.session_state[USER_SCENARIO_KEY]
    scenario.max_model_len = st.session_state[SELECTED_MAX_MODEL_LEN_KEY]
    scenario.concurrency = None