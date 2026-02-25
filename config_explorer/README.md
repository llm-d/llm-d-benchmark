# Configuration Explorer

The configuration explorer is a library that helps find the most cost-effective, optimal configuration for serving models on llm-d based on hardware specification, workload characteristics, and SLO requirements. A CLI and web app front-end are available to use the library immediately.

Features include:

- **Capacity planning**:
  - Get per-GPU memory requirements to load and serve a model, and compare parallelism strategies.
  - Determine KV cache memory requirements based on workload characteristics.
  - Estimate peak activation memory, CUDA graph overhead, and non-torch memory for accurate capacity planning (see empirical results for intermediate memory [here](./empirical-vllm-memory-results.md))
- **GPU recommendation**:
  - Recommend GPU configurations using BentoML's llm-optimizer roofline algorithm.
  - Analyze throughput, latency (TTFT, ITL, E2E), and concurrency trade-offs across different GPU types.
  - Export recommendations in JSON format for integration with other tools.
- **Configuration exploration and recommendation**:
  - Visualize performance metrics for different `llm-d` configurations, filter on SLOs, compare configuration tradeoffs.
  - (soon) Predict latency and throughput for configurations lacking benchmark data.

Core functionality is currently a Python module within `llm-d-benchmark`. In the future we may consider shipping as a separate package depending on community interest.

## Installation

**Requires python 3.11+**

1. (optional) Set up a Python virtual environment

    ```bash
    python -m venv .venv
    source .venv/bin/activate
    ```

2. Install the `config_explorer` Python module after cloning the `llm-d-benchmark` repository.

    ```bash
    git clone https://github.com/llm-d/llm-d-benchmark.git
    cd llm-d-benchmark
    pip install -e ./config_explorer
    ```

# Usage

## CLI

After installation, the `config-explorer` command will become available:

```bash
# Run capacity planning
config-explorer plan --model Qwen/Qwen2.5-3B --gpu-memory 80 --max-model-len 16000

# Run GPU recommendation and performance estimation (BentoML's roofline model)
config-explorer estimate --model Qwen/Qwen2.5-3B --input-len 512 --output-len 128 --max-gpus 8

# Human-readable output
config-explorer estimate --model Qwen/Qwen2.5-3B --input-len 512 --output-len 128 --pretty

# Override GPU costs with custom pricing
config-explorer estimate --model Qwen/Qwen2.5-3B \
  --input-len 512 --output-len 128 \
  --custom-gpu-cost H100:30.50 \
  --custom-gpu-cost A100:22 \
  --custom-gpu-cost L40:25.00 \
  --pretty

# Start the Streamlit web app
pip install -r config_explorer/requirements-streamlit.txt # one-time installation
config-explorer start

# Get help
config-explorer --help
```

## Web Application

A Streamlit frontend is provided to showcase the capabilities of the Configuration Explorer in a more intuitive way. Before using this frontend additional requirements must be installed.

After installing Streamlit requirements (`pip install -r config_explorer/requirements-streamlit.txt`) the web app may then be started with
```bash
config-explorer start
```

### Pages

The Streamlit frontend includes the following pages:

1. **Capacity Planner** - Analyze GPU memory requirements and capacity planning for LLM models
2. **GPU Recommender** - Get optimal GPU recommendations based on model and workload requirements
3. **Sweep Visualizer** - Visualize benchmark results and configuration sweeps

### Using the Sweep Visualizer

The Sweep Visualizer page supports visualizing a collection of `llm-d-benchmark` report files. To get started easily, you may download the data from the [public llm-d-benchmark community Google Drive](https://drive.google.com/drive/u/0/folders/1r2Z2Xp1L0KonUlvQHvEzed8AO9Xj8IPm). Preset options have been selected for each scenario. For example, we recommend viewing

- `qwen-qwen-3-0-6b` using the Chatbot application highlight Inference Scheduling
- `meta-llama/Llama-3.1-70B-Instruct` using the Document Summarization application highlight PD Disaggregation

Default values will be populated once those options are selected. Advanced users may further conduct their own configuration.

### Using the GPU Recommender

The GPU Recommender page helps you find the optimal GPU for running LLM inference. To use it:

1. **Configure Model**: Enter a HuggingFace model ID (e.g., `meta-llama/Llama-2-7b-hf`)
2. **Set Workload Parameters**:
   - Input sequence length (tokens)
   - Output sequence length (tokens)
   - Maximum number of GPUs
3. **Define Constraints (Optional)**:
   - Maximum Time to First Token (TTFT) in milliseconds
   - Maximum Inter-Token Latency (ITL) in milliseconds
   - Maximum End-to-End Latency in seconds
4. **Run Analysis**: Click the "Run Analysis" button to evaluate all available GPUs
5. **Review Results**:
   - Compare GPUs through interactive visualizations
   - Examine throughput, latency metrics, and optimal concurrency
   - View detailed analysis for each GPU
6. **Export**: Download results as JSON or CSV for further analysis

The GPU Recommender uses BentoML's llm-optimizer roofline algorithm to provide synthetic performance estimates across different GPU types, helping you make informed decisions about hardware selection.

**Note**: You'll need a HuggingFace token set as the `HF_TOKEN` environment variable to access gated models.

### Cost Information

The GPU Recommender displays cost information to help you find cost-effective GPU configurations:

- **Default GPU Costs**: Built-in reference costs for common GPUs (H200, H100, A100, L40, etc.)
- **Custom Cost Override**: Specify your own GPU costs using any numbers you prefer (e.g., your actual $/hour or $/token pricing)
- **Cost-Based Sorting**: Sort results by cost to find the most economical option

**⚠️ IMPORTANT**: Default costs are **reference values for relative comparison only**. They do **NOT** represent actual pricing from any provider. Lower values indicate better value. Use custom costs that reflect your actual infrastructure pricing.

## Library

Configuration exploration and benchmark sweep performance comparison is best demonstrated in the Jupyter notebook [analysis.ipynb](../analysis/analysis.ipynb). This notebook can be used for interactive analysis of benchmarking data results, and it utilizes the same core functions as the "Sweep Visualizer" page of the web app. For instructions on using the notebook see [../analysis/README.md](../analysis/README.md).

For GPU recommender API usage see [./examples/gpu_recommender_example.py](./examples/gpu_recommender_example.py).
