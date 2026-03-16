# Workload System

This directory contains the benchmark workloads that `llmdbenchmark` deploys and executes against model-serving endpoints. The run phase renders workload profiles, deploys them as Kubernetes ConfigMaps, launches harness pods, waits for completion, and collects results.

## Table of Contents

- [Directory Layout](#directory-layout)
- [How the Run Phase Works](#how-the-run-phase-works)
  - [Step-by-Step Execution Flow](#step-by-step-execution-flow)
  - [Kubernetes Resource Lifecycle](#kubernetes-resource-lifecycle)
- [Harnesses](#harnesses)
  - [What Is a Harness?](#what-is-a-harness)
  - [Available Harnesses](#available-harnesses)
  - [Harness Script Contract](#harness-script-contract)
  - [How Harness Scripts Are Mounted](#how-harness-scripts-are-mounted)
- [Workload Profiles](#workload-profiles)
  - [What Is a Profile?](#what-is-a-profile)
  - [Template Substitution (REPLACE_ENV_*)](#template-substitution-replace_env_)
  - [Available Profiles by Harness](#available-profiles-by-harness)
  - [Profile Configuration Styles](#profile-configuration-styles)
- [Experiments and Treatments](#experiments-and-treatments)
  - [Design of Experiments (DoE) Concepts](#design-of-experiments-doe-concepts)
  - [Default (No Experiments)](#default-no-experiments)
  - [Single Override (--overrides)](#single-override---overrides)
  - [Multi-Treatment Experiments (--experiments)](#multi-treatment-experiments---experiments)
  - [Experiment File Format](#experiment-file-format)
  - [Treatment x Parallelism Matrix](#treatment-x-parallelism-matrix)
- [Available Experiments](#available-experiments)
  - [inference-scheduling](#inference-scheduling)
  - [tiered-prefix-cache](#tiered-prefix-cache)
  - [precise-prefix-cache-aware](#precise-prefix-cache-aware)
  - [pd-disaggregation](#pd-disaggregation)
  - [Writing Custom Experiments](#writing-custom-experiments)
- [Run Modes](#run-modes)
  - [Full Pipeline Run](#full-pipeline-run)
  - [Run-Only Mode](#run-only-mode)
  - [Skip Mode (Result Collection)](#skip-mode-result-collection)
  - [Debug Mode](#debug-mode)
  - [Dry-Run Mode](#dry-run-mode)
- [CLI Reference](#cli-reference)
- [Adding a New Harness](#adding-a-new-harness)
- [Adding a New Profile](#adding-a-new-profile)

---

## Directory Layout

```
workload/
  experiments/                              # DoE experiment definitions
    inference-scheduling.yaml               # Scheduling strategy comparison
    tiered-prefix-cache.yaml                # CPU-offloaded prefix cache sweep
    precise-prefix-cache-aware.yaml         # Prefix-cache-aware routing sweep
    pd-disaggregation.yaml                  # Prefill-decode disaggregation load curve
  harnesses/                                # Benchmark entry-point scripts
    guidellm-llm-d-benchmark.sh             # guidellm harness wrapper
    inference-perf-llm-d-benchmark.sh       # inference-perf harness wrapper
    inferencemax-llm-d-benchmark.sh         # inferencemax harness wrapper
    nop-llm-d-benchmark.py                  # No-op harness (testing/validation)
    vllm-benchmark-llm-d-benchmark.sh       # vllm-benchmark harness wrapper
  profiles/                                 # Workload profile templates
    guidellm/                               # Profiles for the guidellm harness
      sanity_random.yaml.in
      sanity_concurrent.yaml.in
      chatbot_synthetic.yaml.in
      shared_prefix_synthetic.yaml.in
      summarization_synthetic.yaml.in
    inference-perf/                         # Profiles for the inference-perf harness
      sanity_random.yaml.in
      chatbot_sharegpt.yaml.in
      chatbot_synthetic.yaml.in
      code_completion_synthetic.yaml.in
      random_concurrent.yaml.in
      shared_prefix_multi_turn_chat.yaml.in
      shared_prefix_synthetic.yaml.in
      shared_prefix_synthetic_short.yaml.in
      summarization_synthetic.yaml.in
    inferencemax/                            # Profiles for inferencemax
      random_concurrent.yaml.in
    nop/                                     # Profiles for the no-op harness
      nop.yaml.in
    vllm-benchmark/                          # Profiles for vllm-benchmark
      fixed_dataset.yaml.in
      random_concurrent.yaml.in
      sanity_random.yaml.in
      sharegpt.yaml.in
```

---

## How the Run Phase Works

The run phase is orchestrated by 11 sequential steps, each implemented as a Python `Step` subclass. Steps are grouped into **global** (run once) and **per-stack** (run once per rendered Kubernetes stack).

### Step-by-Step Execution Flow

| Step | Name | Scope | Description |
|------|------|-------|-------------|
| 00 | `run_preflight` | global | Validate cluster connectivity, namespace, output destination |
| 01 | `run_cleanup_previous` | global | Delete leftover harness pods from prior runs |
| 02 | `detect_endpoint` | per-stack | Find the model-serving endpoint URL |
| 03 | `verify_model` | per-stack | Confirm the expected model is served at the endpoint |
| 04 | `render_profiles` | per-stack | Render `.yaml.in` profile templates with runtime values |
| 05 | `create_profile_configmap` | per-stack | Create Kubernetes ConfigMaps for profiles and harness scripts |
| 06 | `deploy_harness` | per-stack | Render and deploy harness pod(s) from Jinja2 template |
| 07 | `wait_completion` | per-stack | Poll pods until Succeeded or Failed |
| 08 | `collect_results` | per-stack | Copy results from PVC to local workspace |
| 09 | `upload_results` | global | Upload results to GCS/S3 if configured |
| 10 | `run_cleanup_post` | global | Delete harness pods and ConfigMaps |

### Kubernetes Resource Lifecycle

During a benchmark run, the following Kubernetes resources are created and managed:

```
                 Step 05 creates                Step 06 creates
                 +--------------------------+   +-------------------------+
                 | ConfigMap:               |   | Pod:                    |
                 |   {harness}-profiles     |   |   {harness}-{random}    |
                 | ConfigMap:               |   |   (one per treatment    |
                 |   llmdbench-harness-     |   |    x parallelism)       |
                 |   scripts                |   |                         |
                 +--------------------------+   +-------------------------+
                        |                              |
                        | mounted at:                  | runs:
                        | /workspace/profiles/         | /workspace/harnesses/
                        | /workspace/harnesses/        |   {harness}-{executable}
                        |                              |
                        +-----------> Harness Pod <----+
                                         |
                                 writes results to
                                         |
                                    PVC: /requests/{experiment-id}/
                                         |
                                 Step 08 copies to
                                         |
                                    Local workspace
```

After results are collected (step 08), step 10 cleans up all created ConfigMaps and pods.

---

## Harnesses

### What Is a Harness?

A **harness** is a benchmark tool wrapper. Each harness consists of:

1. **An entry-point script** in `workload/harnesses/` -- a shell script (or Python script for the nop harness) that sets up the environment, runs the benchmark tool, and captures results.
2. **One or more workload profiles** in `workload/profiles/{harness_name}/` -- YAML configuration files that define what the benchmark measures (request patterns, load levels, data distributions).

The harness script runs inside the **benchmark container image** as a Kubernetes pod. The script and profiles are mounted into the container via ConfigMaps.

### Available Harnesses

| Harness Name | Script | Benchmark Tool | Purpose |
|-------------|--------|----------------|---------|
| `inference-perf` | `inference-perf-llm-d-benchmark.sh` | `inference-perf` | Comprehensive LLM inference benchmarking with detailed metrics |
| `guidellm` | `guidellm-llm-d-benchmark.sh` | `guidellm benchmark` | Load testing with configurable request patterns |
| `vllm-benchmark` | `vllm-benchmark-llm-d-benchmark.sh` | `vllm bench serve` | vLLM-native benchmarking with latency percentiles |
| `inferencemax` | `inferencemax-llm-d-benchmark.sh` | Custom Python script | Benchmarking with warmup and random seed control |
| `nop` | `nop-llm-d-benchmark.py` | No-op | Testing and validation without running real benchmarks |

### Harness Script Contract

Every harness script follows the same contract. The harness pod sets these environment variables before invoking the script:

| Environment Variable | Set By | Description |
|---------------------|--------|-------------|
| `LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR` | Step 06 (pod command) | Where to write results (e.g. `/requests/{experiment-id}`) |
| `LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME` | Step 06 (pod command) | Profile filename to use (e.g. `sanity_random.yaml`) |
| `LLMDBENCH_RUN_WORKSPACE_DIR` | Pod env (template) | Workspace root inside the container (default: `/workspace`) |
| `LLMDBENCH_DEPLOY_CURRENT_MODEL` | Pod env (template) | Model name being served |
| `LLMDBENCH_HARNESS_STACK_ENDPOINT_URL` | Pod env (template) | Model-serving endpoint URL |
| `LLMDBENCH_RUN_EXPERIMENT_ID` | Pod env (template) | Unique experiment identifier |

Each harness script is expected to:

1. Create the results directory (`mkdir -p $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR`)
2. Read the workload profile from `/workspace/profiles/{harness_name}/`
3. Run the benchmark tool
4. Write results (JSON/YAML + stdout.log + stderr.log) to the results directory
5. Capture timing metadata (start/stop timestamps, elapsed time)
6. Exit with the benchmark tool's return code

### How Harness Scripts Are Mounted

The scripts in `workload/harnesses/` are packaged into a Kubernetes ConfigMap named `llmdbench-harness-scripts` by step 05. The harness pod template (`20_harness_pod.yaml.j2`) mounts this ConfigMap at `/workspace/harnesses/` with executable permissions (`defaultMode: 0755`).

The pod's command invokes the script directly:
```
/workspace/harnesses/{harness_name}-{executable}
```

For example, with harness `inference-perf` and executable `llm-d-benchmark.sh`:
```
/workspace/harnesses/inference-perf-llm-d-benchmark.sh
```

---

## Workload Profiles

### What Is a Profile?

A **profile** is a YAML configuration file that tells the harness script what workload to run. Profiles define:

- **Load pattern**: Request rate, duration, concurrency, stages
- **Data generation**: Prompt/output token distributions (random, synthetic, dataset-based)
- **Model endpoint**: Which model to target and where (substituted at render time)
- **Metrics**: What to measure and report (TTFT, TPOT, ITL, E2EL, percentiles)

Profiles are stored as `.yaml.in` templates that contain `REPLACE_ENV_*` placeholder tokens. These tokens are substituted with actual runtime values during step 04 (render_profiles).

### Template Substitution (REPLACE_ENV_*)

Profile templates use a simple token-replacement system (NOT Jinja2). Any occurrence of `REPLACE_ENV_{KEY}` is replaced with the corresponding runtime value.

**Supported tokens:**

| Token | Replaced With | Source |
|-------|--------------|--------|
| `REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL` | Model name | `--model` flag or plan config |
| `REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_TOKENIZER` | Model name (same) | `--model` flag or plan config |
| `REPLACE_ENV_LLMDBENCH_HARNESS_STACK_ENDPOINT_URL` | Endpoint URL | Detected in step 02 or `--endpoint-url` |
| `REPLACE_ENV_LLMDBENCH_RUN_DATASET_DIR` | Dataset directory | `--dataset` flag |

**Example**: Before rendering:
```yaml
server:
  model_name: REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL
  base_url: REPLACE_ENV_LLMDBENCH_HARNESS_STACK_ENDPOINT_URL
```

After rendering (with `--model facebook/opt-125m` and endpoint `http://10.0.0.1:80`):
```yaml
server:
  model_name: facebook/opt-125m
  base_url: http://10.0.0.1:80
```

Unknown tokens (not in the substitution map) are left unchanged.

### Available Profiles by Harness

#### inference-perf (9 profiles)

| Profile | Load Pattern | Data Type | Description |
|---------|-------------|-----------|-------------|
| `sanity_random.yaml.in` | Constant 1 req/s, 30s | Random synthetic | Quick sanity check |
| `chatbot_sharegpt.yaml.in` | Variable | ShareGPT dataset | Real conversation workload |
| `chatbot_synthetic.yaml.in` | Variable | Synthetic chat | Synthetic chatbot simulation |
| `code_completion_synthetic.yaml.in` | Variable | Synthetic code | Code completion workload |
| `random_concurrent.yaml.in` | Concurrent | Random synthetic | Concurrent random requests |
| `shared_prefix_multi_turn_chat.yaml.in` | Variable | Shared prefix | Multi-turn with common prefixes |
| `shared_prefix_synthetic.yaml.in` | Variable | Shared prefix | Shared prefix synthetic data |
| `shared_prefix_synthetic_short.yaml.in` | Variable | Shared prefix | Short shared prefix workload |
| `summarization_synthetic.yaml.in` | Variable | Synthetic | Long-context summarization |

#### guidellm (5 profiles)

| Profile | Load Pattern | Data Type | Description |
|---------|-------------|-----------|-------------|
| `sanity_random.yaml.in` | Constant 1 req/s, 30s | Random | Quick sanity check |
| `sanity_concurrent.yaml.in` | Concurrent | Random | Concurrent sanity check |
| `chatbot_synthetic.yaml.in` | Variable | Synthetic chat | Chatbot simulation |
| `shared_prefix_synthetic.yaml.in` | Variable | Shared prefix | Shared prefix workload |
| `summarization_synthetic.yaml.in` | Variable | Synthetic | Summarization workload |

#### vllm-benchmark (4 profiles)

| Profile | Load Pattern | Data Type | Description |
|---------|-------------|-----------|-------------|
| `sanity_random.yaml.in` | Fixed | Random | Quick sanity check |
| `random_concurrent.yaml.in` | Concurrent (max=1) | Random 10K/1K tokens | Large token random workload |
| `fixed_dataset.yaml.in` | Fixed | Dataset file | Pre-built dataset |
| `sharegpt.yaml.in` | Variable | ShareGPT | Real conversation dataset |

#### inferencemax (1 profile)

| Profile | Load Pattern | Data Type | Description |
|---------|-------------|-----------|-------------|
| `random_concurrent.yaml.in` | Concurrent | Random | Concurrent random workload |

#### nop (1 profile)

| Profile | Data Type | Description |
|---------|-----------|-------------|
| `nop.yaml.in` | None | No-op for testing the pipeline |

### Profile Configuration Styles

Each harness uses a different YAML schema for its profiles:

**inference-perf** -- Hierarchical configuration with `load`, `api`, `server`, `tokenizer`, `data`, `report`, and `storage` sections:
```yaml
load:
  type: constant
  stages:
  - rate: 1
    duration: 30
api:
  type: completion
  streaming: true
server:
  type: vllm
  model_name: REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL
  base_url: REPLACE_ENV_LLMDBENCH_HARNESS_STACK_ENDPOINT_URL
data:
  type: random
  input_distribution:
    min: 10
    max: 100
    mean: 50
    std_dev: 10
    total_count: 100
```

**guidellm** -- Flat key-value format with rate and data parameters:
```yaml
target: REPLACE_ENV_LLMDBENCH_HARNESS_STACK_ENDPOINT_URL
model: REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL
request_type: text_completions
profile: constant
rate: 1
max_seconds: 30
```

**vllm-benchmark** -- Executable-based config mapping to CLI arguments:
```yaml
executable: benchmark_serving.py
model: REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL
base-url: REPLACE_ENV_LLMDBENCH_HARNESS_STACK_ENDPOINT_URL
dataset-name: random
random-input-len: 10000
random-output-len: 1000
max-concurrency: 1
```

---

## Experiments and Treatments

Experiments control **how many times** and **with what parameter variations** a benchmark runs. The experiment system follows **Design of Experiments (DoE)** principles, providing structured, reproducible experiment definitions with clearly documented factors, levels, constants, and treatments.

Pre-built experiment files live in `workload/experiments/`. You can also create custom experiments for ad-hoc sweeps.

### Design of Experiments (DoE) Concepts

Each experiment follows DoE terminology:

| Term | Definition | Example |
|------|-----------|---------|
| **Factor** | An independent variable being varied | `question_len`, `max-concurrency` |
| **Level** | A specific value a factor can take | 100, 300, 1000 tokens |
| **Constant** | A variable held fixed across all treatments | `api.streaming: true` |
| **Treatment** | A specific combination of factor levels | qlen100-olen300 (question_len=100, output_len=300) |
| **Response variable** | What is measured | TTFT, throughput, p99 latency |
| **Design type** | How treatments are selected from the factor space | Full factorial, proportional scaling |

Experiments have two dimensions:

- **Setup treatments** change the *infrastructure* (scheduling plugin, replica count, cache size). Each setup treatment triggers a full standup → run → teardown cycle with different config overrides.
- **Run treatments** change the *workload* (prompt length, concurrency, output length). Multiple run treatments execute against a single stood-up stack.

The `experiment` command automates the full setup × run matrix. For run-only sweeps against an existing stack, use `run --experiments`.

### Default (No Experiments)

When no `--experiments` or `--overrides` flags are provided:

- A single benchmark run is executed
- The profile is rendered as-is (only `REPLACE_ENV_*` substitution)
- One experiment ID is generated: `{harness_name}-{timestamp}-{random}`
- `parallelism` pods are deployed (default: 1)

```bash
llmdbenchmark --spec gpu run
```

**What gets created:**
- 1 rendered profile (e.g. `sanity_random.yaml`)
- 1 experiment ID (e.g. `inference-perf-1710000000-ab1234`)
- 1 harness pod (or N pods if `--parallelism N`)

### Single Override (--overrides)

The `--overrides` flag applies key=value changes to the rendered profile before deployment. This creates a single treatment named "override".

```bash
llmdbenchmark --spec gpu run --overrides "load.stages[0].rate=10,load.stages[0].duration=60"
```

Overrides support dotted key paths for nested YAML values. Values are auto-coerced to the appropriate type (int, float, bool, or string).

**What gets created:**
- 1 rendered profile (e.g. `sanity_random-override.yaml`) with the overrides applied
- 1 experiment ID (e.g. `inference-perf-override-1710000000-ab1234`)
- 1 harness pod (or N pods if `--parallelism N`)

### Multi-Treatment Experiments (--experiments)

The `--experiments` flag points to a YAML file that defines multiple **treatments**. Each treatment gets its own rendered profile, experiment ID, and pod deployment.

```bash
llmdbenchmark --spec inference-scheduling run \
  --harness inference-perf \
  --workload shared_prefix_synthetic.yaml \
  --experiments workload/experiments/inference-scheduling.yaml
```

**What gets created** (for the 9-treatment inference-scheduling experiment):
- 9 rendered profiles: `shared_prefix_synthetic-qlen100-olen100.yaml`, etc.
- 9 experiment IDs: `inference-perf-qlen100-olen100-{ts}-{rand}`, etc.
- 9 harness pods (one per treatment)
- Results collected into 9 separate directories under `workspace/results/`

### Experiment File Format

Experiment files are standalone YAML files in `workload/experiments/`. They contain up to four sections:

1. **DoE metadata** (`experiment`, `design`) -- informational, documents the experimental design
2. **Setup treatments** (`setup`) -- consumed by the `experiment` command orchestrator (optional)
3. **Run treatments** (`treatments`) -- consumed by step_04 render_profiles

The `setup` section is optional. When absent, the file works with `run --experiments` for run-only sweeps. When present, the `experiment` command reads it to drive the standup → run → teardown loop.

**Run-only experiment** (no setup section):

```yaml
experiment:
  name: my-experiment
  harness: inference-perf
  profile: shared_prefix_synthetic.yaml

design:
  type: full_factorial
  run:
    factors:
      - name: question_len
        key: data.shared_prefix.question_len
        levels: [100, 300, 1000]
      - name: output_len
        key: data.shared_prefix.output_len
        levels: [100, 300, 1000]

treatments:
  - name: qlen100-olen100
    data.shared_prefix.question_len: 100
    data.shared_prefix.output_len: 100
  - name: qlen100-olen300
    data.shared_prefix.question_len: 100
    data.shared_prefix.output_len: 300
```

**Full experiment with setup treatments:**

```yaml
experiment:
  name: tiered-prefix-cache
  harness: inference-perf
  profile: shared_prefix_synthetic.yaml

design:
  type: full_factorial
  setup:
    factors:
      - name: numCpuBlocks
        key: vllmCommon.flags.numCpuBlocks
        levels: [500, 1000, 2000, 5000]
    constants:
      - key: model.maxModelLen
        value: 16000
  run:
    factors:
      - name: num_groups
        key: data.shared_prefix.num_groups
        levels: [40, 60]
  total_setup_treatments: 4
  total_run_treatments: 6
  total_matrix: 24

# Setup treatments -- consumed by the experiment orchestrator.
# Each triggers standup → run → teardown with these config overrides.
setup:
  constants:
    model.maxModelLen: 16000
    model.blockSize: 64
  treatments:
    - name: cpu-blocks-500
      vllmCommon.flags.numCpuBlocks: 500
    - name: cpu-blocks-1000
      vllmCommon.flags.numCpuBlocks: 1000

# Run treatments -- consumed by step_04 (same as run-only experiments)
treatments:
  - name: grp40-splen8k
    data.shared_prefix.num_groups: 40
    data.shared_prefix.system_prompt_len: 8000
  - name: grp60-splen1k
    data.shared_prefix.num_groups: 60
    data.shared_prefix.system_prompt_len: 1000
```

**Setup section keys:**
- `setup.constants` -- merged into every setup treatment's overrides (base values)
- `setup.treatments[].name` -- identifier for the treatment
- All other keys in a setup treatment are **config overrides** -- dotted key paths applied to the plan config via deep merge (e.g. `vllmCommon.flags.numCpuBlocks: 500`)

**Treatment keys:**
- `name` (required) -- identifier used in the experiment ID and rendered profile filename
- All other keys are **profile overrides** -- dotted key paths applied to the base profile after `REPLACE_ENV_*` substitution

**Treatment naming convention:**
Treatment names are abbreviated factor values joined by hyphens. The abbreviation should be readable without a lookup table — use enough of the factor name that someone scanning `kubectl get pods` or result directories can tell what the treatment is:

| Factor | Abbreviation | Example names |
|--------|-------------|---------------|
| `question_len` | `qlen` | `qlen100`, `qlen1000` |
| `output_len` | `olen` | `olen300` |
| `num_groups` | `grp` | `grp40`, `grp60` |
| `system_prompt_len` | `splen` | `splen8k`, `splen1k` |
| `max-concurrency` | `conc` | `conc1`, `conc256` |

Use `k` suffix for thousands (e.g. `splen8k` = 8000 tokens). Combined examples: `qlen100-olen300`, `grp40-splen8k`, `conc128`.

**Constants key** (optional):
- A top-level `constants` dict of key-value pairs merged into every treatment before treatment-specific overrides are applied
- Useful for DoE control variables that must be uniform across all treatments
- Treatment-specific values can still override a constant if needed

**How treatments flow through the pipeline:**

```
  --experiments file.yaml
            |
  +---------v----------+     +---------v----------+
  | step_04: render    |     | step_06: deploy    |
  |   profiles         |     |   harness          |
  |                    | --> |                    |
  | For each treatment:|     | For each treatment |
  |  1. Merge constants|     |  x parallelism:    |
  |  2. Render base    |     |  1. Generate       |
  |     profile with   |     |     experiment ID  |
  |     REPLACE_ENV_*  |     |  2. Render pod     |
  |  3. Apply overrides|     |     template       |
  |  4. Write to       |     |  3. kubectl apply  |
  |     {profile}-     |     +--------------------+
  |     {treatment}.   |
  |     yaml           |
  +--------------------+
```

### Treatment x Parallelism Matrix

When combined with `--parallelism`, each treatment spawns multiple identical pods. For example, 3 treatments with `--parallelism 2` creates **6 pods** total:

```
treatment: low-rate     x  parallelism: 1  ->  pod: inference-perf-xxxxx
treatment: low-rate     x  parallelism: 2  ->  pod: inference-perf-yyyyy
treatment: medium-rate  x  parallelism: 1  ->  pod: inference-perf-zzzzz
treatment: medium-rate  x  parallelism: 2  ->  pod: inference-perf-aaaaa
treatment: high-rate    x  parallelism: 1  ->  pod: inference-perf-bbbbb
treatment: high-rate    x  parallelism: 2  ->  pod: inference-perf-ccccc
```

All pods within the same treatment share the same experiment ID (they run the same workload in parallel). This is useful for saturating a model-serving endpoint with concurrent load from multiple harness pods.

**Pod count formula:** `total_pods = len(treatments) * parallelism`

---

## Available Experiments

Pre-built experiment files are in `workload/experiments/`. Each file is self-contained with DoE metadata, setup requirements, and executable treatments.

Each experiment can be run in three ways:

- **Full DoE experiment** (`experiment` command) -- for each setup treatment, stands up the stack with config overrides, runs all run treatments, and tears down. Writes `experiment-summary.yaml` at the end.
- **Full pipeline** (`standup run teardown`) -- stands up a single stack, runs, and tears down. Use for a single setup configuration.
- **Run-only** (`run --experiments`) -- targets an already-running endpoint. You must provide `--endpoint-url`, `--model`, `--namespace`, `--harness`, and `--workload` explicitly.

### inference-scheduling

**File:** `workload/experiments/inference-scheduling.yaml`

Evaluates how different prompt and output token lengths affect inference latency and throughput with shared-prefix workloads. Designed for comparing scheduling strategies (no optimization, prefix-aware, KV-aware, queue-based).

| Property | Value |
|----------|-------|
| Harness | `inference-perf` |
| Profile | `shared_prefix_synthetic.yaml` |
| Design type | Full factorial |
| Run treatments | 9 |

**Factors:**

| Factor | Profile key | Levels | Unit |
|--------|------------|--------|------|
| question_len | `data.shared_prefix.question_len` | 100, 300, 1000 | tokens |
| output_len | `data.shared_prefix.output_len` | 100, 300, 1000 | tokens |

**Treatments:** 3 x 3 = 9 (full factorial: `qlen100-olen100`, `qlen100-olen300`, `qlen100-olen1000`, `qlen300-olen100`, `qlen300-olen300`, `qlen300-olen1000`, `qlen1000-olen100`, `qlen1000-olen300`, `qlen1000-olen1000`)

**Setup treatments:** 4 scheduling plugin configurations (`inf-sche-none`, `inf-sche-prefix`, `inf-sche-kv`, `inf-sche-queue`). Total matrix: 4 setup x 9 run = 36 runs.

**Full DoE experiment** (automated setup × run matrix):
```bash
llmdbenchmark --spec inference-scheduling experiment \
  --experiments workload/experiments/inference-scheduling.yaml
```

**Single setup, all run treatments:**
```bash
llmdbenchmark --spec inference-scheduling standup run teardown \
  --experiments workload/experiments/inference-scheduling.yaml
```

**Run-only** (against an existing endpoint):
```bash
llmdbenchmark --spec inference-scheduling run \
  --endpoint-url http://10.131.0.42:80 \
  --model Qwen/Qwen3-32B \
  --namespace my-namespace \
  --harness inference-perf \
  --workload shared_prefix_synthetic.yaml \
  --experiments workload/experiments/inference-scheduling.yaml
```

### tiered-prefix-cache

**File:** `workload/experiments/tiered-prefix-cache.yaml`

Evaluates how prefix group count and system prompt length affect performance under tiered (CPU-offloaded) prefix caching. Measures cache utilization and latency as the working set changes relative to the cache tier size.

| Property | Value |
|----------|-------|
| Harness | `inference-perf` |
| Profile | `shared_prefix_synthetic.yaml` |
| Design type | Full factorial |
| Run treatments | 6 |

**Factors:**

| Factor | Profile key | Levels | Description |
|--------|------------|--------|-------------|
| num_groups | `data.shared_prefix.num_groups` | 40, 60 | Prefix groups competing for cache |
| system_prompt_len | `data.shared_prefix.system_prompt_len` | 8000, 5000, 1000 | Shared prompt length (tokens) |

**Treatments:** 2 x 3 = 6 (full factorial: `grp40-splen8k`, `grp40-splen5k`, `grp40-splen1k`, `grp60-splen8k`, `grp60-splen5k`, `grp60-splen1k`)

**Setup treatments:** 4 CPU block configurations (`numCpuBlocks`: 500, 1000, 2000, 5000). Setup constants: `model.maxModelLen=16000`, `model.blockSize=64`. Total matrix: 4 setup x 6 run = 24 runs.

**Full DoE experiment:**
```bash
llmdbenchmark --spec tiered-prefix-cache experiment \
  --experiments workload/experiments/tiered-prefix-cache.yaml
```

**Run-only:**
```bash
llmdbenchmark --spec tiered-prefix-cache run \
  --endpoint-url http://10.131.0.42:80 \
  --model Qwen/Qwen3-32B \
  --namespace my-namespace \
  --harness inference-perf \
  --workload shared_prefix_synthetic.yaml \
  --experiments workload/experiments/tiered-prefix-cache.yaml
```

### precise-prefix-cache-aware

**File:** `workload/experiments/precise-prefix-cache-aware.yaml`

Evaluates how prefix group count and system prompt length affect performance under different prefix-cache-aware routing strategies. Measures how well each routing plugin steers requests to replicas that already have the relevant prefix cached.

| Property | Value |
|----------|-------|
| Harness | `inference-perf` |
| Profile | `shared_prefix_synthetic.yaml` |
| Design type | Full factorial |
| Run treatments | 6 |

**Factors:** Same as tiered-prefix-cache (`num_groups` x `system_prompt_len`).

**Treatments:** 2 x 3 = 6 (full factorial, same treatment names as tiered-prefix-cache)

**Setup treatments:** 3 routing plugin configurations (`default`, `prefix-cache-estimate-config`, `prefix-cache-tracking-config`). Setup constants: `model.maxModelLen=16000`, `model.blockSize=64`. Total matrix: 3 setup x 6 run = 18 runs.

**Full DoE experiment:**
```bash
llmdbenchmark --spec precise-prefix-cache-aware experiment \
  --experiments workload/experiments/precise-prefix-cache-aware.yaml
```

**Run-only:**
```bash
llmdbenchmark --spec precise-prefix-cache-aware run \
  --endpoint-url http://10.131.0.42:80 \
  --model Qwen/Qwen3-32B \
  --namespace my-namespace \
  --harness inference-perf \
  --workload shared_prefix_synthetic.yaml \
  --experiments workload/experiments/precise-prefix-cache-aware.yaml
```

### pd-disaggregation

**File:** `workload/experiments/pd-disaggregation.yaml`

Measures how a disaggregated prefill-decode architecture handles increasing concurrency. Concurrency and prompt count scale proportionally (1:10 ratio) to keep per-worker load constant while increasing system pressure, producing a load curve from idle through saturation.

| Property | Value |
|----------|-------|
| Harness | `vllm-benchmark` |
| Profile | `random_concurrent.yaml` |
| Design type | Proportional scaling |
| Run treatments | 6 |

**Factors:**

| Factor | Profile key | Levels | Description |
|--------|------------|--------|-------------|
| max-concurrency | `max-concurrency` | 1, 8, 32, 64, 128, 256 | Concurrent in-flight requests |
| num-prompts | `num-prompts` | 10, 80, 320, 640, 1280, 2560 | Total prompts (= concurrency x 10) |

**Factor relationship:** `num-prompts = max-concurrency * 10`. This proportional scaling isolates the effect of concurrency on throughput and tail latency.

**Treatments:** 6 proportional pairs (`conc1`, `conc8`, `conc32`, `conc64`, `conc128`, `conc256`)

**Setup treatments:** 9 stack topologies (6 modelservice + 3 standalone) varying deploy method, decode/prefill replicas, and tensor parallelism. Total matrix: 9 setup x 6 run = 54 runs.

**Full DoE experiment:**
```bash
llmdbenchmark --spec pd-disaggregation experiment \
  --experiments workload/experiments/pd-disaggregation.yaml
```

**Run-only:**
```bash
llmdbenchmark --spec pd-disaggregation run \
  --endpoint-url http://10.131.0.42:80 \
  --model meta-llama/Llama-3.1-8B-Instruct \
  --namespace my-namespace \
  --harness vllm-benchmark \
  --workload random_concurrent.yaml \
  --experiments workload/experiments/pd-disaggregation.yaml
```

### Writing Custom Experiments

To create a custom experiment, follow this structure:

1. **Identify factors** -- which profile parameters will you vary?
2. **Choose levels** -- what values will each factor take?
3. **Decide on design type** -- full factorial (all combinations), fractional, or proportional?
4. **Identify constants** -- what parameters are held fixed?
5. **Generate treatments** -- enumerate the factor-level combinations

```yaml
experiment:
  name: my-rate-sweep
  description: Sweep request rate to find saturation point
  harness: inference-perf
  profile: sanity_random.yaml

design:
  type: single_factor
  factors:
    - name: request_rate
      key: load.stages.0.rate
      levels: [1, 5, 10, 20, 50]
      unit: req/s

treatments:
  - name: rate-1
    load.stages.0.rate: 1
  - name: rate-5
    load.stages.0.rate: 5
  - name: rate-10
    load.stages.0.rate: 10
  - name: rate-20
    load.stages.0.rate: 20
  - name: rate-50
    load.stages.0.rate: 50
```

Save to `workload/experiments/my-rate-sweep.yaml` and run:

```bash
llmdbenchmark --spec gpu run \
  --harness inference-perf \
  --workload sanity_random.yaml \
  --experiments workload/experiments/my-rate-sweep.yaml
```

---

## Run Modes

### Full Pipeline Run

Runs the benchmark as part of the full standup-run-teardown pipeline:

```bash
llmdbenchmark --spec gpu standup run teardown
```

Steps 00-10 execute in sequence. The endpoint is auto-detected from the stood-up stack.

### Run-Only Mode

Targets an already-running model-serving endpoint without requiring a prior standup:

```bash
llmdbenchmark --spec gpu run \
  --endpoint-url http://10.0.0.1:80 \
  --model facebook/opt-125m \
  --harness inference-perf \
  --workload sanity_random.yaml \
  --namespace my-namespace
```

Step 02 (detect_endpoint) uses the provided URL directly instead of querying Kubernetes services.

#### Finding the Endpoint of a Running Stack

If you stood up a stack earlier (or someone else did) and need the endpoint URL for run-only mode, use these kubectl commands:

**Standalone deployment** (vLLM pod with a Service):

```bash
# Find the service created by llmdbenchmark standup
kubectl get service -l stood-up-from=llm-d-benchmark -n <NAMESPACE>

# Get the ClusterIP and port
kubectl get service -l stood-up-from=llm-d-benchmark -n <NAMESPACE> \
  -o jsonpath='http://{.items[0].spec.clusterIP}:{.items[0].spec.ports[0].port}'
```

**ModelService deployment** (Gateway + InferencePool):

```bash
# Find the gateway address
kubectl get gateway infra-llmdbench-inference-gateway -n <NAMESPACE> \
  -o jsonpath='{.status.addresses[0].value}'

# The port is typically 80 (HTTP) or 443 (HTTPS)
# Check for HTTPS by looking at the gateway listeners:
kubectl get gateway infra-llmdbench-inference-gateway -n <NAMESPACE> -o json \
  | jq '.spec.listeners[].name'
# If any listener is named "https", use port 443; otherwise port 80
```

**Quick model check** (verify the endpoint is serving):

```bash
# Replace HOST:PORT with the values from above
curl http://<HOST>:<PORT>/v1/models
```

**OpenShift route** (if deployed on OpenShift):

```bash
kubectl get route llmdbench-inference-gateway-route -n <NAMESPACE> \
  -o jsonpath='https://{.spec.host}'
```

Then use the discovered URL:

```bash
llmdbenchmark --spec gpu run \
  --endpoint-url http://10.131.0.42:80 \
  --model meta-llama/Llama-3.1-8B \
  --namespace my-namespace \
  --harness inference-perf \
  --workload sanity_random.yaml
```

### Skip Mode (Result Collection)

Skips the benchmark execution and only collects results from a previous run:

```bash
llmdbenchmark --spec gpu run --skip
```

Steps 00-07 are skipped. Only steps 08 (collect_results), 09 (upload_results), and 10 (cleanup) execute. This is useful when pods completed overnight or when re-collecting results.

### Debug Mode

Deploys harness pods with `sleep infinity` instead of the benchmark command. Pods stay running indefinitely for manual inspection:

```bash
llmdbenchmark --spec gpu run --debug
```

You can then `kubectl exec` into the pod to run benchmarks manually. Step 10 (cleanup) is skipped in debug mode to keep pods alive.

### Dry-Run Mode

Logs all commands without touching the cluster:

```bash
llmdbenchmark --spec gpu run --dry-run
```

Every step prints what it *would* do without creating any Kubernetes resources.

---

## CLI Reference

### `experiment` subcommand

The `experiment` command automates the full setup × run treatment matrix:

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--experiments` | `-e` | string | (required) | Experiment YAML with `setup` and `treatments` sections |
| `--namespace` | `-p` | string | (from plan) | Kubernetes namespace |
| `--methods` | `-t` | string | (from plan) | Deploy method |
| `--models` | `-m` | string | (from plan) | Models to deploy |
| `--kubeconfig` | `-k` | string | (from env) | Kubeconfig path |
| `--parallel` | | int | `4` | Max parallel stacks |
| `--monitoring` | `-f` | flag | false | Enable metrics scraping |
| `--harness` | `-l` | string | (from experiment) | Harness name |
| `--workload` | `-w` | string | (from experiment) | Profile name |
| `--stop-on-error` | | flag | false | Abort on first setup treatment failure |
| `--skip-teardown` | | flag | false | Leave stacks running for debugging |

### `run` subcommand

All flags for the `run` subcommand:

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--model` | `-m` | string | (from plan) | Override model name |
| `--harness` | `-l` | string | `inference-perf` | Harness name |
| `--workload` | `-w` | string | `sanity_random.yaml` | Profile template filename |
| `--experiments` | `-e` | string | (none) | Path to experiments YAML file |
| `--overrides` | `-o` | string | (none) | Comma-separated key=value overrides |
| `--output` | `-r` | string | `local` | Output destination (`local`, `gs://...`, `s3://...`) |
| `--parallelism` | `-j` | int | `1` | Number of parallel pods per treatment |
| `--wait-timeout` | | int | `3600` | Max seconds to wait for pod completion |
| `--debug` | `-d` | flag | false | Deploy with `sleep infinity` |
| `--skip` | `-z` | flag | false | Skip execution, only collect results |
| `--endpoint-url` | `-U` | string | (auto-detected) | Direct endpoint URL (run-only mode) |
| `--dataset` | `-x` | string | (none) | Dataset URL/path override |
| `--step` | | string | (all) | Run specific step(s) only (e.g. `--step 4-6`) |
| `--dry-run` | `-n` | flag | false | Log commands without executing |
| `--namespace` | `-p` | string | (from plan) | Kubernetes namespace |

---

## Adding a New Harness

Adding a new benchmark tool requires **only file additions** -- no Python code changes. The pipeline auto-discovers harness scripts and profiles from the filesystem.

### Checklist

- [ ] Create entry-point script in `workload/harnesses/`
- [ ] Create profile directory in `workload/profiles/{name}/`
- [ ] Create at least one profile template (`.yaml.in`)
- [ ] Make the script executable (`chmod +x`)
- [ ] (Optional) Update `defaults.yaml` to set as new default harness
- [ ] (Optional) Install the benchmark tool in the container image

### Step 1: Create the Entry-Point Script

Create `workload/harnesses/{name}-llm-d-benchmark.sh`:

```bash
#!/usr/bin/env bash

# Create results directory
echo "Using experiment result dir: $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
mkdir -p "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"

# Read the profile from the ConfigMap mount
PROFILE="${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/{name}/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME}"

# Run your benchmark tool
start=$(date +%s.%N)
your-benchmark-tool --config "$PROFILE" \
  > >(tee -a "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log") \
  2> >(tee -a "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log" >&2)
RC=$?
stop=$(date +%s.%N)

# Capture timing metadata
export LLMDBENCH_HARNESS_START=$(date -d "@${start}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_STOP=$(date -d "@${stop}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_DELTA=PT$(echo "$stop - $start" | bc)S

exit $RC
```

The naming convention is `{harness_name}-{executable}`. The default executable is `llm-d-benchmark.sh`, configured via `harness.executable` in `defaults.yaml`.

### Step 2: Create Profile Templates

Create `workload/profiles/{name}/` and add at least a `sanity_random.yaml.in`:

```yaml
# workload/profiles/my-tool/sanity_random.yaml.in
#
# Use REPLACE_ENV_* tokens for values injected at runtime.
# See PROFILE_TOKENS in llmdbenchmark/utilities/profile_renderer.py
# for the full token registry.

model: REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL
endpoint: REPLACE_ENV_LLMDBENCH_HARNESS_STACK_ENDPOINT_URL
# ... your tool-specific configuration
```

The profile format is entirely tool-specific -- whatever YAML your benchmark tool expects. The only requirement is using `REPLACE_ENV_*` tokens for values that change per stack/run.

### Step 3: Make Executable

```bash
chmod +x workload/harnesses/{name}-llm-d-benchmark.sh
```

### Step 4: Test

```bash
# Sanity check with dry-run first
llmdbenchmark --spec gpu run --harness {name} --workload sanity_random.yaml --dry-run

# Real run
llmdbenchmark --spec gpu run --harness {name} --workload sanity_random.yaml
```

### How Auto-Discovery Works

No Python code changes are needed because:

- **Step 05** packages *all* files in `workload/harnesses/` into the `llmdbench-harness-scripts` ConfigMap automatically
- **Step 04** looks for profiles in `workload/profiles/{harness_name}/` based on the `--harness` flag
- **Step 06** constructs the pod command as `/workspace/harnesses/{harness_name}-{executable}` using the harness name and `harness.executable` from config

### (Optional) Set as Default Harness

To make your harness the default instead of `inference-perf`, update `config/templates/values/defaults.yaml`:

```yaml
harness:
  name: my-tool                  # ← your harness name
  profile: sanity_random.yaml    # ← default profile
  executable: llm-d-benchmark.sh # ← script suffix (usually unchanged)
```

Or override per-scenario in a scenario file, or at runtime with `--harness my-tool`.

### (Optional) Adding a New REPLACE_ENV Token

If your harness needs a runtime value that doesn't exist yet:

1. Add the token to `PROFILE_TOKENS` in `llmdbenchmark/utilities/profile_renderer.py`:

   ```python
   PROFILE_TOKENS: dict[str, TokenDef] = {
       # ... existing tokens ...
       "LLMDBENCH_MY_CUSTOM_VALUE": TokenDef(
           config_path="mySection.myKey",  # dotted path into defaults.yaml
           description="Description of what this value is",
       ),
   }
   ```

2. Use `REPLACE_ENV_LLMDBENCH_MY_CUSTOM_VALUE` in your profile templates.

3. If the value has no defaults.yaml counterpart (runtime-only), set `config_path=None` and pass it via `runtime_values` in step_04.

---

## Adding a New Profile

To add a new workload profile for an existing harness:

1. Create a `.yaml.in` file in `workload/profiles/{harness_name}/`:

   ```yaml
   # Example: workload/profiles/inference-perf/my_custom_workload.yaml.in
   load:
     type: constant
     stages:
     - rate: 100
       duration: 300
   api:
     type: completion
     streaming: true
   server:
     type: vllm
     model_name: REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL
     base_url: REPLACE_ENV_LLMDBENCH_HARNESS_STACK_ENDPOINT_URL
   data:
     type: random
     input_distribution:
       min: 100
       max: 1000
       mean: 500
       std_dev: 100
       total_count: 10000
     output_distribution:
       min: 50
       max: 500
       mean: 200
       std_dev: 50
       total_count: 10000
   ```

2. Run with `--workload my_custom_workload.yaml`:

   ```bash
   llmdbenchmark --spec gpu run --workload my_custom_workload.yaml
   ```

   The `.in` suffix is automatically stripped; you reference profiles by their rendered filename.

3. For experiment sweeps, use `--experiments` to vary parameters across treatments without creating separate profile files.
