#!/bin/bash
# Save as run_benchmark.sh in your pod

# Default benchmark subfolder
BENCHMARK_SUBFOLDER=${BENCHMARK_SUBFOLDER:-"llm-d-1p1d-vllm-benchmark"}
BENCHMARK_DIR="/workload-data/${BENCHMARK_SUBFOLDER}"

# Benchmark parameters with defaults
BENCHMARK_HOST=${BENCHMARK_HOST:-"inference-gateway"}
BENCHMARK_MODEL=${BENCHMARK_MODEL:-"meta-llama/Llama-3.1-8B-Instruct"}
BENCHMARK_DATASET=${BENCHMARK_DATASET:-"random"}
BENCHMARK_INPUT_LEN=${BENCHMARK_INPUT_LEN:-"8000"}
BENCHMARK_OUTPUT_LEN=${BENCHMARK_OUTPUT_LEN:-"200"}
BENCHMARK_NUM_PROMPTS=${BENCHMARK_NUM_PROMPTS:-"200"}
BENCHMARK_BURSTINESS=${BENCHMARK_BURSTINESS:-"100"}
BENCHMARK_REQUEST_RATE=${BENCHMARK_REQUEST_RATE:-"3.6"}
BENCHMARK_METRIC_PERCENTILES=${BENCHMARK_METRIC_PERCENTILES:-"95"}

# Create a timestamp variable
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")

# Create the directory structure
mkdir -p ${BENCHMARK_DIR}

# Start log file
echo "Starting benchmark setup at ${TIMESTAMP}" | tee ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log

# Log benchmark parameters
echo "Benchmark parameters:" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
echo "Output directory: ${BENCHMARK_DIR}" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
echo "Host: ${BENCHMARK_HOST}" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
echo "Model: ${BENCHMARK_MODEL}" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
echo "Dataset: ${BENCHMARK_DATASET}" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
echo "Input length: ${BENCHMARK_INPUT_LEN}" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
echo "Output length: ${BENCHMARK_OUTPUT_LEN}" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
echo "Number of prompts: ${BENCHMARK_NUM_PROMPTS}" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
echo "Burstiness: ${BENCHMARK_BURSTINESS}" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
echo "Request rate: ${BENCHMARK_REQUEST_RATE}" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
echo "Metric percentiles: ${BENCHMARK_METRIC_PERCENTILES}" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log

# Check if HF token exists and set it up
echo "Hugging Face token is available: $HUGGING_FACE_HUB_TOKEN" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log

# Make sure we're NOT in offline mode
unset TRANSFORMERS_OFFLINE
echo "Ensuring Hugging Face libraries can access network" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log

# Configure Hugging Face credentials properly
mkdir -p ~/.huggingface
echo "hf_token: $HUGGING_FACE_HUB_TOKEN" > ~/.huggingface/token
echo "Hugging Face token configured in ~/.huggingface/token" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log

# Install benchmarking dependencies
echo "Installing benchmarking dependencies..." | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
pip install pandas matplotlib numpy tqdm requests pyyaml scikit-learn openai datasets huggingface_hub | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log

# Print info about the benchmark
echo "Starting benchmark at $(date +"%Y-%m-%d_%H-%M-%S")" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log

# Verify Hugging Face token works
echo "Verifying Hugging Face token..." | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
python3 -c "from huggingface_hub import HfApi; api = HfApi(); print('Token works!' if api.whoami() else 'Token verification failed')" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log

# Create a place to store the result file before running
touch ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.json
chmod 666 ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.json
echo "Created result file: ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.json" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log

# Run the benchmark with the original tokenizer parameter
python3 benchmark_serving.py \
    --host ${BENCHMARK_HOST} \
    --port 80 \
    --endpoint /v1/completions \
    --seed $(date +%s) \
    --model ${BENCHMARK_MODEL} \
    --dataset-name ${BENCHMARK_DATASET} \
    --random-input-len ${BENCHMARK_INPUT_LEN} \
    --random-output-len ${BENCHMARK_OUTPUT_LEN} \
    --num-prompts ${BENCHMARK_NUM_PROMPTS} \
    --burstiness ${BENCHMARK_BURSTINESS} \
    --request-rate ${BENCHMARK_REQUEST_RATE} \
    --metric-percentiles ${BENCHMARK_METRIC_PERCENTILES} \
    --backend openai \
    --ignore-eos \
    --save-result \
    --result-dir "${BENCHMARK_DIR}" \
    --result-filename "benchmark_${TIMESTAMP}.json" \
    --metadata "timestamp=${TIMESTAMP},model=${BENCHMARK_MODEL}" 2>&1 | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log

# Check if the result file has content
echo "Checking result file..." | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
if [ -s "${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.json" ]; then
    echo "Result file has data: $(ls -la ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.json)" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
    # Show file size and first few lines
    ls -lh ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.json | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
    echo "First 10 lines of result file:" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
    head -n 10 ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.json | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
else
    echo "WARNING: Result file is empty or not created" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
    # List the directory contents
    echo "Directory contents:" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
    ls -la ${BENCHMARK_DIR} | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log
fi

echo "Benchmark completed at $(date +"%Y-%m-%d_%H-%M-%S")" | tee -a ${BENCHMARK_DIR}/benchmark_${TIMESTAMP}.log