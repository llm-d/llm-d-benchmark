#!/usr/bin/env bash

# Copyright 2025 The llm-d Authors.
# Licensed under the Apache License, Version 2.0 (the "License");

# Metrics collection script for llm-d-benchmark
# Collects Prometheus metrics and vLLM logs during benchmark execution

set -euo pipefail

# Configuration
METRICS_DIR="${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/metrics"
COLLECTION_INTERVAL="${METRICS_COLLECTION_INTERVAL:-15}"  # seconds between collections
# Use LLMDBENCH_VLLM_COMMON_METRICS_PORT (8200) for model services, LLMDBENCH_VLLM_COMMON_INFERENCE_PORT (8000) for standalone
METRICS_PORT="${LLMDBENCH_VLLM_COMMON_METRICS_PORT:-${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT:-8000}}"
INFERENCE_PORT="${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT:-8000}"
METRICS_PATH="${LLMDBENCH_VLLM_MONITORING_METRICS_PATH:-/metrics}"
METRICS_CURL_TIMEOUT="${METRICS_CURL_TIMEOUT:-30}"  # max-time for curl in seconds

# Function to initialize metrics directory
init_metrics_dir() {
    mkdir -p "$METRICS_DIR"
    mkdir -p "$METRICS_DIR/raw"
    mkdir -p "$METRICS_DIR/processed"
    echo "Metrics directory initialized: $METRICS_DIR"
}

# Function to get pod names and IPs for the deployment.
# Uses the same label-based discovery as build/llm-d-benchmark.sh scrape_vllm_metrics.
# Output: lines of "pod_name pod_ip" pairs.
get_pod_info() {
    local namespace="$1"
    local kubectl_cmd="${KUBECTL_CMD:-kubectl}"

    # Try modelservice labels first
    local pod_info
    pod_info=$($kubectl_cmd --namespace "$namespace" get pods \
        -l llm-d.ai/inferenceServing=true \
        --field-selector=status.phase=Running \
        -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.podIP}{"\n"}{end}' 2>/dev/null || true)

    # Fall back to standalone label
    if [[ -z "$pod_info" ]]; then
        pod_info=$($kubectl_cmd --namespace "$namespace" get pods \
            -l stood-up-via=standalone \
            --field-selector=status.phase=Running \
            -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.podIP}{"\n"}{end}' 2>/dev/null || true)
    fi

    # Final fallback: grep-based discovery using pod pattern
    if [[ -z "$pod_info" ]]; then
        local pod_pattern="${LLMDBENCH_METRICS_POD_PATTERN:-decode}"
        local pod_names
        pod_names=$($kubectl_cmd get pods -n "$namespace" 2>/dev/null | grep "$pod_pattern" | grep "Running" | awk '{print $1}')
        if [[ -n "$pod_names" ]]; then
            for pod in $pod_names; do
                local ip
                ip=$($kubectl_cmd get pod -n "$namespace" "$pod" -o jsonpath='{.status.podIP}' 2>/dev/null || true)
                if [[ -n "$ip" ]]; then
                    pod_info="${pod_info:+${pod_info}$'\n'}${pod} ${ip}"
                fi
            done
        fi
    fi

    echo "$pod_info"
}

# Function to collect Prometheus metrics from a single pod via its IP.
# Curls the pod IP directly from the benchmark pod instead of using kubectl exec,
# so we don't compete for CPU inside the loaded vLLM container.
# Writes curl output directly to file (matching the approach in build/llm-d-benchmark.sh).
collect_prometheus_metrics_from_pod() {
    local pod_name="$1"
    local pod_ip="$2"
    local timestamp="$3"
    local output_file="$4"
    local debug_log="$METRICS_DIR/raw/collection_debug.log"
    local tmp_file="${output_file}.tmp"

    # Write header
    {
        echo "# Timestamp: $timestamp"
        echo "# Pod: $pod_name"
        echo "# PodIP: $pod_ip"
        echo "# Source: prometheus_metrics"
        echo ""
    } > "$output_file"

    # Try the configured metrics port — write directly to temp file
    local url="http://${pod_ip}:${METRICS_PORT}${METRICS_PATH}"
    local rc=0
    curl -sS --connect-timeout 5 --max-time "$METRICS_CURL_TIMEOUT" \
        "$url" > "$tmp_file" 2>>"$debug_log" || rc=$?

    if [[ $rc -ne 0 ]]; then
        echo "  [$(date -u +%H:%M:%S)] curl failed (rc=$rc) for ${pod_name} ${url}" >> "$debug_log"
    fi

    # If metrics port returned empty/failed, try inference port as fallback
    if [[ ! -s "$tmp_file" && "$METRICS_PORT" != "$INFERENCE_PORT" ]]; then
        url="http://${pod_ip}:${INFERENCE_PORT}${METRICS_PATH}"
        echo "  [$(date -u +%H:%M:%S)] Retrying ${pod_name} on inference port: ${url}" >> "$debug_log"
        rc=0
        curl -sS --connect-timeout 5 --max-time "$METRICS_CURL_TIMEOUT" \
            "$url" > "$tmp_file" 2>>"$debug_log" || rc=$?
        if [[ $rc -ne 0 ]]; then
            echo "  [$(date -u +%H:%M:%S)] curl failed (rc=$rc) for ${pod_name} ${url}" >> "$debug_log"
        fi
    fi

    # Append whatever we got (or a warning if empty)
    if [[ -s "$tmp_file" ]]; then
        cat "$tmp_file" >> "$output_file"
    else
        {
            echo "# Warning: Failed to collect Prometheus metrics from pod $pod_name ($pod_ip)"
            echo "# Attempted: ${pod_ip}:${METRICS_PORT}${METRICS_PATH}"
            if [[ "$METRICS_PORT" != "$INFERENCE_PORT" ]]; then
                echo "# Fallback: ${pod_ip}:${INFERENCE_PORT}${METRICS_PATH}"
            fi
            echo "# Debug log: $debug_log"
        } >> "$output_file"
    fi
    echo "" >> "$output_file"

    rm -f "$tmp_file"
    return 0
}

# Function to collect metrics snapshot from all pods
collect_metrics_snapshot() {
    local namespace="${LLMDBENCH_VLLM_COMMON_NAMESPACE:-default}"
    local timestamp=$(date +%s)
    local iso_timestamp=$(date -u +"%Y-%m-%dT%H:%M:%S%z")

    echo "Collecting metrics at $iso_timestamp"
    echo "Namespace: $namespace"

    local pod_info
    pod_info=$(get_pod_info "$namespace")

    if [[ -z "$pod_info" ]]; then
        echo "Warning: No running pods found in namespace $namespace" >&2
        return 1
    fi

    echo "Found pods:"
    echo "$pod_info"

    # Collect from each pod in parallel, curling pod IPs directly
    local pids=""
    echo "$pod_info" | while read -r pod_name pod_ip; do
        [[ -z "$pod_ip" || -z "$pod_name" ]] && continue
        local pod_metrics_file="$METRICS_DIR/raw/${pod_name}_${timestamp}_metrics.txt"

        collect_prometheus_metrics_from_pod "$pod_name" "$pod_ip" "$iso_timestamp" "$pod_metrics_file" &
        pids="$pids $!"
    done

    # Wait for all background collections to finish
    wait
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

    # Pass METRICS_DIR explicitly: it is a shell variable (not exported), so the
    # subprocess would otherwise fall back to the relative default 'metrics'.
    METRICS_DIR="$METRICS_DIR" python3 "${SCRIPT_DIR}/process_metrics.py"
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
