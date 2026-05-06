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
📦 workload
 + 📂 profiles
 | + 📂 guidellm
 | | + 📜 sanity_concurrent.yaml.in
 | + 📂 nop
 | | + 📜 nop.yaml.in
 | + 📂 inference-perf
 | | + 📜 sanity_random.yaml.in
 | | + 📜 summarization_synthetic.yaml.in
 | | + 📜 chatbot_sharegpt.yaml.in
 | | + 📜 shared_prefix_synthetic.yaml.in
 | | + 📜 chatbot_synthetic.yaml.in
 | | + 📜 code_completion_synthetic.yaml.in
 | + 📂 vllm-benchmark
 | | + 📜 sanity_random.yaml.in
 | | + 📜 random_concurrent.yaml.in
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

Finally, new workload profiles can manually crafted and placed under the correct directory. Once crafted, these can then be used by the `run.sh` executable.

## Use
An invocation of `run.sh` without any parameters will result in using all the already defined default values (consult the table below).

If a particular `llm-d` stack was stood up using a highly customized scenario file (e.g., with a different model name, specific `max_model_len`, specific network card), it should be included when invoking `./run.sh`. i.e., `./run.sh -c <scenario>`

The command line parameters allow one to override even individual parameters on a particular workload profile. e.g., `./run.sh -c <scenario> -l inference-perf -w sanity_random -o min=20,total_count=200`

> [!IMPORTANT]
> `run.sh` can, and usually is, used against a stack which was deployed by other means (i.e., outside the `standup.sh` in `llm-d-benchmark).


The following table displays a comprehensive list of environment variables (and corresponding command line parameters) which control the execution of `./run.sh`

> [!NOTE]
> Evidently, `./e2e.sh`, as the executable that **combines** `./setup/standup.sh`, `run.sh` and `setup/teardown.sh` into a singe operation can also consume the (workload) profile.

| Variable                                       | Meaning                                        | Note                                                |
| ---------------------------------------------  | ---------------------------------------------- | --------------------------------------------------- |
| LLMDBENCH_DEPLOY_SCENARIO                      | File containing multiple environment variables which will override defaults | If not specified, defaults to (empty) `none.sh`. Can be overriden with CLI parameter `-c/--scenario` |
| LLMDBENCH_DEPLOY_MODEL_LIST                     | List (comma-separated values) of models to be run against | Default=`meta-llama/Llama-3.2-1B-Instruct`. Can be overriden with CLI parameter `-m/--models` |
| LLMDBENCH_VLLM_COMMON_NAMESPACE                | Namespace where the `llm-d` stack was stood up | Default=`llmdbench`. Can be overriden with CLI parameter `-p/--namespace` |
| LLMDBENCH_HARNESS_NAMESPACE                    | The `namespace` where the `pod` `llmdbench-${LLMDBENCH_HARNESS_NAME}-launcher` will be created | Default=`${LLMDBENCH_VLLM_COMMON_NAMESPACE}`. Can be overriden with CLI parameter `-p/--namespace`.|
| LLMDBENCH_DEPLOY_METHODS                       | List (comma-separated values) of standup methods | Default=`modelservice`. Can be overriden with CLI parameter `-t/--methods` |
| LLMDBENCH_HARNESS_PROFILE_HARNESS_LIST         | Lists all harnesses available to use           | Automatically populated by listing the directories under `workload/profiles` |
| LLMDBENCH_HARNESS_NAME                         | Specifies harness (load generator) to be used  | Default=`inference-perf`. Can be overriden with CLI parameter `-l/--harness`  |
| LLMDBENCH_HARNESS_EXPERIMENT_PROFILE           | Specifies workload to be used (by the harness) | Default=`sanity_random.yaml`. Can be overriden with CLI parameter `-w/--workload` |
| LLMDBENCH_HARNESS_EXPERIMENT_PROFILE_OVERRIDES | A list of key,value pairs overriding entries on the workload file | Default=(empty).Can be overriden with CLI parameter `-o/--overrides`|
| LLMDBENCH_HARNESS_EXECUTABLE                   | Name of the executable inside `llm-d-benchmark` container | default=`llm-d-benchmark.sh`. Can be overriden for debug/experimentation |
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

## Multi-Stack Runs

When a scenario defines more than one stack (e.g.
[`guides/multi-model-wva`](../config/scenarios/guides/multi-model-wva.yaml)),
every per-stack step in the `run` phase executes once per rendered stack -
endpoint detection, model verification, profile rendering, configmap creation,
harness deploy, wait, and collect. Each stack's results are collected into
its own experiment-ID-keyed subdirectory under the workspace.

**Per-stack endpoints.** For shared-HTTPRoute scenarios (`httpRoute.mode: shared`
in the scenario file), step 03 `detect_endpoint` bakes the stack's path prefix
into the detected URL - e.g. `http://gw:80/qwen3-06b` for stack `qwen3-06b`.
Every downstream step treats the endpoint as an opaque base URL, so:

- `test_model_serving` hits `http://gw:80/qwen3-06b/v1/models`.
- The harness pod env var `LLMDBENCH_HARNESS_STACK_ENDPOINT_URL` becomes
  `http://gw:80/qwen3-06b`; the harness script then calls
  `${endpoint_url}/v1/completions` which resolves to
  `http://gw:80/qwen3-06b/v1/completions`.
- The shared HTTPRoute rewrites `/qwen3-06b/*` -> `/*` so the upstream vLLM
  still sees `/v1/completions` and its friends.

Nothing in the harness scripts changes - the routing prefix is invisible to them.

**Parallelism knobs.** `--parallel N` (default 4) caps how many stacks the
executor runs per-stack steps for at once. Set `--parallel 1` to serialize
for easier debugging, especially when multi-stack harness pods compete for
the same accelerator nodes. (Note: the `smoketest` phase always runs stacks
sequentially regardless of `--parallel`, since interleaved `/health` and
`/v1/models` probes across stacks make failures harder to read.)

### Discovering deployed endpoints

After standup, `--list-endpoints` prints a table of per-stack routing URLs
and a copy-paste block of ready-to-run invocations - no harness pods
launched:

```bash
llmdbenchmark --spec guides/multi-model-wva run -p <namespace> --list-endpoints
```

Useful when you've forgotten the stack names, the gateway IP, or just want
a quick sanity-check that both pools resolved correctly. The flag runs
the full render pipeline (so the printed endpoints match exactly what a
real `standup` would produce) and then short-circuits before launching
any harness pods.

### Targeting a single pool (`--stack`)

`--stack NAME` (or comma-separated list) restricts run execution to one
stack. Endpoint URL auto-resolves for the selected stack - no need to
pass `--endpoint-url` manually:

```bash
# Benchmark qwen3-06b only with guidellm, two parallel harness pods
llmdbenchmark --spec guides/multi-model-wva run -p <namespace> \
  --stack qwen3-06b \
  -l guidellm -w sanity_random.yaml -j 2
```

`--stack` also works on `standup`, `smoketest`, and `teardown`. Unknown
names fail loudly with a list of valid ones. Available via
`LLMDBENCH_STACK` env var too.

### CLI overrides in multi-stack scenarios

| Flag | Multi-stack behavior |
|------|----------------------|
| `-p / --namespace` | Applies to every stack (namespaces are scenario-wide). |
| `-t / --methods` | Applies to every stack. |
| `--monitoring` | Applies to every stack. |
| `-u / --wva` | Applies to every stack. |
| `-l / --harness`, `-w / --workload`, `-o / --overrides` | Applies to every stack's harness pod - all stacks run the same workload. |
| `-j / --parallelism` | Applies to every stack - each stack launches N parallel harness pods. |
| `--endpoint-url` | Single endpoint for run-only mode; bypasses auto-detect. In multi-stack, only meaningful when combined with `--stack` (otherwise every stack targets the same endpoint, which is rarely desired). |
| `--stack NAME[,NAME...]` | Scopes every per-stack step to the named subset. |
| **`-m / --models`** | **Scopes to the filter when `--stack NAME` names exactly one stack** - only that stack's model is overridden; siblings keep their scenario-defined models. Without `--stack` (or with a broader filter), `-m` applies to every stack and emits a warning - that collapses a multi-model scenario into N copies of one model, which is almost never desired. |

So the clean pattern for "rerun pool A against a different model":

```bash
llmdbenchmark --spec guides/multi-model-wva run -p <ns> \
  --stack qwen3-06b \
  --model meta-llama/Llama-3.2-3B \
  -l inference-perf -w sanity_random.yaml
```

The filter scopes `-m` to one stack; sibling stacks are left alone.

The rule: anything that's inherently per-stack configuration (model name,
path, shortName) is best edited in the scenario YAML, not overridden via
CLI. CLI flags are designed to override *scenario-wide* knobs (namespace,
harness, workload) uniformly across every stack - or, when combined with
`--stack`, to target a single stack without disturbing the others.

### Benchmarking a single stack from a multi-stack scenario

Preferred - use `--stack`, endpoint auto-resolves:

```bash
# After standup of guides/multi-model-wva
llmdbenchmark --spec guides/multi-model-wva run -p <namespace> \
  --stack qwen3-06b \
  -l inference-perf -w sanity_random.yaml
```

Equivalent - pin `--endpoint-url` yourself (useful if the scenario file
isn't available locally):

```bash
llmdbenchmark run \
  --endpoint-url http://<gateway>:80/qwen3-06b \
  --model Qwen/Qwen3-0.6B \
  --namespace <namespace> \
  -l inference-perf -w sanity_random.yaml
```

Include the path prefix in the URL exactly as shown - the HTTPRoute
rewrites it away before the request reaches vLLM.

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
