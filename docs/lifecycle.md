## Lifecycle

#### Standing up llm-d for experimentation and benchmarking

Cluster access and authentication are configured in the scenario YAML file or via CLI flags. By default, the tool uses your current kubeconfig context.

```yaml
# In your scenario YAML (or set via environment variables)
cluster:
  url: "https://api.fmaas-platform-eval.fmaas.res.ibm.com"
  token: "..."
```

> [!TIP]
> You can simply use your current context. **After running kubectl/oc login**, the tool will use your current context automatically, with no need to configure cluster URL or token.

> [!IMPORTANT]
> For **gated models** (e.g. Llama), a HuggingFace token is required (`LLMDBENCH_HF_TOKEN` environment variable or `huggingface.token` in YAML). For **public models** (e.g. `facebook/opt-125m`), the token is optional -- when no token is found, the tool automatically sets `huggingface.enabled: false` and skips secret creation and authentication steps.

A complete list of available options (and their default values) can be found by running
 `llmdbenchmark standup --help`

> [!NOTE]
> The `namespaces` specified by `namespace.name` and `namespace.harness` in the scenario YAML (or via `-p/--namespace`) will be automatically created.

> [!TIP]
> If you want all generated `yaml` files and all data collected to reside on the same directory, set the environment variable `LLMDBENCH_CONTROL_WORK_DIR` explicitly before starting execution.

#### List of "standup steps"

Run the command line with the option `-h` in order to produce a list of steps

```
llmdbenchmark standup -h
```

> [!NOTE]
> Each standup step is numbered (00-11) and named in a way that briefly describes its purpose.

> [!TIP]
> Steps 0-5 can be considered "preparation" and can be skipped in most standups.

#### to dry-run

```
llmdbenchmark standup -n
```

### Standup

vLLM instances can be deployed by one of the following methods:

- "standalone" (a simple (`Kubernetes`) `deployment` with a (`Kubernetes`) `service` associated to it)
- "modelservice" (invoking a combination of [llm-d-infra](https://github.com/llm-d-incubation/llm-d-infra.git) and [llm-d-modelservice](https://github.com/llm-d/llm-d-model-service.git)).

This is controlled by the `deploy.methods` config key (default "modelservice"), which can be set in the scenario YAML or overridden by the parameter `-t/--methods` (applicable for both `llmdbenchmark teardown` and `llmdbenchmark standup`)

> [!WARNING]
> At this time, only **one simultaneous** deployment method is supported

All available models are listed and controlled by the `model.name` config key. The value can be overridden by the parameter `-m/--model` (applicable for both `llmdbenchmark teardown` and `llmdbenchmark standup`).

### Full cycle (Standup/Run/Teardown)

At this point, with your scenario YAML configured, you should be ready to deploy and test

```
llmdbenchmark standup
```

> [!NOTE]
> The scenario can also be indicated as part of the command line options for `llmdbenchmark standup` (e.g. `llmdbenchmark standup --spec ocp_H100MIG_modelservice_llama-3b`)

To re-execute only individual steps (by number):

```
llmdbenchmark standup -s 10
llmdbenchmark standup -s 7
llmdbenchmark standup -s 3-5
llmdbenchmark standup -s 5,7
```

#### Smoketests

After standup, smoketests run automatically to validate the deployment. They can also be run independently:

```
llmdbenchmark --spec guides/pd-disaggregation smoketest -p <namespace>
```

Smoketests include three steps:
- **Step 00** -- Health check: pods running, `/health` responds, `/v1/models` returns expected model, service/gateway/route reachable
- **Step 01** -- Inference test: sends a sample `/v1/completions` request, logs generated text and a demo curl command
- **Step 02** -- Config validation: per-scenario checks that compare deployed pod configuration against the rendered scenario config (resources, parallelism, env vars, probes, volumes, security, vLLM flags, etc.)

Well-lit-path scenarios (pd-disaggregation, precise-prefix-cache-aware, inference-scheduling, inference-scheduling-wva, tiered-prefix-cache, wide-ep-lws, simulated-accelerators) have dedicated validators with scenario-specific checks. Other scenarios (including multi-stack scenarios like `multi-model-wva`) run steps 00 and 01 only.

Multi-stack scenarios run smoketest steps sequentially (one stack at a time) regardless of the `--parallel` flag — parallel probes of a shared gateway would be noisy and harder to debug. Each stack's `/health` and `/v1/models` requests are automatically prefixed with its routing path (e.g. `/qwen3-06b/...`) when the scenario uses a shared HTTPRoute.

#### Run

Once `llm-d` is fully deployed, an experiment can be run. This script takes in different options where you can specify the harness, workload, etc. if they are not specified as a part of your scenario.

```
llmdbenchmark run
llmdbenchmark run --harness inference-perf --workload chatbot_synthetic.yaml
```

> [!IMPORTANT]
> This command will run an experiment, collect data and perform an initial analysis (generating statistics and plots). One can go straight to the analysis by adding the option `-z`/`--skip` to the above command

> [!NOTE]
> The scenario can also be indicated as part of the command line options for `llmdbenchmark run` (e.g., `llmdbenchmark run --spec ocp_L40_standalone_llama-8b`)

Finally, cleanup everything

```
llmdbenchmark teardown
```

> [!NOTE]
> The scenario can also be indicated as part of the command line options for `llmdbenchmark teardown` (e.g., `llmdbenchmark teardown --spec kubernetes_H200_modelservice_llama-8b`)
