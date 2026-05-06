# Multi-turn Trace Replay Benchmark

This directory contains a benchmark script for replaying multi-turn chat traces against llm-d or any other inference server. It uses the `inference_perf` library to generate load and collect metrics.

## Overview

The script `production-trace-replay-qwen.py`:
1.  Loads a multi-turn chat trace from `qwen_traceA_blksz_16.jsonl`.
2.  Simulates multiple users interacting with the model server, preserving conversation history (context) across turns.
3.  Collects performance metrics (TTFT, TPOT, Latency, etc.).
4.  Generates a summary report.

## Prerequisites

-   Python 3.8+
-   `inference_perf` library installed.
-   A running inference server that supports the OpenAI-compatible Completion or Chat Completion API (though this script uses the Completion API with raw prompt tokens).

## Configuration

You can configure the benchmark using command line arguments:

| Argument | Description | Default |
| :--- | :--- | :--- |
| `--model-name` | The name of the model served by the inference server. | `google/gemma-3-1b-it` (or $MODEL_NAME) |
| `--base-url` | The base URL of the inference server. | `http://localhost:8000` (or $ENDPOINT_BASE_URL) |
| `--limit` | Limit the number of trace entries to replay. | `1000` |
| `--trace-file` | Path to the JSONL trace file to replay. | `experimental/multi-turn/qwen_traceA_blksz_16.jsonl` |

## Usage

**Run the benchmark:**

```bash
# Download trace file (if not already present)
wget https://github.com/alibaba-edu/qwen-bailian-usagetraces-anon/raw/refs/heads/main/qwen_traceA_blksz_16.jsonl

# Install dependencies (ensure inference-perf is available)
pip install inference-perf

# Run the benchmark against a specific endpoint
python production-trace-replay-qwen.py \
    --model-name "meta-llama/Llama-2-7b-chat-hf" \
    --base-url "http://localhost:8000" \
    --trace-file "qwen_traceA_blksz_16.jsonl" \
    --limit 500
```

## Output

After the benchmark completes:
- A summary of lifecycle metrics is printed to the console.
- **TTFT by Turn Buckets**: A specialized report is printed, showing TTFT percentiles (P50, P90, P99) grouped by conversation turn ranges (Turn 1, Turns 2-5, etc.).
- Detailed reports are saved in the `reports` directory within the current working directory.

### Example TTFT by Turn Buckets Report

```text
=== TTFT by Turn Buckets (seconds) ===
Bucket          | Count | P50      | P90      | P99     
-------------------------------------------------------
Turn 1          | 120   | 0.4521   | 0.8912   | 1.2543  
Turns 2-5       | 250   | 0.6123   | 1.1234   | 1.5678  
Turns 6-10      | 85    | 0.8234   | 1.4567   | 1.9876  
Turns 11+       | 45    | 1.0567   | 1.8901   | 2.4567  
======================================
```
