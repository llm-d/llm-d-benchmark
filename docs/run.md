## Concept
Use a specific harness to generate workloads against a stack serving a large language model, according to a specific workload profile. To this end, a new `pod`, `llmdbench-${LLMDBENCH_HARNESS_NAME}-launcher`, is created on the target cluster, with an associated `pvc` (by default `workload-pvc`) to store experimental data. Once the "launcher" `pod` completes its run - which will include data collection **and data analysis** - the experimental data is then extracted from the "workload-pvc" back to the experimenter's workstation.

## Metrics
For a discussion of candidate relevant metrics, please consult this [document](https://docs.google.com/document/d/1SpSp1E6moa4HSrJnS4x3NpLuj88sMXr2tbofKlzTZpk/edit?resourcekey=0-ob5dR-AJxLQ5SvPlA4rdsg&tab=t.0#heading=h.qmzyorj64um1)

| Category | Metric | Unit |
| ---------| ------- | ----- |
| Throughput | Output tokens / second | tokens / second |
| Throughput | Input tokens / second | tokens / second |
| Throughput | Requests / second | qps |
| Latency    | Time per output token (TPOT) | ms per output token |
| Latency    | Time to first token (TTFT) | ms |
| Latency    | Time per request (TTFT + TPOT * output length) | seconds per request |
| Latency    | Normalized time per output token (TTFT/output length +TPOT) aka NTPOT | ms per output token |
| Latency    | Inter Token Latency (ITL) - Time between decode tokens within a request | ms per output token |
| Correctness | Failure rate | queries |
| Experiment | Benchmark duration | seconds |

## Workloads
For a discussion of relevant workloads, please consult this [document](https://docs.google.com/document/d/1Ia0oRGnkPS8anB4g-_XPGnxfmOTOeqjJNb32Hlo_Tp0/edit?tab=t.0)

| Workload                               | Use Case            | ISL    | ISV   | OSL    | OSV    | OSP    | Latency   |
| -------------------------------------- | ------------------- | ------ | ----- | ------ | ------ | ------ | ----------|
| Interactive Chat                       | Chat agent          | Medium | High  | Medium | Medium | Medium | Per token |
| Classification of text                 | Sentiment analysis  | Medium |       | Short  | Low    | High   | Request   |
| Classification of images               | Nudity filter       | Long   | Low   | Short  | Low    | High   | Request   |
| Summarization / Information Retrieval  | Q&A from docs, RAG  | Long   | High  | Short  | Medium | Medium | Per token |
| Text generation                        |                     | Short  | High  | Long   | Medium | Low    | Per token |
| Translation                            |                     | Medium | High  | Medium | Medium | High   | Per token |
| Code completion                        | Type ahead          | Long   | High  | Short  | Medium | Medium | Request   |
| Code generation                        | Adding a feature    | Long   | High  | Medium | High   | Medium | Request   |

## Profiles
A list of pre-defined profiles, each specific to particular harness, can be found on subdirectories under `workloads/profiles`.

```
workload
 |- profiles
 |   |- guidellm
 |   |   |- sanity_concurrent.yaml.in
 |   |- nop
 |   |   |- nop.yaml.in
 |   |- inference-perf
 |   |   |- sanity_random.yaml.in
 |   |   |- summarization_synthetic.yaml.in
 |   |   |- chatbot_sharegpt.yaml.in
 |   |   |- shared_prefix_synthetic.yaml.in
 |   |   |- chatbot_synthetic.yaml.in
 |   |   |- code_completion_synthetic.yaml.in
 |   |- vllm-benchmark
 |   |   |- sanity_random.yaml.in
 |   |   |- random_concurrent.yaml.in
```
What is shown here are the workload profile **templates** (hence, the `yaml.in`) and for each template, parameters which are specific for a particular standup are automatically replaced to generate a `yaml`. This rendered workload profile is then stored as a `configmap` on the target `Kubernetes` cluster. An illustrative example follows (`inference-perf/sanity_random.yaml.in`) :

```
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
  ignore_eos: true
tokenizer:
  pretrained_model_name_or_path: REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL
data:
  type: random
  input_distribution:
    min: 10             # min length of the synthetic prompts
    max: 100            # max length of the synthetic prompts
    mean: 50            # mean length of the synthetic prompts
    std_dev: 10         # standard deviation of the length of the synthetic prompts
    total_count: 100    # total number of prompts to generate to fit the above mentioned distribution constraints
  output_distribution:
    min: 10             # min length of the output to be generated
    max: 100            # max length of the output to be generated
    mean: 50            # mean length of the output to be generated
    std_dev: 10         # standard deviation of the length of the output to be generated
    total_count: 100    # total number of output lengths to generate to fit the above mentioned distribution constraints
report:
  request_lifecycle:
    summary: true
    per_stage: true
    per_request: true
storage:
  local_storage:
    path: /workspace
```

Entries `REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL` and `REPLACE_ENV_LLMDBENCH_HARNESS_STACK_ENDPOINT_URL` will be automatically replaced with the current value of the environment variables `LLMDBENCH_DEPLOY_CURRENT_MODEL` and `LLMDBENCH_HARNESS_STACK_ENDPOINT_URL` respectively.

In addition to that, **any other parameter (on the workload profile) can be ovewritten** by setting a list of `<key>,<value>` as the contents of environment variable `LLMDBENCH_HARNESS_EXPERIMENT_PROFILE_OVERRIDES`.

Finally, new workload profiles can manually crafted and placed under the correct directory. Once crafted, these can then be used by the `llmdbenchmark run` command.

## Use
An invocation of `llmdbenchmark run` without any parameters will result in using all the already defined default values (consult the table below).

If a particular `llm-d` stack was stood up using a highly customized scenario file (e.g., with a different model name, specific `max_model_len`, specific network card), it should be included when invoking `llmdbenchmark run`. i.e., `llmdbenchmark run --spec <scenario>`

The command line parameters allow one to override even individual parameters on a particular workload profile. e.g., `llmdbenchmark run --spec <scenario> -l inference-perf -w sanity_random -o min=20,total_count=200`

> [!IMPORTANT]
> `llmdbenchmark run` can, and usually is, used against a stack which was deployed by other means (i.e., outside the `llmdbenchmark standup` in `llm-d-benchmark).

The following table displays a comprehensive list of environment variables (and corresponding command line parameters) which control the execution of `llmdbenchmark run`

> [!NOTE]
> Evidently, `llmdbenchmark experiment`, as the command that **combines** `llmdbenchmark standup`, `llmdbenchmark run` and `llmdbenchmark teardown` into a single operation can also consume the (workload) profile.

| Variable                                       | Meaning                                        | Note                                                |
| ---------------------------------------------  | ---------------------------------------------- | --------------------------------------------------- |
| LLMDBENCH_DEPLOY_SCENARIO                      | File containing multiple environment variables which will override defaults | If not specified, defaults to (empty) `none.yaml`. Can be overriden with CLI parameter `-c/--scenario` |
| LLMDBENCH_DEPLOY_MODEL_LIST                     | List (comma-separated values) of models to be run against | Default=`meta-llama/Llama-3.2-1B-Instruct`. Can be overriden with CLI parameter `-m/--models` |
| LLMDBENCH_VLLM_COMMON_NAMESPACE                | Namespace where the `llm-d` stack was stood up | Default=`llmdbench`. Can be overriden with CLI parameter `-p/--namespace` |
| LLMDBENCH_HARNESS_NAMESPACE                    | The `namespace` where the `pod` `llmdbench-${LLMDBENCH_HARNESS_NAME}-launcher` will be created | Default=`${LLMDBENCH_VLLM_COMMON_NAMESPACE}`. Can be overriden with CLI parameter `-p/--namespace`.|
| LLMDBENCH_DEPLOY_METHODS                       | List (comma-separated values) of standup methods | Default=`modelservice`. Can be overriden with CLI parameter `-t/--methods` |
| LLMDBENCH_HARNESS_PROFILE_HARNESS_LIST         | Lists all harnesses available to use           | Automatically populated by listing the directories under `workload/profiles` |
| LLMDBENCH_HARNESS_NAME                         | Specifies harness (load generator) to be used  | Default=`inference-perf`. Can be overriden with CLI parameter `-l/--harness`  |
| LLMDBENCH_HARNESS_EXPERIMENT_PROFILE           | Specifies workload to be used (by the harness) | Default=`sanity_random.yaml`. Can be overriden with CLI parameter `-w/--workload` |
| LLMDBENCH_HARNESS_EXPERIMENT_PROFILE_OVERRIDES | A list of key,value pairs overriding entries on the workload file | Default=(empty).Can be overriden with CLI parameter `-o/--overrides`|
| LLMDBENCH_HARNESS_EXECUTABLE                   | Name of the executable inside `llm-d-benchmark` container | default=`llm-d-benchmark.sh` (harness entrypoint script). Can be overriden for debug/experimentation |
| LLMDBENCH_HARNESS_CONDA_ENV_NAME               | Local conda environment name                   | Default=`${LLMDBENCH_HARNESS_NAME}-runner`. Only used when `LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY` is set to `1` (Default=`0`) |
| LLMDBENCH_HARNESS_WAIT_TIMEOUT                 | How long to wait for `pod` `llmdbench-${LLMDBENCH_HARNESS_NAME}-launcher` to complete its execution | Default=`3600`. Can be overriden with CLI parameter `-s/--wait |
| LLMDBENCH_HARNESS_CPU_NR                       | How many CPUs should be requested for `pod` `llmdbench-${LLMDBENCH_HARNESS_NAME}-launcher` | Default=`16` |
| LLMDBENCH_HARNESS_CPU_MEM                      | How many CPUs should be requested for `pod` `llmdbench-${LLMDBENCH_HARNESS_NAME}-launcher` | Default=`32Gi` |
| LLMDBENCH_HARNESS_PVC_NAME                     | The `pvc` where experimental results will be stored | Default=`workload-pvc`. Can be overriden with CLI parameter `-k/--pvc`      |
| LLMDBENCH_HARNESS_PVC_SIZE                     | The size of the `pvc` where experimental results will be stored | Default=`20Gi` |
| LLMDBENCH_HARNESS_SKIP_RUN                     | Skip the execution of the experiment, and only collect data already on the `pvc` | Default=(empty) |
| LLMDBENCH_HARNESS_LOAD_PARALLELISM             | Controls the number harness pods which will be created to generate load (all pods execute the same workload profile) | Default=`1`, can be overriden with ` -j/--parallelism` |
| LLMDBENCH_HARNESS_ENVVARS_TO_YAML              | List all environment variables to be added to all harness pods | Default=`LLMDBENCH_RUN_EXPERIMENT`, can be overriden with `-g/--envvarspod` |
| LLMDBENCH_HARNESS_DEBUG                        | Execute harness in "debug-mode" (i.e., `sleep infinity`) | Default=`0`.  Can be overriden with CLI parameter `-d/--debug`|

> [!TIP]
> In case the full path is ommited for the (workload) profile (either by setting `LLMDBENCH_HARNESS_EXPERIMENT_PROFILE` or CLI parameter `-w/--workload`), it is assumed that the file exists inside the `workload/profiles/<harness name>` folder


## Harnesses

### [inference-perf](https://github.com/kubernetes-sigs/inference-perf)

### [guidellm](https://github.com/vllm-project/guidellm.git)

### [vLLM benchmark](https://github.com/vllm-project/vllm/tree/main/benchmarks)

### Nop (No Op)

The `nop` harness, combined with environment variables and when using in `standalone` mode, will parse the vLLM log and create reports with
loading time statistics.

The additional environment variables to set are:

| Environment Variable                         | Example Values  |
| -------------------------------------------- | -------------- |
| LLMDBENCH_VLLM_COMMON_VLLM_LOAD_FORMAT   | `safetensors, tensorizer, runai_streamer, fastsafetensors` |
| LLMDBENCH_VLLM_COMMON_ENABLE_SLEEP_MODE  | `false, true` |
| LLMDBENCH_VLLM_COMMON_VLLM_LOGGING_LEVEL | `DEBUG, INFO, WARNING` etc |
| LLMDBENCH_VLLM_STANDALONE_PREPROCESS         | `source /setup/preprocess/standalone-preprocess.sh ; /setup/preprocess/standalone-preprocess.py` |

The variable `LMDBENCH_VLLM_COMMON_VLLM_LOGGING_LEVEL` must be set to `DEBUG` so that the `nop` categories report finds all categories.

The variable `LLMDBENCH_VLLM_COMMON_ENABLE_SLEEP_MODE` must be set to `true` in order to run sleep/wake benchmarks.

The variable `LLMDBENCH_VLLM_STANDALONE_PREPROCESS` must be set to the above value for the `nop` harness in order to install load format
dependencies, export additional environment variables and pre-serialize models when using the `tensorizer` load format.

The preprocess scripts will run in the vLLM standalone pod before the vLLM server starts.

## Local Analysis (`--analyze`)

The `--analyze` flag on `llmdbenchmark run` enables local analysis of collected results after the benchmark completes. When enabled, step 11 (`analyze_results`) runs the following analysis pipeline on the experimenter's workstation:

1. **Harness-native analysis** -- For `inference-perf`, invokes `inference-perf --analyze` on the collected results directory.
2. **Per-request distribution plots** -- Generates histograms and CDFs from `per_request_lifecycle_metrics.json` (see [Analysis](analysis.md#per-request-distribution-plots)).
3. **Cross-treatment comparison** -- If multiple treatments were executed, produces a CSV summary table and comparison charts (see [Analysis](analysis.md#cross-treatment-comparison)).
4. **Prometheus metric visualization** -- Generates time series plots from collected Prometheus scrapes (see [Analysis](analysis.md#prometheus-metric-visualization)).

```bash
# Run benchmark with local analysis
llmdbenchmark --spec gpu run -l inference-perf -w sanity_random.yaml --analyze

# Equivalent via environment variable
export LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY=1
llmdbenchmark --spec gpu run -l inference-perf -w sanity_random.yaml
```

Without `--analyze`, analysis is still performed **inside the harness container** (by the harness entrypoint script). The `--analyze` flag adds a second pass on the local machine after results have been collected, which is useful for generating plots that require matplotlib (not always available in the container image).

### `run --experiments` vs `experiment`

Both commands support parameter sweeps, but they differ in scope:

| | `run --experiments` | `experiment` |
|---|---|---|
| **Scope** | Varies **workload** parameters only (`run` treatments) | Varies **both** infrastructure (`setup` treatments) and workload (`run` treatments) |
| **Stack lifecycle** | Runs against a single, already-deployed stack | Stands up, benchmarks, and tears down a fresh stack per setup treatment |
| **Use case** | Sweep concurrency, prompt length, etc. against a fixed deployment | Compare replica counts, scheduler plugins, cache configs across deployments |
| **Command** | `llmdbenchmark run -e <experiment.yaml>` | `llmdbenchmark experiment -e <experiment.yaml>` |

## Run Summary Banner

After each run completes, a summary banner is printed to the CLI showing key run parameters and results:

```
============================================================
BENCHMARK RUN SUMMARY
============================================================
  Harness:       inference-perf
  Workload:      sanity_random.yaml
  Model:         meta-llama/Llama-3.1-8B
  Namespace:     llmdbench
  Mode:          full
  Parallelism:   1
  Treatments:    2
    - experiment_001
      [1/1] experiment_001_1 (12 files)
    - experiment_002
      [1/1] experiment_002_1 (12 files)
  Local results: /path/to/workspace/results
  PVC results:   oc exec -n llmdbench <pod> -- ls /requests/
============================================================
Run complete (mode=full, harness=inference-perf).
```

The banner provides at a glance: harness, workload, model, namespace, execution mode, parallelism level, per-treatment result file counts, and commands to access results both locally and on the PVC.

## Run Parameters ConfigMap

After each run, a ConfigMap named `llm-d-benchmark-run-parameters` is created (or updated) in the harness namespace. This provides an auditable record of every benchmark run executed against the namespace.

Each run appends a timestamped key (`run-<timestamp>`) to the ConfigMap data, plus a `latest` key that always points to the most recent run. The stored data includes:

- User and hostname of the experimenter
- Harness, workload, and model
- Namespace and endpoint URL
- Experiment IDs and PVC result paths
- Parallelism level and output destination

```bash
# View all run parameters stored in the namespace
kubectl get configmap llm-d-benchmark-run-parameters -n <namespace> -o yaml

# View just the latest run
kubectl get configmap llm-d-benchmark-run-parameters -n <namespace> \
  -o jsonpath='{.data.latest}'
```

This ConfigMap is not deleted during teardown, so it persists as a historical record across multiple standup/teardown cycles.

An additional container can be added to `standalone` mode that starts the inference launcher from https://github.com/llm-d-incubation/llm-d-fast-model-actuation/blob/main/inference_server/launcher/launcher.py

This launcher is contained in an image that also contains vLLM.

The environment variables to set are:

| Environment Variable                         | Example Values | |
| -------------------------------------------- | -------------- | -------------------------------------------------------------------------------- |
| LLMDBENCH_VLLM_STANDALONE_LAUNCHER           | `true, false`  | default is `false`, it will enable the launcher container |
| LLMDBENCH_VLLM_STANDALONE_LAUNCHER_PORT      |  8001 etc | default is 8001, the launcher will listen on this port |
| LLMDBENCH_VLLM_STANDALONE_LAUNCHER_VLLM_PORT |  8002 etc | default is 8002, the vLLM server started byt the launcher will wait on this port |

When using the launcher, the `nop` harness will create a report with both the standalone vLLM server and the launched vLLM server metrics.
The launcher image with vLLM will be used in both cases as well as all the env. variables to ensure they run under the same scenario.
