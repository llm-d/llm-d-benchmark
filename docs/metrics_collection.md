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

Metrics collection can be enabled via CLI (`llmdbenchmark run --monitoring`) or via scenario config. The CLI flag sets `metricsScrapeEnabled: true` at runtime, overriding the scenario default.

| Environment Variable | Default | Description |
|---|---|---|
| `LLMDBENCH_VLLM_COMMON_METRICS_SCRAPE_ENABLED` | `false` | Enable/disable metrics collection. Set automatically when `--monitoring` is passed. |
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

1. **vLLM Pod Prometheus Metrics** - Cache, queue, memory, preemption, and NIXL KV transfer metrics scraped from vLLM pods
2. **EPP Prometheus Metrics** - Pool-level gauges, scheduler histograms, token distributions, P/D decision counters scraped from EPP pods
3. **DCGM GPU Metrics** - GPU utilization, framebuffer usage, and power consumption (requires DCGM exporter deployed on the cluster)
4. **Container Metrics (cAdvisor)** - Memory, CPU, and network usage from kubelet
5. **Metrics Processing** - Per-pod and cluster-wide aggregation with statistics (mean, stddev, min, max, p25/p50/p75/p90/p95/p99)
6. **Replica Status** - Desired vs ready vs available replica counts per Deployment/StatefulSet, grouped by model and role
7. **Pod Startup Times** - Creation-to-Ready duration per pod, per node, grouped by model and role
8. **EPP Log-Derived Metrics** - Dispatch latency, endpoint scores, request distribution, plugin latencies, saturation config from EPP pod logs
9. **Time-Series Visualization** - Static PNG graphs generated after benchmark completion
10. **Benchmark Report Integration** - All metrics surfaced in `results.observability` section
11. **RBAC Setup** - Automatic ServiceAccount creation with required permissions
12. **Metrics Storage** - Raw and processed metrics saved to results directory

### Not Yet Implemented

1. **Real-time Visualization** - Live metric streaming during benchmark execution
   - Currently generates static graphs after benchmark completion

2. **Custom Metric Queries** - User-defined Prometheus queries
   - Currently collects predefined set of metrics

## Collected Metrics

The metrics collection system scrapes Prometheus endpoints from vLLM pods and EPP pods, collects Kubernetes infrastructure snapshots, and extracts scheduling metrics from EPP logs. All metrics are aggregated into per-pod statistics (mean, stddev, min, max, p25/p50/p75/p90/p95/p99).

### vLLM Pod Metrics (Prometheus `/metrics` endpoint)

Scraped from each vLLM pod (default port 8200 for modelservice, 8000 for standalone):

#### Cache Metrics
- **`vllm:kv_cache_usage_perc`** - KV cache utilization (%)
- **`vllm:gpu_cache_usage_perc`** - GPU cache utilization (%)
- **`vllm:cpu_cache_usage_perc`** - CPU cache utilization (%)
- **`vllm:prefix_cache_hits_total`** - Prefix cache hits (tokens)
- **`vllm:prefix_cache_queries_total`** - Prefix cache queries (tokens)
- **`vllm:external_prefix_cache_hits_total`** - Cross-instance external cache hits (tokens)
- **`vllm:external_prefix_cache_queries_total`** - Cross-instance external cache queries (tokens)
- **`vllm:prefix_cache_hit_rate`** - Computed: `hits / queries` (%)
- **`vllm:external_prefix_cache_hit_rate`** - Computed: `external_hits / external_queries` (%)

#### Request Queue Metrics
- **`vllm:num_requests_running`** - Requests currently in execution batches (count)
- **`vllm:num_requests_waiting`** - Requests waiting to be processed (count)
- **`vllm:num_requests_swapped`** - Requests swapped to CPU (count)
- **`vllm:num_preemptions_total`** - Cumulative request preemptions (count)

#### Memory Metrics
- **`vllm:gpu_memory_usage_bytes`** - GPU memory usage (bytes)
- **`vllm:cpu_memory_usage_bytes`** - CPU memory usage (bytes)

#### NIXL KV Transfer Metrics
- **`vllm:nixl_xfer_time_seconds_sum`** - Cumulative NIXL transfer time (seconds)
- **`vllm:nixl_xfer_time_seconds_count`** - Number of NIXL transfers (count)
- **`vllm:nixl_bytes_transferred_sum`** - Cumulative bytes transferred via NIXL (bytes)
- **`vllm:nixl_bytes_transferred_count`** - Number of NIXL byte transfers (count)

### GPU/System Metrics (DCGM and cAdvisor)

Scraped from cluster monitoring infrastructure when available (requires DCGM exporter or kubelet cAdvisor):

#### DCGM GPU Metrics
- **`DCGM_FI_DEV_FB_USED`** - GPU framebuffer memory used (bytes)
- **`DCGM_FI_DEV_GPU_UTIL`** - GPU utilization (%)
- **`DCGM_FI_DEV_POWER_USAGE`** - GPU power consumption (watts)

#### Container Metrics (cAdvisor)
- **`container_memory_usage_bytes`** - Container memory usage (converted to GB in output)
- **`container_memory_working_set_bytes`** - Container working set memory (converted to GB)
- **`container_network_receive_bytes_total`** - Network bytes received (converted to MB)
- **`container_network_transmit_bytes_total`** - Network bytes transmitted (converted to MB)
- **`container_cpu_usage_seconds_total`** - Container CPU time (seconds)

### EPP Prometheus Metrics (Inference Extension)

Scraped from EPP pods (default port 9090, bearer token auth):

#### Pool-Level Gauges
- **`inference_pool_average_kv_cache_utilization`** - Pool-wide KV cache utilization (%)
- **`inference_pool_average_queue_size`** - Average request queue depth (count)
- **`inference_pool_average_running_requests`** - Average running requests (count)
- **`inference_pool_ready_pods`** - Ready pod count (count)

#### Scheduler Histograms
- **`inference_extension_scheduler_e2e_duration_seconds`** - End-to-end scheduling latency (bucket/sum/count)
- **`inference_extension_plugin_duration_seconds`** - Per-plugin processing time (bucket/sum/count)
- **`inference_extension_request_duration_seconds`** - Total request duration (bucket/sum/count)
- **`inference_extension_request_ttft_duration_seconds`** - Time to first token (bucket/sum/count)

#### Token and Routing Metrics
- **`inference_extension_input_tokens`** - Input token count distribution (bucket)
- **`inference_extension_output_tokens`** - Output token count distribution (bucket)
- **`inference_extension_normalized_time_per_output_token`** - NTPOT distribution (bucket)
- **`inference_extension_prefix_indexer_hit_ratio`** - Prefix indexer hit ratio (bucket)
- **`inference_extension_prefix_indexer_size`** - Prefix indexer size (count)

#### P/D Decision Metrics
- **`llm_d_inference_scheduler_pd_decision_total`** - P/D routing decisions (count)
- **`llm_d_inference_scheduler_disagg_decision_total`** - Disaggregation decisions (count)

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

### EPP Log-Derived Metrics (`epp_metrics_summary.json`)

Extracted from EPP pod structured JSON logs by `process_epp_logs.py`:
- **Dispatch latency** - End-to-end request routing latency (`assembled_time` to `picker_complete_time`), with mean/stddev/min/max/p50/p95/p99
- **Endpoint scores** - Per-endpoint scoring statistics
- **Request distribution** - Request count per picked endpoint
- **Plugin latencies** - Per filter and scorer plugin processing times (seconds)
- **Saturation config** - `queueDepthThreshold`, `kvCacheUtilThreshold`, `metricsStalenessThreshold` from EPP startup logs
- **Error tracking** - Error message counts by type

## Key Files

| File | Purpose |
|---|---|
| `workload/harnesses/collect_metrics.sh` | Main collection driver (pod discovery, scraping, infrastructure snapshots) |
| `workload/harnesses/process_metrics.py` | Raw metric aggregation into `metrics_summary.json` |
| `workload/harnesses/process_epp_logs.py` | EPP log parsing into `epp_metrics_summary.json` |
| `llmdbenchmark/analysis/visualize_metrics.py` | Time-series PNG graph generation |
| `llmdbenchmark/analysis/benchmark_report/metrics_processor.py` | Benchmark report integration |
