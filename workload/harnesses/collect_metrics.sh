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

# Function to collect Prometheus metrics from a single pod
collect_prometheus_metrics_from_pod() {
    local pod="$1"
    local namespace="$2"
    local timestamp="$3"
    local output_file="$4"
    
    # Use kubectl/oc exec to curl the metrics endpoint
    local kubectl_cmd="${KUBECTL_CMD:-kubectl}"
    
    {
        echo "# Timestamp: $timestamp"
        echo "# Pod: $pod"
        echo "# Namespace: $namespace"
        echo "# Source: prometheus_metrics"
        echo ""
        
        # Try multiple methods to collect metrics
        local metrics_collected=false
        
        # Method 1: Try curl with the configured port
        if $kubectl_cmd exec -n "$namespace" "$pod" -- sh -c "command -v curl >/dev/null 2>&1" 2>/dev/null; then
            if metrics=$($kubectl_cmd exec -n "$namespace" "$pod" -- curl -s -m 5 "http://localhost:${METRICS_PORT}/metrics" 2>/dev/null); then
                if [ -n "$metrics" ] && echo "$metrics" | grep -q "^[a-zA-Z_]"; then
                    echo "$metrics"
                    metrics_collected=true
                fi
            fi
        fi
        
        # Method 2: Try wget if curl failed
        if [ "$metrics_collected" = false ]; then
            if $kubectl_cmd exec -n "$namespace" "$pod" -- sh -c "command -v wget >/dev/null 2>&1" 2>/dev/null; then
                if metrics=$($kubectl_cmd exec -n "$namespace" "$pod" -- wget -q -O - -T 5 "http://localhost:${METRICS_PORT}/metrics" 2>/dev/null); then
                    if [ -n "$metrics" ] && echo "$metrics" | grep -q "^[a-zA-Z_]"; then
                        echo "$metrics"
                        metrics_collected=true
                    fi
                fi
            fi
        fi
        
        # Method 3: Try alternative common ports if default failed
        if [ "$metrics_collected" = false ]; then
            for port in 8080 9090 3000; do
                if $kubectl_cmd exec -n "$namespace" "$pod" -- sh -c "command -v curl >/dev/null 2>&1" 2>/dev/null; then
                    if metrics=$($kubectl_cmd exec -n "$namespace" "$pod" -- curl -s -m 5 "http://localhost:${port}/metrics" 2>/dev/null); then
                        if [ -n "$metrics" ] && echo "$metrics" | grep -q "^[a-zA-Z_]"; then
                            echo "# Note: Metrics found on port $port instead of ${METRICS_PORT}"
                            echo "$metrics"
                            metrics_collected=true
                            break
                        fi
                    fi
                fi
            done
        fi
        
        # If all methods failed, log detailed error
        if [ "$metrics_collected" = false ]; then
            echo "# Warning: Failed to collect Prometheus metrics from pod $pod"
            echo "# Attempted ports: ${METRICS_PORT}, 8080, 9090, 3000"
            echo "# Troubleshooting:"
            echo "#   1. Verify metrics endpoint is enabled in vLLM"
            echo "#   2. Check if curl/wget is available in pod"
            echo "#   3. Verify correct metrics port (default: 8000)"
            echo "#   4. Check pod logs for vLLM startup messages"
        fi
        
        echo ""
    } >> "$output_file"
    
    return 0
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
        echo "# Source: pod_logs"
        echo ""
        $kubectl_cmd logs -n "$namespace" "$pod" --tail=100 2>/dev/null || echo "# Warning: Failed to collect logs from pod $pod"
        echo ""
    } >> "$output_file"
    
    return 0
}

# Function to collect cluster-wide Prometheus metrics for specific pods
collect_cluster_prometheus_metrics() {
    local pod="$1"
    local namespace="$2"
    local timestamp="$3"
    local output_file="$4"
    
    # Check if we have oc command and can query Prometheus
    if ! command -v oc &> /dev/null; then
        return 0
    fi
    
    # Get authentication token
    local token=$(oc whoami -t 2>/dev/null || echo "")
    if [[ -z "$token" ]]; then
        return 0
    fi
    
    local prometheus_url="https://thanos-querier-openshift-monitoring.apps.pokprod001.ete14.res.ibm.com"
    
    # Define metrics to collect from cluster Prometheus
    local metrics=(
        "container_memory_usage_bytes{pod=\"$pod\",namespace=\"$namespace\",container=\"vllm\"}"
        "container_memory_working_set_bytes{pod=\"$pod\",namespace=\"$namespace\",container=\"vllm\"}"
        "container_cpu_usage_seconds_total{pod=\"$pod\",namespace=\"$namespace\",container=\"vllm\"}"
        "container_network_receive_bytes_total{pod=\"$pod\",namespace=\"$namespace\"}"
        "container_network_transmit_bytes_total{pod=\"$pod\",namespace=\"$namespace\"}"
    )
    
    {
        echo "# Timestamp: $timestamp"
        echo "# Pod: $pod"
        echo "# Namespace: $namespace"
        echo "# Source: cluster_prometheus"
        echo ""
        
        for metric_query in "${metrics[@]}"; do
            # Query Prometheus
            local result=$(curl -k -s -H "Authorization: Bearer $token" \
                "${prometheus_url}/api/v1/query?query=${metric_query}" 2>/dev/null)
            
            # Parse and output in Prometheus format
            if [[ -n "$result" ]]; then
                echo "$result" | jq -r '.data.result[]? | "\(.metric.__name__){pod=\"\(.metric.pod)\",namespace=\"\(.metric.namespace)\",container=\"\(.metric.container // "")\"} \(.value[1])"' 2>/dev/null || true
            fi
        done
        
        echo ""
    } >> "$output_file"
    
    return 0
}

# Function to collect metrics snapshot (both Prometheus and logs)
collect_metrics_snapshot() {
    local namespace="${LLMDBENCH_VLLM_COMMON_NAMESPACE:-default}"
    local pod_pattern="${LLMDBENCH_METRICS_POD_PATTERN:-decode}"
    local timestamp=$(date +%s)
    local iso_timestamp=$(date -u +"%Y-%m-%dT%H:%M:%S%z")
    
    echo "Collecting metrics at $iso_timestamp"
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
        local pod_metrics_file="$METRICS_DIR/raw/${pod}_${timestamp}_metrics.txt"
        local pod_log_file="$METRICS_DIR/raw/${pod}_${timestamp}_logs.txt"
        
        # Collect Prometheus metrics from pod
        collect_prometheus_metrics_from_pod "$pod" "$namespace" "$iso_timestamp" "$pod_metrics_file"
        
        # Collect cluster-wide Prometheus metrics
        collect_cluster_prometheus_metrics "$pod" "$namespace" "$iso_timestamp" "$pod_metrics_file"
        
        # Collect logs for additional context
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
    
    # Get the directory where this script is located
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    
    # Call the Python script to process metrics files
    python3 "${SCRIPT_DIR}/process_metrics.py"
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
