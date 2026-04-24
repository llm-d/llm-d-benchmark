# llmdbenchmark.standup

Standup phase of the benchmark lifecycle. Provisions infrastructure, creates namespaces, deploys model-serving pods, and validates deployment health.

## Step Ordering

Steps are registered in `steps/__init__.py` via `get_standup_steps()` and execute in order:

| Step | Name | Scope | Description |
|------|------|-------|-------------|
| 00 | `EnsureInfraStep` | global | Validate system dependencies (kubectl, helm, etc.) and print cluster summary banner |
| 02 | `AdminPrerequisitesStep` | global | Install cluster-level admin prerequisites (CRDs, gateways, LeaderWorkerSet, SCCs) |
| 03 | `WorkloadMonitoringStep` | global | Validate cluster resources and configure workload monitoring (PodMonitors). Installs WVA controller once per `wva.namespace` across all rendered stacks. |
| 04 | `ModelNamespaceStep` | global | Prepare the model namespace. Creates one shared model PVC (idempotent across stacks) and one download Job per stack with `modelservice.uriProtocol: pvc` (or standalone). Jobs are launched in parallel (phase 1) and waited on in turn (phase 2), so total wall time ~ slowest model. Every stack's weights live in a distinct `model.path` subdirectory on the shared PVC. |
| 05 | `HarnessNamespaceStep` | global | Prepare the harness namespace (scenario-wide workload PVC, data access pod, secrets) |
| 06 | `FMADeployStep` | global | Deploy FMA controllers |
| 06 | `StandaloneDeployStep` | global | Deploy vLLM as standalone Kubernetes Deployments and Services |
| 08 | `DeploySetupStep` | global | Set up Helm repos and deploy gateway infrastructure for modelservice mode |
| 09 | `DeployGaieStep` | global | Deploy GAIE (Gateway API Inference Extension) |
| 10 | `DeployModelserviceStep` | global | Deploy the model via the llm-d modelservice Helm chart |

Note: Step 01 is intentionally absent (reserved). Steps 10 and 11 (smoketest and inference test) were moved to the `llmdbenchmark.smoketests` module and now run as a separate phase after standup.

## Deployment Methods

Steps 06-09 handle two mutually exclusive deployment methods:

- **FMA** (step 06) -- Deploys Fast Model Actuation controllers. For more information on FMA: https://github.com/llm-d-incubation/llm-d-fast-model-actuation
- **Standalone** (step 06) -- Deploys vLLM directly as Kubernetes Deployments and Services. OpenShift routes use the naming pattern `sa-{model_id_label}-route` to stay within the 63-character DNS label limit. Step 06 is skipped when modelservice is the active method.
- **Modelservice** (steps 08-10) -- Deploys via the llm-d modelservice Helm chart with gateway infrastructure and GAIE. Steps 07-09 are skipped when standalone is the active method.

The `should_skip()` method on each step checks `context.deployed_methods` to determine which path to take.

## Post-Standup Smoketests

After standup completes, smoketests run automatically as a separate phase. The smoketest phase (in `llmdbenchmark.smoketests`) has three steps:

1. **Health check** (step 00) -- Pod status, `/health`, `/v1/models`, service reachability, pod direct IP, OpenShift route.
2. **Inference test** (step 01) -- Sends a sample request via `/v1/completions` (falls back to `/v1/chat/completions`), logs the response and a demo curl command.
3. **Config validation** (step 02) -- Per-scenario validators compare live pod specs against the rendered config.

Use `--skip-smoketest` to skip the automatic post-standup smoketests. They can also be run independently via `llmdbenchmark smoketest`. See [smoketests/README.md](../smoketests/README.md) for details.

## `-f` / `--monitoring` Flag

When passed, `-f` enables monitoring infrastructure during standup:

- Creates PodMonitor resources for Prometheus to scrape vLLM pods
- Sets EPP (inference scheduler) log verbosity to level 4 for detailed scheduling diagnostics

This is separate from the run-phase `-f` flag, which controls metrics scraping and log capture during benchmark execution.

## Dry-Run Behavior

In dry-run mode:

- Step 00 still connects to the cluster and resolves metadata (needed for subsequent commands).
- Steps 02-09 log the commands they would execute without applying them. Commands wrapped in `cmd.kube()`, `cmd.helm()`, and `cmd.execute()` return dry-run `CommandResult` objects. Wait helpers (`wait_for_pods`, `wait_for_pvc`) return success immediately.

## preprocess/ Subdirectory

Contains scripts executed during standalone deployment setup:

| File | Description |
|------|-------------|
| `set_llmdbench_environment.py` | Network environment detection (IP addresses, RDMA/IB devices, GID mapping) for NIXL connectivity |
| `standalone-preprocess.py` | Serialize tensorizer files if needed; runs as a pre-deployment step |

## Files

```
standup/
+-- __init__.py              -- Package marker
+-- preprocess/
|   +-- set_llmdbench_environment.py
|   +-- standalone-preprocess.py
+-- steps/
    +-- __init__.py           -- Step registry (get_standup_steps)
    +-- step_00_ensure_infra.py
    +-- step_02_admin_prerequisites.py
    +-- step_03_workload_monitoring.py
    +-- step_04_model_namespace.py
    +-- step_05_harness_namespace.py
    +-- step_06_fma_deploy.py
    +-- step_06_standalone_deploy.py
    +-- step_07_deploy_setup.py
    +-- step_08_deploy_gaie.py
    +-- step_09_deploy_modelservice.py
```
