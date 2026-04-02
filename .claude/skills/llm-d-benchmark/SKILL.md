---
name: llm-d-benchmark
description: |
  Comprehensive reference for the llm-d-benchmark framework — a declarative Python CLI for deploying,
  benchmarking, and tearing down LLM inference stacks on Kubernetes. TRIGGER when: user asks about
  llmdbenchmark CLI commands, scenario/template configuration, standup/run/teardown phases, experiment
  design (DoE), workload profiles, or extending the step framework. DO NOT TRIGGER when: the task is
  unrelated to LLM benchmarking infrastructure or Kubernetes deployment.
---

# llm-d-benchmark

A declarative Python framework for end-to-end LLM inference benchmarking on Kubernetes.
Deploys llm-d stacks (standalone or modelservice), runs benchmark harnesses, collects results,
and tears down infrastructure — all driven by YAML scenarios and Jinja2 templates.

**Python:** >=3.11
**Entry point:** `llmdbenchmark` CLI (defined in `llmdbenchmark/cli.py`)
**Repository:** https://github.com/llm-d/llm-d-benchmark

---

## CLI Commands

Six commands, all requiring `--spec <specification>`:

```
llmdbenchmark --spec <spec> [--dry-run] [--verbose] <command> [args]
```

### Global Flags

| Flag | Env Var | Description |
|------|---------|-------------|
| `--spec` | `LLMDBENCH_SPEC` | Specification file (required). Bare name (`gpu`), category/name (`guides/pd-disaggregation`), or absolute path |
| `--dry-run` | — | Preview without applying to cluster |
| `--verbose` | — | Verbose logging |
| `--base-dir` | `LLMDBENCH_BASE_DIR` | Project root for templates/scenarios (default: `.`) |

### `plan` — Render templates without cluster access

| Flag | Description |
|------|-------------|
| `-p/--namespace` | Namespace(s) (deploy,harness format) |
| `-m/--models` | Model to render |
| `-t/--methods` | Deployment method (standalone, modelservice) |
| `-f/--monitoring` | Enable PodMonitor in rendered output |
| `-k/--kubeconfig` | Kubeconfig for cluster resource auto-detection |

### `standup` — Deploy infrastructure + model

| Flag | Description |
|------|-------------|
| `-p/--namespace` | Namespace(s) |
| `-t/--methods` | Deployment methods |
| `-m/--models` | Models to deploy |
| `-s/--step` | Run specific steps (e.g., `0,3-5,9`) |
| `-r/--release` | Helm release name |
| `-f/--monitoring` | Enable PodMonitor |
| `--skip-smoketest` | Skip auto-chained smoketest |
| `--parallel` | Max parallel stacks (default: 4) |
| `-k/--kubeconfig` | Kubeconfig path |
| `--non-admin` | Skip admin-only steps |

### `smoketest` — Validate deployment

| Flag | Description |
|------|-------------|
| `-p/--namespace` | Namespace(s) |
| `-t/--methods` | Deployment methods |
| `-s/--step` | Specific steps (0-2) |

### `run` — Execute benchmarks

| Flag | Description |
|------|-------------|
| `-p/--namespace` | Namespace(s) |
| `-t/--methods` | Deployment methods |
| `-l/--harness` | Harness name (inference-perf, guidellm, vllm-benchmark) |
| `-w/--workload` | Workload profile (e.g., `sanity_random.yaml`) |
| `-e/--experiments` | Experiment YAML (DoE treatments) |
| `-o/--overrides` | Profile parameter overrides (`param=value,...`) |
| `-r/--output` | Results destination (`local`, `gs://...`, `s3://...`) |
| `-j/--parallelism` | Parallel harness pods |
| `--wait-timeout` | Seconds to wait for completion (default: 3600) |
| `-U/--endpoint-url` | Explicit endpoint (run-only mode, no standup) |
| `-d/--debug` | Debug mode (harness pod sleeps infinity) |
| `--analyze` | Run local analysis on results |
| `-s/--step` | Specific steps (0-12) |

### `teardown` — Remove deployed resources

| Flag | Description |
|------|-------------|
| `-p/--namespace` | Namespace(s) |
| `-t/--methods` | Methods to tear down |
| `-d/--deep` | Deep clean (delete ALL resources in namespaces) |
| `-s/--step` | Specific steps (0-4) |

### `experiment` — Full DoE lifecycle (standup/run/teardown per treatment)

| Flag | Description |
|------|-------------|
| `-e/--experiments` | Experiment YAML (required) |
| `-p/--namespace` | Namespace(s) |
| `--stop-on-error` | Stop on first failure |
| `--skip-teardown` | Keep infra up after experiment |

---

## Rendering Pipeline

Three-level override hierarchy (each layer overrides the previous):

1. **`defaults.yaml`** (1296 lines) — base configuration with YAML anchors and presets
2. **Scenario YAML** — per-environment overrides (GPU type, replicas, model, storage)
3. **CLI flags** — `--namespace`, `--models`, `--methods`, `--monitoring`

### Flow

```
Specification (.yaml.j2) → RenderSpecification
    ↓
defaults.yaml + scenario.yaml → deep_merge
    ↓
CLI overrides → _resolve_namespace, _resolve_model, _resolve_deploy_method
    ↓
Resolvers → VersionResolver (image tags), ClusterResourceResolver (GPUs), HF Token
    ↓
23 Jinja2 templates + _macros.j2 → Rendered plan (23 YAML files per stack)
```

### Key Files

| File | Purpose |
|------|---------|
| `llmdbenchmark/parser/render_specification.py` | Resolves spec file, validates paths |
| `llmdbenchmark/parser/render_plans.py` | Core rendering engine with custom Jinja filters |
| `llmdbenchmark/parser/version_resolver.py` | Resolves `"auto"` image tags via skopeo/crane |
| `llmdbenchmark/parser/cluster_resource_resolver.py` | Auto-detects accelerators/network from nodes |
| `llmdbenchmark/parser/config_schema.py` | Pydantic v2 validation schema |
| `config/templates/values/defaults.yaml` | Base configuration (all defaults) |
| `config/templates/jinja/_macros.j2` | Shared Jinja2 macros (KV transfer, vLLM commands) |

### Custom Jinja Filters

- `indent(width, first)` — text indentation
- `toyaml(indent, default_flow_style)` — dict to YAML
- `tojson` — dict to JSON
- `b64encode` / `b64pad` — base64 encoding
- `model_id_label` — generates K8s-safe label: `{first8}-{sha256_8}-{last8}`
- `is_empty` / `default_if_empty` — emptiness helpers

### Specification Files

Located in `config/specification/`. Jinja2 templates that point to defaults, templates, and scenario:

```yaml
{% set base_dir = base_dir | default('../') -%}
base_dir: {{ base_dir }}
values_file:
  path: {{ base_dir }}/config/templates/values/defaults.yaml
template_dir:
  path: {{ base_dir }}/config/templates/jinja
scenario_file:
  path: {{ base_dir }}/config/scenarios/examples/gpu.yaml
```

Categories: `examples/` (gpu, cpu, sim, spyre), `guides/` (pd-disaggregation, inference-scheduling, etc.), `cicd/` (kind-sim, ocp, gke-h100, cks)

### 23 Jinja2 Templates

| # | Template | Purpose |
|---|----------|---------|
| 01 | `pvc_workload-pvc` | Workload PVC |
| 02 | `pvc_model-pvc` | Model PVC |
| 03 | `cluster-monitoring-config` | OpenShift monitoring config |
| 04 | `download_job` | Model download job |
| 05 | `namespace_sa_rbac_secret` | Namespace, SA, RBAC, secrets |
| 06 | `pod_access_to_harness_data` | Data access pod |
| 07 | `service_access_to_harness_data` | Data access service |
| 08 | `httproute` | Gateway API HTTPRoute |
| 09 | `helmfile-gateway-provider` | Istio/kgateway helmfile |
| 10 | `helmfile-main` | Main helmfile (infra, modelservice, GAIE) |
| 11 | `infra` | llm-d infra values |
| 12 | `gaie-values` | GAIE (inference extension) values |
| 13 | `ms-values` | Modelservice Helm values |
| 14 | `standalone-deployment_yaml` | Standalone vLLM deployment |
| 15 | `standalone-service_yaml` | Standalone service |
| 16 | `pvc_extra-pvc` | Extra PVC (optional) |
| 17 | `standalone-podmonitor` | Standalone PodMonitor |
| 18 | `podmonitor` | Modelservice PodMonitor |
| 19 | `wva-values` | Workload Variant Autoscaler values |
| 20 | `harness_pod` | Benchmark harness pod |
| 21 | `prometheus-adapter-values` | Prometheus adapter |
| 22 | `prometheus-rbac` | Prometheus RBAC |
| 23 | `wva-namespace` | WVA namespace |

---

## Executor Framework

All phases share a common step-based execution model.

### Phase Enum

```python
class Phase(Enum):
    STANDUP = "standup"
    SMOKETEST = "smoketest"
    RUN = "run"
    TEARDOWN = "teardown"
```

### Step Base Class (`llmdbenchmark/executor/step.py`)

```python
class Step(ABC):
    number: int           # Execution order
    name: str             # Unique identifier
    description: str      # Human-readable
    phase: Phase          # Which phase this belongs to
    per_stack: bool       # Global (once) or per-stack (parallel)

    def should_skip(self, context: ExecutionContext) -> bool
    def execute(self, context: ExecutionContext, stack_path: Path | None) -> StepResult
```

### StepResult

```python
@dataclass
class StepResult:
    step_number: int
    step_name: str
    success: bool
    message: str
    errors: list[str]
```

### ExecutionContext (`llmdbenchmark/executor/context.py`)

Shared mutable state across all steps. Key fields:

- **Paths:** `plan_dir`, `workspace`, `base_dir`, `rendered_stacks`
- **Flags:** `dry_run`, `verbose`, `non_admin`, `deep_clean`
- **Cluster:** `kubeconfig`, `is_openshift`, `is_kind`, `is_minikube`, `cluster_server`
- **Namespace:** `namespace`, `harness_namespace`
- **Model:** `model_name`
- **State:** `deployed_methods`, `deployed_endpoints`, `deployed_pod_names`
- **Run config:** `harness_name`, `harness_profile`, `harness_parallelism`, `harness_output`

Methods: `rebuild_cmd()`, `resolve_cluster()`, `require_cmd()`, `require_namespace()`

### CommandExecutor (`llmdbenchmark/executor/command.py`)

Wraps shell commands with retry, logging, dry-run support.

```python
cmd.execute("some command", attempts=3, check=True)
cmd.kube("get", "pods", namespace="ns")         # kubectl or oc (auto-detected)
cmd.helm("install", "release", "chart")
cmd.helmfile("apply", "-f", "helmfile.yaml", use_kubeconfig=False)
cmd.wait_for_pods(label="app=foo", namespace="ns", timeout=300)
cmd.wait_for_job(job_name="download", namespace="ns", timeout=3600)
cmd.wait_for_pvc(pvc_name="model-pvc", namespace="ns")
```

Uses `oc` on OpenShift, `kubectl` otherwise (via `_kube_bin`).

### StepExecutor (`llmdbenchmark/executor/step_executor.py`)

Orchestrates step execution. Global steps run sequentially; per-stack steps run in parallel (up to `max_parallel_stacks`). Supports `--step` flag for running specific steps.

---

## Standup Phase — 9 Steps

| Step | Class | Scope | Description |
|------|-------|-------|-------------|
| 00 | `EnsureInfraStep` | Global | Validate tools, detect cluster type (Kind/OpenShift/GKE) |
| 02 | `AdminPrerequisitesStep` | Global | Install CRDs (Gateway API, GAIE, Prometheus), deploy gateway |
| 03 | `WorkloadMonitoringStep` | Global | Validate cluster resources, configure monitoring |
| 04 | `ModelNamespaceStep` | Global | Create namespace, model PVC, download job (pvc:// protocol only) |
| 05 | `HarnessNamespaceStep` | Global | Create harness namespace, workload PVC, data access pod |
| 06 | `StandaloneDeployStep` | Per-Stack | Deploy vLLM as K8s Deployment + Service (**standalone only**) |
| 07 | `DeploySetupStep` | Per-Stack | Deploy gateway infra via helmfile (**modelservice only**) |
| 08 | `DeployGaieStep` | Per-Stack | Deploy EPP, InferencePool (**modelservice only**) |
| 09 | `DeployModelserviceStep` | Per-Stack | Deploy decode/prefill pods via Helm chart (**modelservice only**) |

Steps 06-09 are conditional on `context.deployed_methods`.

## Run Phase — 13 Steps

| Step | Class | Description |
|------|-------|-------------|
| 00 | `RunPreflightStep` | Validate connectivity, namespace exists |
| 01 | `RunCleanupPreviousStep` | Remove prior run artifacts |
| 02 | `HarnessNamespaceStep` | Ensure harness namespace ready |
| 03 | `DetectEndpointStep` | Discover inference endpoint (gateway or standalone) |
| 04 | `VerifyModelStep` | Confirm model is loaded and responding |
| 05 | `RenderProfilesStep` | Render benchmark profile templates |
| 06 | `CreateProfileConfigmapStep` | Upload profiles as K8s ConfigMap |
| 07 | `DeployHarnessStep` | Launch harness pod (inference-perf, guidellm, etc.) |
| 08 | `WaitCompletionStep` | Poll harness pod until complete/timeout |
| 09 | `CollectResultsStep` | kubectl cp results from harness pod |
| 10 | `UploadResultsStep` | Push to GCS/S3 (optional) |
| 11 | `RunCleanupPostStep` | Remove harness pods and ConfigMaps |
| 12 | `AnalyzeResultsStep` | Run local analysis scripts |

## Smoketest Phase — 3 Steps

| Step | Class | Description |
|------|-------|-------------|
| 00 | `HealthCheckStep` | Pod status, /health, /v1/models, service endpoints |
| 01 | `InferenceTestStep` | Sample /v1/completions request |
| 02 | `ValidateConfigStep` | Per-scenario config validation (resources, flags, env) |

Scenario-specific validators: `cpu`, `gpu`, `inference_scheduling`, `pd_disaggregation`, `precise_prefix_cache_aware`, `simulated_accelerators`, `spyre`, `tiered_prefix_cache`, `wide_ep_lws`

## Teardown Phase — 5 Steps

| Step | Class | Description |
|------|-------|-------------|
| 00 | `TeardownPreflightStep` | Preflight checks |
| 01 | `UninstallHelmStep` | Uninstall Helm releases |
| 02 | `CleanHarnessStep` | Clean harness pods and resources |
| 03 | `DeleteResourcesStep` | Delete namespaced resources |
| 04 | `CleanClusterRolesStep` | Delete cluster roles (with `--deep`) |

---

## Deployment Methods

### Standalone

Steps 06 only. Deploys vLLM as a K8s Deployment + Service. Direct endpoint access. No gateway, no CRDs. For dev/testing.

### Modelservice

Steps 07 + 08 + 09. Deploys via llm-d Helm charts: decode pods + prefill pods + EPP routing through a Gateway (Istio/kgateway). Full prefill-decode disaggregation. For production benchmarks.

---

## Scenarios & Configuration

### Scenario Structure

A scenario file overrides `defaults.yaml`:

```yaml
scenario:
  - name: "my-benchmark"
    model:
      name: meta-llama/Llama-3.1-8B
    decode:
      replicas: 2
      parallelism:
        tensor: 4
    harness:
      name: inference-perf
      experimentProfile: chatbot_sharegpt.yaml
```

### Key defaults.yaml Sections

| Section | Purpose |
|---------|---------|
| `model` | Model identifiers (name, path, huggingfaceId, maxModelLen, gpuMemoryUtilization) |
| `namespace` | Deploy and harness namespace names |
| `decode` / `prefill` | Replicas, resources, parallelism, vLLM flags, probes |
| `standalone` | Standalone deployment config (enabled: false by default) |
| `modelservice` | Modelservice config (enabled: true by default, uriProtocol: pvc) |
| `storage` | PVC sizes, storage class, download settings |
| `huggingface` | Token, secret name, enabled flag (auto-detected) |
| `images` | Container image repos/tags (benchmark, vllm, inferenceScheduler, routingSidecar) |
| `vllmCommon` | Shared vLLM settings (ports, KV transfer, flags, volumes) |
| `gateway` | Gateway class, provider namespace |
| `monitoring` | PodMonitor, metrics, installPrometheusCrds |
| `affinity` | Node selector, GPU label matching |
| `accelerator` | GPU count, resource name, memory |
| `harness` | Benchmark harness defaults (name, profile, resources, timeout) |
| `routing` | Proxy sidecar config (enabled, ports) |
| `resourcePresets` | Named presets (small/medium/large/xlarge) |

### HuggingFace Token

Auto-detected from `HF_TOKEN` or `LLMDBENCH_HF_TOKEN`. When no token found, `huggingface.enabled` is set to `false` — skips secret creation, auth login, secretKeyRef mounts. Public models work without token; gated models fail early.

---

## Experiments (Design of Experiments)

### Structure

```yaml
experiment:
  name: "tiered-prefix-cache"
  harness: inference-perf
  profile: shared_prefix_synthetic.yaml

setup:
  constants:
    model.maxModelLen: 16000
  treatments:
    - name: cpu-blocks-500
      vllmCommon.flags.numCpuBlocks: 500
    - name: cpu-blocks-1000
      vllmCommon.flags.numCpuBlocks: 1000

treatments:
  - name: qlen100
    data.shared_prefix.question_len: 100
  - name: qlen300
    data.shared_prefix.question_len: 300
```

**Setup treatments** require re-standup (infra changes: TP, CPU blocks, routing).
**Run treatments** run within a single standup (workload params: concurrency, prompt length).
**Total matrix** = setup treatments × run treatments.

### Key Classes (`llmdbenchmark/experiment/parser.py`)

- `ExperimentPlan` — parsed experiment with `setup_treatments`, `run_treatments_count`, `total_matrix`
- `SetupTreatment` — name + nested overrides dict
- `dotted_to_nested()` — converts `"a.b.c": 1` to `{"a": {"b": {"c": 1}}}`

---

## Workload Profiles & Harnesses

### Harnesses

| Harness | Package | Purpose |
|---------|---------|---------|
| `inference-perf` | kubernetes-sigs/inference-perf | Primary benchmark tool |
| `vllm-benchmark` | vllm-project/vllm | vLLM native benchmark |
| `guidellm` | vllm-project/guidellm | Guided LLM benchmark |
| `inferencemax` | kimbochen/bench_serving | Max throughput benchmark |
| `nop` | — | No-op (testing) |

### Profiles (`workload/profiles/`)

- `chatbot_sharegpt.yaml.in` — real ShareGPT conversation traces
- `chatbot_synthetic.yaml.in` — synthetic chat (configurable ISL/OSL)
- `code_completion_synthetic.yaml.in` — code completion
- `shared_prefix_synthetic.yaml.in` — shared system prompt + questions
- `summarization_synthetic.yaml.in` — long input, short output
- `random_concurrent.yaml.in` — random prompts at fixed concurrency
- `sanity_random.yaml.in` — minimal CI/CD sanity check

Profiles are `.yaml.in` templates rendered with experiment parameters.

---

## Utilities

### Cluster (`llmdbenchmark/utilities/cluster.py`)

- `resolve_cluster()` — connect, detect platform, store kubeconfig
- `kube_connect()` — supports kubeconfig, token, or **in-cluster** auth (`load_incluster_config()`)
- Platform detection: Kind, Minikube, OpenShift, vanilla K8s

### KubeHelpers (`llmdbenchmark/utilities/kube_helpers.py`)

- `CRASH_STATES` — `{CrashLoopBackOff, Error, OOMKilled, ImagePullBackOff, ...}`
- `wait_for_pods_by_label()` — two-phase: Ready=True (running), then Completed
- `find_data_access_pod()` — discover data access pod by label

### Endpoint (`llmdbenchmark/utilities/endpoint.py`)

- `find_standalone_endpoint()` — discover vLLM service IP
- `find_gateway_endpoint()` — discover EPP gateway endpoint
- `test_model_serving()` — test /v1/completions endpoint

### HuggingFace (`llmdbenchmark/utilities/huggingface.py`)

- `check_model_access()` — verify model is accessible (public vs gated)

---

## Extending the Framework

### Adding a New Step

1. Create `llmdbenchmark/<phase>/steps/step_XX_<name>.py`
2. Implement the Step subclass:

```python
from llmdbenchmark.executor.step import Step, StepResult, Phase

class MyStep(Step):
    def __init__(self):
        super().__init__(
            number=XX, name="my_step",
            description="What this step does",
            phase=Phase.STANDUP,  # or RUN, TEARDOWN, SMOKETEST
            per_stack=True,       # or False for global steps
        )

    def should_skip(self, context):
        return "modelservice" not in context.deployed_methods

    def execute(self, context, stack_path):
        cmd = context.require_cmd()
        result = cmd.kube("get", "pods", namespace=context.namespace)
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=result.success,
            message="Done",
        )
```

3. Register in `llmdbenchmark/<phase>/steps/__init__.py`

### Adding a New Scenario

Create a YAML file in `config/scenarios/<category>/` that overrides `defaults.yaml` values. Then create a matching spec file in `config/specification/<category>/` pointing to it.

### Adding a New Template

Create a Jinja2 file in `config/templates/jinja/` with numeric prefix (e.g., `24_my_template.yaml.j2`). It auto-renders during plan phase. Use `_macros.j2` for shared helpers.

---

## Exceptions

| Exception | Module | Purpose |
|-----------|--------|---------|
| `TemplateError` | `llmdbenchmark.exceptions` | Jinja2 rendering errors |
| `ConfigurationError` | `llmdbenchmark.exceptions` | Config validation errors |
| `ExecutionError` | `llmdbenchmark.exceptions` | Step execution failures |
| `PhaseError` | `llmdbenchmark.exceptions` | Lifecycle phase errors |

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `LLMDBENCH_SPEC` | Specification file |
| `LLMDBENCH_WORKSPACE` | Workspace directory |
| `LLMDBENCH_BASE_DIR` | Project root |
| `LLMDBENCH_NAMESPACE` | Namespace |
| `LLMDBENCH_METHODS` | Deploy methods |
| `LLMDBENCH_MODELS` | Model list |
| `LLMDBENCH_HARNESS` | Harness name |
| `LLMDBENCH_WORKLOAD` | Workload profile |
| `LLMDBENCH_HF_TOKEN` / `HF_TOKEN` | HuggingFace token |
| `LLMDBENCH_KUBECONFIG` / `KUBECONFIG` | Kubeconfig path |
| `LLMDBENCH_OUTPUT` | Results destination |
| `LLMDBENCH_ENDPOINT_URL` | Explicit endpoint (run-only mode) |

---

## CI/CD Workflows

| Workflow | Purpose |
|----------|---------|
| `ci-pr-benchmark.yaml` | Unit tests + Standalone (Kind) + Modelservice (Kind) in parallel |
| `ci-pr-plan-rendering-validation.yaml` | Renders all 15 specs to catch template breakage |
| `ci-pr-markdown-validation.yaml` | Validates markdown links |

Kind simulation uses `llm-d-inference-sim` (no GPU required).

---

## Required System Tools

Managed by `install.sh`:

**Required:** curl, git, kubectl (or oc), helm, helm-diff plugin, helmfile, kustomize, jq, yq, skopeo, crane

**Optional:** oc (OpenShift CLI)

---

## Key File Reference

| Path | Purpose |
|------|---------|
| `llmdbenchmark/cli.py` | CLI entry point, argument parsing, phase dispatch |
| `llmdbenchmark/executor/step.py` | Step, StepResult, Phase, ExecutionResult |
| `llmdbenchmark/executor/context.py` | ExecutionContext dataclass |
| `llmdbenchmark/executor/command.py` | CommandExecutor (kubectl/helm wrapper) |
| `llmdbenchmark/executor/step_executor.py` | Step orchestration |
| `llmdbenchmark/parser/render_plans.py` | Template rendering engine |
| `llmdbenchmark/parser/render_specification.py` | Spec file resolution |
| `llmdbenchmark/parser/config_schema.py` | Pydantic validation |
| `llmdbenchmark/utilities/cluster.py` | Cluster connectivity |
| `llmdbenchmark/utilities/kube_helpers.py` | Pod/job/PVC waiting |
| `llmdbenchmark/utilities/endpoint.py` | Endpoint discovery |
| `config/templates/values/defaults.yaml` | Base configuration (1296 lines) |
| `config/templates/jinja/_macros.j2` | Shared Jinja2 macros |
| `install.sh` | System tool installation |
| `build/Dockerfile` | Harness container image |
