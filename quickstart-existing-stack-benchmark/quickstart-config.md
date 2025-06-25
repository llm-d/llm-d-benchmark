# llm-d-benchmark Configuration Guide

This guide provides detailed instructions for configuring the llm-d-benchmark Kubernetes workflow to match your deployment and benchmarking requirements.

## Overview

Before running the workflow, you'll need to customize several configuration files:

1. **Model Configuration** - Specify which model to benchmark
2. **Workload Profiles and Scenarios** - Choose benchmark parameters and test scenarios
3. **Environment Variables** - Configure cluster-specific settings
4. **Scenario-Based Configuration** - Use predefined hardware-optimized configurations

## 1. Model Configuration

To customize which model is being benchmarked, update the `model_name` field in `resources/benchmark-workload-configmap.yaml`:

```yaml
data:
  llmdbench_workload.yaml: |
    # Change this to match your deployed model name, from model_endpoint/v1/models
    model_name: "meta-llama/Llama-3.2-3B-Instruct"
```

## 2. Workload Profiles and Scenarios

### Available Workload Profiles

From `../workload/profiles/`, you can choose from these benchmark profiles:

- **`sanity_short-input.yaml.in`** - Quick sanity test with short inputs
- **`sanity_long-input.yaml.in`** - Quick sanity test with long inputs
- **`sanity_sharegpt.yaml.in`** - Quick test using ShareGPT dataset
- **`small_model_long_input.yaml.in`** - Optimized for small models (1B-3B parameters)
- **`medium_model_long_input.yaml.in`** - Optimized for medium models (8B parameters)
- **`large_model_long_input.yaml.in`** - Optimized for large models (70B+ parameters)

### Available Scenarios

Configure the `scenarios` field in `resources/benchmark-workload-configmap.yaml`:

```yaml
scenarios: "long-input"  # Options: short-input, long-input, sharegpt
```

**Scenario Options:**
- **`short-input`** - Tests with shorter prompts and responses
- **`long-input`** - Tests with longer prompts and responses
- **`sharegpt`** - Uses ShareGPT conversation dataset

### QPS (Queries Per Second) Configuration

Customize the load testing parameters:

```yaml
qps_values: "0.1 0.25 0.5"  # Space-separated list of QPS values to test
```

**Recommended QPS Values by Model Size:**
- **Small models (1B-3B)**: `"0.5 1.0 2.0"`
- **Medium models (8B)**: `"0.1 0.25 0.5"`
- **Large models (70B+)**: `"0.05 0.1 0.25"`

## 3. Environment Variables Configuration

Update `resources/benchmark-env.yaml` with your cluster-specific settings.

### Required Environment Variables

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: benchmark-env
  namespace: llm-d-benchmark
data:
  # Core benchmark configuration
  LLMDBENCH_FMPERF_NAMESPACE: "llm-d-benchmark"           # Namespace for benchmark jobs
  LLMDBENCH_HARNESS_STACK_TYPE: "vllm-prod"               # "vllm-prod" for standalone, "llm-d" for llm-d stack
  LLMDBENCH_HARNESS_ENDPOINT_URL: "https://your-model-endpoint"  # UPDATE: Your model service endpoint
  LLMDBENCH_HARNESS_STACK_NAME: "standalone-vllm-llama-3b"      # Unique identifier for this benchmark run
  LLMDBENCH_HARNESS_WORKLOAD_FILE: "llmdbench_workload.yaml"    # Workload configuration file name
  LLMDBENCH_FMPERF_REPETITION: "1"                       # Number of times to repeat the benchmark
  LLMDBENCH_FMPERF_RESULTS_DIR: "/requests"              # Directory to store results (keep as /requests)
```

### Key Variables to Update

#### 1. LLMDBENCH_HARNESS_ENDPOINT_URL (REQUIRED)

This is the most critical variable to update. Point it to your deployed model service:

**For standalone vLLM deployments:**

Use internal service URL, `http://service-name.namespace.svc.cluster.local:port`
```yaml
LLMDBENCH_HARNESS_ENDPOINT_URL: "http://vllm-service.vllm-namespace.svc.cluster.local:8000"
```

**For llm-d stack deployments:**

Use internal service URL, `http://llm-d-inference-gateway.namespace.svc.cluster.local:80`
```yaml
LLMDBENCH_HARNESS_ENDPOINT_URL: "http://llm-d-inference-gateway.llm-d.svc.cluster.local:80"
```

#### 2. LLMDBENCH_HARNESS_STACK_TYPE

Set based on your deployment type:

- **`"vllm-prod"`** for standalone vLLM deployments
- **`"llm-d"`** for llm-d stack deployments

#### 3. LLMDBENCH_HARNESS_STACK_NAME

Choose a descriptive name for your benchmark run:

**Examples:**
- `standalone-vllm-3b-instruct`
- `llm-d-8b-base`
- `standalone-vllm-70b-instruct`

### Optional Environment Variables

```yaml
# Advanced configuration (optional)
LLMDBENCH_FMPERF_REPETITION: "3"                        # Run benchmark 3 times for better statistics
LLMDBENCH_CONTROL_WAIT_TIMEOUT: "3600"                  # Increase timeout for large models (seconds)
```

## 4. Scenario-Based Configuration (Alternative)

Instead of manually configuring environment variables, you can use predefined scenarios from `../scenarios/`. These scenarios contain optimized configurations for specific hardware and model combinations.

### Available Scenarios

- **`ocp_H100_deployer_llama-70b.sh`** - H100 GPU with 70B model using llm-d deployer
- **`ocp_H100_standalone_llama-70b.sh`** - H100 GPU with 70B model using standalone vLLM
- **`ocp_L40_deployer_llama-3b.sh`** - L40 GPU with 3B model using llm-d deployer
- **`ocp_L40_standalone_llama-3b.sh`** - L40 GPU with 3B model using standalone vLLM
- **`ocp_L40_standalone_llama-8b.sh`** - L40 GPU with 8B model using standalone vLLM
- **`kubernetes_H200_deployer_llama-8b.sh`** - H200 GPU with 8B model using llm-d deployer
- **`ocp_H100MIG_deployer_llama-3b.sh`** - H100 MIG with 3B model using llm-d deployer
- **`ocp_H100MIG_deployer_llama-8b.sh`** - H100 MIG with 8B model using llm-d deployer

### Using a Scenario

1. **Choose a scenario** that matches your hardware and model requirements
2. **View the scenario file** to see the environment variables:
   ```bash
   cat ../scenarios/ocp_L40_deployer_llama-3b.sh
   ```
3. **Copy relevant variables** to your `resources/benchmark-env.yaml`

**Example scenario content:**
```bash
export LLMDBENCH_DEPLOY_MODEL_LIST=llama-3b
export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-L40S
export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=nfs-client-pokprod
export LLMDBENCH_VLLM_COMMON_REPLICAS=1
```

## Configuration Examples

### Example 1: Small Model (3B) on L40 GPU

**resources/benchmark-env.yaml:**
```yaml
data:
  LLMDBENCH_HARNESS_STACK_TYPE: "vllm-prod"
  LLMDBENCH_HARNESS_ENDPOINT_URL: "http://vllm-service.vllm-ns.svc.cluster.local:8000"
  LLMDBENCH_HARNESS_STACK_NAME: "standalone-vllm-3b-instruct"
  LLMDBENCH_FMPERF_JOB_ID: "standalone-vllm-3b-instruct"
```

**resources/benchmark-workload-configmap.yaml:**
```yaml
data:
  llmdbench_workload.yaml: |
    model_name: "meta-llama/Llama-3.2-3B-Instruct"
    scenarios: "long-input"
    qps_values: "0.5 1.0 2.0"
```

### Example 2: Large Model (70B) with llm-d Stack

**resources/benchmark-env.yaml:**
```yaml
data:
  LLMDBENCH_HARNESS_STACK_TYPE: "llm-d"
  LLMDBENCH_HARNESS_ENDPOINT_URL: "http://llm-d-inference-gateway.llm-d.svc.cluster.local:80"
  LLMDBENCH_HARNESS_STACK_NAME: "llm-d-70b-instruct"
  LLMDBENCH_FMPERF_JOB_ID: "llm-d-70b-instruct"
  LLMDBENCH_CONTROL_WAIT_TIMEOUT: "3600"
```

**resources/benchmark-workload-configmap.yaml:**
```yaml
data:
  llmdbench_workload.yaml: |
    model_name: "meta-llama/Llama-3.1-70B-Instruct"
    scenarios: "long-input"
    qps_values: "0.05 0.1 0.25"
```
