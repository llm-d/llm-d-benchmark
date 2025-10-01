## Concept
A `yaml` file which contains a list of `standup` and `run` parameters of interest, termed `factors` and a list of values of interest, termed `levels` for each one of the `factors`. The each set of values for each factor produces a list of combinations termed `treatments`. These concepts and nomenclature follow the "Design of Experiments" (DOE) approach, and it allows a systematic and reproducible investigation on how different parameters affect the overall performance of a stack.

## Motivation
While the triplet `<scenario>`,`<harness>`,`<(workload) profile>`, contains all information required for the `llm-d-benchmark` to be able to carry out a `standup`->`run`->`teardown` [lifecycle](lifecycle.md), in order to compare and validate the performance of different stacks, a large number of parameters on `llm-d` must be swept. Hence, the need for an automated mechanism to loop through this (potentially) large parameter space.

## Use
An experiment file has to be manually crafted as a `yaml`. Once crafted, it can used by the `e2e.sh` executable. It access is controlled by the following parameters:

> [!NOTE]
> `./e2e.sh` (executable which **combines** `./setup/standup.sh`, `run.sh` and `setup/teardown.sh`) is the only one that can have an experiment file supplied to it.


| Variable                                     | Meaning                                        | Note                                                  |
| -------------------------------------------- | ---------------------------------------------- | ----------------------------------------------------- |
| LLMDBENCH_HARNESS_EXPERIMENT_TREATMENTS      | `yaml` file containing an experiment description | Can be overriden with CLI parameter `-e/--experiments` |

> [!TIP]
> In case the full path is ommited for the experiment file (either by setting `LLMDBENCH_HARNESS_EXPERIMENT_TREATMENTS` or CLI parameter `-e/--experiments`, it is assumed that the file exists inside the `experiments` folder

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
./e2e.sh --scenario disaggregated_vs_llmd --experiments disaggregated_vs_llmd
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
./e2e.sh --scenario precise-prefix-cache-aware --experiments precise-prefix-cache-aware
```
