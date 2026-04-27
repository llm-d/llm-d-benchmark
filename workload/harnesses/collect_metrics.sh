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
EPP_METRICS_SECRET="${LLMDBENCH_EPP_METRICS_SECRET:-inference-gateway-sa-metrics-reader-secret}"  # pragma: allowlist secret
_EPP_AUTH_HEADER=""  # cached bearer token header for EPP scrapes

# Function to initialize metrics directory
init_metrics_dir() {
    mkdir -p "$METRICS_DIR/raw" "$METRICS_DIR/processed"
    echo "Metrics directory initialized: $METRICS_DIR"
}

# ---------------------------------------------------------------------------
# Pod discovery
# ---------------------------------------------------------------------------

# Get vLLM pod names and IPs.
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

# Get EPP (inference scheduler) pod names and IPs.
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

# ---------------------------------------------------------------------------
# Replica status & pod startup times
# ---------------------------------------------------------------------------

# Collect replica status snapshot and append to time series.
# Filters controllers to only include those matching the current benchmark's
# model (via LLMDBENCH_HARNESS_STACK_NAME), so stale controllers from
# previous runs in the same namespace are excluded.
collect_replica_status() {
    local namespace="${LLMDBENCH_VLLM_COMMON_NAMESPACE:-default}"
    local kubectl_cmd="${KUBECTL_CMD:-kubectl}"
    local model_filter="${LLMDBENCH_HARNESS_STACK_NAME:-}"
    local output_file="$METRICS_DIR/processed/replica_status.json"
    local timeseries_file="$METRICS_DIR/processed/replica_status_timeseries.json"
    local debug_log="$METRICS_DIR/raw/collection_debug.log"

    mkdir -p "$METRICS_DIR/processed" "$METRICS_DIR/raw"

    local all_json
    all_json=$($kubectl_cmd --namespace "$namespace" get deployments,statefulsets -o json 2>>"$debug_log") || all_json='{"items":[]}'

    echo "$all_json" | _RS_NAMESPACE="$namespace" _RS_OUTPUT="$output_file" _RS_TS_OUTPUT="$timeseries_file" _RS_MODEL_FILTER="$model_filter" python3 -c '
import json, sys, os
from datetime import datetime, timezone

namespace = os.environ["_RS_NAMESPACE"]
output_file = os.environ["_RS_OUTPUT"]
ts_file = os.environ["_RS_TS_OUTPUT"]
model_filter = os.environ.get("_RS_MODEL_FILTER", "")

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

    # Filter by model if LLMDBENCH_HARNESS_STACK_NAME is set
    model = tmpl_labels.get("llm-d.ai/model", tmpl_labels.get("app", ""))
    if model_filter and model != model_filter:
        continue

    snapshot["controllers"].append({
        "name": metadata.get("name", ""),
        "kind": kind,
        "model": model,
        "role": tmpl_labels.get("llm-d.ai/role", "unknown"),
        "desired_replicas": spec.get("replicas", 0),
        "ready_replicas": status.get("readyReplicas", 0),
        "available_replicas": status.get("availableReplicas", 0),
        "updated_replicas": status.get("updatedReplicas", 0),
    })

# Write latest snapshot (backward compatible)
with open(output_file, "w") as f:
    json.dump(snapshot, f, indent=2)

# Append to time series (every snapshot, so the graph always renders)
ts_data = {"snapshots": []}
if os.path.exists(ts_file):
    try:
        with open(ts_file) as f:
            ts_data = json.load(f)
    except (json.JSONDecodeError, OSError):
        ts_data = {"snapshots": []}

ts_data["snapshots"].append(snapshot)
changed = True
if len(ts_data["snapshots"]) >= 2:
    prev = ts_data["snapshots"][-2]
    prev_counts = {c["name"]: c.get("ready_replicas", 0) for c in prev.get("controllers", [])}
    curr_counts = {c["name"]: c.get("ready_replicas", 0) for c in snapshot.get("controllers", [])}
    changed = prev_counts != curr_counts

with open(ts_file, "w") as f:
    json.dump(ts_data, f, indent=2)

print("Replica status: %d controller(s), %d snapshots%s" % (
    len(snapshot["controllers"]), len(ts_data["snapshots"]),
    "" if changed else " (unchanged)"))
' 2>>"$debug_log"
    if [[ $? -ne 0 ]]; then
        echo "Warning: Failed to collect replica status (see $debug_log)" >&2
    fi
}

# Collect pod startup times incrementally (detects newly-scaled pods).
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

# Load existing data
existing = {"collected_at": "", "pods": []}
if os.path.exists(output_file):
    try:
        with open(output_file) as f:
            existing = json.load(f)
    except (json.JSONDecodeError, OSError):
        pass

# Map pod name -> index for updating previously-seen pods
seen_idx = {p["name"]: i for i, p in enumerate(existing.get("pods", []))}
new_count = 0
updated_count = 0

def _parse_ready(status):
    """Return (ready_ts, startup_seconds) for a pod status dict."""
    creation_ts = status.get("_creation_ts", "")
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
    return ready_ts, startup_seconds

for item in data.get("items", []):
    metadata = item.get("metadata", {})
    pod_name = metadata.get("name", "")
    spec = item.get("spec", {})
    status = item.get("status", {})
    labels = metadata.get("labels", {})
    creation_ts = metadata.get("creationTimestamp", "")

    # Inject creation_ts so _parse_ready can compute startup_seconds
    status["_creation_ts"] = creation_ts
    ready_ts, startup_seconds = _parse_ready(status)

    if pod_name in seen_idx:
        # Update existing entry if it was missing ready_timestamp
        idx = seen_idx[pod_name]
        if not existing["pods"][idx].get("ready_timestamp") and ready_ts:
            existing["pods"][idx]["ready_timestamp"] = ready_ts
            existing["pods"][idx]["startup_seconds"] = startup_seconds
            updated_count += 1
        continue

    existing["pods"].append({
        "name": pod_name,
        "node": spec.get("nodeName", ""),
        "model": labels.get("llm-d.ai/model", labels.get("app", "")),
        "role": labels.get("llm-d.ai/role", "unknown"),
        "creation_timestamp": creation_ts,
        "ready_timestamp": ready_ts,
        "startup_seconds": startup_seconds,
    })
    seen_idx[pod_name] = len(existing["pods"]) - 1
    new_count += 1

existing["collected_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

with open(output_file, "w") as f:
    json.dump(existing, f, indent=2)

changes = []
if new_count > 0:
    changes.append("%d new" % new_count)
if updated_count > 0:
    changes.append("%d updated" % updated_count)
if changes:
    print("Pod startup times: %s (%d total)" % (", ".join(changes), len(existing["pods"])))
' 2>>"$debug_log"
    if [[ $? -ne 0 ]]; then
        echo "Warning: Failed to collect pod startup times (see $debug_log)" >&2
    fi
}

# ---------------------------------------------------------------------------
# Prometheus metrics scraping
# ---------------------------------------------------------------------------

# Retrieve bearer token for EPP metrics endpoint (cached after first call).
# The upstream inferencepool chart requires auth by default; the token is
# stored in a Kubernetes secret created alongside the EPP deployment.
_get_epp_auth_header() {
    if [[ -n "$_EPP_AUTH_HEADER" ]]; then
        echo "$_EPP_AUTH_HEADER"
        return
    fi
    local namespace="${LLMDBENCH_VLLM_COMMON_NAMESPACE:-default}"
    local kubectl_cmd="${KUBECTL_CMD:-kubectl}"
    local token
    token=$($kubectl_cmd get secret "$EPP_METRICS_SECRET" \
        --namespace "$namespace" \
        -o jsonpath='{.data.token}' 2>/dev/null | base64 -d 2>/dev/null) || true
    if [[ -n "$token" ]]; then
        _EPP_AUTH_HEADER="Authorization: Bearer $token"
    fi
    echo "$_EPP_AUTH_HEADER"
}

# Scrape Prometheus /metrics from a single pod.
# Usage: _scrape_pod <pod_name> <pod_ip> <timestamp> <output_file> <port> <source_tag> [<fallback_port>] [<auth_header>]
_scrape_pod() {
    local pod_name="$1"
    local pod_ip="$2"
    local timestamp="$3"
    local output_file="$4"
    local port="$5"
    local source_tag="$6"
    local fallback_port="${7:-}"
    local auth_header="${8:-}"
    local debug_log="$METRICS_DIR/raw/collection_debug.log"
    local tmp_file="${output_file}.tmp"

    # Build curl auth args
    local -a curl_auth=()
    if [[ -n "$auth_header" ]]; then
        curl_auth=(-H "$auth_header")  # pragma: allowlist secret
    fi

    # Write header
    {
        echo "# Timestamp: $timestamp"
        echo "# Pod: $pod_name"
        echo "# PodIP: $pod_ip"
        echo "# Source: $source_tag"
        echo ""
    } > "$output_file"

    # Try primary port
    local url="http://${pod_ip}:${port}${METRICS_PATH}"
    local rc=0
    curl -sS --connect-timeout 5 --max-time "$METRICS_CURL_TIMEOUT" \
        "${curl_auth[@]+"${curl_auth[@]}"}" \
        "$url" > "$tmp_file" 2>>"$debug_log" || rc=$?

    if [[ $rc -ne 0 ]]; then
        echo "  [$(date -u +%H:%M:%S)] curl failed (rc=$rc) for ${pod_name} ${url}" >> "$debug_log"
    fi

    # Check for auth failure (HTTP 401/403 body) and retry without auth
    if [[ -s "$tmp_file" ]] && head -1 "$tmp_file" | grep -qiE '^(Unauthorized|Forbidden)$'; then
        echo "  [$(date -u +%H:%M:%S)] Auth rejected for ${pod_name}, retrying without auth" >> "$debug_log"
        rc=0
        curl -sS --connect-timeout 5 --max-time "$METRICS_CURL_TIMEOUT" \
            "$url" > "$tmp_file" 2>>"$debug_log" || rc=$?
    fi

    # Try fallback port if primary failed/empty
    if [[ ! -s "$tmp_file" && -n "$fallback_port" && "$port" != "$fallback_port" ]]; then
        url="http://${pod_ip}:${fallback_port}${METRICS_PATH}"
        echo "  [$(date -u +%H:%M:%S)] Retrying ${pod_name} on fallback port: ${url}" >> "$debug_log"
        rc=0
        curl -sS --connect-timeout 5 --max-time "$METRICS_CURL_TIMEOUT" \
            "${curl_auth[@]+"${curl_auth[@]}"}" \
            "$url" > "$tmp_file" 2>>"$debug_log" || rc=$?
        if [[ $rc -ne 0 ]]; then
            echo "  [$(date -u +%H:%M:%S)] curl failed (rc=$rc) for ${pod_name} ${url}" >> "$debug_log"
        fi
    fi

    # Append content or warning
    if [[ -s "$tmp_file" ]]; then
        cat "$tmp_file" >> "$output_file"
    else
        echo "# Warning: Failed to collect metrics from pod $pod_name ($pod_ip)" >> "$output_file"
    fi
    echo "" >> "$output_file"

    rm -f "$tmp_file"
    return 0
}

# Collect metrics snapshot from all vLLM and EPP pods
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
            _scrape_pod "$pod_name" "$pod_ip" "$iso_timestamp" \
                "$METRICS_DIR/raw/${pod_name}_${timestamp}_metrics.log" \
                "$METRICS_PORT" "prometheus_metrics" "$INFERENCE_PORT" &
        done
    fi

    # Collect EPP pod metrics
    local epp_info
    epp_info=$(get_epp_pod_info "$namespace")

    if [[ -n "$epp_info" ]]; then
        echo "Found EPP pods:"
        echo "$epp_info"

        local epp_auth
        epp_auth=$(_get_epp_auth_header)

        echo "$epp_info" | while read -r pod_name pod_ip; do
            [[ -z "$pod_ip" || -z "$pod_name" ]] && continue
            _scrape_pod "$pod_name" "$pod_ip" "$iso_timestamp" \
                "$METRICS_DIR/raw/${pod_name}_${timestamp}_metrics.log" \
                "$EPP_METRICS_PORT" "epp_prometheus_metrics" "" "$epp_auth" &
        done
    fi

    # Wait for all background collections to finish
    wait
}

# ---------------------------------------------------------------------------
# Collection lifecycle
# ---------------------------------------------------------------------------

# Start continuous collection in background
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

# Stop continuous collection
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

# Parse and aggregate collected logs
process_collected_metrics() {
    echo "Processing collected logs..."
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
