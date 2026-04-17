# Metrics Collection for Benchmarking

This document describes the metrics collection feature, which captures system and application metrics during benchmark runs.

## Overview

The metrics collection system automatically gathers performance and resource utilization metrics from deployed pods during benchmark execution. These metrics are integrated into the benchmark report and can be visualized as time series graphs.

## Architecture

```
Harness Pod (in-cluster)
  |
  |-- collect_metrics.sh start     # continuous Prometheus scraping (every 15s)
  |     |-- collect_replica_status()       # one-time: Deployment/StatefulSet replica counts
  |     |-- collect_pod_startup_times()    # one-time: pod creation-to-Ready durations
  |     \-- collect_metrics_snapshot()     # repeated: curl /metrics from each vLLM pod
  |
  |-- collect_metrics.sh stop      # sends SIGTERM to collector process
  |-- collect_metrics.sh process   # delegates to process_metrics.py for aggregation
  |
  \-- Results directory:
        metrics/
          raw/                     # per-pod, per-snapshot .log files
          processed/
            metrics_summary.json   # aggregated statistics per pod per metric
            replica_status.json    # desired vs ready vs available replicas
            pod_startup_times.json # creation-to-Ready time per pod per node
          graphs/                  # time-series PNG plots (generated post-run)
```

### Data Flow

1. **Collection** (`collect_metrics.sh`): Discovers vLLM pods via label selectors, scrapes Prometheus `/metrics` endpoints every 15s via direct HTTP to pod IPs. Also collects one-time infrastructure snapshots (replica counts, startup times).
2. **Processing** (`process_metrics.py`): Parses raw `.log` files, aggregates per-pod statistics (mean, stddev, min, max, p25/p50/p75/p90/p95/p99). Computes ratio metrics (e.g., prefix cache hit rate).
3. **Visualization** (`visualize_metrics.py`): Generates time-series PNG graphs from raw metric files for each tracked metric.
4. **Report Integration** (`metrics_processor.py`): Loads processed summaries and feeds them into the benchmark report under `results.observability`.

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `LLMDBENCH_VLLM_COMMON_METRICS_SCRAPE_ENABLED` | `false` | Enable/disable metrics collection |
| `METRICS_COLLECTION_INTERVAL` | `15` | Seconds between collection snapshots |
| `LLMDBENCH_VLLM_COMMON_METRICS_PORT` | `8200` | Prometheus metrics port (modelservice) |
| `LLMDBENCH_VLLM_COMMON_INFERENCE_PORT` | `8000` | Fallback port (standalone vLLM) |
| `LLMDBENCH_VLLM_MONITORING_METRICS_PATH` | `/metrics` | Prometheus endpoint path |
| `METRICS_CURL_TIMEOUT` | `30` | Max seconds per curl request |
| `LLMDBENCH_METRICS_POD_PATTERN` | `decode` | Fallback pod name pattern for discovery |

## Pod Discovery

Pods are discovered using label selectors in priority order:

1. `llm-d.ai/inferenceServing=true` (modelservice deployments)
2. `stood-up-via=standalone` (standalone deployments)
3. Pod name pattern matching (fallback)

Only pods with `status.phase=Running` are scraped.

## Implementation Status

### Currently Implemented and Working

1. **Pod-Level Prometheus Metrics** - Collecting 117+ metrics from vLLM pods via `/metrics` endpoint
2. **Metrics Processing** - Aggregating and calculating statistics (mean, stddev, min, max, percentiles)
3. **Replica Status** - Desired vs ready vs available replica counts per Deployment/StatefulSet, grouped by model and role
4. **Pod Startup Times** - Creation-to-Ready duration per pod, per node, grouped by model and role
5. **EPP Metrics** - Endpoint Picker metrics (dispatch latency, endpoint scores, request distribution, plugin latencies)
6. **Time-Series Visualization** - Static PNG graphs generated after benchmark completion
7. **Benchmark Report Integration** - All metrics surfaced in `results.observability` section
8. **RBAC Setup** - Automatic ServiceAccount creation with required permissions
9. **Metrics Storage** - Raw and processed metrics saved to results directory

### Not Yet Implemented

1. **DCGM GPU Metrics** - Direct GPU monitoring metrics (DCGM_FI_DEV_GPU_UTIL, DCGM_FI_DEV_POWER_USAGE, etc.)
   - These metrics require DCGM exporter to be deployed in the cluster
   - Currently relying on vLLM's built-in Prometheus metrics instead

2. **Real-time Visualization** - Live metric streaming during benchmark execution
   - Currently generates static graphs after benchmark completion

3. **Custom Metric Queries** - User-defined Prometheus queries
   - Currently collects predefined set of metrics

## Collected Metrics

The metrics collection system gathers metrics from vLLM pod Prometheus endpoints.

### Pod-Level Metrics (vLLM Prometheus Endpoint)

Collected from each vLLM pod's `/metrics` endpoint (default port 8200 for modelservice, 8000 for standalone):

#### Cache Metrics
- **`vllm:kv_cache_usage_perc`** - KV cache utilization percentage (0-100)
- **`vllm:prefix_cache_hits_total`** - Total number of prefix cache hits (tokens)
- **`vllm:prefix_cache_queries_total`** - Total number of prefix cache queries (tokens)
- **`vllm:external_prefix_cache_hits_total`** - External cache hits from KV connector cross-instance sharing
- **`vllm:external_prefix_cache_queries_total`** - External cache queries from KV connector
- **`vllm:mm_cache_hits_total`** - Multi-modal cache hits (items)
- **`vllm:mm_cache_queries_total`** - Multi-modal cache queries (items)
- **Prefix cache hit rate** - Computed from `prefix_cache_hits_total / prefix_cache_queries_total`
- **External prefix cache hit rate** - Computed from `external_prefix_cache_hits_total / external_prefix_cache_queries_total`

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

#### NIXL KV Transfer Metrics
- **`vllm:nixl_xfer_time_seconds`** - Transfer duration for NIXL KV cache transfers (histogram)
- **`vllm:nixl_bytes_transferred`** - Bytes transferred via NIXL (histogram)
- **`vllm:nixl_num_descriptors`** - Number of NIXL descriptors (histogram)
- **`vllm:nixl_num_failed_transfers_total`** - Failed NIXL transfers
- **`vllm:nixl_num_failed_notifications_total`** - Failed NIXL notifications
- **`vllm:nixl_num_kv_expired_reqs_total`** - Expired KV transfer requests
- **`vllm:nixl_post_time_seconds`** - NIXL post time (histogram)

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

### Infrastructure Metrics (Kubernetes API)

Collected as one-time snapshots at the start of metrics collection:

#### Replica Status (`replica_status.json`)
Per Deployment/StatefulSet with `llm-d.ai/inferenceServing=true` pod template label:
- **`desired_replicas`** - `spec.replicas`
- **`ready_replicas`** - `status.readyReplicas`
- **`available_replicas`** - `status.availableReplicas`
- **`updated_replicas`** - `status.updatedReplicas`
- **`model`** - From `llm-d.ai/model` label
- **`role`** - From `llm-d.ai/role` label (decode/prefill/both)

#### Pod Startup Times (`pod_startup_times.json`)
Per Running vLLM pod:
- **`startup_seconds`** - `conditions[Ready].lastTransitionTime - metadata.creationTimestamp`
- **`node`** - `spec.nodeName`
- **`model`** - From `llm-d.ai/model` label
- **`role`** - From `llm-d.ai/role` label

### EPP Metrics (`epp_metrics_summary.json`)

Collected from EPP (Endpoint Picker) pod logs by `process_epp_logs.py`:
- **Dispatch latency** - End-to-end request routing latency
- **Endpoint scores** - Per-endpoint scoring metrics
- **Request distribution** - Request count per endpoint
- **Plugin latencies** - Per-plugin processing times (filter, scorer, picker)

## Key Files

| File | Purpose |
|---|---|
| `workload/harnesses/collect_metrics.sh` | Main collection driver (pod discovery, scraping, infrastructure snapshots) |
| `workload/harnesses/process_metrics.py` | Raw metric aggregation into `metrics_summary.json` |
| `workload/harnesses/process_epp_logs.py` | EPP log parsing into `epp_metrics_summary.json` |
| `llmdbenchmark/analysis/visualize_metrics.py` | Time-series PNG graph generation |
| `llmdbenchmark/analysis/benchmark_report/metrics_processor.py` | Benchmark report integration |
