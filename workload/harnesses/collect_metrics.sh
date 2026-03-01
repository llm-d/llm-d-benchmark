#!/usr/bin/env bash

# Copyright 2025 The llm-d Authors.
# Licensed under the Apache License, Version 2.0 (the "License");

# Metrics collection script for llm-d-benchmark
# Collects Prometheus metrics and vLLM logs during benchmark execution

set -euo pipefail

# Configuration
METRICS_DIR="${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/metrics"
COLLECTION_INTERVAL="${METRICS_COLLECTION_INTERVAL:-5}"  # seconds between collections
METRICS_PORT="${METRICS_PORT:-8000}"  # Default vLLM metrics port

# Function to initialize metrics directory
init_metrics_dir() {
    mkdir -p "$METRICS_DIR"
    mkdir -p "$METRICS_DIR/raw"
    mkdir -p "$METRICS_DIR/processed"
    echo "Metrics directory initialized: $METRICS_DIR"
}

# Function to get pod names for the deployment
get_pod_names() {
    local namespace="${1:-default}"
    local label_selector="${2:-}"
    
    if [[ -n "$label_selector" ]]; then
        kubectl get pods -n "$namespace" -l "$label_selector" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || echo ""
    else
        kubectl get pods -n "$namespace" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || echo ""
    fi
}

# Function to collect logs from a single pod
collect_logs_from_pod() {
    local pod="$1"
    local namespace="$2"
    local timestamp="$3"
    local output_file="$4"
    
    # Use kubectl/oc logs to get pod logs
    local kubectl_cmd="${KUBECTL_CMD:-kubectl}"
    
    {
        echo "# Timestamp: $timestamp"
        echo "# Pod: $pod"
        echo "# Namespace: $namespace"
        echo ""
        $kubectl_cmd logs -n "$namespace" "$pod" --tail=100 2>/dev/null || echo "# Warning: Failed to collect logs from pod $pod"
        echo ""
    } >> "$output_file"
    
    return 0
}

# Function to collect logs snapshot
collect_metrics_snapshot() {
    local namespace="${LLMDBENCH_VLLM_COMMON_NAMESPACE:-default}"
    local pod_pattern="${LLMDBENCH_METRICS_POD_PATTERN:-decode}"
    local timestamp=$(date +%s)
    local iso_timestamp=$(date --iso-8601=seconds)
    
    echo "Collecting logs at $iso_timestamp"
    echo "Namespace: $namespace"
    echo "Pod pattern: $pod_pattern"
    
    # Get pod names using simple grep pattern
    local kubectl_cmd="${KUBECTL_CMD:-kubectl}"
    echo "Using kubectl command: $kubectl_cmd"
    
    # Get pods using grep pattern - simpler approach that doesn't require label selectors
    local pods=$($kubectl_cmd get pods -n "$namespace" 2>&1 | grep "$pod_pattern" | grep "Running" | awk '{print $1}')
    local rc=$?
    
    if [[ $rc -ne 0 ]]; then
        echo "Error getting pods: $pods" >&2
        return 1
    fi
    
    if [[ -z "$pods" ]]; then
        echo "Warning: No running pods found in namespace $namespace matching pattern '$pod_pattern'" >&2
        echo "Trying to list all pods for debugging..." >&2
        $kubectl_cmd get pods -n "$namespace" 2>&1 | head -10 >&2
        return 1
    fi
    
    echo "Found pods: $pods"
    
    # Collect from each pod
    for pod in $pods; do
        local pod_log_file="$METRICS_DIR/raw/${pod}_${timestamp}.log"
        collect_logs_from_pod "$pod" "$namespace" "$iso_timestamp" "$pod_log_file"
    done
}

# Function to start continuous collection in background
start_continuous_collection() {
    local duration="${1:-0}"  # 0 means run until stopped
    
    init_metrics_dir
    
    echo "Starting continuous metrics collection (interval: ${COLLECTION_INTERVAL}s)"
    echo $$ > "$METRICS_DIR/collector.pid"
    
    local start_time=$(date +%s)
    local iterations=0
    
    while true; do
        collect_metrics_snapshot
        iterations=$((iterations + 1))
        
        # Check if we should stop (duration exceeded)
        if [[ $duration -gt 0 ]]; then
            local current_time=$(date +%s)
            local elapsed=$((current_time - start_time))
            if [[ $elapsed -ge $duration ]]; then
                echo "Collection duration reached ($duration seconds), stopping"
                break
            fi
        fi
        
        sleep "$COLLECTION_INTERVAL"
    done
    
    echo "Collected $iterations snapshots"
    rm -f "$METRICS_DIR/collector.pid"
}

# Function to stop continuous collection
stop_continuous_collection() {
    if [[ -f "$METRICS_DIR/collector.pid" ]]; then
        local pid=$(cat "$METRICS_DIR/collector.pid")
        if kill -0 "$pid" 2>/dev/null; then
            echo "Stopping metrics collector (PID: $pid)"
            kill "$pid"
            rm -f "$METRICS_DIR/collector.pid"
        fi
    fi
}

# Function to parse and aggregate collected logs
process_collected_metrics() {
    echo "Processing collected logs..."
    
    # Call Python script to process vLLM logs
    python3 - <<'PYTHON_SCRIPT'
import os
import re
import json
import glob
from collections import defaultdict
import statistics

metrics_dir = os.environ.get('METRICS_DIR', 'metrics')
raw_dir = os.path.join(metrics_dir, 'raw')
processed_dir = os.path.join(metrics_dir, 'processed')

def parse_vllm_log(file_path):
    """Parse vLLM logs to extract metrics."""
    metrics = defaultdict(list)
    timestamp = None
    pod_name = None
    namespace = None
    
    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()
            
            # Extract metadata
            if line.startswith('# Timestamp:'):
                timestamp = line.split(':', 1)[1].strip()
            elif line.startswith('# Pod:'):
                pod_name = line.split(':', 1)[1].strip()
            elif line.startswith('# Namespace:'):
                namespace = line.split(':', 1)[1].strip()
            
            # Parse KV cache usage: "GPU KV cache usage: 45.2%"
            match = re.search(r'GPU KV cache usage:\s*([\d.]+)%', line)
            if match:
                metrics['kv_cache_usage_percent'].append(float(match.group(1)))
            
            # Parse KV cache hit rate: "Prefix cache hit rate: 51.0%"
            match = re.search(r'Prefix cache hit rate:\s*([\d.]+)%', line)
            if match:
                hit_rate = float(match.group(1))
                metrics['cache_hit_rate_percent'].append(hit_rate)
            
            # Parse cache hits and misses: "Cache hits: 1234, misses: 56"
            match = re.search(r'Cache hits:\s*(\d+)(?:,\s*misses:\s*(\d+))?', line)
            if match:
                hits = int(match.group(1))
                metrics['cache_hits'].append(hits)
                if match.group(2):
                    misses = int(match.group(2))
                    metrics['cache_misses'].append(misses)
                    # Calculate hit rate
                    total = hits + misses
                    if total > 0:
                        metrics['cache_hit_rate_percent'].append((hits / total) * 100)
            
            # Parse GPU memory: "GPU memory usage: 12.5 GB / 80.0 GB"
            match = re.search(r'GPU memory usage:\s*([\d.]+)\s*GB\s*/\s*([\d.]+)\s*GB', line)
            if match:
                used_gb = float(match.group(1))
                total_gb = float(match.group(2))
                metrics['gpu_memory_used_gb'].append(used_gb)
                metrics['gpu_memory_total_gb'].append(total_gb)
                metrics['gpu_memory_usage_percent'].append((used_gb / total_gb) * 100 if total_gb > 0 else 0)
            
            # Parse CPU memory: "CPU memory usage: 2.3 GB"
            match = re.search(r'CPU memory usage:\s*([\d.]+)\s*GB', line)
            if match:
                metrics['cpu_memory_used_gb'].append(float(match.group(1)))
            
            # Parse GPU utilization: "GPU utilization: 87%"
            match = re.search(r'GPU utilization:\s*([\d.]+)%', line)
            if match:
                metrics['gpu_utilization_percent'].append(float(match.group(1)))
            
            # Parse requests: "Avg prompt throughput: 123.4 tokens/s, Avg generation throughput: 456.7 tokens/s"
            match = re.search(r'Avg prompt throughput:\s*([\d.]+)\s*tokens/s', line)
            if match:
                metrics['prompt_throughput_tokens_per_sec'].append(float(match.group(1)))
            
            match = re.search(r'Avg generation throughput:\s*([\d.]+)\s*tokens/s', line)
            if match:
                metrics['generation_throughput_tokens_per_sec'].append(float(match.group(1)))
            
            # Parse running requests: "Running: 5 reqs"
            match = re.search(r'Running:\s*(\d+)\s*reqs?', line)
            if match:
                metrics['running_requests'].append(int(match.group(1)))
            
            # Parse waiting requests: "Waiting: 12 reqs"
            match = re.search(r'Waiting:\s*(\d+)\s*reqs?', line)
            if match:
                metrics['waiting_requests'].append(int(match.group(1)))
    
    return timestamp, pod_name, namespace, dict(metrics)

def aggregate_logs():
    """Aggregate metrics from all collected log files."""
    # Structure: {pod_name: {metric_name: [values]}}
    pod_metrics = defaultdict(lambda: defaultdict(list))
    pod_metadata = {}
    
    # Process all raw log files
    log_files = glob.glob(os.path.join(raw_dir, '*.log'))
    
    if not log_files:
        print("Warning: No raw log files found to process")
        print(f"Checked directory: {raw_dir}")
        # Create an informative empty result
        results = {
            '_info': {
                'status': 'no_data',
                'message': 'No metrics collected - no raw log files found',
                'raw_dir': raw_dir,
                'possible_reasons': [
                    'Metrics collection could not find any pods',
                    'kubectl/oc command may not have access to the cluster',
                    'Label selector may not match any pods',
                    'Namespace may be incorrect'
                ]
            }
        }
        output_file = os.path.join(processed_dir, 'metrics_summary.json')
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"Empty metrics summary saved to: {output_file}")
        return results
    
    print(f"Processing {len(log_files)} log files...")
    
    for file_path in log_files:
        timestamp, pod_name, namespace, metrics = parse_vllm_log(file_path)
        
        if pod_name:
            if pod_name not in pod_metadata:
                pod_metadata[pod_name] = {'namespace': namespace, 'log_files': []}
            pod_metadata[pod_name]['log_files'].append(os.path.basename(file_path))
            
            for metric_name, values in metrics.items():
                pod_metrics[pod_name][metric_name].extend(values)
    
    # Calculate statistics for each metric
    results = {}
    for pod_name, metrics in pod_metrics.items():
        results[pod_name] = {
            'metadata': pod_metadata.get(pod_name, {}),
            'metrics': {}
        }
        
        for metric_name, values in metrics.items():
            if values:
                results[pod_name]['metrics'][metric_name] = {
                    'mean': statistics.mean(values),
                    'stddev': statistics.stdev(values) if len(values) > 1 else 0,
                    'min': min(values),
                    'max': max(values),
                    'count': len(values),
                    'unit': get_metric_unit(metric_name)
                }
    
    # Save aggregated results
    output_file = os.path.join(processed_dir, 'metrics_summary.json')
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"Metrics summary saved to: {output_file}")
    print(f"Processed metrics from {len(results)} pods")
    return results

def get_metric_unit(metric_name):
    """Get the unit for a metric."""
    units = {
        'kv_cache_usage_percent': '%',
        'cache_hit_rate_percent': '%',
        'cache_hits': 'count',
        'cache_misses': 'count',
        'gpu_memory_used_gb': 'GB',
        'gpu_memory_total_gb': 'GB',
        'gpu_memory_usage_percent': '%',
        'cpu_memory_used_gb': 'GB',
        'gpu_utilization_percent': '%',
        'prompt_throughput_tokens_per_sec': 'tokens/s',
        'generation_throughput_tokens_per_sec': 'tokens/s',
        'running_requests': 'count',
        'waiting_requests': 'count',
    }
    return units.get(metric_name, '')

if __name__ == '__main__':
    aggregate_logs()
PYTHON_SCRIPT
}

# Main command dispatcher
case "${1:-}" in
    start)
        start_continuous_collection "${2:-0}"
        ;;
    stop)
        stop_continuous_collection
        ;;
    snapshot)
        init_metrics_dir
        collect_metrics_snapshot
        ;;
    process)
        process_collected_metrics
        ;;
    *)
        echo "Usage: $0 {start [duration]|stop|snapshot|process}"
        echo "  start [duration]  - Start continuous collection (optional duration in seconds)"
        echo "  stop              - Stop continuous collection"
        echo "  snapshot          - Collect a single snapshot"
        echo "  process           - Process and aggregate collected metrics"
        exit 1
        ;;
esac

# Made with Bob
