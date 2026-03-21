# llm-d-benchmark

A single CLI for planning, deploying, benchmarking, data collection, and tearingdown LLM inference stacks across multiple environments and deployment styles using `llm-d`.

## Why llm-d-benchmark

Benchmarking LLM inference stack performance on Kubernetes involves stitching together kubectl commands, Helm installs, load-generator scripts, tweaking many knobs for various different configurations, and collecting results by hand in very different formats depending on harness. Each step of that process is fragile, hard to reproduce, and comparing results across configurations requires careful bookkeeping.

`llm-d-benchmark` **solves this problem by providing**:

- **Transparency through a Declarative Lifecyle** -- Infrastructure, workloads, and experiments are defined upfront, in a `plan`. Everything being deployed for a given specification, scenario, profile, and experiment are all first rendered into human-readable plain text that can be version-controlled, diffed, and reviewed. No imperative scripts encode hidden assumptions. This promomtes transparency for before and after benchmarking, as well as providing a **reliable** method to reproduce results.

- **End-to-end automation** -- A single `llmdbenchmark` CLI covers the full lifecycle from rendering all Kubernetes manifests, to deploying various `llm` and `llm-d` stacks, executing benchmarks, collecting results, and tearing down infrastructure in a safe manner. No shell scripts to chain together, no manual steps to forget, and it's entirely automated.

- **Reproducibility by default** -- Every run starts from a versioned specification file and a deterministic config merge chain (`defaults.yaml` → scenario → CLI overrides). The rendered `config.yaml` in each workspace captures the exact configuration used, so any result can be traced back to its inputs.

- **Flexible entry points** -- Use the full pipeline when you want deployment handled for you, or use run-only mode to benchmark an endpoint that already exists. The same workload profiles and experiment definitions work in both modes. Maybe you aren't ready to benchmark, but want to deploy the infrastructure to investigate by hand, no problem, `standup` covers that for you.

- **Structured experiment support** -- Built-in Design of Experiments (DoE) support lets you define factor sweeps in YAML. The `experiment` command automates the full setup × run treatment matrix -- standing up a different infrastructure configuration for each setup treatment, running all workload variations, tearing down, and producing a summary.

- **Multiple harnesses** -- Swap between [inference-perf](https://github.com/kubernetes-sigs/inference-perf), [guidellm](https://github.com/vllm-project/guidellm.git), [vllm-benchmark](https://github.com/vllm-project/vllm.git), and others with a simple CLI flag. Each harness uses the same profile and experiment system, as well as APIs to provide the benchmarker a common experience.

- **CI/CD ready** -- Every flag has a `LLMDBENCH_*` environment variable counterpart, `--dry-run` previews without touching the cluster, and `--non-admin` skips steps that require cluster-level privileges.

## Getting Started

### Install

```bash
git clone https://github.com/llm-d/llm-d-benchmark.git
cd llm-d-benchmark
./install.sh
source .venv/bin/activate
llmdbenchmark --version
```

The install script creates a virtualenv, validates system tools (kubectl, helm, Python 3.11+), and installs the `llmdbenchmark` package. See [Installation](#installation) for manual install and flags.

### Choose a specification

Every command takes a `--spec` that selects the configuration for your cluster and GPU type. Specs are Jinja2 templates under `config/specification/`:

```bash
--spec gpu                              # NVIDIA GPU setup (config/specification/examples/gpu.yaml.j2)
--spec inference-scheduling             # inference scheduling guide
--spec pd-disaggregation               # prefill-decode disaggregation guide
--spec /full/path/to/my-spec.yaml.j2    # custom spec
```

If the name is ambiguous or not found, the CLI lists all available specs and exits.

### Deploy and benchmark (full pipeline)

Stand up the `llm-d` stack, run a quick sanity benchmark, and tear down:

```bash
# Preview what would be deployed (no cluster changes)
llmdbenchmark --spec gpu --dry-run standup

# Deploy for real
llmdbenchmark --spec gpu standup

# Run a sanity benchmark against the deployed endpoint
llmdbenchmark --spec gpu run -l inference-perf -w sanity_random.yaml

# Tear down when done
llmdbenchmark --spec gpu teardown
```

Each command renders Kubernetes manifests from your spec's templates and defaults, then applies them. The workspace directory captures rendered configs, manifests, and results for later inspection.

### Benchmark an existing endpoint (run-only mode)

Already have a model-serving endpoint running? Skip deployment entirely:

```bash
llmdbenchmark --spec gpu run \
  --endpoint-url http://10.131.0.42:80 \
  --model meta-llama/Llama-3.1-8B \
  --namespace my-namespace \
  --harness inference-perf \
  --workload sanity_random.yaml
```

This uses the same harness, profile rendering, and result collection pipeline -- just without the standup and teardown phases. Useful when someone else manages the infrastructure, or when you want to test different workloads against a long-lived endpoint.

### Run a parameter sweep

Experiment files in `workload/experiments/` define structured parameter sweeps. Each file lists treatments (combinations of factor levels) that the benchmark iterates over:

```bash
# Sweep workload parameters against an existing stack
llmdbenchmark --spec inference-scheduling run \
  --experiments workload/experiments/inference-scheduling.yaml

# Full DoE: auto standup/run/teardown per infrastructure configuration
llmdbenchmark --spec tiered-prefix-cache experiment \
  --experiments workload/experiments/tiered-prefix-cache.yaml
```

The `run --experiments` form varies workload parameters (prompt length, concurrency) against a single endpoint.

The `experiment` command goes even further by providing an interface to variy infrastructure parameters (replica counts, cache sizes, routing plugins) and stands up a fresh stack for each configuration. This is for advanced performance benchmarking that expands beyond simple configurations - **everything** becomes tunable from infrastructure to inference time.

See [workload/README.md](workload/README.md) for the full experiment file format and all pre-built experiments, as well as advanced functionality.

## Next Steps

| Topic | Where to look |
|-------|---------------|
| Configuration system, defaults, scenarios, overrides | [config/README.md](config/README.md) |
| Workloads, harnesses, profiles, experiments | [workload/README.md](workload/README.md) |
| Standup phase, deployment methods, step details | [llmdbenchmark/standup/README.md](llmdbenchmark/standup/README.md) |
| Smoketests, per-scenario validation, adding validators | [llmdbenchmark/smoketests/README.md](llmdbenchmark/smoketests/README.md) |
| Run phase, benchmark execution, result collection | [llmdbenchmark/run/README.md](llmdbenchmark/run/README.md) |
| Teardown phase and deep clean | [llmdbenchmark/teardown/README.md](llmdbenchmark/teardown/README.md) |
| Design of Experiments (DoE) orchestration | [llmdbenchmark/experiment/README.md](llmdbenchmark/experiment/README.md) |
| Plan-phase rendering pipeline | [llmdbenchmark/parser/README.md](llmdbenchmark/parser/README.md) |
| Execution framework and step contribution guide | [llmdbenchmark/executor/README.md](llmdbenchmark/executor/README.md) |
| CLI reference (all flags, env vars) | [CLI Reference](#cli-reference) below |

---

## Prerequisites

Please refer to the official [llm-d prerequisites](https://github.com/llm-d/llm-d/blob/main/README.md#pre-requisites) for the most up-to-date requirements.

### System Requirements

- **Python 3.11+**
- **kubectl** -- Kubernetes CLI
- **helm** -- Helm package manager
- **curl**, **git** -- Standard system tools
- **helmfile** (optional) -- Required for modelservice deployments
- **oc** (optional) -- Required for OpenShift clusters

### Administrative Requirements

Deploying the llm-d stack requires **cluster-level admin** privileges for configuring cluster-level resources. However, **namespace-level admin** users can run the tool as long as [Kubernetes infrastructure components](https://github.com/llm-d-incubation/llm-d-infra) are configured and the target namespace already exists. Use `--non-admin` to skip admin-only steps.

## Installation

### Quick Install (recommended)

```bash
git clone https://github.com/llm-d/llm-d-benchmark.git
cd llm-d-benchmark
./install.sh
source .venv/bin/activate
```

The install script:

1. Creates a Python virtual environment at `.venv/`
2. Validates Python 3.11+ and pip
3. Checks for required system tools (curl, git, kubectl, helm)
4. Checks for optional tools (oc, helmfile, kustomize, jq, yq, skopeo)
5. Installs `llmdbenchmark` and `config_explorer` in editable mode
6. Verifies all Python packages are importable

**Flags:**

- `-y` -- Non-interactive mode (use system Python, skip venv creation)
- `noreset` -- Reuse cached dependency checks from previous run
- `-h` -- Show help

### Manual Install

```bash
git clone https://github.com/llm-d/llm-d-benchmark.git
cd llm-d-benchmark
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
pip install -e config_explorer/
```

### Verify Installation

```bash
llmdbenchmark --version
```

## CLI Reference

### Global Options

| Flag | Env Var | Description |
|------|---------|-------------|
| `--spec SPEC` | `LLMDBENCH_SPEC` | Specification name or path (bare name, category/name, or full path) |
| `--workspace DIR` / `--ws` | `LLMDBENCH_WORKSPACE` | Workspace directory for outputs (default: temp dir) |
| `--base-dir DIR` / `--bd` | `LLMDBENCH_BASE_DIR` | Base directory for templates/scenarios (default: `.`) |
| `--non-admin` / `-i` | `LLMDBENCH_NON_ADMIN` | Skip admin-only steps |
| `--dry-run` / `-n` | `LLMDBENCH_DRY_RUN` | Generate YAML without applying to cluster |
| `--verbose` / `-v` | `LLMDBENCH_VERBOSE` | Enable debug logging |
| `--version` | | Show version |

### Standup Options

| Flag | Env Var | Description |
|------|---------|-------------|
| `-s STEPS` | | Step filter (e.g., `0,1,5` or `1-7`) |
| `-c FILE` | `LLMDBENCH_SCENARIO` | Scenario file |
| `-m MODELS` | `LLMDBENCH_MODELS` | Models to deploy |
| `-p NS` | `LLMDBENCH_NAMESPACE` | Namespace(s) |
| `-t METHODS` | `LLMDBENCH_METHODS` | Deployment methods (`standalone`, `modelservice`) |
| `-r NAME` | `LLMDBENCH_RELEASE` | Helm release name |
| `-k FILE` | `LLMDBENCH_KUBECONFIG` / `KUBECONFIG` | Kubeconfig path |
| `--parallel N` | `LLMDBENCH_PARALLEL` | Max parallel stacks (default: 4) |
| `--monitoring` | `LLMDBENCH_MONITORING` | Enable workload monitoring |
| `--affinity` | `LLMDBENCH_AFFINITY` | Node affinity / tolerations label |
| `--annotations` | `LLMDBENCH_ANNOTATIONS` | Extra annotations for deployed resources |
| `--wva` | `LLMDBENCH_WVA` | Workload Variant Autoscaler config |

### Teardown Options

| Flag | Env Var | Description |
|------|---------|-------------|
| `-s STEPS` | | Step filter |
| `-m MODELS` | `LLMDBENCH_MODELS` | Model that was deployed (for resource name resolution) |
| `-t METHODS` | `LLMDBENCH_METHODS` | Methods to tear down (`standalone`, `modelservice`) |
| `-r NAME` | `LLMDBENCH_RELEASE` | Helm release name (default: `llmdbench`) |
| `-d` / `--deep` | `LLMDBENCH_DEEP_CLEAN` | Deep clean: delete ALL resources in both namespaces |
| `-p NS` | `LLMDBENCH_NAMESPACE` | Comma-separated namespaces (model,harness) |
| `-k FILE` | `LLMDBENCH_KUBECONFIG` / `KUBECONFIG` | Kubeconfig path |

### Experiment Options

| Flag | Env Var | Description |
|------|---------|-------------|
| `-e FILE` | `LLMDBENCH_EXPERIMENTS` | Experiment YAML with setup and run treatments (required) |
| `-p NS` | `LLMDBENCH_NAMESPACE` | Namespace(s) |
| `-t METHODS` | `LLMDBENCH_METHODS` | Deploy method |
| `-m MODELS` | `LLMDBENCH_MODELS` | Models to deploy |
| `-k FILE` | `LLMDBENCH_KUBECONFIG` / `KUBECONFIG` | Kubeconfig path |
| `--parallel N` | `LLMDBENCH_PARALLEL` | Max parallel stacks (default: 4) |
| `-l HARNESS` | `LLMDBENCH_HARNESS` | Harness name |
| `-w PROFILE` | `LLMDBENCH_WORKLOAD` | Workload profile |
| `--stop-on-error` | | Abort on first setup treatment failure |
| `--skip-teardown` | | Leave stacks running for debugging |

### Run Options

| Flag | Env Var | Description |
|------|---------|-------------|
| `-m MODEL` | `LLMDBENCH_MODEL` | Model name override (e.g. facebook/opt-125m) |
| `-p NS` | `LLMDBENCH_NAMESPACE` | Namespaces (deploy,benchmark) |
| `-t METHODS` | `LLMDBENCH_METHODS` | Deploy method used during standup |
| `-k FILE` | `LLMDBENCH_KUBECONFIG` / `KUBECONFIG` | Kubeconfig path |
| `-l HARNESS` | `LLMDBENCH_HARNESS` | Harness name (inference-perf, guidellm, vllm-benchmark) |
| `-w PROFILE` | `LLMDBENCH_WORKLOAD` | Workload profile YAML |
| `-e FILE` | `LLMDBENCH_EXPERIMENTS` | Experiment treatments YAML for parameter sweeping |
| `-o OVERRIDES` | `LLMDBENCH_OVERRIDES` | Workload parameter overrides (param=value,...) |
| `-r DEST` | `LLMDBENCH_OUTPUT` | Results destination (local, gs://, s3://) |
| `-j N` | `LLMDBENCH_PARALLELISM` | Parallel harness pods |
| `-U URL` | `LLMDBENCH_ENDPOINT_URL` | Explicit endpoint URL (run-only mode) |
| `-c FILE` | | Run config YAML (run-only mode) |
| `--generate-config` | | Generate config and exit |
| `-x DATASET` | `LLMDBENCH_DATASET` | Dataset URL for harness replay |
| `--wait-timeout N` | `LLMDBENCH_WAIT_TIMEOUT` | Seconds to wait for harness completion |
| `-z` / `--skip` | `LLMDBENCH_SKIP` | Skip execution, only collect existing results |
| `-d` / `--debug` | `LLMDBENCH_DEBUG` | Debug mode: start harness pods with sleep infinity |

### Environment Variables

Every CLI flag can be set via a `LLMDBENCH_*` environment variable (see tables above). The priority chain is:

1. **CLI flag** (highest) -- explicitly passed on the command line
2. **Environment variable** -- exported in the user's shell
3. **Rendered config** (lowest) -- defaults.yaml + scenario YAML

This is useful for CI/CD pipelines, `.bashrc` configuration, or migrating from the original bash-based workflow.

```bash
# Example: set common defaults via env vars, override per-run via CLI
export LLMDBENCH_SPEC=inference-scheduling
export LLMDBENCH_NAMESPACE=my-team-ns
export LLMDBENCH_KUBECONFIG=~/.kube/my-cluster

# These use the env vars above; --dry-run overrides nothing, just adds a flag
llmdbenchmark standup --dry-run
llmdbenchmark standup                          # live deploy to my-team-ns
llmdbenchmark standup -p override-ns           # CLI wins over env var
```

Boolean env vars accept `1`, `true`, or `yes` (case-insensitive). Active `LLMDBENCH_*` overrides are logged at startup for debugging.

## Architecture

The tool operates in three phases, each composed of numbered steps executed by a shared [`StepExecutor`](llmdbenchmark/executor/README.md) framework.

### [Config Override Chain](config/README.md#config-override-chain)

Values flow through a merge pipeline during the plan phase:

![Config Override Chain](docs/images/config-override-chain.svg)

Steps read from the rendered `config.yaml` and never define their own fallback defaults. If a required key is missing from the rendered config, the step raises a clear error. This ensures `defaults.yaml` is the single source of truth for all default values. Environment variables (`LLMDBENCH_*`) sit between scenario overrides and CLI flags in the priority chain.

See [config/README.md](config/README.md) for the full configuration reference, including [how to override values](config/README.md#how-to-override-values).

### [Deployment Methods](llmdbenchmark/standup/README.md#deployment-methods)

The standup phase supports two deployment paths:

- **standalone** -- Direct Kubernetes Deployments and Services for each model (step 06)
- **modelservice** -- Helm-based deployment with gateway infrastructure, GAIE, and LWS support (steps 07-09)

Both paths share steps 00-05 (infrastructure, namespaces, secrets) and step 10 (smoketest).

### [Standup Steps](llmdbenchmark/standup/README.md)

| Step | Name | Scope | Description |
|------|------|-------|-------------|
| 00 | ensure_infra | Global | Validate dependencies, cluster connectivity, kubeconfig |
| 02 | admin_prerequisites | Global | Admin prerequisites (CRDs, gateway, LWS, namespaces) |
| 03 | workload_monitoring | Global | Workload monitoring, node resource discovery |
| 04 | model_namespace | Per-stack | Model namespace (PVCs, secrets, download job) |
| 05 | harness_namespace | Per-stack | Harness namespace (PVC, data access pod, preprocess) |
| 06 | standalone_deploy | Per-stack | Standalone vLLM deployment (Deployment + Service) |
| 07 | deploy_setup | Per-stack | Helm repos and gateway infrastructure (helmfile) |
| 08 | deploy_gaie | Per-stack | GAIE inference extension deployment |
| 09 | deploy_modelservice | Per-stack | Modelservice deployment (helmfile + LWS) |
| 10 | smoketest | Per-stack | Health check, inference test, per-scenario config validation |
| 11 | inference_test | Per-stack | Sample inference request with demo curl command |

### [Run Steps](llmdbenchmark/run/README.md)

| Step | Name | Scope | Description |
|------|------|-------|-------------|
| 00 | preflight | Global | Validate cluster connectivity and run-phase prerequisites |
| 01 | cleanup_previous | Global | Remove leftover harness pods from previous runs |
| 02 | detect_endpoint | Per-stack | Discover or accept the model-serving endpoint |
| 03 | verify_model | Per-stack | Verify the expected model is served at the endpoint |
| 04 | render_profiles | Per-stack | Render workload profile templates with runtime values |
| 05 | create_profile_configmap | Per-stack | Create profile and harness-scripts ConfigMaps |
| 06 | deploy_harness | Per-stack | Deploy harness pod(s) and execute the full treatment cycle |
| 07 | wait_completion | Per-stack | Wait for harness pod(s) to complete |
| 08 | collect_results | Per-stack | Collect results from PVC to local workspace |
| 09 | upload_results | Global | Upload results to cloud storage (safety-net bulk upload) |
| 10 | cleanup_post | Global | Clean up harness pods and ConfigMaps |
| 11 | analyze_results | Global | Run local analysis on collected results |

### [Teardown Steps](llmdbenchmark/teardown/README.md)

| Step | Name | Description | Condition |
|------|------|-------------|-----------|
| 00 | preflight | Validate cluster connectivity, load config | Always |
| 01 | uninstall_helm | Uninstall Helm releases, delete routes and jobs | Modelservice only |
| 02 | clean_harness | Clean harness ConfigMaps, pods, secrets | Always |
| 03 | delete_resources | Delete namespaced resources (normal or deep) | Always |
| 04 | clean_cluster_roles | Clean cluster-scoped ClusterRoles/Bindings | Admin + modelservice only |

## Project Structure

```text
config/                       Declarative configuration (all plan-phase inputs)
    templates/
        jinja/                Jinja2 templates for Kubernetes manifests
        values/defaults.yaml  Base configuration with all anchored defaults
    scenarios/                Deployment overrides (guides/, examples/, cicd/)
    specification/            Specification templates (guides/, examples/, cicd/)

llmdbenchmark/                Python package
    cli.py                    Entry point, workspace setup, command dispatch
    config.py                 Plan-phase workspace configuration singleton

    interface/                CLI subcommand definitions (argparse)
        commands.py           Command enum (plan, standup, teardown, run, experiment)
        env.py                Environment variable helpers for CLI defaults
        plan.py               Plan subcommand
        standup.py            Standup subcommand
        teardown.py           Teardown subcommand
        run.py                Run subcommand
        experiment.py         Experiment subcommand (DoE orchestration)

    parser/                   Plan-phase template rendering (see parser/README.md)
        render_specification.py   Specification file parsing and validation
        render_plans.py           Jinja2 template rendering engine
        render_result.py          Structured error tracking for renders
        config_schema.py          Pydantic config validation (typo/type detection)
        version_resolver.py       Auto-resolve image tags and chart versions
        cluster_resource_resolver.py  Auto-detect accelerator/network values

    experiment/               DoE experiment orchestration (see experiment/README.md)
        parser.py             Parse experiment YAML (setup + run treatments)
        summary.py            Per-treatment result tracking and summary output

    executor/                 Execution framework (see executor/README.md)
        step.py               Step ABC, Phase enum, result dataclasses
        step_executor.py      Step orchestrator (sequential + parallel)
        command.py            kubectl/helm/helmfile subprocess wrapper
        context.py            Shared state (ExecutionContext dataclass)
        protocols.py          Structural typing (LoggerProtocol)
        deps.py               System dependency checker

    smoketests/               Post-deployment validation (see smoketests/README.md)
        base.py               Health checks, inference tests, pod inspection helpers
        report.py             CheckResult / SmoketestReport tracking
        steps/                Smoketest step implementations (00-02)
        validators/           Per-scenario config validators

    standup/                  Standup phase (see standup/README.md)
        preprocess/           Scripts mounted as ConfigMaps in vLLM pods
        steps/                Step implementations (00-11)

    teardown/                 Teardown phase (see teardown/README.md)
        steps/                Step implementations (00-05)

    run/                      Run phase (see run/README.md)
        steps/                Step implementations (00-11)

    logging/                  Structured logger with emoji support (see logging/README.md)
    exceptions/               Error hierarchy (Template, Configuration, Execution)
    utilities/                Shared helpers (see utilities/README.md)
        cluster.py            Kubernetes connection, platform detection
        capacity_validator.py GPU capacity validation
        huggingface.py        HuggingFace model access checks
        endpoint.py           Endpoint discovery and model verification
        profile_renderer.py   Workload profile template rendering
        kube_helpers.py       Shared kubectl patterns (wait, collect, cleanup)
        cloud_upload.py       Unified cloud storage upload (GCS, S3)
        os/
            filesystem.py     Workspace and directory management
            platform.py       Host OS detection
```

See module-level READMEs for detailed documentation:

- [executor/README.md](llmdbenchmark/executor/README.md) -- Execution framework and step contribution guide
- [smoketests/README.md](llmdbenchmark/smoketests/README.md) -- Post-deployment validation and per-scenario config checking
- [standup/README.md](llmdbenchmark/standup/README.md) -- Standup phase details
- [run/README.md](llmdbenchmark/run/README.md) -- Run phase, benchmark execution, result collection
- [teardown/README.md](llmdbenchmark/teardown/README.md) -- Teardown phase details
- [experiment/README.md](llmdbenchmark/experiment/README.md) -- DoE experiment orchestration
- [parser/README.md](llmdbenchmark/parser/README.md) -- Plan-phase rendering pipeline
- [logging/README.md](llmdbenchmark/logging/README.md) -- Logger, stream separation, file logging
- [utilities/README.md](llmdbenchmark/utilities/README.md) -- Shared utilities, workspace architecture

## Well-Lit Path Guides

`llm-d-benchmark` supports all available [Well-Lit Path Guides](https://github.com/llm-d/llm-d/blob/main/guides/README.md). Each guide has a corresponding specification:

```bash
llmdbenchmark --spec inference-scheduling standup       # Inference scheduling
llmdbenchmark --spec pd-disaggregation standup          # Prefill-decode disaggregation
llmdbenchmark --spec tiered-prefix-cache standup        # Tiered prefix cache
llmdbenchmark --spec precise-prefix-cache-aware standup # Precise prefix cache-aware routing
llmdbenchmark --spec wide-ep-lws standup                # Wide expert-parallel with LWS
```

## Main Concepts

### [Scenarios](docs/standup.md#scenarios)

Cluster-specific configuration: GPU model, LLM, and `llm-d` parameters. Scenarios are YAML files under `config/scenarios/` that override `defaults.yaml` for a particular deployment context.

### [Harnesses](docs/run.md#harnesses)

Load generators that drive benchmark traffic. Supported: [inference-perf](https://github.com/kubernetes-sigs/inference-perf), [guidellm](https://github.com/vllm-project/guidellm.git), [vllm benchmarks](https://github.com/vllm-project/vllm.git), [inferencemax](https://github.com/InferenceMAX/InferenceMAX.git), and nop (for model load time benchmarking).

### (Workload) [Profiles](docs/run.md#profiles)

Benchmark load specifications including LLM use case, traffic pattern, input/output distribution, and dataset. Found under [`workload/profiles`](./workload/profiles).

> **Reproducibility:** The triplet `<scenario>`, `<harness>`, `<(workload) profile>`, combined with the standup/teardown capabilities, provides enough information to reproduce any single experiment.

### [Experiments](docs/doe.md)

Design of Experiments (DOE) files describing parameter sweeps across standup and run configurations. The `experiment` command automates the full setup x run treatment matrix -- standing up a different infrastructure configuration for each setup treatment, running all workload variations, tearing down, and producing a summary. See [llmdbenchmark/experiment/README.md](llmdbenchmark/experiment/README.md) for the full experiment lifecycle documentation.

### [Configuration Explorer](config_explorer/README.md)

The configuration explorer is a library that helps find the most cost-effective, optimal configuration for serving models on llm-d based on hardware specification, workload characteristics, and SLO requirements. A "Capacity Planner" is provided as an initial component to help determine if a vLLM configuration is feasible for deployment.

### [Benchmark Report](llmdbenchmark/analysis/benchmark_report/README.md)

Results are saved in the native format of each harness, as well as a universal Benchmark Report format (v0.1 and v0.2). The benchmark report is a standard data format describing the cluster configuration, workload, and results of a benchmark run. It acts as a common API for comparing results across different harnesses and configurations. See [llmdbenchmark/analysis/benchmark_report/README.md](llmdbenchmark/analysis/benchmark_report/README.md) for the full schema documentation and Python API.

### [Analysis](docs/analysis.md)

The analysis pipeline generates per-request distribution plots, cross-treatment comparison tables and charts, and Prometheus metric visualizations. Analysis runs both inside the harness container (automatically) and locally via `--analyze`. For interactive exploration, a Jupyter notebook is also available at [`docs/analysis/README.md`](docs/analysis/README.md).

## Dependencies

- [llm-d-infra](https://github.com/llm-d-incubation/llm-d-infra.git)
- [llm-d-modelservice](https://github.com/llm-d/llm-d-model-service.git)
- [inference-perf](https://github.com/kubernetes-sigs/inference-perf)
- [guidellm](https://github.com/vllm-project/guidellm.git)
- [vllm](https://github.com/vllm-project/vllm.git)
- [inferencemax](https://github.com/InferenceMAX/InferenceMAX.git)

## News

- `llm-d-benchmark` supports all available [Well-Lit Path Guides](https://github.com/llm-d/llm-d/blob/main/guides/README.md)
- Data from benchmarking experiments is made available on the [main project's Google Drive](https://drive.google.com/drive/folders/1sqnibn_mFlciV3-qZIFgZYmk-p9zemzH)

## Topics

- [Analysis Pipeline](docs/analysis.md)
- [Metrics Collection](docs/metrics_collection.md)
- [Benchmark Report](docs/benchmark_report.md)
- [Design of Experiments (DoE)](docs/doe.md)
- [Lifecycle](docs/lifecycle.md)
- [Run](docs/run.md)
- [Standup](docs/standup.md)
- [Reproducibility](docs/reproducibility.md)
- [Observability](docs/observability.md)
- [Quickstart](docs/quickstart.md)
- [Resource Requirements](docs/resource_requirements.md)
- [WVA (Workload Variant Autoscaler)](docs/workload-variant-autoscaler.md)
- [Upstream Versions](docs/upstream-versions.md)
- [FAQ](docs/faq.md)

## Developing

- [Developer Guide](docs/developer-guide.md) -- How to add new steps, analysis modules, harnesses, scenarios, and experiments
- [Package Architecture](llmdbenchmark/README.md) -- Overview of the `llmdbenchmark` package structure and submodules

## Contribute

- [How to contribute](CONTRIBUTING.md), including development process and governance.
- See [Developer Guide](docs/developer-guide.md) for how to add new steps, harnesses, scenarios, and analysis modules.
- Join [Slack](https://llm-d.ai/slack) (`sig-benchmarking` channel) for cross-org development discussion.
- Bi-weekly contributor standup: Tuesdays 13:00 EST. [Calendar](https://calendar.google.com/calendar/u/0?cid=NzA4ZWNlZDY0NDBjYjBkYzA3NjdlZTNhZTk2NWQ2ZTc1Y2U5NTZlMzA5MzhmYTAyZmQ3ZmU1MDJjMDBhNTRiNEBncm91cC5jYWxlbmRhci5nb29nbGUuY29t) | [Meeting notes](https://docs.google.com/document/d/1njjeyBJF6o69FlyadVbuXHxQRBGDLcIuT7JHJU3T_og/edit?usp=sharing) | [Google group](https://groups.google.com/g/llm-d-contributors)

## License

Licensed under Apache License 2.0. See [LICENSE](LICENSE) for details.
