# Configuration Explorer

The configuration explorer is a library that helps find the most cost-effective, optimal configuration for serving models on llm-d based on hardware specification, workload characteristics, and SLO requirements.

This library provides the tooling for LLM serving such as
- Capacity planning:
  - for any LLM on Hugging Face, determine the per-GPU memory requirement for loading the model and serving requests, taking into account parallelism strategies
  - from workload characteristics in terms of max model length and concurrency, determine the KV cache memory requirement
- GPU Recommendation:
  - given model specifications and performance requirements, recommend optimal GPU configurations using BentoML's llm-optimizer roofline algorithm
  - analyze throughput, latency (TTFT, ITL, E2E), and concurrency trade-offs across different GPU types
  - export recommendations in JSON format for integration with other tools
- 🚧 Configuration sweep and recommendation
  - given SLO requirements in terms of TTFT, TPOT, and throughput, visualize the optimal `llm-d` configuration, including Inference Scheduler and vLLM, for achieving the SLO
  - For unseen configurations, predict latency and throughput from a inference performance estimator


## Installation

* Requires python 3.11+
* Requires pip3

Currently, the core functionality is in the form of a Python module within `llm-d-benchmark`. In the future, we might consider shipping as a separate package depending on community interest.

1. (optional) Set up a Python virtual environment
    ```bash
    python -m venv .venv
    source .venv/bin/activate
    ```

2. The `config_explorer` package can be installed via `pip`

    If you have cloned the `llm-d-benchmark` repository, run the following at the project root:

    ```bash
    pip install ./config_explorer
    ```

    Otherwise, to use `config_explorer` independently of `llm-d-benchmark`, run the following:

    ```bash
    python -m pip install "config_explorer @ git+https://github.com/llm-d/llm-d-benchmark.git/#subdirectory=config_explorer"
    ```

3. Invoke functions in the package via

    ```python
    import config_explorer.capacity_planner as cp
    import config_explorer.explorer as ex
    from config_explorer.plotting import (
        plot_scenario,
        plot_scenario_tradeoff,
        plot_pareto_tradeoff
    )
    ```

## Use

To demonstrate usage of the configuration explorer library, an experimental frontend has been developed. A Jupyter notebook [analysis.ipynb](../analysis/analysis.ipynb) is also available, which can be used for interactive analysis of benchmarking data results.

### Frontend
A Streamlit frontend is provided to showcase the capabilities of the Configuration Explorer rapidly. Since the core functions are in a module, users may feel free to build their own frontend, such as a CLI, by making use of those functions.

Running the Streamlit frontend requires cloning the `llm-d-benchmark` repo. Make sure you have already installed the required dependencies for the `config_explorer` package following the Installation guide [here](#installation).

```
git clone https://github.com/llm-d/llm-d-benchmark.git
pip install -r config_explorer/requirements-streamlit.txt
.venv/bin/streamlit run config_explorer/Capacity_Planner.py
```

#### Pages

The Streamlit frontend includes the following pages:

1. **Capacity Planner** - Analyze GPU memory requirements and capacity planning for LLM models
2. **Sweep Visualizer** - Visualize benchmark results and configuration sweeps
3. **GPU Recommender** - Get optimal GPU recommendations based on model and workload requirements

#### Using the Sweep Visualizer

The Sweep Visualizer page supports visualizing a collection of `llm-d-benchmark` report files. To get started easily, you may download the data from the [public llm-d-benchmark community Google Drive](https://drive.google.com/drive/u/0/folders/1r2Z2Xp1L0KonUlvQHvEzed8AO9Xj8IPm). Preset options have been selected for each scenario. For example, we recommend viewing

- `qwen-qwen-3-0-6b` using the Chatbot application highlight Inference Scheduling
- `meta-llama/Llama-3.1-70B-Instruct` using the Document Summarization application highlight PD Disaggregation

Default values will be populated once those options are selected. Advanced users may further conduct their own configuration.

#### Using the GPU Recommender

The GPU Recommender page helps you find the optimal GPU for running LLM inference. To use it:

1. **Configure Model**: Enter a HuggingFace model ID (e.g., `meta-llama/Llama-2-7b-hf`)
2. **Set Workload Parameters**:
   - Input sequence length (tokens)
   - Output sequence length (tokens)
   - Maximum number of GPUs
3. **Define Constraints (Optional)**:
   - Maximum Time to First Token (TTFT) in milliseconds
   - Maximum Inter-Token Latency (ITL) in milliseconds
   - Maximum End-to-End Latency in milliseconds
4. **Run Analysis**: Click the "Run Analysis" button to evaluate all available GPUs
5. **Review Results**:
   - Compare GPUs through interactive visualizations
   - Examine throughput, latency metrics, and optimal concurrency
   - View detailed analysis for each GPU
6. **Export**: Download results as JSON or CSV for further analysis

The GPU Recommender uses BentoML's llm-optimizer roofline algorithm to provide synthetic performance estimates across different GPU types, helping you make informed decisions about hardware selection.

**Note**: You'll need a HuggingFace token set as the `HF_TOKEN` environment variable to access gated models.

### Analysis Notebook

See [../analysis/README.md](../analysis/README.md) for notebook usage.

### Library
Users may import the functions like the following to use in their code after `pip install git+https://github.com/llm-d/llm-d-benchmark.git`.

```
from config_explorer.capacity_planner import *
```

Here is a list of all the data classes and functions for the Capacity Planner:

| Class         | Function/method                   | Description                                                                                                                                                                                                                                                                                                              |   |
|---------------|-----------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---|
|               | `get_model_info_from_hf()`        | retrieves `ModelInfo` such as number of parameters and precision types. Used as input for                                                                                                                                                                                                                                |   |
|               | `get_model_config_from_hf()`      | retrieves `AutoConfig` including number of layers and attention architecture. Requires                                                                                                                                                                                                                                   |   |
|               | `get_text_config`                 | retrieves LLM-specific information from `AutoConfig`. This is preferred over `get_model_config_from_hf` for methods like `find_possible_tp`                                                                                                                                                                              |   |
|               | `model_total_params()`            | finds the total number of parameters for a model                                                                                                                                                                                                                                                                         |   |
|               | `max_context_len()`               | finds the max context length supported by the model                                                                                                                                                                                                                                                                      |   |
|               | `precision_to_byte()`             | converts a string representing precision, such as `BF16` or `F8_E4M3`, to its byte requirement                                                                                                                                                                                                                           |   |
|               | `parameter_memory_req()`          | given parameter count and precision, finds the memory requirement of the parameter count                                                                                                                                                                                                                                 |   |
|               | `model_memory_req()`              | finds the model GPU memory requirement                                                                                                                                                                                                                                                                                   |   |
|               | `inference_dtype()`               | finds the default KV cache data type used for inference                                                                                                                                                                                                                                                                  |   |
|               | `kv_cache_req()`                  | finds the KV cache memory requirement given context length and batch size                                                                                                                                                                                                                                                |   |
|               | `max_concurrent_req()`            | finds the max concurrent requests the model can serve given context length and GPU memory                                                                                                                                                                                                                                |   |
|               | `find_possible_tp()`              | returns the list of possible values of `--tensor-parallel-size` for the given model                                                                                                                                                                                                                                      |   |
|               | `available_gpu_memory()`          | calculates the available GPU memory given `--gpu-memory-utilization`                                                                                                                                                                                                                                                     |   |
|               | `gpus_required()`                 | determine number of GPUs required given parallelism strategies                                                                                                                                                                                                                                                           |   |
|               | `per_gpu_model_memory_required()` | calculates the per-GPU memory requirement for loading model given parallelism                                                                                                                                                                                                                                            |   |
|               | `allocatable_kv_cache_memory()`   | calculates the allocatable memory for KV cache in the system                                                                                                                                                                                                                                                             |   |
|               | `per_gpu_memory_required()`       | calculates the per-GPU memory requirement for model and KV cache given parallelism                                                                                                                                                                                                                                       |   |
|               | `use_mla()`                       | determines if model uses multi-head latent attention (statistically determined by model name)                                                                                                                                                                                                                            |   |
|               | `is_moe()`                        | determines if model is a Mixture-of-Experts (MoE) model                                                                                                                                                                                                                                                                  |   |
|               | `get_num_experts()`               | finds the number of experts for MoE models                                                                                                                                                                                                                                                                               |   |
|               | `get_ep_size()`                   | finds the EP size given parallelism strategies                                                                                                                                                                                                                                                                           |   |
|               | `experts_per_ep_group()`          | finds the number of experts per EP group given parallelism strategies                                                                                                                                                                                                                                                    |   |
| KVCacheDetail | `__init__()`                      | initializes class by passing in ModelInfo, ModelConfig, context_len (int), and batch_size (int)                                                                                                                                                                                                                          |   |
|               | `set_context_len()`               | recomputes KV cache details given a new context length                                                                                                                                                                                                                                                                   |   |
|               | `set_batch_size()`                | recomputes KV cache details given a new batch size / concurrency
|               | `total_kv_cache_blocks()`                | Calculate the total number of KV cache blocks that can fit in GPU memory                                                                                                          |   |
|               | attributes                        | KVCacheDetail stores information relevant to calculating KV cache requirement, <br>such as `attention_type`, `num_hidden_layers`, `kv_lora_rank` for MLA models. <br>Outputs include `num_attention_group`, `per_token_memory_bytes`, `per_request_kv_cache_bytes`,<br>`per_request_kv_cache_gb`, and `kv_cache_size_gb` |   |
