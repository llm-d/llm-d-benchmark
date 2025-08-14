## Concept
Use an specific harness to generate load against a stack service a large language model according to a specific workload profile

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

## Use
A (workload) profile has to be manually crafted as a `yaml`. Once crafted, it can used by the `run.sh` executable. It access is controlled by the following parameters

> [!NOTE]
> Evidently, `./e2e.sh`, as the executable that **combines** `./setup/standup.sh`, `run.sh` and `setup/teardown.sh` into a singe operation can also consume the (workload) profile.

| Variable                                       | Meaning                                        | Note                                                |
| ---------------------------------------------  | ---------------------------------------------- | --------------------------------------------------- |
| LLMDBENCH_HARNESS_PROFILE_HARNESS_LIST         |                                                |                                                     |
| LLMDBENCH_HARNESS_NAME                         |                                                | Can be overriden with CLI parameter `-l/--harness`  |
| LLMDBENCH_HARNESS_EXPERIMENT_PROFILE           |                                                | Can be overriden with CLI parameter `-w/--workload` |
| LLMDBENCH_HARNESS_EXPERIMENT_PROFILE_OVERRIDES |                                                | Can be overriden with CLI parameter `-o/--overrides`|
| LLMDBENCH_HARNESS_EXECUTABLE                   |                                                |                                                     |
| LLMDBENCH_HARNESS_CONDA_ENV_NAME               |                                                |                                                     |
| LLMDBENCH_HARNESS_WAIT_TIMEOUT                 |                                                | Can be overriden with CLI parameter `-s/--wait      |
| LLMDBENCH_HARNESS_CPU_NR                       |                                                |                                                     |
| LLMDBENCH_HARNESS_CPU_MEM                      |                                                |                                                     |
| LLMDBENCH_HARNESS_NAMESPACE                    |                                                |                                                     |
| LLMDBENCH_HARNESS_SERVICE_ACCOUNT              |                                                |                                                     |
| LLMDBENCH_HARNESS_PVC_NAME                     |                                                | Can be overriden with CLI parameter `-k/--pvc`      |
| LLMDBENCH_HARNESS_PVC_SIZE                     |                                                |                                                     |
| LLMDBENCH_HARNESS_CONTAINER_IMAGE              |                                                |                                                     |
| LLMDBENCH_HARNESS_SKIP_RUN                     |                                                |                                                     |

> [!TIP]
> In case the full path is ommited for the (workload) profile (either by setting `LLMDBENCH_HARNESS_EXPERIMENT_PROFILE` or CLI parameter `-w/--workload`, it is assumed that the file exists inside the `workload/profiles/<harness name>` folder


## Harnesses

### [inference-perf](https://github.com/kubernetes-sigs/inference-perf)

### [guidellm](https://github.com/vllm-project/guidellm.git)

### [fmperf](https://github.com/fmperf-project/fmperf)

### Nop (No Op)

The `nop` harness, combined with env. variables and when using in `standalone` mode, will parse the vLLM log and create reports with
loading time statistics.

The additional env. variables to set are:

| Environment Variable                         | Example Values  |
| -------------------------------------------- | -------------- |
| LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT   | `safetensors, tensorizer, runai_streamer, fastsafetensors` |
| LLMDBENCH_VLLM_STANDALONE_VLLM_LOGGING_LEVEL | `DEBUG, INFO, WARNING` etc |
| LLMDBENCH_VLLM_STANDALONE_PREPROCESS         | `source /setup/preprocess/standalone-preprocess.sh ; /setup/preprocess/standalone-preprocess.py` |

The env. `LMDBENCH_VLLM_STANDALONE_VLLM_LOGGING_LEVEL` must be set to `DEBUG` so that the `nop` categories report finds all categories.

The env. `LLMDBENCH_VLLM_STANDALONE_PREPROCESS` must be set to the above value for the `nop` harness in order to install load format
dependencies, export additional env. variables and pre-serialize models when using the `tensorizer` load format.
The preprocess scripts will run in the vLLM standalone pod before the vLLM server starts.

## Profiles