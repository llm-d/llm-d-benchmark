#!/usr/bin/env bash
# EPP Scale Limits Benchmark
#
# Usage:
#   ./run-benchmark.sh --config benchmark-templates/stress-ramp.yaml
#   ./run-benchmark.sh --config benchmark-templates/stress-ramp.yaml --replicas 4
#   ./run-benchmark.sh --config benchmark-templates/stress-ramp.yaml --replicas 2 --cpu 4
#   ./run-benchmark.sh --config benchmark-templates/stress-ramp.yaml --routing optimized-baseline
#   ./run-benchmark.sh --config benchmark-templates/gpu-ramp.yaml --gpu --model Qwen/Qwen2.5-7B-Instruct
#   ./run-benchmark.sh --cleanup
#
# Prerequisites:
#   - kubectl configured with target cluster context
#   - helm v3+
#   - Inference CRDs installed (InferencePool, InferenceModel)
#   - For GPU mode: nvidia.com/gpu resources available, llm-d-hf-token secret created

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMESPACE="${NAMESPACE:-epp-benchmark}"
EPP_RELEASE="${EPP_RELEASE:-epp-benchmark}"
EPP_TAG="${EPP_TAG:-v0.8.0}"
SIM_IMAGE="${SIM_IMAGE:-ghcr.io/llm-d/llm-d-inference-sim:v0.8.0}"
VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:v0.19.1}"
BENCH_IMAGE="${BENCH_IMAGE:-ghcr.io/llm-d/llm-d-benchmark:v0.6.0}"
CHART_VERSION="${CHART_VERSION:-v1.5.0}"
CHART_URL="oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone"
MODEL_NAME="${MODEL_NAME:-meta-llama/Llama-3.1-8B-Instruct}"
TOKENIZER="${TOKENIZER:-HuggingFaceTB/SmolLM2-135M-Instruct}"
WARMUP_SECONDS="${WARMUP_SECONDS:-120}"
MODEL_REPLICAS="${MODEL_REPLICAS:-3}"
RESULTS_DIR="${RESULTS_DIR:-${SCRIPT_DIR}/results}"

CONFIG_FILE=""
EPP_REPLICAS=1
EPP_CPU_REQUEST=""
EPP_MEMORY_REQUEST="8Gi"
EPP_MEMORY_LIMIT="16Gi"
ROUTING="default"
USE_GPU=false
CLEANUP_ONLY=false

usage() {
    cat <<USAGE
Usage: $0 [OPTIONS]

Options:
  -c, --config FILE       inference-perf config YAML (required unless --cleanup)
  -r, --replicas N        EPP replica count (default: 1)
  --cpu REQUEST           EPP CPU request (e.g., '4', default: no request)
  --routing STRATEGY      Routing strategy: default|optimized-baseline|active-request (default: default)
  --gpu                   Use real vLLM on GPUs instead of simulators
  --model NAME            Model name for GPU mode (default: meta-llama/Llama-3.1-8B-Instruct)
  --model-replicas N      Model server replica count (default: 3)
  --warmup SECONDS        Warm-up wait before benchmarking (default: 120)
  --results-dir DIR       Output directory (default: ./results)
  --cleanup               Tear down all benchmark resources and exit
  -h, --help              Show this help
USAGE
    exit 0
}

log() { echo "[$(date '+%H:%M:%S')] $*"; }

while [[ $# -gt 0 ]]; do
    case $1 in
        -c|--config) CONFIG_FILE="$2"; shift 2 ;;
        -r|--replicas) EPP_REPLICAS="$2"; shift 2 ;;
        --cpu) EPP_CPU_REQUEST="$2"; shift 2 ;;
        --routing) ROUTING="$2"; shift 2 ;;
        --gpu) USE_GPU=true; shift ;;
        --model) MODEL_NAME="$2"; shift 2 ;;
        --model-replicas) MODEL_REPLICAS="$2"; shift 2 ;;
        --warmup) WARMUP_SECONDS="$2"; shift 2 ;;
        --results-dir) RESULTS_DIR="$2"; shift 2 ;;
        --cleanup) CLEANUP_ONLY=true; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

cleanup() {
    log "Cleaning up benchmark resources in ${NAMESPACE}..."
    kubectl delete pod inference-perf -n "${NAMESPACE}" --ignore-not-found 2>/dev/null
    kubectl delete deployment benchmark-model -n "${NAMESPACE}" --ignore-not-found 2>/dev/null
    helm uninstall "${EPP_RELEASE}" -n "${NAMESPACE}" 2>/dev/null || true
    log "Cleanup complete."
}

if [[ "${CLEANUP_ONLY}" == "true" ]]; then
    cleanup
    exit 0
fi

if [[ -z "${CONFIG_FILE}" ]]; then
    echo "Error: --config is required"
    usage
fi

if [[ ! -f "${CONFIG_FILE}" ]]; then
    echo "Error: config file not found: ${CONFIG_FILE}"
    exit 1
fi

mkdir -p "${RESULTS_DIR}"

# --- Build routing plugins config ---
get_plugins_config() {
    case "${ROUTING}" in
        default)
            echo "" # use chart default
            ;;
        optimized-baseline)
            cat <<'PLUGINS'
apiVersion: inference.networking.x-k8s.io/v1alpha1
kind: EndpointPickerConfig
plugins:
- type: queue-scorer
- type: kv-cache-utilization-scorer
- type: prefix-cache-scorer
- type: no-hit-lru-scorer
schedulingProfiles:
- name: default
  plugins:
  - pluginRef: queue-scorer
    weight: 2
  - pluginRef: kv-cache-utilization-scorer
    weight: 2
  - pluginRef: prefix-cache-scorer
    weight: 3
  - pluginRef: no-hit-lru-scorer
    weight: 2
PLUGINS
            ;;
        active-request)
            cat <<'PLUGINS'
apiVersion: inference.networking.x-k8s.io/v1alpha1
kind: EndpointPickerConfig
plugins:
- type: active-request-scorer
- type: kv-cache-utilization-scorer
- type: no-hit-lru-scorer
- type: max-score-picker
schedulingProfiles:
- name: default
  plugins:
  - pluginRef: active-request-scorer
    weight: 3
  - pluginRef: kv-cache-utilization-scorer
    weight: 2
  - pluginRef: no-hit-lru-scorer
    weight: 2
  - pluginRef: max-score-picker
PLUGINS
            ;;
        *)
            echo "Error: unknown routing strategy: ${ROUTING}" >&2
            exit 1
            ;;
    esac
}

# --- Step 1: Deploy EPP (always start with 1 replica, scale up after) ---
log "Deploying EPP (routing: ${ROUTING}, target replicas: ${EPP_REPLICAS}, CPU: ${EPP_CPU_REQUEST:-default})..."

HELM_ARGS=(
    --namespace "${NAMESPACE}"
    --set inferencePool.modelServers.matchLabels.app=benchmark-model
    --set inferenceExtension.image.registry=ghcr.io
    --set inferenceExtension.image.repository=llm-d/llm-d-inference-scheduler
    --set inferenceExtension.image.tag="${EPP_TAG}"
    --set inferenceExtension.replicas=1
    --set inferenceExtension.resources.requests.memory="${EPP_MEMORY_REQUEST}"
    --set inferenceExtension.resources.limits.memory="${EPP_MEMORY_LIMIT}"
    --version "${CHART_VERSION}"
)

if [[ -n "${EPP_CPU_REQUEST}" ]]; then
    HELM_ARGS+=(--set "inferenceExtension.resources.requests.cpu=${EPP_CPU_REQUEST}")
fi

PLUGINS_CONFIG=$(get_plugins_config)
if [[ -n "${PLUGINS_CONFIG}" ]]; then
    PLUGINS_FILE="${ROUTING}-plugins.yaml"
    HELM_ARGS+=(--set "inferenceExtension.pluginsConfigFile=${PLUGINS_FILE}")
    HELM_ARGS+=(--set "inferenceExtension.pluginsCustomConfig.${PLUGINS_FILE}=${PLUGINS_CONFIG}")
fi

if helm status "${EPP_RELEASE}" -n "${NAMESPACE}" &>/dev/null; then
    helm uninstall "${EPP_RELEASE}" -n "${NAMESPACE}" 2>/dev/null
    sleep 5
fi
helm install "${EPP_RELEASE}" "${CHART_URL}" --dependency-update "${HELM_ARGS[@]}"

# --- Step 2: Deploy model servers ---
if [[ "${USE_GPU}" == "true" ]]; then
    log "Deploying ${MODEL_REPLICAS} vLLM GPU pod(s) with ${MODEL_NAME}..."
    cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: benchmark-model
  namespace: ${NAMESPACE}
spec:
  replicas: ${MODEL_REPLICAS}
  selector:
    matchLabels:
      app: benchmark-model
  template:
    metadata:
      labels:
        app: benchmark-model
        inference.networking.k8s.io/engine-type: vllm
    spec:
      containers:
        - name: vllm
          image: ${VLLM_IMAGE}
          args: ["--model", "${MODEL_NAME}", "--port", "8000", "--max-model-len", "4096"]
          ports:
            - containerPort: 8000
              name: http
              protocol: TCP
          resources:
            requests:
              cpu: "4"
              memory: "16Gi"
              nvidia.com/gpu: "1"
            limits:
              cpu: "8"
              memory: "32Gi"
              nvidia.com/gpu: "1"
EOF
else
    log "Deploying ${MODEL_REPLICAS} simulator(s)..."
    cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: benchmark-model
  namespace: ${NAMESPACE}
spec:
  replicas: ${MODEL_REPLICAS}
  selector:
    matchLabels:
      app: benchmark-model
  template:
    metadata:
      labels:
        app: benchmark-model
        inference.networking.k8s.io/engine-type: vllm
    spec:
      containers:
        - name: vllm-sim
          image: ${SIM_IMAGE}
          args: ["--model", "${MODEL_NAME}"]
          ports:
            - containerPort: 8000
              name: http
              protocol: TCP
          resources:
            requests:
              cpu: "500m"
              memory: "512Mi"
            limits:
              cpu: "1"
              memory: "1Gi"
EOF
fi

# --- Step 3: Deploy benchmark pod ---
if ! kubectl get pod inference-perf -n "${NAMESPACE}" &>/dev/null; then
    log "Deploying benchmark pod..."
    kubectl run inference-perf \
        --image="${BENCH_IMAGE}" \
        -n "${NAMESPACE}" \
        --restart=Never \
        --command -- sleep infinity
fi

# --- Step 4: Wait for 1-replica EPP to be ready ---
log "Waiting for EPP pod to be ready..."
kubectl rollout status deployment/"${EPP_RELEASE}-epp" -n "${NAMESPACE}" --timeout=300s
kubectl rollout status deployment/benchmark-model -n "${NAMESPACE}" --timeout=300s
kubectl wait --for=condition=Ready pod/inference-perf -n "${NAMESPACE}" --timeout=180s

# --- Step 5: Scale to target replicas (if >1) ---
if [[ "${EPP_REPLICAS}" -gt 1 ]]; then
    log "Scaling EPP from 1 to ${EPP_REPLICAS} replicas..."
    kubectl scale deployment/"${EPP_RELEASE}-epp" -n "${NAMESPACE}" --replicas="${EPP_REPLICAS}"
    log "Waiting for all replicas to be ready..."
    kubectl rollout status deployment/"${EPP_RELEASE}-epp" -n "${NAMESPACE}" --timeout=300s
fi

log "All pods ready. Warming up for ${WARMUP_SECONDS}s..."
sleep "${WARMUP_SECONDS}"

# --- Step 6: Copy config and run benchmark ---
RUN_ID="$(date +%Y%m%d-%H%M%S)-r${EPP_REPLICAS}-cpu${EPP_CPU_REQUEST:-default}-${ROUTING}"
if [[ "${USE_GPU}" == "true" ]]; then
    RUN_ID="${RUN_ID}-gpu"
fi
log "Starting benchmark run: ${RUN_ID}"

RENDERED_CONFIG="$(mktemp /tmp/bench-config-XXXXXX.yaml)"
trap 'rm -f "${RENDERED_CONFIG}"' EXIT
EPP_ENDPOINT="http://${EPP_RELEASE}-epp:8081"
sed -e "s|REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL|${MODEL_NAME}|g" \
    -e "s|REPLACE_ENV_LLMDBENCH_HARNESS_STACK_ENDPOINT_URL|${EPP_ENDPOINT}|g" \
    -e "s|REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_TOKENIZER|${TOKENIZER}|g" \
    "${CONFIG_FILE}" > "${RENDERED_CONFIG}"

kubectl cp "${RENDERED_CONFIG}" "${NAMESPACE}/inference-perf:/tmp/bench-config.yml"

kubectl exec -n "${NAMESPACE}" inference-perf -- \
    inference-perf -c /tmp/bench-config.yml --log-level INFO 2>&1 | tee "${RESULTS_DIR}/${RUN_ID}.log"

# --- Step 7: Collect results ---
REPORT_DIR=$(kubectl exec -n "${NAMESPACE}" inference-perf -- \
    find /workspace -maxdepth 1 -name "reports-*" -type d | sort | tail -1)

if [[ -n "${REPORT_DIR}" ]]; then
    log "Copying results from ${REPORT_DIR}..."
    kubectl cp "${NAMESPACE}/inference-perf:${REPORT_DIR}" "${RESULTS_DIR}/${RUN_ID}"

    log "=== Results Summary ==="
    python3 -c "
import json, sys, os
results_dir = '${RESULTS_DIR}/${RUN_ID}'
summary = os.path.join(results_dir, 'summary_lifecycle_metrics.json')
if not os.path.exists(summary):
    print('No summary file found')
    sys.exit(0)
d = json.load(open(summary))
s = d['successes']
f = d['failures']
print(f'  Total requests: {s[\"count\"]}')
print(f'  Failures:       {f[\"count\"]}')
print(f'  Throughput:     {s[\"throughput\"][\"requests_per_sec\"]:.1f} QPS')
print(f'  TTFT P50:       {s[\"latency\"][\"time_to_first_token\"][\"median\"]*1000:.2f}ms')
print(f'  TTFT P99:       {s[\"latency\"][\"time_to_first_token\"][\"p99\"]*1000:.2f}ms')
print(f'  Req latency P99:{s[\"latency\"][\"request_latency\"][\"p99\"]*1000:.2f}ms')
" 2>/dev/null || echo "  (install python3 to see summary)"

    log "=== Per-Stage Breakdown ==="
    for stage_file in "${RESULTS_DIR}/${RUN_ID}"/stage_*_lifecycle_metrics.json; do
        [[ -f "${stage_file}" ]] || continue
        stage_num=$(basename "${stage_file}" | grep -o '[0-9]')
        python3 -c "
import json
d = json.load(open('${stage_file}'))
s = d['successes']
f = d['failures']
print(f'  Stage ${stage_num}: {s[\"throughput\"][\"requests_per_sec\"]:.0f} QPS, {f[\"count\"]} failures, TTFT_P50={s[\"latency\"][\"time_to_first_token\"][\"median\"]*1000:.1f}ms, P99={s[\"latency\"][\"time_to_first_token\"][\"p99\"]*1000:.1f}ms')
" 2>/dev/null
    done
    log "Generating charts..."
    kubectl exec -n "${NAMESPACE}" inference-perf -- \
        inference-perf --analyze "${REPORT_DIR}" 2>&1 | grep -E "saved|Warning"
    # Re-copy to include generated PNGs
    kubectl cp "${NAMESPACE}/inference-perf:${REPORT_DIR}" "${RESULTS_DIR}/${RUN_ID}" 2>/dev/null
else
    log "Warning: no report directory found in pod"
fi

log "Run ${RUN_ID} complete. Results at: ${RESULTS_DIR}/${RUN_ID}"
