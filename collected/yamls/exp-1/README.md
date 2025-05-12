# Experiment 1

## Setup

Single pod llama3.1-70b model with TP=2, on top of x2 NVIDIA A100 80GB GPUs.

## Objective

Isolate the different LMCache functionalities within the `llm-d:0.0.4` image.

## Experiment

- **baseline-llm-d**: `llmd-0.0.4` with LMCache disabled
- **lmcache-llm-d**: `llmd-0.0.4` with LMCache enabled with KVCache offloading/reuse only
- **lmcache-llm-d-indexing**: `llmd-0.0.4` with LMCache enabled with KVCache offloading/reuse and indexing