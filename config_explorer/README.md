# Configuration Explorer

The configuration explorer is a library that helps find the most cost-effective, optimal configuration for serving models on llm-d based on hardware specification, workload characteristics, and SLO requirements.

This library provides the tooling for LLM serving such as
- Capacity planning:
  - from a selected model and GPU, determine the minimum number of GPUs required to load the model
  - from workload characteristics (in terms of max model length), determine the maximum number of concurrent requests that can be process given number of GPUs available
- Configuration sweep and recommendation (WIP)
  - given SLO requirements in terms of TTFT, TPOT, and throughput, visualize the optimal llm-d configuration for achieving the SLO


## Installation

* Requires python 3.11+
* Requires pip3

Currently, the core functionality is in the form of a Python module within `llm-d-benchmark`. In the future, we might consider shipping as a separate package depending on community interest.

1. (optional) Set up a Python virtual environment
    ```
    python -m venv .venv
    source .venv/bin/activate
    ```

2. The `config_explorer` package can be installed via `pip`

    ```
    pip install git+https://github.com/llm-d/llm-d-benchmark.git
    ```

3. Invoke functions in the package via

    ```
    from config_explorer.capacity_planner import *
    ```


## Use

There are two ways to interact with the Configuration Explorer: via an experimental frontend or via a library.

### Frontend
A Streamlit frontend is provided to showcase the capabilities of the Configuration Explorer rapidly. Since the core functions are in a module, users may feel free to build their own frontend, such as a CLI, by making use of those functions.

Run the Streamlit frontend by cloning the `llm-d-benchmark` repo

```
git clone https://github.com/llm-d/llm-d-benchmark.git
pip install -r config_explorer/requirements-streamlit.txt
streamlit run config_explorer/Home.py
```

### Library
Users may import the functions like the following to use in their code.

```
from config_explorer.capacity_planner import *
```

Here's some functions you may find useful:

* `get_model_info_from_hf()`: retrieves `ModelInfo` such as number of parameters and precision types. Use for memory calculation
* `get_model_config_from_hf()`: retrieves `AutoConfig` including number of layers and attention architecture. Requires `HF_TOKEN` for gated models. Used for memory calculation
* `model_memory_req()`: calculates GPU memory required for loading the model
* `min_gpu_req()`: calculates minimum number of GPU required for loading the model
* `kv_cache_req()`: calculates the KV cache GPU memory requirement for the model given context length and batch size (default = 1)
* `max_concurrent_req()`: calculates the max number of concurrent requests the model can process given number of GPUs available