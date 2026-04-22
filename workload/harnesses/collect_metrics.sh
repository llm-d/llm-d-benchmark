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
EPP_METRICS_PORT="${LLMDBENCH_EPP_METRICS_PORT:-9090}"  # EPP Prometheus metrics port

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

# Function to get EPP (inference scheduler) pod names and IPs.
# Discovers pods via the inferencepool label used by llm-d.
# Output: lines of "pod_name pod_ip" pairs.
get_epp_pod_info() {
    local namespace="$1"
    local kubectl_cmd="${KUBECTL_CMD:-kubectl}"

    # EPP pods are labeled with inferencepool=<name>
    local pod_info
    pod_info=$($kubectl_cmd --namespace "$namespace" get pods \
        -l inferencepool \
        --field-selector=status.phase=Running \
        -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.podIP}{"\n"}{end}' 2>/dev/null || true)

    # Fallback: match pods with "epp" in the name
    if [[ -z "$pod_info" ]]; then
        local pod_names
        pod_names=$($kubectl_cmd get pods -n "$namespace" 2>/dev/null | grep -i "epp" | grep "Running" | awk '{print $1}')
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

# Function to collect replica status (desired vs ready vs available) for all
# vLLM-related Deployments and StatefulSets in the namespace.
# Queries all controllers, then filters by pod-template label
# llm-d.ai/inferenceServing=true so both modelservice and standalone are captured.
# Writes the latest snapshot to $METRICS_DIR/processed/replica_status.json
# and appends to $METRICS_DIR/processed/replica_status_timeseries.json.
collect_replica_status() {
    local namespace="${LLMDBENCH_VLLM_COMMON_NAMESPACE:-default}"
    local kubectl_cmd="${KUBECTL_CMD:-kubectl}"
    local output_file="$METRICS_DIR/processed/replica_status.json"
    local timeseries_file="$METRICS_DIR/processed/replica_status_timeseries.json"
    local debug_log="$METRICS_DIR/raw/collection_debug.log"

    mkdir -p "$METRICS_DIR/processed" "$METRICS_DIR/raw"

    local all_json
    all_json=$($kubectl_cmd --namespace "$namespace" get deployments,statefulsets -o json 2>>"$debug_log") || all_json='{"items":[]}'

    echo "$all_json" | _RS_NAMESPACE="$namespace" _RS_OUTPUT="$output_file" _RS_TS_OUTPUT="$timeseries_file" python3 -c '
import json, sys, os
from datetime import datetime, timezone

namespace = os.environ["_RS_NAMESPACE"]
output_file = os.environ["_RS_OUTPUT"]
ts_file = os.environ["_RS_TS_OUTPUT"]

data = json.load(sys.stdin)
snapshot = {
    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "namespace": namespace,
    "controllers": [],
}

for item in data.get("items", []):
    kind = item.get("kind", "Deployment")
    metadata = item.get("metadata", {})
    spec = item.get("spec", {})
    status = item.get("status", {})

    tmpl_labels = (
        spec.get("template", {}).get("metadata", {}).get("labels", {})
    )
    if tmpl_labels.get("llm-d.ai/inferenceServing") != "true":
        continue

    snapshot["controllers"].append({
        "name": metadata.get("name", ""),
        "kind": kind,
        "model": tmpl_labels.get("llm-d.ai/model", tmpl_labels.get("app", "")),
        "role": tmpl_labels.get("llm-d.ai/role", "unknown"),
        "desired_replicas": spec.get("replicas", 0),
        "ready_replicas": status.get("readyReplicas", 0),
        "available_replicas": status.get("availableReplicas", 0),
        "updated_replicas": status.get("updatedReplicas", 0),
    })

# Write latest snapshot (backward compatible)
with open(output_file, "w") as f:
    json.dump(snapshot, f, indent=2)

# Append to time series
ts_data = {"snapshots": []}
if os.path.exists(ts_file):
    try:
        with open(ts_file) as f:
            ts_data = json.load(f)
    except (json.JSONDecodeError, OSError):
        ts_data = {"snapshots": []}
ts_data["snapshots"].append(snapshot)
with open(ts_file, "w") as f:
    json.dump(ts_data, f, indent=2)

print("Replica status: %d controller(s), %d snapshots total" % (len(snapshot["controllers"]), len(ts_data["snapshots"])))
' 2>>"$debug_log"
    if [[ $? -ne 0 ]]; then
        echo "Warning: Failed to collect replica status (see $debug_log)" >&2
    fi
}

# Function to collect pod startup times (creation to Ready) for all vLLM pods.
# Uses the same label-based discovery as get_pod_info(), then extracts
# creationTimestamp, conditions[Ready].lastTransitionTime, and spec.nodeName.
# Called every collection interval to detect newly-scaled pods.
# Incrementally appends new pods (not seen before) to
# $METRICS_DIR/processed/pod_startup_times.json.
collect_pod_startup_times() {
    local namespace="${LLMDBENCH_VLLM_COMMON_NAMESPACE:-default}"
    local kubectl_cmd="${KUBECTL_CMD:-kubectl}"
    local output_file="$METRICS_DIR/processed/pod_startup_times.json"
    local debug_log="$METRICS_DIR/raw/collection_debug.log"

    mkdir -p "$METRICS_DIR/processed" "$METRICS_DIR/raw"

    # Try modelservice labels first, fall back to standalone
    local pods_json
    pods_json=$($kubectl_cmd --namespace "$namespace" get pods \
        -l llm-d.ai/inferenceServing=true \
        --field-selector=status.phase=Running \
        -o json 2>>"$debug_log") || pods_json='{"items":[]}'

    local count
    count=$(echo "$pods_json" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('items',[])))" 2>>"$debug_log" || echo 0)
    if [[ "$count" == "0" ]]; then
        pods_json=$($kubectl_cmd --namespace "$namespace" get pods \
            -l stood-up-via=standalone \
            --field-selector=status.phase=Running \
            -o json 2>>"$debug_log") || pods_json='{"items":[]}'
    fi

    echo "$pods_json" | _ST_OUTPUT="$output_file" python3 -c '
import json, sys, os
from datetime import datetime, timezone

output_file = os.environ["_ST_OUTPUT"]

data = json.load(sys.stdin)

# Load existing data to detect only new pods
existing = {"collected_at": "", "pods": []}
if os.path.exists(output_file):
    try:
        with open(output_file) as f:
            existing = json.load(f)
    except (json.JSONDecodeError, OSError):
        pass

seen_names = {p["name"] for p in existing.get("pods", [])}
new_count = 0

for item in data.get("items", []):
    metadata = item.get("metadata", {})
    pod_name = metadata.get("name", "")
    if pod_name in seen_names:
        continue

    spec = item.get("spec", {})
    status = item.get("status", {})
    labels = metadata.get("labels", {})

    creation_ts = metadata.get("creationTimestamp", "")
    ready_ts = ""
    for cond in status.get("conditions", []):
        if cond.get("type") == "Ready" and cond.get("status") == "True":
            ready_ts = cond.get("lastTransitionTime", "")
            break

    startup_seconds = None
    if creation_ts and ready_ts:
        try:
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            created = datetime.strptime(creation_ts, fmt)
            ready = datetime.strptime(ready_ts, fmt)
            startup_seconds = round((ready - created).total_seconds(), 1)
        except (ValueError, TypeError):
            pass

    existing["pods"].append({
        "name": pod_name,
        "node": spec.get("nodeName", ""),
        "model": labels.get("llm-d.ai/model", labels.get("app", "")),
        "role": labels.get("llm-d.ai/role", "unknown"),
        "creation_timestamp": creation_ts,
        "ready_timestamp": ready_ts,
        "startup_seconds": startup_seconds,
    })
    new_count += 1

existing["collected_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

with open(output_file, "w") as f:
    json.dump(existing, f, indent=2)

if new_count > 0:
    print("Pod startup times: %d new pod(s) detected (%d total)" % (new_count, len(existing["pods"])))
' 2>>"$debug_log"
    if [[ $? -ne 0 ]]; then
        echo "Warning: Failed to collect pod startup times (see $debug_log)" >&2
    fi
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

# Function to collect Prometheus metrics from an EPP pod.
# EPP exposes metrics on a different port (default 9090) than vLLM.
collect_prometheus_metrics_from_epp_pod() {
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
        echo "# Source: epp_prometheus_metrics"
        echo ""
    } > "$output_file"

    local url="http://${pod_ip}:${EPP_METRICS_PORT}/metrics"
    local rc=0
    curl -sS --connect-timeout 5 --max-time "$METRICS_CURL_TIMEOUT" \
        "$url" > "$tmp_file" 2>>"$debug_log" || rc=$?

    if [[ $rc -ne 0 ]]; then
        echo "  [$(date -u +%H:%M:%S)] curl failed (rc=$rc) for EPP ${pod_name} ${url}" >> "$debug_log"
    fi

    if [[ -s "$tmp_file" ]]; then
        cat "$tmp_file" >> "$output_file"
    else
        {
            echo "# Warning: Failed to collect EPP Prometheus metrics from pod $pod_name ($pod_ip)"
            echo "# Attempted: ${url}"
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

    # Collect vLLM pod metrics
    local pod_info
    pod_info=$(get_pod_info "$namespace")

    if [[ -z "$pod_info" ]]; then
        echo "Warning: No running vLLM pods found in namespace $namespace" >&2
    else
        echo "Found vLLM pods:"
        echo "$pod_info"

        echo "$pod_info" | while read -r pod_name pod_ip; do
            [[ -z "$pod_ip" || -z "$pod_name" ]] && continue
            local pod_metrics_file="$METRICS_DIR/raw/${pod_name}_${timestamp}_metrics.log"
            collect_prometheus_metrics_from_pod "$pod_name" "$pod_ip" "$iso_timestamp" "$pod_metrics_file" &
        done
    fi

    # Collect EPP pod metrics
    local epp_info
    epp_info=$(get_epp_pod_info "$namespace")

    if [[ -n "$epp_info" ]]; then
        echo "Found EPP pods:"
        echo "$epp_info"

        echo "$epp_info" | while read -r pod_name pod_ip; do
            [[ -z "$pod_ip" || -z "$pod_name" ]] && continue
            local epp_metrics_file="$METRICS_DIR/raw/${pod_name}_${timestamp}_metrics.log"
            collect_prometheus_metrics_from_epp_pod "$pod_name" "$pod_ip" "$iso_timestamp" "$epp_metrics_file" &
        done
    fi

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
        # Collect infrastructure state every iteration to track autoscaling
        collect_replica_status
        collect_pod_startup_times

        # Collect Prometheus metrics from all pods
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
        collect_replica_status
        collect_pod_startup_times
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
