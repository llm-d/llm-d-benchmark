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

# Function to collect metrics from a single pod via localhost
collect_metrics_from_pod() {
    local pod="$1"
    local namespace="$2"
    local timestamp="$3"
    local output_file="$4"
    
    # Use kubectl exec to curl metrics from within the pod
    if kubectl exec -n "$namespace" "$pod" -- curl -s http://localhost:${METRICS_PORT}/metrics >> "$output_file" 2>/dev/null; then
        echo "# Timestamp: $timestamp" >> "$output_file"
        echo "# Pod: $pod" >> "$output_file"
        echo "" >> "$output_file"
        return 0
    else
        echo "Warning: Failed to collect metrics from pod $pod" >&2
        return 1
    fi
}

# Function to collect metrics snapshot
collect_metrics_snapshot() {
    local namespace="${LLMDBENCH_VLLM_COMMON_NAMESPACE:-default}"
    local label_selector="${LLMDBENCH_METRICS_LABEL_SELECTOR:-}"
    local timestamp=$(date +%s)
    local iso_timestamp=$(date --iso-8601=seconds)
    
    echo "Collecting metrics at $iso_timestamp"
    
    # Get pod names
    local pods=$(get_pod_names "$namespace" "$label_selector")
    
    if [[ -z "$pods" ]]; then
        echo "Warning: No pods found in namespace $namespace" >&2
        return 1
    fi
    
    # Collect from each pod
    for pod in $pods; do
        local pod_metrics_file="$METRICS_DIR/raw/${pod}_${timestamp}.txt"
        collect_metrics_from_pod "$pod" "$namespace" "$iso_timestamp" "$pod_metrics_file"
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

# Function to parse and aggregate collected metrics
process_collected_metrics() {
    echo "Processing collected metrics..."
    
    # Call Python script to process metrics
    python3 - <<'PYTHON_SCRIPT'
import os
import re
import json
import glob
from collections import defaultdict
from datetime import datetime
import statistics

metrics_dir = os.environ.get('METRICS_DIR', 'metrics')
raw_dir = os.path.join(metrics_dir, 'raw')
processed_dir = os.path.join(metrics_dir, 'processed')

# Metrics of interest
METRICS_OF_INTEREST = [
    'vllm:kv_cache_usage_perc',
    'vllm:gpu_cache_usage_perc',
    'vllm:cpu_cache_usage_perc',
    'vllm:gpu_memory_usage_bytes',
    'vllm:cpu_memory_usage_bytes',
    'container_memory_usage_bytes',
    'container_cpu_usage_seconds_total',
    'DCGM_FI_DEV_GPU_UTIL',
    'DCGM_FI_DEV_FB_USED',
    'DCGM_FI_DEV_POWER_USAGE',
]

def parse_prometheus_metrics(file_path):
    """Parse Prometheus metrics from a file."""
    metrics = {}
    timestamp = None
    pod_name = None
    
    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()
            
            # Extract timestamp
            if line.startswith('# Timestamp:'):
                timestamp = line.split(':', 1)[1].strip()
            
            # Extract pod name
            if line.startswith('# Pod:'):
                pod_name = line.split(':', 1)[1].strip()
            
            # Skip comments and empty lines
            if line.startswith('#') or not line:
                continue
            
            # Parse metric line
            match = re.match(r'([a-zA-Z_:][a-zA-Z0-9_:]*(?:\{[^}]*\})?) ([\d.eE+-]+)', line)
            if match:
                metric_name = match.group(1)
                value = float(match.group(2))
                
                # Extract base metric name (without labels)
                base_name = metric_name.split('{')[0]
                
                if base_name in METRICS_OF_INTEREST:
                    if base_name not in metrics:
                        metrics[base_name] = []
                    metrics[base_name].append(value)
    
    return timestamp, pod_name, metrics

def aggregate_metrics():
    """Aggregate metrics across all collected snapshots."""
    # Structure: {pod_name: {metric_name: [values]}}
    pod_metrics = defaultdict(lambda: defaultdict(list))
    
    # Process all raw metric files
    for file_path in glob.glob(os.path.join(raw_dir, '*.txt')):
        timestamp, pod_name, metrics = parse_prometheus_metrics(file_path)
        
        if pod_name:
            for metric_name, values in metrics.items():
                pod_metrics[pod_name][metric_name].extend(values)
    
    # Calculate statistics for each metric
    results = {}
    for pod_name, metrics in pod_metrics.items():
        results[pod_name] = {}
        for metric_name, values in metrics.items():
            if values:
                results[pod_name][metric_name] = {
                    'mean': statistics.mean(values),
                    'stddev': statistics.stdev(values) if len(values) > 1 else 0,
                    'min': min(values),
                    'max': max(values),
                    'count': len(values),
                    'raw_data_file': f'raw/{pod_name}_*.txt'
                }
    
    # Save aggregated results
    output_file = os.path.join(processed_dir, 'metrics_summary.json')
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"Metrics summary saved to: {output_file}")
    return results

if __name__ == '__main__':
    aggregate_metrics()
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
