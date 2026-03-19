# `llm-d-benchmark` Example

***Benchmarking Interactively***


## Goal

A simple, minimal example of using `llm-d-benchmark` to interactively test against an already deployed `llm-d` stack with `guidellm`.

>  **Note:** For ease of presentation, the example assumes an OpenShift cluster and uses `oc`. For a Kubernetes cluster replace `oc` by `kubectl`.


## Preliminaries


### 📦 Setup the `llm-d-benchamrk` repository

```
git clone https://github.com/llm-d/llm-d-benchmark.git
cd llm-d-benchmark
./setup/install_deps.sh
```

See [list of dependencies](https://github.com/deanlorenz/llm-d-benchmark?tab=readme-ov-file#dependencies).


### Prepare an `llm-d` cluster

Set up the stack for benchmarking. Since you are starting from an existing stack, you may need to restart some pods to create a clean baseline. For this simple example, if you want to compare different setups (e.g., various `epp` configurations) then you have to set up each configuration manually and rerun the example for each.

In this example, the benchmark sends requests to an `infra-inference-scheduling-inference-gateway` endpoint. Replace `infra-inference-scheduling-inference-gateway` with the name your inference gateway service. Alternatively, use a pod name if you want to benchmark a single `vllm` instead.


## Benchmarking Steps


### 1. Prepare workload specification (profile)

You will need a `yaml` specification to tell `guidellm` how to generate the _Workload_ that would be used to benchmark your stack. `guidellm` will generate prompts (AKA a _Data Set_) with timing (AKA _Load_).

Several workload examples are available under `llm-d-benchmark/workload/profiles/inference-perf`. We demonstrate with `shared_prefix_synthetic`.

<details>
<summary>Click to view `shared_prefix_synthetic.yaml.in`</summary>

For example, `shared_prefix_synthetic.yaml.in`:
```yaml
target: REPLACE_ENV_LLMDBENCH_HARNESS_STACK_ENDPOINT_URL
model: REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL
request_type: text_completions
profile: constant
rate: [2,5,8,10,12,15,20]
max_seconds: 50
data:
  prefix_tokens: 2048
  prefix_count: 32
  prompt_tokens: 256
  output_tokens: 256
```
</details>

If you want to create your own `guidellm` profile then save your custom `yaml` specification with a `.yaml.in` suffix in the same directory.

⚠️ Unless you know exactly what you are doing, you should only edit the `rate` and `data` sections. `rate` tells `guidellm` how many sub-test to run; each sub-test has a rate and (max) duration. `data` defines how to create the _DataSet_; the configuration parameters are different for each `type`.


### 2. Log on into your cluster and namespace
Run `oc login ...`

Then, run
```bash
oc project <_namespace-name_>
```
or, if using `kubectl`
```bash
kubectl config set-context --current --namespace=<_namespace-name_>
```

### 3. Gather required parameters (mostly information about your `llm-d` stack)

* **Work Directory**:
  Choose a local work directory to save the results on your computer.

* **Harness Profile**:
  The name of your `.yaml.in` file _without the suffix_, e.g., `shared_prefix_synthetic`

* **PVC**:
  A Persistent Volume Claim for storing benchmarking results. Must be one of the available PVCs in the cluster.

  <details>
  <summary>Click to view bash code snippet</summary>

  ```bash
  oc get persistentvolumeclaims -o name
  ```
  </details>


* **Hugging-Face Token [Optional]**:
  If none is specified then the `HF_TOKEN` used in the existing `llm-d` stack will be used.
  <details>
  <summary>Click to view bash code snippet</summary>

  ```bash
  oc get secrets llm-d-hf-token -o jsonpath='{.data.*}' | base64 -d
  ```
  </details>

<!--
* **Namespace**:
  The K8S namespace / RHOS project being use.
  <details>
  <summary>Click to view bash code snippet</summary>

  ```bash
  oc config current-context | awk -F / '{print $1}'
  ```
  </details>
-->

<!--
* **Model**:
  The exact model name of the LLM being served by your `llm-d` stack.

  <details>
  <summary>Click to view bash code snippet</summary>

  ```bash
  get oc get routes -l app.kubernetes.io/name=inference-gateway

  # note the HOST and PORT from the above command

  curl -s http://<HOST>:<PORT>/v1/models | jq '.data[].root'`
  ```
  </details>
-->

### 4. Create Environment Configuration File
Prepare a file `./myenv.sh` with the following content: (file name must have a `.sh` suffix)

```bash
# export LLMDBENCH_HF_TOKEN=<_your Hugging Face token_>

# Work Directory; for example "/tmp/<_namespace_>"
export LLMDBENCH_CONTROL_WORK_DIR="<_name of your local Work Direcotry_>"
```


### 5. Call `run.sh`

`cd` into `llm-d-benchmark` root directory.

```bash
run.sh -t inference-gateway -k workload-pvc -d
```

The execution will end with messages such as (illustrative example)

```
⚠️  WARNING: environment variable LLMDBENCH_CLUSTER_URL=auto. Will attempt to use current context "_" (_).

==> Fri Nov 21 16:35:24 EST 2025 - ./setup/run.sh - 🗑️  Deleting pods with label "llmdbench-harness-launcher"...
oc --kubeconfig /tmp/test/environment/context.ctx --namespace marcions delete pod -l llmdbench-harness-launcher --ignore-not-found
==> Fri Nov 21 16:35:24 EST 2025 - ./setup/run.sh - ℹ️  Done deleting pods with label "llmdbench-harness-launcher" (it will be now recreated)
==> Fri Nov 21 16:35:24 EST 2025 - ./setup/run.sh - ⚠️  Deployment method - inference-gateway - is neither "standalone" nor "modelservice".
==> Fri Nov 21 16:35:24 EST 2025 - ./setup/run.sh - 🔍 Trying to find a matching endpoint name...
==> Fri Nov 21 16:35:25 EST 2025 - ./setup/run.sh - ℹ️  Stack Endpoint URL detected is "http://_.svc.cluster.local:80"
==> Fri Nov 21 16:35:25 EST 2025 - ./setup/run.sh - 🔍 Trying to find a matching hugging face token (hf.*token*.)...
==> Fri Nov 21 16:35:25 EST 2025 - ./setup/run.sh - ℹ️  Hugging face token detected is "llm-d-hf-token"
==> Fri Nov 21 16:35:25 EST 2025 - ./setup/run.sh - 🔍 Trying to detect the model name served by the stack (http://_.svc.cluster.local:80)...
==> Fri Nov 21 16:35:29 EST 2025 - ./setup/run.sh - ℹ️  Stack model detected is "meta-llama/Llama-3.1-8B-Instruct"
==> Fri Nov 21 16:35:29 EST 2025 - ./setup/run.sh - 🛠️  Rendering "all" workload profile templates under "_/repos/llm-d/private-llm-d-benchmark/workload/profiles/"...
==> Fri Nov 21 16:35:31 EST 2025 - ./setup/run.sh - ✅ Done rendering "all" workload profile templates to "/tmp/test/workload/profiles/"
==> Fri Nov 21 16:35:34 EST 2025 - ./setup/run.sh - 🚀 Starting 1 pod(s) labeled with "llmdbench-harness-launcher" for model "meta-llama/Llama-3.1-8B-Instruct" (meta-llama-llama-3-1-8b-instruct)...
==> Fri Nov 21 16:35:36 EST 2025 - ./setup/run.sh - ✅ 1 pod(s) "llmdbench-harness-launcher" for model "meta-llama/Llama-3.1-8B-Instruct" started
==> Fri Nov 21 16:35:36 EST 2025 - ./setup/run.sh - ⏳ Waiting for 1 pod(s) "llmdbench-harness-launcher" for model "meta-llama/Llama-3.1-8B-Instruct" to be Running (timeout=900s)...
==> Fri Nov 21 16:35:48 EST 2025 - ./setup/run.sh - ℹ️  You can follow the execution's output with "oc --kubeconfig /tmp/test/environment/context.ctx --namespace _ logs llmdbench-harness-launcher_<PARALLEL_NUMBER> -f"...
==> Fri Nov 21 16:35:49 EST 2025 - ./setup/run.sh - ℹ️  Harness was started in "debug mode". The pod can be accessed through "oc --kubeconfig /tmp/test/environment/context.ctx --namespace _ exec -it pod/<POD_NAME> -- bash"
==> Fri Nov 21 16:35:49 EST 2025 - ./setup/run.sh - ℹ️  To list pod names "oc --kubeconfig /tmp/test/environment/context.ctx --namespace marcions get pods -l app=llmdbench-harness-launcher"
==> Fri Nov 21 16:35:49 EST 2025 - ./setup/run.sh - ℹ️  In order to execute a given workload profile, run "llm-d-benchmark.sh -l <[guidellm,inference-perf,inferencemax,nop,vllm-benchmark]> -w [WORKLOAD FILE NAME]" (all inside the pod <POD_NAME>)
```
At this point, issue the command `oc exec -it llmdbench-harness-launcher-1-of-1 -- bash`. Once inside, command line options for `llm-d-benchmark.sh` can be obtained.

```
bash % oc exec -it llmdbench-harness-launcher-1-of-1 -- bash
root@llmdbench-harness-launcher-1-of-1:/workspace# llm-d-benchmark.sh --help
Usage: /usr/local/bin/llm-d-benchmark.sh -l/--harness [harness used to generate load (default=inference-perf, possible values guidellm,inference-perf,inferencemax,nop,vllm-benchmark]
                                         -w/--workload [workload to be used by the harness (default=random_concurrent.yaml, possible values ("ls /workspace/profiles/*/*.yaml")]
                                         -h/--help (show this help)
```
An example execution follows

```
root@llmdbench-harness-launcher-1-of-1:/workspace# llm-d-benchmark.sh -l guidellm -w sanity_random
LLMDBENCH_CONTROL_WORK_DIR=/requests/guidellm_1763762673_inference-gateway-8b-instruct
LLMDBENCH_DEPLOY_CURRENT_MODEL=meta-llama/Llama-3.1-8B-Instruct
LLMDBENCH_DEPLOY_CURRENT_MODELID=meta-llama-llama-3-1-8b-instruct
LLMDBENCH_DEPLOY_CURRENT_TOKENIZER=meta-llama/Llama-3.1-8B-Instruct
LLMDBENCH_DEPLOY_METHODS=inference-gateway
LLMDBENCH_HARNESS_GIT_BRANCH=f6175cdd8a88f0931bd46822ed7a71787dcd7cee
LLMDBENCH_HARNESS_GIT_REPO=https://github.com/vllm-project/guidellm.git
LLMDBENCH_HARNESS_LOAD_PARALLELISM=1
LLMDBENCH_HARNESS_NAME=guidellm
LLMDBENCH_HARNESS_NAMESPACE=marcions
LLMDBENCH_HARNESS_STACK_ENDPOINT_URL=http://infra-marcior-inference-gateway.marcions.svc.cluster.local:80
LLMDBENCH_HARNESS_STACK_NAME=inference-gateway-8b-instruct
LLMDBENCH_HARNESS_STACK_TYPE=vllm-prod
LLMDBENCH_MAGIC_ENVAR=harness_pod
LLMDBENCH_RUN_DATASET_URL=
LLMDBENCH_RUN_EXPERIMENT_ANALYZER=guidellm-analyze_results.sh
LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY=0
LLMDBENCH_RUN_EXPERIMENT_HARNESS=guidellm-llm-d-benchmark.sh
LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR=guidellm
LLMDBENCH_RUN_EXPERIMENT_HARNESS_EC=1
LLMDBENCH_RUN_EXPERIMENT_HARNESS_NAME_AUTO=0
LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_AUTO=0
LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME=sanity_random.yaml
LLMDBENCH_RUN_EXPERIMENT_ID=1763762673
LLMDBENCH_RUN_EXPERIMENT_LAUNCHER=1
LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR=/requests/guidellm_1763762673_inference-gateway-8b-instruct
LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_PREFIX=/requests
LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_SUFFIX=guidellm_1763762673_inference-gateway-8b-instruct
LLMDBENCH_RUN_WORKSPACE_DIR=/workspace
LLMDBENCH_VLLM_COMMON_NAMESPACE=marcions
Running harness: /usr/local/bin/guidellm-llm-d-benchmark.sh
Using experiment result dir: /requests/guidellm_1763762673_inference-gateway-8b-instruct
✔ OpenAIHTTPBackend backend validated with model meta-llama/Llama-3.1-8B-Instruct
  {'target': 'http://infra-marcior-inference-gateway.marcions.svc.cluster.local:80', 'model': 'meta-llama/Llama-3.1-8B-Instruct', 'timeout': 60.0, 'http2': True,
  'follow_redirects': True, 'verify': False, 'openai_paths': {'health': 'health', 'models': 'v1/models', 'text_completions': 'v1/completions', 'chat_completions':
  'v1/chat/completions', 'audio_transcriptions': 'v1/audio/transcriptions', 'audio_translations': 'v1/audio/translations'}, 'validate_backend': {'method': 'GET', 'url':
  'http://infra-marcior-inference-gateway.marcions.svc.cluster.local:80/health'}}
✔ Processor resolved
  Using model 'meta-llama/Llama-3.1-8B-Instruct' as processor
✔ Request loader initialized with inf unique requests
  {'data': "[{'prompt_tokens': 50, 'prompt_tokens_stdev': 10, 'prompt_tokens_min': 10, 'prompt_tokens_max': 100, 'output_tokens': 50, 'output_tokens_stdev': 10,
  'output_tokens_min': 10, 'output_tokens_max': 100}]", 'data_args': '[]', 'data_samples': -1, 'preprocessors': ['GenerativeColumnMapper',
  'GenerativeTextCompletionsRequestFormatter'], 'collator': 'GenerativeRequestCollator', 'sampler': 'None', 'num_workers': 1, 'random_seed': 42}
✔ Resolved transient phase configurations
  Warmup: percent=None value=None mode='prefer_duration'
  Cooldown: percent=None value=None mode='prefer_duration'
  Rampup (Throughput/Concurrent): 0.0
✔ AsyncProfile profile resolved
  {'str': "type_='constant' completed_strategies=[] constraints={'max_seconds': 30} rampup_duration=0.0 strategy_type='constant' rate=[1.0] max_concurrency=None
  random_seed=42 strategy_types=['constant']", 'type': 'AsyncProfile', 'class': 'AsyncProfile', 'module': 'guidellm.benchmark.profiles', 'attributes': {'type_':
  'constant', 'completed_strategies': [], 'constraints': {'max_seconds': 30}, 'rampup_duration': 0.0, 'strategy_type': 'constant', 'rate': [1.0], 'max_concurrency':
  'None', 'random_seed': 42}}
✔ Output formats resolved
  {'json': "output_path=PosixPath('/requests/guidellm_1763762673_inference-gateway-8b-instruct/results.json')"}
✔ Setup complete, starting benchmarks...





ℹ Run Summary Info
|===========|==========|==========|======|======|======|========|=====|=====|========|=====|=====|
| Benchmark | Timings                              ||||| Input Tokens     ||| Output Tokens    |||
| Strategy  | Start    | End      | Dur  | Warm | Cool | Comp   | Inc | Err | Comp   | Inc | Err |
|           |          |          | Sec  | Sec  | Sec  | Tot    | Tot | Tot | Tot    | Tot | Tot |
|-----------|----------|----------|------|------|------|--------|-----|-----|--------|-----|-----|
| constant  | 22:06:37 | 22:07:07 | 30.0 | 0.0  | 0.0  | 1468.0 | 0.0 | 0.0 | 1421.0 | 0.0 | 0.0 |
|===========|==========|==========|======|======|======|========|=====|=====|========|=====|=====|


ℹ Text Metrics Statistics (Completed Requests)
|===========|=======|======|======|======|=======|======|======|======|=======|=======|=======|=======|
| Benchmark | Input Tokens            |||| Input Words             |||| Input Characters           ||||
| Strategy  | Per Request || Per Second || Per Request || Per Second || Per Request  || Per Second   ||
|           | Mdn   | p95  | Mdn  | Mean | Mdn   | p95  | Mdn  | Mean | Mdn   | p95   | Mdn   | Mean  |
|-----------|-------|------|------|------|-------|------|------|------|-------|-------|-------|-------|
| constant  | 51.0  | 62.0 | 50.6 | 52.1 | 41.0  | 52.0 | 41.2 | 42.6 | 279.0 | 328.0 | 273.3 | 277.5 |
|===========|=======|======|======|======|=======|======|======|======|=======|=======|=======|=======|
| Benchmark | Output Tokens           |||| Output Words            |||| Output Characters          ||||
| Strategy  | Per Request || Per Second || Per Request || Per Second || Per Request  || Per Second   ||
|           | Mdn   | p95  | Mdn  | Mean | Mdn   | p95  | Mdn  | Mean | Mdn   | p95   | Mdn   | Mean  |
|-----------|-------|------|------|------|-------|------|------|------|-------|-------|-------|-------|
| constant  | 49.0  | 65.0 | 47.2 | 50.5 | 38.0  | 54.0 | 35.6 | 38.6 | 197.0 | 311.0 | 206.9 | 216.3 |
|===========|=======|======|======|======|=======|======|======|======|=======|=======|=======|=======|


ℹ Request Token Statistics (Completed Requests)
|===========|======|======|======|======|======|=======|=======|=======|=========|========|
| Benchmark | Input Tok  || Output Tok || Total Tok   || Stream Iter  || Output Tok      ||
| Strategy  | Per Req    || Per Req    || Per Req     || Per Req      || Per Stream Iter ||
|           | Mdn  | p95  | Mdn  | p95  | Mdn  | p95   | Mdn   | p95   | Mdn     | p95    |
|-----------|------|------|------|------|------|-------|-------|-------|---------|--------|
| constant  | 51.0 | 62.0 | 49.0 | 65.0 | 98.0 | 126.0 | 102.0 | 134.0 | 1.0     | 1.0    |
|===========|======|======|======|======|======|=======|=======|=======|=========|========|


ℹ Request Latency Statistics (Completed Requests)
|===========|=========|========|======|======|=====|=====|=====|=====|
| Benchmark | Request Latency || TTFT       || ITL      || TPOT     ||
| Strategy  | Sec             || ms         || ms       || ms       ||
|           | Mdn     | p95    | Mdn  | p95  | Mdn | p95 | Mdn | p95 |
|-----------|---------|--------|------|------|-----|-----|-----|-----|
| constant  | 0.4     | 0.5    | 23.2 | 30.9 | 7.8 | 8.0 | 8.1 | 8.4 |
|===========|=========|========|======|======|=====|=====|=====|=====|


ℹ Server Throughput Statistics
|===========|=====|======|=======|======|=======|=======|=======|========|=======|=======|
| Benchmark | Requests               |||| Input Tokens || Output Tokens || Total Tokens ||
| Strategy  | Per Sec   || Concurrency || Per Sec      || Per Sec       || Per Sec      ||
|           | Mdn | Mean | Mdn   | Mean | Mdn   | Mean  | Mdn   | Mean   | Mdn   | Mean  |
|-----------|-----|------|-------|------|-------|-------|-------|--------|-------|-------|
| constant  | 1.0 | 1.0  | 0.0   | 0.4  | 50.9  | 52.4  | 3.4   | 49.7   | 127.6 | 101.1 |
|===========|=====|======|=======|======|=======|=======|=======|========|=======|=======|



✔ Benchmarking complete, generated 1 benchmark(s)
…   json    : /requests/guidellm_1763762673_inference-gateway-8b-instruct/results.json
Harness completed successfully.
Converting results.json
Warning: LLMDBENCH_DEPLOY_METHODS is not "modelservice" or "standalone", cannot extract environmental details.Results data conversion completed successfully.
Harness completed: /usr/local/bin/guidellm-llm-d-benchmark.sh
Running analysis: /usr/local/bin/guidellm-analyze_results.sh
Done. Data is available at "/requests/guidellm_1763762673_inference-gateway-8b-instruct"
```

The data resides on the `pvc` initially chosen (option `-k`) and can be extracted from the `pod` by different methods (such as `kubectl cp`).