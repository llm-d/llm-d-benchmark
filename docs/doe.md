## Concept
A `yaml` file which contains a list of `standup` and `run` parameters of interest, termed `factors` and a list of values of interest, termed `levels` for each one of the `factors`. The each set of values for each factor produces a list of combinations termed `treatments`. These concepts and nomenclature follow the "Design of Experiments" (DOE) approach, and it allows a systematic and reproducible investigation on how different parameters affect the overall performance of a stack.

## Motivation
While the triplet `<scenario>`,`<harness>`,`<(workload) profile>`, contains all information required for the `llm-d-benchmark` to be able to carry out a `standup`->`run`->`teardown` [lifecycle](lifecycle.md), in order to compare and validate the performance of different stacks, a large number of parameters on `llm-d` must be swept. Hence, the need for an automated mechanism to loop through this (potentially) large parameter space.

## Use
An experiment file has to be manually crafted as a `yaml`. Once crafted, it can be used by the `llmdbenchmark experiment` command. Its access is controlled by the following parameters:

> [!NOTE]
> `llmdbenchmark experiment` (which **combines** `llmdbenchmark standup`, `llmdbenchmark run` and `llmdbenchmark teardown`) is the only command that can have an experiment file supplied to it.


| Variable                                     | Meaning                                        | Note                                                  |
| -------------------------------------------- | ---------------------------------------------- | ----------------------------------------------------- |
| LLMDBENCH_HARNESS_EXPERIMENT_TREATMENTS      | `yaml` file containing an experiment description | Can be overriden with CLI parameter `-e/--experiments` |

> [!TIP]
> In case the full path is ommited for the experiment file (either by setting `LLMDBENCH_HARNESS_EXPERIMENT_TREATMENTS` or CLI parameter `-e/--experiments`, it is assumed that the file exists inside the `experiments` folder

> [!NOTE]
> For detailed information on the experiment lifecycle, including how treatments are executed and results are collected, see [llmdbenchmark/experiment/README.md](../llmdbenchmark/experiment/README.md).

## Illustrative examples

1) Compare `standalone` vllm with `llm-d` in a stack with a variable number of `prefill` and `decode` `pods`. Each time a new combination is deployed, run a workload profile with varying `max-concurrecy` and `num-prompts`

> [!IMPORTANT]
> The harness - `vllm-benchmark` and (workload) `profile` (`random_concurrent`) are **not** defined here, but on the [scenario](standup.md#scenarios)

```
setup:
  factors:
    - LLMDBENCH_DEPLOY_METHODS
    - LLMDBENCH_VLLM_COMMON_REPLICAS
    - LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM
    - LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS
    - LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM
    - LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS
    - LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM
  levels:
    LLMDBENCH_VLLM_COMMON_REPLICAS: "2,4"
    LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM: "8"
    LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS: "2,4,6,8"
    LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM: "1,2"
    LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS: "1,2,4"
    LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM: "2,4,8"
  treatments:
    - "modelservice,NA,NA,6,2,1,4"
    - "modelservice,NA,NA,4,2,1,8"
    - "modelservice,NA,NA,8,1,1,8"
    - "modelservice,NA,NA,4,2,2,4"
    - "modelservice,NA,NA,4,2,4,2"
    - "modelservice,NA,NA,2,2,4,4"
    - "standalone,2,8,NA,NA,NA,NA"
    - "standalone,4,8,NA,NA,NA,NA"
run:
  factors:
    - max-concurrency
    - num-prompts
  levels:
    max-concurrency: "1,4,8,16,32,64,128,256,512,1024"
    num-prompts: "10,40,80,160,320,640,1280,2560,5120,10240"
  treatments:
    - "1,10"
    - "4,40"
    - "8,80"
    - "16,160"
    - "32,320"
    - "64,640"
    - "128,1280"
    - "256,2560"
    - "512,5120"
    - "1024,10240"
```

> [!NOTE]
> The `NA` ("Not Applicable") is used to explicitate stand up parameters not used by particular methods (e.g., `LLMDBENCH_VLLM_COMMON_REPLICAS` is not really used when standing up an `llm-d` stack via `modelservice`).

** This particular example can be used with the following command :

```
llmdbenchmark experiment --spec disaggregated_vs_llmd --experiments disaggregated_vs_llmd
```

2) Compare different parameters for GAIE (Gateway API Inference Extension), using a fixed set of `decode` `pods`. Once deployed, run a workload profile varying `num_groups` and `system_prompt_len`)

> [!IMPORTANT]
> The harness - `inference-perf` and (workload) `profile` (`shared_prefix_synthetic`) are **not** defined here, but on the [scenario](standup.md)

```
setup:
  factors:
    - LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE
  levels:
    LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE: "default,prefix-cache-estimate-config,prefix-cache-tracking-config"
  treatments:
    - "default"
    - "prefix-cache-estimate-config"
    - "prefix-cache-tracking-config"
run:
  factors:
    - num_groups
    - system_prompt_len
  levels:
    num_groups: "40,60"
    system_prompt_len: "80000,5000,1000"
  treatments:
    - "40,8000"
    - "60,5000"
    - "60,1000"
```

** This particular example can be used with the following command

```
llmdbenchmark experiment --spec precise-prefix-cache-aware --experiments precise-prefix-cache-aware
```

## Treatment Execution Lifecycle

The `experiment` command orchestrates a nested loop of setup and run treatments. Understanding this lifecycle is important for designing experiments and interpreting results.

### Setup Treatment Cycling

Each **setup treatment** represents a distinct infrastructure configuration. The experiment command cycles through setup treatments sequentially, performing the full standup/run/teardown lifecycle for each:

```
For each setup treatment:
    1. standup   -- Deploy the stack with this treatment's infrastructure parameters
    2. run       -- Execute ALL run treatments against this stack
    3. teardown  -- Tear down the stack before moving to the next setup treatment
```

For example, if you have 3 setup treatments (different replica counts) and 5 run treatments (different concurrency levels), the experiment executes:

```
Setup treatment 1 (replicas=2):
    standup → run treatment 1..5 → teardown
Setup treatment 2 (replicas=4):
    standup → run treatment 1..5 → teardown
Setup treatment 3 (replicas=8):
    standup → run treatment 1..5 → teardown
```

This produces 15 result sets total (3 setup x 5 run treatments).

### Run Treatments Within Each Cycle

Within a single setup treatment, run treatments are executed sequentially against the same deployed stack. Each run treatment applies its factor values as workload profile overrides (e.g., `max-concurrency=64, num-prompts=640`). The harness pod is deployed, executes the workload, collects results, and is cleaned up before the next run treatment begins.

### Step Ordering

During each lifecycle phase, steps execute in three partitions:

1. **Pre-global steps** -- Global steps that run before any per-stack work (e.g., preflight checks, cleanup of previous runs)
2. **Per-stack steps** -- Steps that operate on each stack individually (e.g., endpoint detection, profile rendering, harness deployment, result collection)
3. **Post-global steps** -- Global steps that run after all per-stack work completes (e.g., result upload, post-run cleanup, local analysis)

For the run phase specifically, steps 00-01 are pre-global, steps 02-08 are per-stack, and steps 09-11 are post-global. This ensures that operations like result upload and analysis happen only after all per-stack collection is complete.

## Parallelism Levels

The benchmark supports parallelism at three levels:

| Level | Flag | Description |
|-------|------|-------------|
| **Pod parallelism** | `-j/--parallelism` | Number of identical harness pods running the same workload profile concurrently. Useful for increasing aggregate load or reducing statistical variance. Each pod writes results to its own subdirectory (`<experiment_id>_1`, `<experiment_id>_2`, etc.). |
| **Treatment parallelism** | (sequential) | Run treatments within a setup cycle execute sequentially. Each treatment waits for the previous to complete before starting. |
| **Setup parallelism** | `--parallel N` | Maximum number of stacks to deploy in parallel during standalone `standup` (not used during `experiment`, which processes setup treatments sequentially to avoid resource contention). |

Pod parallelism is the most commonly used level. With `-j 4`, four harness pods are created simultaneously, each executing the same workload profile. Results from all pods are collected and can be analyzed together for statistical significance.
