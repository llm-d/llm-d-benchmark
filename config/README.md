# Configuration

All declarative configuration for `llmdbenchmark` lives in this directory. The three subdirectories correspond to the inputs consumed by the plan phase rendering pipeline.

## Table of Contents

- [Directory Layout](#directory-layout)
- [How the Pieces Fit Together](#how-the-pieces-fit-together)
- [Config Override Chain](#config-override-chain)
  - [Method 1: Scenario File](#method-1-scenario-file-recommended-for-deployment-specific-config)
  - [Method 2: Environment Variables](#method-2-environment-variables-for-shellci-defaults)
  - [Method 3: CLI Arguments](#method-3-cli-arguments-highest-priority-runtime-overrides)
  - [Method 4: Experiment Treatments](#method-4-experiment-treatments-for-parameter-sweeps)
- [Templates](#templates)
  - [Jinja2 Templates](#templatesjinja)
  - [defaults.yaml](#templatesvaluesdefaultsyaml)
- [KV Transfer Configuration](#kv-transfer-configuration)
- [Resource Configuration](#resource-configuration)
  - [Ephemeral Storage](#ephemeral-storage)
  - [Network Resources (RDMA/InfiniBand)](#network-resources-rdmainfiniband)
  - [Accelerator Resources](#accelerator-resources)
- [Affinity Configuration](#affinity-configuration)
- [Scenario Organization](#scenario-organization)
- [vLLM Command Generation](#vllm-command-generation)
- [Init Containers](#init-containers)
- [Monitoring and Metrics](#monitoring-and-metrics)
- [Container Images](#container-images)
  - [Image Config Paths](#image-config-paths)
  - [Which Template Uses Which Image](#which-template-uses-which-image)
  - [Fallback Chains](#fallback-chains)
  - [Overriding Images](#overriding-images)
- [Scenarios](#scenarios)
  - [Guide Scenarios](#scenariosguides)
  - [Example Scenarios](#scenariosexamples)
  - [CI/CD Scenarios](#scenarioscicd)
  - [Creating a New Scenario](#creating-a-new-scenario)
- [Specifications](#specifications)
  - [Required Fields](#required-fields)
  - [Optional Fields](#optional-fields)
  - [Specification Auto-Discovery](#specification-auto-discovery)
  - [The base_dir Variable](#the-base_dir-variable)
  - [Creating a New Specification](#creating-a-new-specification)
  - [Naming and Collisions](#naming-and-collisions)
  - [Experiments](#experiments)
  - [Available Specifications](#available-specifications)
- [Usage](#usage)

---

## Directory Layout

```text
config/
    templates/
        jinja/                  Jinja2 templates that produce Kubernetes manifests
            _macros.j2          Shared macros (vLLM command generation, etc.)
            01_pvc_workload-pvc.yaml.j2    ... through 23_wva-namespace.yaml.j2
        values/
            defaults.yaml       Base configuration with all anchored defaults

    scenarios/                  Deployment overrides (merged on top of defaults)
        guides/                 Well-lit-path guide scenarios
        examples/               Minimal working examples (cpu, gpu, spyre)
        cicd/                   CI/CD pipeline environments

    specification/              Plan specifications (entry points for the CLI)
        guides/                 Well-lit-path guide specifications
        examples/               Minimal working specifications
        cicd/                   CI/CD pipeline specifications
```

## How the Pieces Fit Together

![Rendering Pipeline](../docs/images/rendering-pipeline.svg)

## Config Override Chain

Values are merged in a strict priority order during the plan phase. Later sources override earlier ones:

![Config Override Chain](../docs/images/config-override-chain.svg)

The merged result is written as `config.yaml` inside each rendered stack directory. This is the **single source of truth** for all step execution. Steps never define their own fallback defaults -- they read from `config.yaml` using `_require_config()` and raise a clear error if a required key is missing.

### How to Override Values

#### Method 1: Scenario File (recommended for deployment-specific config)

Create a scenario YAML under `config/scenarios/` that overrides only the values you need:

```yaml
scenario:
  - name: "my-deployment"
    model:
      name: Qwen/Qwen3-32B
      shortName: qwen-qwen3-32b
    decode:
      replicas: 4
    namespace:
      name: my-namespace
```

Only the keys you specify are overridden. Everything else comes from `defaults.yaml`.

**Example: GPU scenario with a custom vLLM image**

The GPU example uses standalone deployment, so the container image is set under `standalone.image` (not `images.vllm`, which is the fallback for modelservice deployments). See [Container Images](#container-images) for the full image config reference.

```yaml
scenario:
  - name: "gpu-custom-vllm"
    standalone:
      enabled: true
      image:
        repository: docker.io/vllm/vllm-openai
        tag: v0.8.5
      replicas: 1
      parallelism:
        tensor: 1
    namespace:
      name: my-gpu-ns
```

```bash
llmdbenchmark --spec gpu standup -c config/scenarios/my-gpu-custom.yaml
```

For modelservice deployments (e.g. `inference-scheduling`), override `images.vllm` instead:

```yaml
scenario:
  - name: "ms-custom-vllm"
    images:
      vllm:
        repository: ghcr.io/llm-d/llm-d-cuda
        tag: v0.5.0
```

The deployed image is recorded in the `llm-d-benchmark-standup-parameters` ConfigMap for audit.

#### Method 2: Environment Variables (for shell/CI defaults)

Export `LLMDBENCH_*` environment variables to set defaults without passing CLI flags every time. Env vars override scenario/defaults values but are themselves overridden by explicit CLI flags.

```bash
# Set common defaults in .bashrc or CI pipeline
export LLMDBENCH_SPEC=inference-scheduling
export LLMDBENCH_NAMESPACE=my-team-ns
export LLMDBENCH_KUBECONFIG=~/.kube/my-cluster
export LLMDBENCH_DRY_RUN=true

# Now run without repeating flags
llmdbenchmark standup
llmdbenchmark standup -p override-ns   # CLI -p wins over LLMDBENCH_NAMESPACE
```

Boolean env vars accept `1`, `true`, or `yes` (case-insensitive). See the [CLI Reference](../README.md#cli-reference) for the full mapping of flags to env var names. Active overrides are logged at startup.

#### Method 3: CLI Arguments (highest priority, runtime overrides)

CLI arguments override both defaults, scenario values, and environment variables:

```bash
# Override namespace
llmdbenchmark --spec my-spec.yaml.j2 standup -p my-namespace

# Override deployment method
llmdbenchmark --spec my-spec.yaml.j2 standup -t standalone

# Override model
llmdbenchmark --spec my-spec.yaml.j2 standup -m "meta-llama/Llama-3.1-8B"

# Override Helm release name
llmdbenchmark --spec my-spec.yaml.j2 standup -r my-release

# Combine multiple overrides
llmdbenchmark --spec my-spec.yaml.j2 standup -p my-ns -t modelservice -r my-release
```

#### Method 4: Experiment Treatments (for parameter sweeps)

Specification files can define experiments with setup and run treatments that generate multiple stacks with different parameter values:

```yaml
experiments:
  - name: "replica-sweep"
    attributes:
      - name: "setup"
        factors:
          - name: decode.replicas
            levels: [1, 2, 4]
        treatments:
          - decode.replicas: 1
          - decode.replicas: 2
          - decode.replicas: 4
```

Each treatment produces a separate rendered stack, enabling parallel deployment and comparison.

---

## Templates

### `templates/jinja/`

Jinja2 templates that produce Kubernetes resource definitions. Each template corresponds to a specific infrastructure component:

| Template | Output |
|----------|--------|
| `01_pvc_workload-pvc.yaml.j2` | Workload PVC for harness data |
| `02_pvc_model-pvc.yaml.j2` | Model storage PVC |
| `03_cluster-monitoring-config.yaml.j2` | OpenShift workload monitoring config |
| `04_download_job.yaml.j2` | Model download Job |
| `05_namespace_sa_rbac_secret.yaml.j2` | Namespace, ServiceAccount, RBAC, secrets |
| `06_pod_access_to_harness_data.yaml.j2` | Harness data access pod |
| `07_service_access_to_harness_data.yaml.j2` | Harness data access service |
| `08_httproute.yaml.j2` | HTTPRoute for inference gateway |
| `09_helmfile-gateway-provider.yaml.j2` | Helmfile for gateway provider (Istio/kgateway) |
| `10_helmfile-main.yaml.j2` | Main helmfile (llm-d-infra, modelservice) |
| `11_infra.yaml.j2` | Infrastructure chart values |
| `12_gaie-values.yaml.j2` | GAIE (inference extension) Helm values |
| `13_ms-values.yaml.j2` | Modelservice Helm values |
| `14_standalone-deployment_yaml.j2` | Standalone vLLM Deployment |
| `15_standalone-service_yaml.j2` | Standalone vLLM Service |
| `16_pvc_extra-pvc.yaml.j2` | Extra PVCs (e.g., scratch space) |
| `17_standalone-podmonitor.yaml.j2` | Standalone PodMonitor for metrics |
| `18_podmonitor.yaml.j2` | Modelservice PodMonitor for metrics |
| `19_wva-values.yaml.j2` | Workload Variant Autoscaler values |
| `20_harness_pod.yaml.j2` | Benchmark harness pod |
| `21_prometheus-adapter-values.yaml.j2` | Prometheus adapter values |
| `22_prometheus-rbac.yaml.j2` | Prometheus RBAC resources |
| `23_wva-namespace.yaml.j2` | WVA namespace resources |
| `_macros.j2` | Shared Jinja2 macros (vLLM command gen, etc.) |

Templates use Jinja2 conditionals to skip rendering when their feature is disabled. For example, standalone templates only render when `standalone.enabled` is `true`. Steps check for empty rendered files via `_has_yaml_content()` and skip applying them.

### `templates/values/defaults.yaml`

The base configuration file containing every configurable parameter with sensible defaults. Uses YAML anchors extensively for DRY references across sections.

**Key sections:**

| Section | Purpose |
|---------|---------|
| `_anchors` | Reusable YAML anchors for ports, resources, probes, parallelism |
| `model` | Model identifiers, paths, cache settings |
| `namespace` | Deploy and harness namespace names |
| `release` | Helm release name prefix |
| `gateway` | Gateway class and provider configuration |
| `serviceAccount` | Service account name and configuration |
| `huggingface` | HuggingFace token secret name and key |
| `storage` | PVC sizes, storage class, download settings |
| `decode` | Decode pod configuration (replicas, resources, vLLM settings) |
| `prefill` | Prefill pod configuration (disabled by default) |
| `standalone` | Standalone deployment settings (disabled by default) |
| `modelservice` | Modelservice deployment settings (enabled by default) |
| `images` | Container image repositories, tags, and pull policies |
| `vllmCommon` | Shared vLLM settings (ports, KV transfer, flags, volumes) |
| `harness` | Benchmark harness configuration |
| `wva` | Workload Variant Autoscaler settings |
| `control` | Context secret name |
| `lws` | LeaderWorkerSet configuration |
| `kgateway` | kgateway provider configuration |
| `openshiftMonitoring` | OpenShift-specific monitoring settings |
| `inferenceExtension` | GAIE plugin configuration |

**YAML anchors:** The file uses anchors (`&name`) and aliases (`*name`) to ensure consistency. For example, `&vllm_service_port` is defined once as `8000` and referenced by `decode.vllm.servicePort`, `prefill.vllm.servicePort`, and `vllmCommon.inferencePort`.

## KV Transfer Configuration

The `vllmCommon.kvTransfer` section controls the `--kv-transfer-config` argument passed to the `vllm serve` command. This is how vLLM knows which KV cache transfer connector to use and how to configure it.

#### Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `kvTransfer.enabled` | `bool` | `false` | Append `--kv-transfer-config` to the vLLM serve command |
| `kvTransfer.connector` | `str` | `NixlConnector` | KV connector class name (e.g. `NixlConnector`, `OffloadingConnector`, `FileSystemConnector`) |
| `kvTransfer.role` | `str` | `kv_both` | KV role: `kv_both`, `kv_producer`, or `kv_consumer` |
| `kvTransfer.extraConfig` | `dict\|null` | `null` | Arbitrary key-value pairs passed as `kv_connector_extra_config` |

#### How it works

The `build_kv_transfer_config()` macro in `_macros.j2` assembles the JSON from these fields. When `extraConfig` is omitted or `null`, the output contains only `kv_connector` and `kv_role`. When `extraConfig` is set, it is serialized as `kv_connector_extra_config` inside the same JSON object.

The macro is called automatically when `kvTransfer.enabled: true` --both in the default vLLM serve command and when using `customCommand`.

#### Override examples

**Standard P/D disaggregation (NixlConnector):**

```yaml
# In your scenario file
vllmCommon:
  kvTransfer:
    enabled: true
    connector: NixlConnector
    role: kv_both
```

Produces: `--kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_both"}'`

**Tiered prefix cache (OffloadingConnector with extra config):**

```yaml
vllmCommon:
  kvTransfer:
    enabled: true
    connector: OffloadingConnector
    role: kv_both
    extraConfig:
      num_cpu_blocks: 5000
      cpu_bytes_to_use: 1000000000
```

Produces: `--kv-transfer-config '{"kv_connector":"OffloadingConnector","kv_role":"kv_both","kv_connector_extra_config":{"num_cpu_blocks":5000,"cpu_bytes_to_use":1000000000}}'`

**FileSystem connector:**

```yaml
vllmCommon:
  kvTransfer:
    enabled: true
    connector: FileSystemConnector
    role: kv_both
    extraConfig:
      storage_path: /mnt/kv-cache
```

Produces: `--kv-transfer-config '{"kv_connector":"FileSystemConnector","kv_role":"kv_both","kv_connector_extra_config":{"storage_path":"/mnt/kv-cache"}}'`

**Disabling KV transfer (default):**

```yaml
vllmCommon:
  kvTransfer:
    enabled: false
```

No `--kv-transfer-config` flag is added to the vLLM serve command.

#### Relationship to customCommand

Before `extraConfig` was available, scenarios that needed `kv_connector_extra_config` had to use `customCommand` and hardcode the entire `--kv-transfer-config` JSON inline (see `tiered-prefix-cache.yaml` for an example). With `extraConfig`, this workaround is no longer necessary --the macro handles it. Note that when `customCommand` is used, the macro still appends `--kv-transfer-config` if `kvTransfer.enabled: true`, so set `enabled: false` if you are handling it in `customCommand` to avoid duplication.

#### Defaults chain

The global defaults in `defaults.yaml` set:

```yaml
_internal:
  kv_connector: &kv_connector NixlConnector
  kv_role: &kv_role kv_both

vllmCommon:
  kvTransfer:
    enabled: false
    connector: *kv_connector    # NixlConnector
    role: *kv_role              # kv_both
    # extraConfig: null         # not set by default
```

A scenario file overrides any of these fields under `vllmCommon.kvTransfer`. The override is a full merge --you must include `enabled`, `connector`, and `role` in your scenario if you want them to differ from defaults.

---

## Resource Configuration

Pod resource limits and requests are configured per role (decode/prefill) in the scenario YAML. The template reads `memory` and `cpu` from `resources.limits` and `resources.requests`, but some resource types use dedicated fields.

### Ephemeral Storage

Ephemeral storage is configured via a dedicated field, **not** inside the `resources` block:

```yaml
vllmCommon:
  ephemeralStorage: 1Ti    # applied to both decode and prefill
```

Or per role:

```yaml
decode:
  ephemeralStorage: 1Ti
prefill:
  ephemeralStorage: 500Gi
```

The resource name used in the rendered output is controlled by `vllmCommon.ephemeralStorageResource` (default: `ephemeral-storage`). Values placed in `resources.limits.ephemeral-storage` are **not** read by the template and will be silently ignored.

### Network Resources (RDMA/InfiniBand)

Network resources for high-performance interconnects are configured via:

```yaml
vllmCommon:
  networkResource: "auto"   # auto-detect from cluster nodes
  networkNr: "1"            # number of network devices
```

When `networkResource` is set to `"auto"`, the `ClusterResourceResolver` queries the cluster nodes at render time and discovers available RDMA/IB resources (e.g., `rdma/roce_gdr`, `rdma/ib`). The resolved resource name and count are then rendered into the pod resource limits and requests.

**Requirements:** Auto-detection requires cluster connectivity. Running `plan` without a cluster when `networkResource: "auto"` is set will fail with a clear error. Scenarios that don't need RDMA/IB should not set this field (the default is empty).

### Accelerator Resources

The accelerator resource for GPU/TPU/other devices is configured at the top level:

```yaml
accelerator:
  type: nvidia               # accelerator family
  resource: "nvidia.com/gpu"  # Kubernetes resource name
```

Per-role overrides are supported:

```yaml
decode:
  accelerator:
    resource: ibm.com/spyre_vf  # override for non-NVIDIA accelerators
    count: 1                     # explicit device count (defaults to tensor parallelism)
```

When `count` is not set, it defaults to `decode.parallelism.tensor`. Set `count` explicitly when the accelerator count differs from tensor parallelism (e.g., Spyre devices where 1 device supports multiple tensor parallel ranks).

---

## Affinity Configuration

Node affinity controls which cluster nodes pods are scheduled on. Configured under the top-level `affinity` section in `defaults.yaml` or per scenario.

#### Modes

| Mode | Config | Behavior |
|------|--------|----------|
| **Disabled** (default) | `affinity.enabled: false` or omit entirely | Pods schedule on any available node |
| **Explicit** | `affinity.enabled: true` with `nodeSelector` labels | Pods only schedule on nodes matching the specified labels |
| **Auto** | Pass `--affinity auto` via CLI or set `LLMDBENCH_AFFINITY=auto` | Auto-detects GPU/accelerator labels from cluster nodes at render time |

#### Explicit example

```yaml
affinity:
  enabled: true
  nodeSelector:
    nvidia.com/gpu.product: NVIDIA-H100-80GB-HBM3
```

#### Optional pod affinity/anti-affinity

The `affinity` section also supports `podAffinity` and `podAntiAffinity` rules for co-locating or spreading pods across nodes:

```yaml
affinity:
  enabled: true
  nodeSelector:
    nvidia.com/gpu.product: NVIDIA-H100-80GB-HBM3
  podAntiAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          topologyKey: kubernetes.io/hostname
```

---

## Scenario Organization

Scenario files use comment headers to organize settings into three categories:

- **COMMON** -- settings that apply to all deployment methods (model config, namespace, decode/prefill resources, vllmCommon, monitoring, etc.)
- **STANDALONE** -- settings that only apply when `standalone.enabled: true` (standalone image, replicas, volumes)
- **MODELSERVICE** -- settings that only apply when `modelservice.enabled: true` (Helm chart values, gateway config, GAIE settings)

Each scenario must set either `standalone.enabled: true` or `modelservice.enabled: true` (or both, though in practice only one is used). Templates use Jinja2 conditionals to skip rendering when the corresponding flag is `false`.

---

## vLLM Command Generation

The `build_vllm_command()` macro in `_macros.j2` assembles the `vllm serve` command from config values. When no `customCommand` is set on a role (decode/prefill), the macro builds the command from:

- Model path and `--served-model-name`
- Parallelism settings (`--tensor-parallel-size`, `--pipeline-parallel-size`, `--data-parallel-size`)
- Port (`--port`)
- KV transfer config (when `kvTransfer.enabled: true`)
- Additional vLLM flags from `vllmCommon.extraArgs` and per-role `extraArgs`

When `customCommand` is set on a role, it is used verbatim. The KV transfer macro still appends `--kv-transfer-config` if `kvTransfer.enabled: true`, so set `enabled: false` if you handle it in the custom command.

---

## Init Containers

Init containers run before the main vLLM container to perform environment setup tasks such as network configuration (RDMA/InfiniBand route tables), hardware detection, and environment variable preparation.

#### How it works

1. The init container runs the benchmark image with `set_llmdbench_environment.py -i` (init container mode)
2. It writes environment configuration to `/shared-config/llmdbench_env.sh` on a shared emptyDir volume
3. The main vLLM container sources this file via `preprocessScript: "source /shared-config/llmdbench_env.sh"`

The `shared-config` emptyDir volume and volumeMount are already configured in `defaults.yaml` under `vllmCommon.volumes` and `vllmCommon.volumeMounts`.

#### Image resolution

Init container images follow the same `auto` resolution as all other images in the benchmark. The resolution works through two layers:

1. **`images.benchmark` entry** (in `defaults.yaml`): Defines the benchmark image with `tag: auto`. During rendering, the `VersionResolver` resolves this tag to the latest available tag via `skopeo list-tags` (or `podman` as fallback).

2. **Template defaulting** (in `13_ms-values.yaml.j2`): When rendering init containers, if an init container's `image` field is missing or ends with `:auto`, the template automatically replaces it with the resolved `images.benchmark.repository:images.benchmark.tag`.

This means:
- You can set `image: ghcr.io/llm-d/llm-d-benchmark:auto` --the `:auto` suffix is resolved at render time
- You can omit the `image` field entirely --it defaults to the benchmark image
- You can set a specific image (e.g., `image: my-registry/my-init:v1.0`) --it is used as-is

The resolved image tag is visible in the rendered `ms-values.yaml` in the workspace directory.

#### Scenario configuration

Init containers are configured per scenario (the default in `defaults.yaml` is `initContainers: []`). Each guide scenario explicitly defines the preprocess init container:

```yaml
decode:
  initContainers:
    - name: preprocess
      image: ghcr.io/llm-d/llm-d-benchmark:auto  # resolved to latest tag at render time
      imagePullPolicy: Always
      command: ["set_llmdbench_environment.py", "-e", "/shared-config/llmdbench_env.sh", "-i"]
      securityContext:
        capabilities:
          add:
            - IPC_LOCK
            - SYS_RAWIO
      volumeMounts:
        - name: shared-config
          mountPath: /shared-config
```

The `securityContext` capabilities vary by scenario:
- `IPC_LOCK` and `SYS_RAWIO` are the base capabilities needed for most deployments
- `NET_ADMIN` and `NET_RAW` are additionally required for scenarios that need network configuration (route tables, InfiniBand detection) --e.g., `precise-prefix-cache-aware` and `tiered-prefix-cache`
- Scenarios like `inference-scheduling` and `pd-disaggregation` use only the base capabilities

For scenarios with prefill pods (e.g., `pd-disaggregation`, `wide-ep-lws`), add the same block under the `prefill` section as well.

#### Custom preprocessing

To use a different preprocessing script, change the `command` and/or `image`:

```yaml
decode:
  initContainers:
    - name: preprocess
      image: my-registry/my-init:v1.0  # custom image --used as-is, no auto-resolution
      command: ["my-setup-script.sh", "-o", "/shared-config/llmdbench_env.sh"]
      volumeMounts:
        - name: shared-config
          mountPath: /shared-config
```

The script must write a sourceable shell file to `/shared-config/llmdbench_env.sh` --the main container's `preprocessScript` sources it on startup.

#### Adding additional init containers

```yaml
decode:
  initContainers:
    - name: preprocess
      image: ghcr.io/llm-d/llm-d-benchmark:auto
      command: ["set_llmdbench_environment.py", "-e", "/shared-config/llmdbench_env.sh", "-i"]
      volumeMounts:
        - name: shared-config
          mountPath: /shared-config
    - name: my-custom-init
      image: my-registry/my-init:latest
      command: ["my-setup-script.sh"]
```

#### Disabling init containers

Omit the `initContainers` field or leave it as the default (`[]`). CI/simulated scenarios like `simulated-accelerators` don't define init containers.

---

## Harness Entrypoint Configuration

The harness entrypoint is the shell script executed inside the harness pod to orchestrate benchmark execution, metrics collection, and in-container analysis. By default, the entrypoint is `llm-d-benchmark.sh`, but it can be overridden per scenario.

#### Configuration

The entrypoint is read from `harness.entrypoint` in the plan config (which comes from `defaults.yaml` or a scenario override). If not set, it defaults to `llm-d-benchmark.sh`.

```yaml
harness:
  entrypoint: llm-d-benchmark.sh  # default
```

#### Custom entrypoints

To use a different entrypoint script (e.g., for a custom harness or specialized workflow):

```yaml
harness:
  entrypoint: my-custom-entrypoint.sh
```

The custom script must be present in the harness container image. It receives the same environment variables as the default entrypoint (`LLMDBENCH_*` variables, kubeconfig, namespace, results directory, etc.).

#### What the default entrypoint does

The `llm-d-benchmark.sh` entrypoint handles:

1. **Kubeconfig setup** -- Configures cluster access inside the pod using the base64-encoded context from `LLMDBENCH_BASE64_CONTEXT_CONTENTS`
2. **Pre-benchmark metrics scrape** -- Collects baseline vLLM metrics before the benchmark starts
3. **Harness execution** -- Runs the selected harness script (e.g., `inference-perf-llm-d-benchmark.sh`) with retry logic
4. **Post-benchmark metrics scrape** -- Collects final vLLM metrics after the benchmark completes
5. **In-container analysis** -- Runs analyzer scripts to produce benchmark reports

---

## Flow Control Configuration

Flow control is an EPP (inference scheduler) feature that manages request queuing and load distribution. When enabled, the EPP buffers requests based on pool capacity rather than sending them immediately to pods.

#### Enabling flow control

Flow control is configured through the GAIE plugin configuration. The specific plugin config file is set in the scenario YAML:

```yaml
inferenceExtension:
  plugins:
    configFile: flow-control-config  # name of the plugin config
```

#### Monitoring flow control

When flow control is active, additional Prometheus metrics are emitted by the EPP pod (see [Monitoring and Metrics](#monitoring-and-metrics) below for the full list). To scrape these metrics, enable EPP monitoring:

```yaml
inferenceExtension:
  monitoring:
    prometheus:
      enabled: true
    interval: "10s"
```

#### Using flow control in experiments

To compare performance with and without flow control, define setup treatments that vary the plugin configuration:

```yaml
setup:
  factors:
    - LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE
  levels:
    LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE: "default,flow-control-config"
  treatments:
    - "default"
    - "flow-control-config"
```

---

## Monitoring and Metrics

The benchmark supports Prometheus-based monitoring at three levels: global monitoring configuration, per-deployment PodMonitors, and EPP (inference scheduler) metrics.

#### Global monitoring settings

Configured under the top-level `monitoring` section in `defaults.yaml`:

| Field | Default | Description |
|---|---|---|
| `monitoring.enabled` | `true` | Enable monitoring infrastructure |
| `monitoring.enableUserWorkload` | `true` | Enable OpenShift user workload monitoring |
| `monitoring.podmonitor.enabled` | `true` | Create PodMonitor resources for Prometheus scraping |
| `monitoring.metricsPath` | `/metrics` | Prometheus scrape path |
| `monitoring.scrapeInterval` | `"30s"` | Prometheus scrape interval |

When `monitoring.enabled` is `true` and running on OpenShift, the `03_cluster-monitoring-config.yaml.j2` template renders a ConfigMap to enable user workload monitoring.

#### Per-deployment PodMonitors

Decode and prefill sections have their own `monitoring.podmonitor` config that controls PodMonitor creation:

```yaml
decode:
  monitoring:
    podmonitor:
      enabled: true
      portName: "metrics"
      path: "/metrics"
      interval: "30s"
      labels: {}
      annotations: {}
      relabelings: []
      metricRelabelings: []
```

When `podmonitor.enabled: true`, the templates `17_standalone-podmonitor.yaml.j2` (standalone) or `18_podmonitor.yaml.j2` (modelservice) render PodMonitor CRDs that tell Prometheus to scrape vLLM pods.

**Metrics exposed by vLLM pods** (scraped via PodMonitor):
- `vllm:kv_cache_usage_perc` --KV cache utilization
- `vllm:num_requests_running` --active requests in batch
- `vllm:num_requests_waiting` --queued requests
- `vllm:prompt_tokens_total` --prefill token count
- `vllm:generation_tokens_total` --decode token count
- `vllm:prefix_cache_hits_total` / `vllm:prefix_cache_queries_total` --cache hit rate

#### EPP (Inference Scheduler) monitoring

The inference extension has its own monitoring config under `inferenceExtension.monitoring`:

```yaml
inferenceExtension:
  monitoring:
    secretName: kv-events-gateway-sa-metrics-reader-secret
    interval: "10s"
    prometheus:
      enabled: true
      auth:
        enabled: true
```

This creates a ServiceMonitor for the EPP pod, enabling Prometheus to scrape inference scheduler metrics:
- `inference_extension_scheduler_e2e_duration_seconds` --scheduling latency
- `inference_pool_average_kv_cache_utilization` --pool-wide cache utilization
- `inference_pool_average_queue_size` --average request queue depth
- `inference_pool_ready_pods` --ready pod count

When flow control is enabled (see [KV Transfer Configuration](#kv-transfer-configuration) for EPP config), additional metrics are emitted:
- `inference_extension_flow_control_queue_size` --flow control queue depth
- `inference_extension_flow_control_pool_saturation` --pool saturation level

#### Enabling monitoring in a scenario

To enable PodMonitor-based metrics collection for a deployment:

```yaml
scenario:
  - name: "my-monitored-deployment"
    monitoring:
      podmonitor:
        enabled: true
    decode:
      monitoring:
        podmonitor:
          enabled: true
```

#### Benchmark report integration

The analysis pipeline converts collected results into v0.2 benchmark reports (`llmdbenchmark/analysis/benchmark_report/`). Reports include:
- **Performance metrics**: TTFT, TPOT, ITL, request latency, throughput
- **Resource metrics**: KV cache usage, GPU/CPU memory, GPU utilization
- **Time series data**: Per-interval metric snapshots

Reports are generated in both YAML and JSON formats. See `llmdbenchmark/analysis/benchmark_report/README.md` for the full schema reference.

#### Prometheus adapter (for autoscaling)

The `21_prometheus-adapter-values.yaml.j2` template configures a Prometheus adapter that bridges WVA (Workload Variant Autoscaler) metrics to the Kubernetes external metrics API. This is only needed when using WVA-based autoscaling.

---

## Container Images

The tool uses several container images across different components. Which config key controls which image depends on the deployment method (standalone vs. modelservice).

### Image Config Paths

All images are defined in `defaults.yaml`. There are two groups: the shared `images` section and per-component overrides.

**Shared images** (under `images`):

| Key | Default | Used by |
|-----|---------|---------|
| `images.vllm` | `ghcr.io/llm-d/llm-d-cuda:auto` | Modelservice decode/prefill pods, standalone fallback |
| `images.benchmark` | `ghcr.io/llm-d/llm-d-benchmark:auto` | Download job, harness pod, data access pod |
| `images.inferenceScheduler` | `ghcr.io/llm-d/llm-d-inference-scheduler:auto` | GAIE inference extension |
| `images.routingSidecar` | `ghcr.io/llm-d/llm-d-routing-sidecar:auto` | Modelservice routing sidecar |
| `images.python` | `python:3.10` | Utility containers |
| `images.vllmOpenai` | `docker.io/vllm/vllm-openai:auto` | Not currently used by any template (reserved) |

**Per-component images** (override the shared defaults):

| Key | Default | Used by |
|-----|---------|---------|
| `standalone.image` | `docker.io/vllm/vllm-openai:auto` | Standalone vLLM container |
| `standalone.launcher.image` | _(falls back to `standalone.image`)_ | Standalone launcher container (repo/tag only) |
| `wva.image` | `ghcr.io/llm-d/llm-d-workload-variant-autoscaler:auto` | Workload Variant Autoscaler |

Each image key has `repository`, `tag`, and `pullPolicy` sub-fields. The one exception is `standalone.launcher` --its pull policy is set via a separate flat key `standalone.launcher.imagePullPolicy` (defaults to `Always`), not nested under `image`.

### Which Template Uses Which Image

| Template | Image Config | Component |
|----------|-------------|-----------|
| `04_download_job.yaml.j2` | `images.benchmark` | Model download job |
| `06_pod_access_to_harness_data.yaml.j2` | `images.benchmark` | Harness data access pod |
| `12_gaie-values.yaml.j2` | `images.inferenceScheduler` | Inference scheduling extension |
| `13_ms-values.yaml.j2` (decode) | `images.vllm` | Decode pods in modelservice |
| `13_ms-values.yaml.j2` (prefill) | `images.vllm` | Prefill pods in modelservice |
| `13_ms-values.yaml.j2` (sidecar) | `images.routingSidecar` | Routing sidecar in modelservice |
| `14_standalone-deployment_yaml.j2` | `standalone.image` | Standalone vLLM container |
| `14_standalone-deployment_yaml.j2` (launcher) | `standalone.launcher.image` | Standalone launcher container |
| `19_wva-values.yaml.j2` | `wva.image` | Workload Variant Autoscaler |
| `20_harness_pod.yaml.j2` | `images.benchmark` | Benchmark harness pod |

### Fallback Chains

Templates use Jinja2 `default()` filters to create fallback chains. If a per-component image isn't set, the template falls back to the shared `images` section.

**Standalone main container:**

```
standalone.image.repository  maps to  images.vllm.repository
standalone.image.tag         maps to  images.vllm.tag
standalone.image.pullPolicy  maps to  images.vllm.pullPolicy
```

Since `standalone.image` is explicitly set in `defaults.yaml` (`docker.io/vllm/vllm-openai:auto`), the `images.vllm` fallback only kicks in if you clear `standalone.image` in your scenario. The `auto` tag is resolved at render time by the `VersionResolver`. In practice, to change the standalone image you must override `standalone.image` directly.

**Standalone launcher container** (three-level chain for repo/tag):

```
standalone.launcher.image.repository  maps to  standalone.image.repository  maps to  images.vllm.repository
standalone.launcher.image.tag         maps to  standalone.image.tag         maps to  images.vllm.tag
standalone.launcher.imagePullPolicy   maps to  'Always' (hardcoded default, no fallback chain)
```

Note: the launcher's `imagePullPolicy` is a flat key on `standalone.launcher`, not nested under `standalone.launcher.image`. It does not inherit from `standalone.image.pullPolicy`.

**Everything else** (modelservice decode/prefill, download job, harness, etc.) reads directly from the `images` section with no fallback chain.

### Overriding Images

**Standalone deployment** (gpu, cpu, spyre examples):

Override `standalone.image` in your scenario:

```yaml
scenario:
  - name: "my-standalone"
    standalone:
      enabled: true
      image:
        repository: docker.io/vllm/vllm-openai
        tag: v0.8.5
```

**Modelservice deployment** (inference-scheduling, pd-disaggregation, etc.):

Override `images.vllm` in your scenario:

```yaml
scenario:
  - name: "my-modelservice"
    images:
      vllm:
        repository: ghcr.io/llm-d/llm-d-cuda
        tag: v0.5.0
```

**Benchmark harness / download job:**

Override `images.benchmark`:

```yaml
scenario:
  - name: "my-deployment"
    images:
      benchmark:
        repository: my-registry/llm-d-benchmark
        tag: dev-branch
```

**Inference scheduler (GAIE):**

Override `images.inferenceScheduler`:

```yaml
scenario:
  - name: "my-deployment"
    images:
      inferenceScheduler:
        repository: my-registry/llm-d-inference-scheduler
        tag: v1.2.3
```

**How to tell which one to use:** Check whether your specification's scenario has `standalone.enabled: true`. If it does, the vLLM serving image comes from `standalone.image`. Otherwise (modelservice path), it comes from `images.vllm`. You can verify by running `plan` and inspecting the rendered YAML in the stack output directory.

After standup, the deployed images are recorded in the `llm-d-benchmark-standup-parameters` ConfigMap:

```bash
oc get configmap llm-d-benchmark-standup-parameters -n <namespace> -o yaml
```

---

## Scenarios

Scenario files provide deployment-specific overrides that are merged on top of `defaults.yaml`. They configure things like model name, GPU count, namespace, image tags, and deployment topology.

### `scenarios/guides/`

Map directly to the [llm-d well-lit-path guides](https://github.com/llm-d/llm-d/tree/main/guides). Each scenario reproduces the deployment described in its corresponding guide.

| Scenario | Description |
|----------|-------------|
| `inference-scheduling.yaml` | Qwen3-32B with inference scheduling plugins |
| `pd-disaggregation.yaml` | Prefill/decode disaggregation |
| `precise-prefix-cache-aware.yaml` | Prefix cache aware routing |
| `tiered-prefix-cache.yaml` | Tiered CPU/GPU prefix cache |
| `wide-ep-lws.yaml` | Expert parallelism with LeaderWorkerSet |
| `simulated-accelerators.yaml` | CPU-only simulation with opt-125m |

### `scenarios/examples/`

Minimal starting points for common hardware:

| Scenario | Description |
|----------|-------------|
| `cpu.yaml` | CPU-only deployment (no GPU) |
| `gpu.yaml` | Standard GPU deployment |
| `spyre.yaml` | IBM Spyre accelerator |
| `sim-small.yaml` | Simulated small model deployment |

### `scenarios/cicd/`

Used by automated CI/CD pipelines:

| Scenario | Description |
|----------|-------------|
| `kind-sim.yaml` | Kind cluster with simulated accelerators |
| `gke-h100.yaml` | Google Kubernetes Engine with H100 |
| `cks.yaml` | Cloud Kubernetes Service with H200 |
| `ocp.yaml` | OpenShift Container Platform with Istio |

### Creating a New Scenario

1. Start from an existing scenario or from scratch
2. Only specify the values you want to override -- everything else comes from `defaults.yaml`
3. Place it under `config/scenarios/` in the appropriate subdirectory

Example minimal scenario:

```yaml
scenario:
  - name: "my-deployment"

    model:
      name: meta-llama/Llama-3.1-8B
      shortName: meta-llama-3-1-8b
      path: models/meta-llama/Llama-3.1-8B
      huggingfaceId: meta-llama/Llama-3.1-8B

    namespace:
      name: llm-benchmark

    decode:
      replicas: 2
      resources:
        limits:
          memory: 64Gi
          cpu: "16"
        requests:
          memory: 64Gi
          cpu: "16"
```

---

## Specifications

Specification files are the entry points for the CLI. Each is a Jinja2 template (`.yaml.j2`) that declares paths to the defaults, templates, and scenario files, plus optional experiment definitions.

### Required Fields

Every specification must declare three paths:

```yaml
{% set base_dir = base_dir | default('../') -%}
base_dir: {{ base_dir }}

values_file:
  path: {{ base_dir }}/config/templates/values/defaults.yaml

template_dir:
  path: {{ base_dir }}/config/templates/jinja
```

### Optional Fields

```yaml
scenario_file:
  path: {{ base_dir }}/config/scenarios/guides/inference-scheduling.yaml

experiments:
  - name: "experiment-name"
    attributes:
      - name: "setup"
        factors: [...]
        treatments: [...]
      - name: "run"
        factors: [...]
        treatments: [...]
```

### Specification Auto-Discovery

The `--spec` flag supports three input forms --you don't need to type the full path:

| Form | Example | Resolves to |
|------|---------|-------------|
| **Bare name** | `--spec gpu` | `config/specification/examples/gpu.yaml.j2` |
| **Category/name** | `--spec guides/inference-scheduling` | `config/specification/guides/inference-scheduling.yaml.j2` |
| **Full path** | `--spec config/specification/guides/inference-scheduling.yaml.j2` | Used as-is |

The `.yaml.j2` suffix is added automatically. If a bare name matches files in multiple categories, you'll be prompted to disambiguate with the category prefix.

### The `base_dir` Variable

All paths are relative to `base_dir`, which defaults to `../` (the repository root when running from the repo directory). Override it with `--bd`:

```bash
llmdbenchmark --bd /path/to/repo --spec inference-scheduling plan
```

### Creating a New Specification

1. Create a scenario YAML under `config/scenarios/` with your deployment overrides
2. Create a specification template under `config/specification/` in the appropriate category subdirectory:

```yaml
{% set base_dir = base_dir | default('../') -%}
base_dir: {{ base_dir }}

values_file:
  path: {{ base_dir }}/config/templates/values/defaults.yaml

template_dir:
  path: {{ base_dir }}/config/templates/jinja

scenario_file:
  path: {{ base_dir }}/config/scenarios/my-scenario.yaml
```

3. Run: `llmdbenchmark --spec my-spec plan`

#### Naming and Collisions

Choose a **unique file name** for your specification. Auto-discovery searches across all subdirectories under `config/specification/`, so two files with the same base name in different categories will collide:

```text
config/specification/
    guides/inference-scheduling.yaml.j2     <- exists
    examples/inference-scheduling.yaml.j2   <- collision!
```

Running `--spec inference-scheduling` with both present produces an error:

```text
Ambiguous specification name 'inference-scheduling' matches 2 files:
  - /path/to/config/specification/examples/inference-scheduling.yaml.j2
  - /path/to/config/specification/guides/inference-scheduling.yaml.j2

Use category/name to disambiguate, e.g.
'--spec guides/inference-scheduling' or '--spec examples/inference-scheduling'.
```

To avoid this:

- **Use a distinct name** that reflects your use case (e.g. `my-team-inference.yaml.j2` instead of reusing `inference-scheduling.yaml.j2`)
- **Or always use category/name** when specs share a base name: `--spec guides/inference-scheduling`

### Experiments

To add parameter sweeps, include an `experiments` section. Experiments have two attribute categories:

- **`setup`** -- Parameters that change the deployment (e.g., replicas, scheduler plugin). Each treatment generates a separate rendered stack.
- **`run`** -- Parameters that change the benchmark workload (e.g., concurrency, prompt length). Used during the run phase, not standup.

Each category contains:

| Field | Purpose |
|-------|---------|
| `factors` | Parameters being varied, each with a list of `levels` (possible values) |
| `constants` | Fixed parameters applied to every treatment (optional) |
| `treatments` | Explicit combinations of factor levels to test |

### Available Specifications

**Guides:**

| Specification | Experiments |
|---------------|-------------|
| `inference-scheduling.yaml.j2` | GAIE plugin configs x prompt/output lengths |
| `pd-disaggregation.yaml.j2` | Deployment method, replicas, TP sizes x concurrency |
| `precise-prefix-cache-aware.yaml.j2` | GAIE prefix cache configs x prompt groups |
| `tiered-prefix-cache.yaml.j2` | CPU block sizes x prompt groups |
| `wide-ep-lws.yaml.j2` | Standup only |
| `simulated-accelerators.yaml.j2` | Standup only |

**Examples:** `cpu.yaml.j2`, `gpu.yaml.j2`, `spyre.yaml.j2`

**CI/CD:** `cks.yaml.j2`, `gke-h100.yaml.j2`, `kind-sim.yaml.j2`, `ocp.yaml.j2`

---

## Usage

```bash
# Plan (render templates into manifests)
llmdbenchmark --spec inference-scheduling plan

# Standup (plan + apply to cluster)
llmdbenchmark --spec inference-scheduling standup

# Dry run
llmdbenchmark --spec inference-scheduling --dry-run standup

# Teardown
llmdbenchmark --spec inference-scheduling teardown

# Override namespace at runtime
llmdbenchmark --spec inference-scheduling standup -p my-ns

# Override deployment method
llmdbenchmark --spec inference-scheduling standup -t standalone

# Use category/name to disambiguate
llmdbenchmark --spec guides/inference-scheduling standup

# Full path still works
llmdbenchmark --spec config/specification/guides/inference-scheduling.yaml.j2 standup
```
