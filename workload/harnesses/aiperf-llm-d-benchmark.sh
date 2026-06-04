#!/usr/bin/env bash

mkdir -p "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"

# Download dataset from S3 if configured
if [[ -n "${LLMDBENCH_RUN_DATASET_URL:-}" && "${LLMDBENCH_RUN_DATASET_URL}" == s3://* ]]; then
  DATASET_DIR="${LLMDBENCH_RUN_DATASET_DIR:-/requests/datasets}"
  DATASET_FILE=$(basename "$LLMDBENCH_RUN_DATASET_URL")
  DATASET_PATH="$DATASET_DIR/$DATASET_FILE"
  if [[ ! -f "$DATASET_PATH" ]]; then
    echo "Downloading dataset from $LLMDBENCH_RUN_DATASET_URL ..."
    mkdir -p "$DATASET_DIR"
    python3 -c "
import boto3, os
url = os.environ['LLMDBENCH_RUN_DATASET_URL']
parts = url.replace('s3://', '').split('/', 1)
bucket, key = parts[0], parts[1]
dest = '$DATASET_PATH'
print(f'Downloading s3://{bucket}/{key} -> {dest}')
boto3.client('s3').download_file(bucket, key, dest)
print(f'Download complete ({os.path.getsize(dest)} bytes)')
"
    if [[ $? -ne 0 ]]; then
      echo "ERROR: Failed to download dataset from S3"
      exit 1
    fi
  else
    echo "Dataset already exists at $DATASET_PATH, skipping download"
  fi
fi
cp -f ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/aiperf/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME}

# Start metrics collection in background if enabled
if [[ "${LLMDBENCH_VLLM_COMMON_METRICS_SCRAPE_ENABLED:-false}" == "true" ]]; then
  echo "Starting metrics collection..."
  /usr/local/bin/collect_metrics.sh start >> $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/metrics_collection.log 2>&1 &
  METRICS_COLLECTOR_PID=$!
  echo "Metrics collector started with PID: $METRICS_COLLECTOR_PID"
  echo "Metrics collection logs: $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/metrics_collection.log"
fi

# Wait for endpoint to be ready before running benchmark
ENDPOINT_URL=$(cat ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/aiperf/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} | yq -r '.url')
if [[ -n "$ENDPOINT_URL" && "$ENDPOINT_URL" != "null" ]]; then
  MAX_WAIT=60
  INTERVAL=10
  echo "Waiting for endpoint at ${ENDPOINT_URL}/v1/models ..."
  for i in $(seq 1 $MAX_WAIT); do
    if curl -sf -o /dev/null --max-time 5 "${ENDPOINT_URL}/v1/models" 2>/dev/null; then
      echo "Endpoint is ready (attempt $i/${MAX_WAIT})"
      break
    fi
    if [[ $i -eq $MAX_WAIT ]]; then
      echo "ERROR: Endpoint not ready after ${MAX_WAIT} attempts ($((MAX_WAIT * INTERVAL))s)"
      exit 1
    fi
    echo "Attempt $i/${MAX_WAIT}: endpoint not ready, retrying in ${INTERVAL}s..."
    sleep $INTERVAL
  done
fi

# Build CLI args from YAML profile, skipping empty values
export LLMDBENCH_HARNESS_ARGS="--$(cat ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/aiperf/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} \
  | yq -r 'to_entries | map(select(.value != null and .value != "")) | map("\(.key)=\(.value)") | join(" --")' \
  | sed -e 's^=none ^ ^g' -e 's^=none$^^g')"

# Inject output-artifact-dir (framework-managed, not in profile)
LLMDBENCH_HARNESS_ARGS="$LLMDBENCH_HARNESS_ARGS --output-artifact-dir=${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}"

# Append extra pass-through args if provided
if [[ -n "${LLMDBENCH_AIPERF_EXTRA_ARGS:-}" ]]; then
  LLMDBENCH_HARNESS_ARGS="$LLMDBENCH_HARNESS_ARGS $LLMDBENCH_AIPERF_EXTRA_ARGS"
fi

echo "Running aiperf benchmark"
echo "Args: $LLMDBENCH_HARNESS_ARGS"
AIPERF_BIN=$(command -v aiperf 2>/dev/null)
if [[ -z "$AIPERF_BIN" ]]; then
  echo "ERROR: aiperf not found in PATH. Ensure the container image includes aiperf."
  exit 1
fi
start=$(date +%s.%N)
$AIPERF_BIN profile $LLMDBENCH_HARNESS_ARGS > >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log) 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC=$?
stop=$(date +%s.%N)

# Stop metrics collection
if [[ "${LLMDBENCH_VLLM_COMMON_METRICS_SCRAPE_ENABLED:-false}" == "true" ]] && [[ -n "${METRICS_COLLECTOR_PID:-}" ]]; then
  echo "Stopping metrics collection..."
  /usr/local/bin/collect_metrics.sh stop >> $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/metrics_collection.log 2>&1
  wait $METRICS_COLLECTOR_PID 2>/dev/null || true

  # Process collected metrics
  echo "Processing collected metrics..."
  /usr/local/bin/collect_metrics.sh process >> $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/metrics_collection.log 2>&1

  echo "Metrics collection complete. Check metrics_collection.log for details."
fi

export LLMDBENCH_HARNESS_START=$(date -d "@${start}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_STOP=$(date -d "@${stop}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_DELTA=PT$(echo "$stop - $start" | bc)S
export LLMDBENCH_HARNESS_VERSION=$(${AIPERF_BIN:-aiperf} --version 2>/dev/null || echo "unknown")

# Write run metadata to a file so the analyzer can read it.
# Environment variables exported here are lost when this subshell exits,
# so the file serves as the handoff mechanism to the analysis phase.
cat > "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/run_metadata.yaml" <<METADATA
harness_start: "${LLMDBENCH_HARNESS_START}"
harness_stop: "${LLMDBENCH_HARNESS_STOP}"
harness_delta: "${LLMDBENCH_HARNESS_DELTA}"
harness_args: "${LLMDBENCH_HARNESS_ARGS}"
harness_version: "${LLMDBENCH_HARNESS_VERSION}"
harness_name: "${LLMDBENCH_HARNESS_NAME:-aiperf}"
harness_workload: "${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME:-}"
harness_rc: "${LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC}"
model: "${LLMDBENCH_DEPLOY_CURRENT_MODEL:-}"
endpoint_url: "${LLMDBENCH_HARNESS_STACK_ENDPOINT_URL:-}"
namespace: "${LLMDBENCH_VLLM_COMMON_NAMESPACE:-}"
METADATA
echo "Run metadata written to $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/run_metadata.yaml"

# If benchmark harness returned with an error, exit here
if [[ $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC -ne 0 ]]; then
  echo "Harness returned with error $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC"
  exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC
fi
echo "Harness completed successfully."

exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC
