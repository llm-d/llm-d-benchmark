# Metrics Collection for Benchmarking

This document describes the metrics collection feature, which captures system and application metrics during benchmark runs.

## Overview

The metrics collection system automatically gathers performance and resource utilization metrics from deployed pods during benchmark execution. These metrics are integrated into the benchmark report and can be visualized as time series graphs.

## Implementation Status

### Currently Implemented and Working

The following metrics collection capabilities are fully implemented and operational:

1. **Pod-Level Prometheus Metrics** - Collecting 117+ metrics from vLLM pods via `/metrics` endpoint
2. **Metrics Processing** - Aggregating and calculating statistics (mean, stddev, min, max, percentiles)
3. **RBAC Setup** - Automatic ServiceAccount creation with required permissions
4. **Metrics Storage** - Raw and processed metrics saved to results directory

### Not Yet Implemented

The following features from the original design are not yet implemented:

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

Collected from each vLLM pod's `/metrics` endpoint (default port 8000):

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
