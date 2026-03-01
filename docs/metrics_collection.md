# Metrics Collection for Benchmarking

This document describes the metrics collection feature added to llm-d-benchmark, which captures system and application metrics during benchmark runs.

## Overview

The metrics collection system automatically gathers performance and resource utilization metrics from deployed pods during benchmark execution. These metrics are integrated into the benchmark report and can be visualized as time series graphs.

## Collected Metrics

The following metrics are collected from each pod in the deployment:

### Cache Metrics
- **KV Cache Usage** (`vllm:kv_cache_usage_perc`) - Percentage of KV cache utilized
- **Cache Hit Rate** (`cache_hit_rate_percent`) - Prefix cache hit rate percentage (parsed from vLLM logs)
- **GPU Cache Usage** (`vllm:gpu_cache_usage_perc`) - GPU cache utilization percentage
- **CPU Cache Usage** (`vllm:cpu_cache_usage_perc`) - CPU cache utilization percentage

### Memory Metrics
- **GPU Memory Usage** (`vllm:gpu_memory_usage_bytes`, `DCGM_FI_DEV_FB_USED`) - GPU memory consumption
- **CPU/RAM Usage** (`vllm:cpu_memory_usage_bytes`, `container_memory_usage_bytes`) - System memory usage
- **Storage Usage** - Disk/storage utilization

### Compute Metrics
- **GPU Utilization** (`DCGM_FI_DEV_GPU_UTIL`) - GPU compute utilization percentage
- **CPU Utilization** (`container_cpu_usage_seconds_total`) - CPU usage percentage

### Performance Metrics
- **TTFT** (Time to First Token) - Already captured in request-level metrics
- **Power Consumption** (`DCGM_FI_DEV_POWER_USAGE`) - GPU power usage in Watts
- **Drop Rate** - Request drop/failure rate
- **Replicas** - Number of active replicas

### Queue Metrics
- **Running Requests** (`vllm:num_requests_running`) - Currently executing requests
- **Waiting Requests** (`vllm:num_requests_waiting`) - Queued requests
- **Swapped Requests** (`vllm:num_requests_swapped`) - Swapped out requests

## Architecture

The metrics collection system consists of several components:

1. **`collect_metrics.sh`** - Shell script that collects metrics from pods via Prometheus endpoints
2. **`metrics_processor.py`** - Python module that processes raw metrics and calculates statistics
3. **`visualize_metrics.py`** - Python module that generates time series graphs
4. **`integrate_metrics.py`** - Python module that integrates metrics into benchmark reports
5. **Schema extensions** - Extended `schema_v0_2.py` with observability metrics classes

## Usage

### Automatic Collection (Default)

Metrics collection is enabled by default. When you run a benchmark, metrics are automatically collected:

```bash
# Metrics will be collected automatically
./run.sh -m model-name -l inference-perf
```

### Disable Metrics Collection

To disable metrics collection:

```bash
export LLMDBENCH_COLLECT_METRICS=0
./run.sh -m model-name -l inference-perf
```

### Configure Collection Interval

Adjust how frequently metrics are collected (default: 5 seconds):

```bash
export METRICS_COLLECTION_INTERVAL=10  # Collect every 10 seconds
./run.sh -m model-name -l inference-perf
```

### Configure Metrics Port

If your deployment uses a non-standard metrics port:

```bash
export METRICS_PORT=9090  # Default is 8000
./run.sh -m model-name -l inference-perf
```

### Filter Pods by Label

To collect metrics only from specific pods:

```bash
export LLMDBENCH_METRICS_LABEL_SELECTOR="app=vllm,role=inference"
./run.sh -m model-name -l inference-perf
```

## Output Structure

After a benchmark run with metrics collection, the results directory contains:

```
results/
├── metrics/
│   ├── raw/
│   │   ├── pod-name-1_1234567890.txt
│   │   ├── pod-name-1_1234567895.txt
│   │   ├── pod-name-2_1234567890.txt
│   │   └── ...
│   ├── processed/
│   │   └── metrics_summary.json
│   └── graphs/
│       ├── vllm_kv_cache_usage_perc.png
│       ├── vllm_gpu_memory_usage_bytes.png
│       ├── DCGM_FI_DEV_GPU_UTIL.png
│       └── ...
├── benchmark_report.yaml
├── stdout.log
└── stderr.log
```

### Directory Contents

- **`raw/`** - Raw Prometheus metrics snapshots, one file per pod per collection interval
- **`processed/`** - Aggregated metrics with statistics (mean, stddev, min, max, percentiles)
- **`graphs/`** - Time series visualizations in PNG format

## Benchmark Report Integration

Metrics are automatically integrated into the benchmark report under `results.observability.components`:

```yaml
results:
  observability:
    components:
      - component_label: vllm-service
        replica_id: vllm-pod-1
        aggregate:
          kv_cache_usage:
            units: percent
            mean: 45.2
            stddev: 8.3
            min: 32.1
            max: 68.9
          cache_hit_rate:
            units: percent
            mean: 65.5
            stddev: 12.1
            min: 48.0
            max: 82.3
          gpu_memory_usage:
            units: GiB
            mean: 42.5
            stddev: 2.1
            min: 38.2
            max: 46.8
          gpu_utilization:
            units: percent
            mean: 78.4
            stddev: 12.6
            min: 45.0
            max: 95.2
          power_consumption:
            units: Watts
            mean: 285.3
            stddev: 45.2
            min: 180.0
            max: 350.0
          running_requests:
            units: count
            mean: 12.5
            stddev: 3.2
            min: 5
            max: 20
          waiting_requests:
            units: count
            mean: 2.1
            stddev: 1.8
            min: 0
            max: 8
        raw_data_path: metrics/raw/vllm-pod-1_*.txt
```

## Manual Operations

### Collect a Single Snapshot

```bash
./workload/harnesses/collect_metrics.sh snapshot
```

### Process Collected Metrics

```bash
./workload/harnesses/collect_metrics.sh process
```

### Generate Visualizations

```bash
python3 -m benchmark_report.visualize_metrics /path/to/metrics/dir
```

### Integrate Metrics into Existing Report

```bash
python3 -m benchmark_report.integrate_metrics \
  benchmark_report.yaml \
  metrics/ \
  -o updated_report.yaml
```

## Visualization

The visualization module generates time series graphs for key metrics:

- KV cache usage over time
- GPU/CPU memory usage trends
- GPU utilization patterns
- Power consumption profile
- Request queue depths

Graphs are saved as PNG files in the `metrics/graphs/` directory.

### Custom Visualization

You can create custom visualizations using the collected data:

```python
from benchmark_report.visualize_metrics import collect_time_series_data, plot_metric_time_series

# Load data
pod_data = collect_time_series_data('/path/to/metrics')

# Plot specific metric
plot_metric_time_series(
    pod_data,
    'vllm:kv_cache_usage_perc',
    'custom_plot.png',
    title='Custom KV Cache Analysis',
    ylabel='Cache Usage (%)'
)
```

## Requirements

### Core Functionality
- Python 3.8+
- `kubectl` access to the cluster
- Prometheus metrics endpoint on pods (default: port 8000)

### Visualization (Optional)
- `matplotlib` - Install with: `pip install matplotlib`

If matplotlib is not available, metrics will still be collected and processed, but graphs won't be generated.

## Troubleshooting

### No Metrics Collected

1. **Check pod accessibility**: Ensure `kubectl` or `oc` commands work for target pods
2. **Verify metrics endpoint**: Confirm pods expose metrics on the configured port
3. **Check namespace**: Ensure `LLMDBENCH_VLLM_COMMON_NAMESPACE` is set correctly
4. **Verify RBAC permissions**: Ensure the harness pod has permissions to list pods and read logs

```bash
# Test metrics endpoint manually
kubectl exec -n <namespace> <pod-name> -- curl http://localhost:8000/metrics

# Check if oc command is available (for OpenShift)
oc version

# Verify pod discovery
oc get pods -n <namespace> | grep <pod-pattern>
```

### RBAC Permission Issues

If you see "Forbidden" errors when collecting metrics:

1. **For existing_stack/run_only.sh**: The script automatically creates a ServiceAccount with minimal required permissions
2. **For custom deployments**: Ensure your harness pod has a ServiceAccount with these permissions:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: llmdbench-metrics-reader
rules:
- apiGroups: [""]
  resources: ["pods", "pods/log"]
  verbs: ["get", "list"]
```

### Empty Raw Folder

If `metrics/raw/` is empty after a benchmark run:

1. **Check OpenShift CLI**: Ensure `oc` command is installed in the harness container
2. **Verify pod pattern**: Confirm `LLMDBENCH_METRICS_POD_PATTERN` matches your vLLM pods
3. **Check logs**: Review `metrics_collection.log` for errors
4. **Test pod discovery**: Run `oc get pods -n <namespace> | grep <pattern>` manually

### Empty metrics_summary.json

If `metrics/processed/metrics_summary.json` contains only `{}`:

1. **Check raw data**: Verify that raw metric files exist in `metrics/raw/`
2. **Verify log parsing**: Ensure vLLM logs contain expected metrics (e.g., "Prefix cache hit rate: X.X%")
3. **Check regex patterns**: The collection script uses specific patterns to parse metrics from logs
4. **Review processing logs**: Check for errors during the processing phase

### Incorrect Benchmark Report Filename

If benchmark report files have commas in their names (e.g., `benchmark_report_v0.2,_stage_0...`):

This was a bug in the analysis scripts that has been fixed. Ensure you're using the latest version of:
- `analysis/inference-perf-analyze_results.sh`
- `analysis/guidellm-analyze_results.sh`

### Missing Specific Metrics

Some metrics may not be available depending on:
- vLLM version and configuration
- GPU drivers and DCGM availability
- Kubernetes monitoring setup

### Visualization Errors

If graph generation fails:
1. Install matplotlib: `pip install matplotlib`
2. Check for sufficient data points in raw metrics
3. Verify timestamp parsing in metric files

## Running Against Existing Stacks

When running benchmarks against existing inference deployments (e.g., using `existing_stack/run_only.sh`):

### Prerequisites

1. **OpenShift CLI**: Ensure the harness container image includes `oc` command
2. **RBAC Permissions**: The script automatically creates a ServiceAccount with minimal permissions
3. **Pod Pattern**: Configure `LLMDBENCH_METRICS_POD_PATTERN` to match your vLLM pods

### Configuration Example

```yaml
harness:
  image: your-registry/llm-d-benchmark-metrics:v0.9.1-amd64
  env:
    LLMDBENCH_COLLECT_METRICS: "1"
    LLMDBENCH_METRICS_POD_PATTERN: "vllm"  # or "decode" for disaggregated
    LLMDBENCH_VLLM_COMMON_NAMESPACE: "your-namespace"
    METRICS_COLLECTION_INTERVAL: "5"
```

### Workload Configuration for Cache Hit Testing

To test prefix cache hit rates, use workloads with shared prefixes:

```yaml
workload:
  profile: shared_prefix_test
  data:
    shared_prefix: "You are a helpful AI assistant. Please answer the following question: "
    num_requests: 100
    request_rate: 10
```

### Verification Steps

After running a benchmark:

```bash
# Check raw logs were collected
ls -la results/metrics/raw/

# Verify metrics summary
cat results/metrics/processed/metrics_summary.json | jq '.[] | .metrics.cache_hit_rate_percent'

# Check benchmark report filename (should not contain commas)
ls -la results/benchmark_report*.yaml
```

## Best Practices

1. **Collection Interval**: Use 5-10 second intervals for most workloads. Shorter intervals increase overhead.

2. **Storage**: Metrics can consume significant disk space for long runs. Monitor available storage.

3. **Analysis**: Use the aggregated statistics in `metrics_summary.json` for quick insights before examining time series.

4. **Comparison**: When comparing benchmarks, ensure consistent collection intervals and pod configurations.

5. **Production**: In production environments, consider using dedicated monitoring solutions (Prometheus, Grafana) alongside this feature.

6. **Cache Hit Testing**: Use workloads with shared prefixes to generate meaningful cache hit rate metrics (50-80% is typical for well-configured prefix caching).

7. **RBAC**: Always use minimal required permissions. The auto-created ServiceAccount only has `get` and `list` permissions for pods and logs.

## Future Enhancements

Potential improvements for future versions:

- Support for custom metric queries
- Real-time metric streaming
- Integration with external monitoring systems
- Automated anomaly detection
- Multi-cluster metric aggregation
- Cost analysis based on resource usage

## See Also

- [Benchmark Report Documentation](benchmark_report.md)
- [Observability Guide](observability.md)
- [Resource Requirements](resource_requirements.md)