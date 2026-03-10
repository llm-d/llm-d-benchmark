# Available Harnesses and Profiles

This document lists the available load generators (harnesses) and their workload profiles.

## Harnesses

| Harness | Description |
|---------|-------------|
| `inference-perf` | Default harness, comprehensive performance testing |
| `guidellm` | Alternative load generator |
| `vllm-benchmark` | vLLM native benchmarking |
| `nop` | No-op harness for testing |

## Profiles by Harness

### inference-perf

| Profile | Use Case |
|---------|----------|
| `sanity_random.yaml` | Quick validation tests |
| `chatbot_synthetic.yaml` | Chat workload simulation |
| `chatbot_sharegpt.yaml` | Real conversation patterns |
| `shared_prefix_synthetic.yaml` | Prefix caching tests |
| `shared_prefix_multi_turn_chat.yaml` | Multi-turn conversations |
| `summarization_synthetic.yaml` | Long context summarization |
| `code_completion_synthetic.yaml` | Code completion patterns |
| `random_concurrent.yaml` | Concurrent request stress |

### guidellm

| Profile | Use Case |
|---------|----------|
| `sanity_random.yaml` | Basic validation |
| `sanity_concurrent.yaml` | Concurrent validation |
| `chatbot_synthetic.yaml` | Chat simulation |
| `shared_prefix_synthetic.yaml` | Prefix caching |
| `summarization_synthetic.yaml` | Summarization |

### vllm-benchmark

| Profile | Use Case |
|---------|----------|
| `sanity_random.yaml` | Basic validation |
| `random_concurrent.yaml` | Concurrent stress test |
| `sharegpt.yaml` | ShareGPT dataset |

## Default Values

When harness or profile are not specified in `/convert-guide`:

- **Default Harness**: `inference-perf`
- **Default Profile**: `sanity_random.yaml`

These defaults will be used in the generated scenario file:

```bash
export LLMDBENCH_HARNESS_NAME=inference-perf
export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=sanity_random.yaml
```
