# Configuration Explorer Tool

Finding the most cost-effective, optimal configuration for serving models on llm-d based on hardware specification, workload characteristics, and SLO requirements.

This library provides the tooling for LLM serving such as
- Capacity planning:
  - from a selected model and GPU, determine the minimum number of GPUs required to load the model
  - from workload characteristics (in terms of max model length), determine the maximum number of concurrent requests that can be process given number of GPUs available
- Configuration sweep and recommendation (WIP)
  - given SLO requirements in terms of TTFT, TPOT, and throughput, visualize the optimal llm-d configuration for achieving the SLO


## Installation

* Requires python 3.11+

Currently, the core functionality is in the form of a Python module within `llm-d-benchmark`. In the future, we might consider shipping as package depending on community interest.

1. Clone the `llm-d-benchmark` repository or a fork of it

    ```
    git clone https://github.com/llm-d/llm-d-benchmark.git
    cd llm-d-benchmark
    ```

2. (optional) Set up a venv
    ```
    python -m venv .venv
    source .venv/bin/activate
    ```
3. Install requirements

    ```
    pip install -r config_explorer/requirements.txt
    ```

## Use

There are two ways to interact with the Configuration Explorer: frontend and in the form of a library.

### Frontend
A Streamlit frontend is provided to showcase the capabilities of the Configuration Explorer rapidly. Since the core functions are in a module, users may feel free to build their own frontend, such as a CLI, by making use of those functions.

Run the Streamlit frontend:

```
streamlit run config_explorer/Home.py
```

### Library
Users may import the functions like the following to use in their code.

```
from config_explorer.src.config_explorer.capacity_planner import *
```