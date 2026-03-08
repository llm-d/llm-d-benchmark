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

### Implemented but Requires Additional Setup

The following capability is implemented but requires cluster-admin permissions to function:

1. **Cluster-Level Prometheus Metrics** - Queries cluster Prometheus/Thanos for container resource metrics
   - **Status**: Code implemented and deployed
   - **Requirement**: Cluster-admin must grant ClusterRole permissions
   - **Impact**: Without permissions, 117+ pod-level metrics still work normally

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

The metrics collection system gathers metrics from three sources:

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

### 2. Cluster-Level Metrics (Prometheus/Thanos) 🔧 Implemented, Needs Permissions

These metrics are collected from the cluster-wide Prometheus/Thanos querier. The collection code is implemented and deployed, but requires cluster-admin to grant additional RBAC permissions:

#### Container Resource Metrics
- **`container_memory_usage_gb`** - Container memory usage (converted from bytes)
- **`container_memory_working_set_gb`** - Container working set memory (converted from bytes)
- **`container_cpu_usage_seconds_total`** - Cumulative CPU usage in seconds
- **`container_network_receive_mb_total`** - Total network bytes received (converted to MB)
- **`container_network_transmit_mb_total`** - Total network bytes transmitted (converted to MB)

### 3. Log-Parsed Metrics Working

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
