# Metrics Collection for Benchmarking

This document describes the metrics collection feature, which captures system and application metrics during benchmark runs.

## Overview

The metrics collection system automatically gathers performance and resource utilization metrics from deployed pods during benchmark execution. These metrics are integrated into the benchmark report and can be visualized as time series graphs.

## Implementation Status

### Currently Implemented and Working

The following metrics collection capabilities are fully implemented and operational:

1. **Pod-Level Prometheus Metrics** - Collecting 117+ metrics from vLLM pods via `/metrics` endpoint
2. **Log Parsing** - Extracting additional metrics from vLLM pod logs
3. **Metrics Processing** - Aggregating and calculating statistics (mean, stddev, min, max, percentiles)
4. **RBAC Setup** - Automatic ServiceAccount creation with required permissions
5. **Metrics Storage** - Raw and processed metrics saved to results directory

### Not Yet Implemented

The following features from the original design are not yet implemented:

1. **DCGM GPU Metrics** - Direct GPU monitoring metrics (DCGM_FI_DEV_GPU_UTIL, DCGM_FI_DEV_POWER_USAGE, etc.)
   - These metrics require DCGM exporter to be deployed in the cluster
   - Currently relying on vLLM's built-in metrics and log parsing instead

2. **Real-time Visualization** - Live metric streaming during benchmark execution
   - Currently generates static graphs after benchmark completion

3. **Custom Metric Queries** - User-defined Prometheus queries
   - Currently collects predefined set of metrics

## Collected Metrics

The metrics collection system gathers metrics from two sources:

### 1. Pod-Level Metrics (vLLM Prometheus Endpoint) Working

Collected from each vLLM pod's `/metrics` endpoint (default port 8000):

#### Cache Metrics
- **`vllm:kv_cache_usage_perc`** - KV cache utilization percentage (0-100)
- **`vllm:prefix_cache_hits_total`** - Total number of prefix cache hits (tokens)
- **`vllm:prefix_cache_queries_total`** - Total number of prefix cache queries (tokens)
- **`vllm:external_prefix_cache_hits_total`** - External cache hits from KV connector cross-instance sharing
- **`vllm:external_prefix_cache_queries_total`** - External cache queries from KV connector
- **`vllm:mm_cache_hits_total`** - Multi-modal cache hits (items)
- **`vllm:mm_cache_queries_total`** - Multi-modal cache queries (items)
- **`cache_hit_rate_percent`** - Calculated prefix cache hit rate (parsed from logs)

#### Request & Token Metrics
- **`vllm:num_requests_running`** - Number of requests currently in execution batches
- **`vllm:num_requests_waiting`** - Number of requests waiting to be processed
- **`vllm:prompt_tokens_total`** - Total number of prefill tokens processed
- **`vllm:generation_tokens_total`** - Total number of generation tokens produced
- **`vllm:iteration_tokens_total`** - Total tokens processed per iteration
- **`vllm:request_prompt_tokens`** - Prompt tokens per request (histogram)
- **`vllm:request_generation_tokens`** - Generation tokens per request (histogram)
- **`vllm:request_max_num_generation_tokens`** - Maximum generation tokens per request
- **`vllm:request_success_total`** - Total number of successful requests

#### System Metrics
- **`vllm:num_preemptions_total`** - Cumulative number of request preemptions
- **`vllm:engine_sleep_state`** - Engine sleep state (awake/weights_offloaded/discard_all)
- **`process_cpu_seconds_total`** - Total CPU time consumed by the process
- **`process_resident_memory_bytes`** - Resident memory size (RSS)
- **`process_virtual_memory_bytes`** - Virtual memory size
- **`process_open_fds`** - Number of open file descriptors
- **`process_max_fds`** - Maximum number of file descriptors

#### Python Runtime Metrics
- **`python_gc_collections_total`** - Number of garbage collection cycles
- **`python_gc_objects_collected_total`** - Objects collected during GC
- **`python_gc_objects_uncollectable_total`** - Uncollectable objects found during GC
- **`python_info`** - Python version information

### 2. Log-Parsed Metrics Working

Additional metrics extracted from vLLM pod logs:

- **`cache_hit_rate_percent`** - Prefix cache hit rate percentage
- **`kv_cache_usage_percent`** - KV cache usage from log messages
- **`gpu_memory_used_gb`** - GPU memory usage from log messages
- **`gpu_memory_total_gb`** - Total GPU memory available
- **`gpu_memory_usage_percent`** - Calculated GPU memory utilization
- **`cpu_memory_used_gb`** - CPU/RAM usage from log messages
- **`gpu_utilization_percent`** - GPU compute utilization
- **`prompt_throughput_tokens_per_sec`** - Average prompt processing throughput
- **`generation_throughput_tokens_per_sec`** - Average generation throughput
- **`running_requests`** - Number of running requests (from logs)
- **`waiting_requests`** - Number of waiting requests (from logs)
- **`swapped_requests`** - Number of swapped requests (from logs)
- **`power_consumption_watts`** - GPU power consumption in Watts

## In-Container Metrics Collection (`collect_metrics.sh`)

The `collect_metrics.sh` script runs inside the harness pod during benchmark execution. It is sourced by each harness entrypoint script (e.g., `inference-perf-llm-d-benchmark.sh`) and handles continuous metric collection in the background.

### How It Works

1. **Initialization** -- Creates a `metrics/` directory structure under the results directory with `raw/` and `processed/` subdirectories.
2. **Pod discovery** -- Discovers vLLM pod names and IPs via label selectors. Tries `llm-d.ai/inferenceServing=true` (modelservice) first, falls back to `stood-up-via=standalone` (standalone), and finally uses grep-based pattern matching.
3. **Periodic scraping** -- Runs in a background loop, scraping the Prometheus `/metrics` endpoint from each discovered pod at a configurable interval (default: 15 seconds via `METRICS_COLLECTION_INTERVAL`).
4. **Port selection** -- Uses `LLMDBENCH_VLLM_COMMON_METRICS_PORT` (default: 8200) for modelservice deployments or `LLMDBENCH_VLLM_COMMON_INFERENCE_PORT` (default: 8000) for standalone.
5. **Timestamped snapshots** -- Each scrape is saved as a timestamped file in `metrics/raw/` with metadata headers (timestamp, pod name, namespace).
6. **Post-processing** -- After the benchmark completes, `process_metrics.py` aggregates raw scrapes into summary statistics (mean, stddev, min, max, percentiles) in `metrics/processed/`.

### Integration With Harness Entrypoints

Each harness entrypoint script calls `collect_metrics.sh` functions at specific points:

```bash
# Before benchmark: start background collection
source collect_metrics.sh
start_metrics_collection "$NAMESPACE" &
METRICS_PID=$!

# Run the benchmark...

# After benchmark: stop collection and process
stop_metrics_collection $METRICS_PID
process_metrics
```

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `METRICS_COLLECTION_INTERVAL` | `15` | Seconds between metric scrapes |
| `LLMDBENCH_VLLM_COMMON_METRICS_PORT` | `8200` | Prometheus metrics port (modelservice) |
| `LLMDBENCH_VLLM_COMMON_INFERENCE_PORT` | `8000` | Prometheus metrics port (standalone) |
| `LLMDBENCH_VLLM_MONITORING_METRICS_PATH` | `/metrics` | Prometheus scrape path |
| `METRICS_CURL_TIMEOUT` | `30` | Maximum seconds per curl request |

## Metric Visualization (`visualize_metrics.py`)

The `visualize_metrics.py` module generates time series plots from the raw Prometheus metric files collected by `collect_metrics.sh`. It can be invoked automatically via the `--analyze` flag or used as a standalone script.

### Generated Plots

The module produces PNG plots for the following metric categories:

| Plot | Metric(s) | Description |
|------|-----------|-------------|
| KV Cache Usage | `vllm:kv_cache_usage_perc` | KV cache utilization over time |
| GPU Utilization | `gpu_utilization_percent` | GPU compute utilization (from logs) |
| GPU Memory | `gpu_memory_used_gb`, `gpu_memory_total_gb` | GPU memory usage over time |
| Running/Waiting Requests | `vllm:num_requests_running`, `vllm:num_requests_waiting` | Request queue depth over time |
| Throughput | `prompt_throughput_tokens_per_sec`, `generation_throughput_tokens_per_sec` | Token throughput over time |
| Cache Hit Rate | `cache_hit_rate_percent` | Prefix cache hit rate over time |
| Power Consumption | `power_consumption_watts` | GPU power draw over time |

All plots include timestamps on the x-axis and are saved to `<results_dir>/analysis/metrics/`.

### Standalone Usage

```bash
python -m llmdbenchmark.analysis.visualize_metrics /path/to/results/metrics/raw
```

## Flow Control Metrics (EPP)

When flow control is enabled on the EPP (inference scheduler), additional metrics are available for collection and visualization. These metrics provide visibility into how the scheduler manages request queuing and load distribution across model-serving pods.

### Available Flow Control Metrics

| Metric | Description |
|--------|-------------|
| `inference_extension_flow_control_queue_size` | Number of requests queued by the flow controller |
| `inference_extension_flow_control_pool_saturation` | Fraction of total pool capacity currently in use |
| `inference_extension_scheduler_e2e_duration_seconds` | End-to-end scheduling latency |
| `inference_pool_average_kv_cache_utilization` | Pool-wide average KV cache utilization |
| `inference_pool_average_queue_size` | Average request queue depth across all pods |
| `inference_pool_ready_pods` | Number of ready pods in the inference pool |

### Enabling Flow Control Metrics

Flow control metrics require:

1. The EPP (inference extension) deployed with flow control enabled in the GAIE plugin configuration
2. A ServiceMonitor configured for the EPP pod (see `inferenceExtension.monitoring` in [config/README.md](../config/README.md#monitoring-and-metrics))
3. Prometheus scraping enabled for the EPP metrics endpoint

These metrics are collected alongside standard vLLM pod metrics and appear in the same results directory for unified visualization.
