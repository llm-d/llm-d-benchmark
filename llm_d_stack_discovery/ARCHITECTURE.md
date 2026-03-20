# Architecture

## Overview

The llm-d stack discovery tool takes an OpenAI-compatible endpoint URL and traces through a Kubernetes cluster to discover every component that makes up the serving stack behind that endpoint. It resolves the URL to a Kubernetes entry-point resource, walks the resource graph with breadth-first search, collects configuration from each resource using specialised collectors, and emits a structured report of the full stack topology and configuration.

## High-level data flow

```
                                   ┌──────────────────────┐
  URL ──► CLI ──► StackTracer ────►│ Entry Point Resolution│
          │       (tracer.py)      │                      │
          │                        │ 1. svc.cluster.local │
          │                        │ 2. OpenShift Route   │
          │                        │ 3. Gateway API       │
          │                        │ 4. LB/NodePort Svc   │
          │                        └──────────┬───────────┘
          │                                   │
          │                                   ▼
          │                        ┌──────────────────────┐
          │                        │   BFS Graph Traversal │
          │                        │                      │
          │                        │  queue ← entry_point │
          │                        │  while queue:        │
          │                        │    resource = dequeue│
          │                        │    skip if visited   │
          │                        │    collect(resource) │
          │                        │    enqueue children  │
          │                        └──────────┬───────────┘
          │                                   │
          │                    ┌───────┬──────┴──────┬─────────┐
          │                    ▼       ▼             ▼         ▼
          │               VLLMCollector  GAIECollector  GatewayCollector  GenericCollector
          │                    │       │             │         │
          │                    └───────┴──────┬──────┴─────────┘
          │                                   │
          │                                   ▼
          │                           DiscoveryResult
          │                                   │
          ▼                                   ▼
  OutputFormatter ◄───────────────────────────┘
          │
          ▼
   stdout / file
   (json, yaml, summary, native, native-yaml, benchmark-report)
```

## Directory layout

```
llm_d_stack_discovery/
├── cli.py                       # Click CLI: parses args, connects to K8s, runs tracer, formats output
├── __main__.py                  # `python -m llm_d_stack_discovery` entry point
├── __init__.py
├── discovery/
│   ├── tracer.py                # StackTracer: orchestrates entry-point resolution and BFS traversal
│   ├── utils.py                 # K8s helpers: CRD classes, resource queries, URL parsing, OpenShift detection
│   └── collectors/
│       ├── base.py              # BaseCollector ABC: metadata extraction, env-var redaction, pod parsing
│       ├── vllm.py              # VLLMCollector: vLLM pod detection, model/parallelism/GPU extraction
│       ├── gaie.py              # GAIECollector: InferencePool resources and GAIE controller (EPP) pods
│       ├── gateway.py           # GatewayCollector: OpenShift Routes, Gateways, HTTPRoutes
│       └── generic.py           # GenericCollector: Services, ConfigMaps, Deployments, StatefulSets, fallback
├── models/
│   └── components.py            # Dataclasses: ComponentMetadata, Component, DiscoveryResult
├── output/
│   ├── formatter.py             # OutputFormatter: six output formats, component filtering
│   └── benchmark_report.py      # Converts DiscoveryResult → benchmark report v0.2 schema with replica grouping
├── examples/
│   └── basic_discovery.py       # Programmatic usage example
├── k8s/
│   ├── rbac.yaml                # ClusterRole, ServiceAccount, ClusterRoleBinding
│   └── job.yaml                 # Kubernetes Job manifest for in-cluster discovery
├── tests/
│   └── unit/                    # Unit tests for each module
├── Dockerfile                   # Container image (python:3.11-slim based)
├── Makefile                     # image-build / image-push / buildah-build targets
├── setup.py                     # Package setup (entry point: llm-d-discover)
├── pyproject.toml               # Build config, ruff/black settings
└── requirements.txt             # Runtime dependencies
```

## Core components

### CLI (`cli.py`)

Click-based command that accepts a URL argument and options for kubeconfig path, context, output format, output file, component-type filter, and verbosity. It connects to Kubernetes via `kube_connect()`, creates a `StackTracer`, calls `tracer.trace(url)`, formats the `DiscoveryResult` with `OutputFormatter`, and writes to stdout or a file. Exits with code 1 if the result contains errors.

### StackTracer (`discovery/tracer.py`)

The orchestrator. On `trace(url)`:

1. **URL parsing** -- Delegates to `parse_endpoint_url()` to extract hostname, port, scheme, and path.
2. **Cluster info** -- Queries the Kubernetes version API and detects OpenShift via SCC presence.
3. **Entry-point resolution** -- Tries four strategies in order (see [Entry-point resolution](#entry-point-resolution)).
4. **BFS traversal** -- Walks the resource graph from the entry point (see [BFS traversal detail](#bfs-traversal-detail)).
5. **Result assembly** -- Returns a `DiscoveryResult` containing the component list and any non-fatal errors.

#### Entry-point resolution

The tracer resolves the URL hostname to a Kubernetes resource by trying these strategies in order:

| Priority | Strategy | Condition | How |
|----------|----------|-----------|-----|
| 1 | Cluster-internal DNS | Hostname contains `.svc.cluster.local` | Parse `<svc>.<ns>.svc.cluster.local`, look up Service by name in namespace |
| 2 | OpenShift Route | Cluster is OpenShift | List all Routes, match `spec.host == hostname` |
| 3 | Gateway API | Always tried | List all Gateways, match listener hostname |
| 4 | LoadBalancer / NodePort Service | Always tried | List all Services, match LB ingress hostname/IP or NodePort |

The first match wins and becomes the BFS root.

> **Limitation:** Gateway listeners must specify an explicit hostname that exactly matches the requested host; wildcard listeners (e.g., `*.example.com` or listeners with no hostname) are not currently considered during entry-point resolution.

### Collectors (`discovery/collectors/`)

Each collector inherits from `BaseCollector` and implements a `collect(resource)` method that returns a `Component` or `None`.

#### BaseCollector (`base.py`)

Abstract base class providing shared utilities:

- `get_metadata()` -- Extracts `ComponentMetadata` (namespace, name, kind, labels, annotations) from any K8s resource.
- `extract_pod_info()` -- Pulls image, command, args, filtered env vars, resources, and node name from a pod.
- `_filter_env_vars()` -- Redacts values of environment variables whose names contain sensitive patterns (`TOKEN`, `KEY`, `SECRET`, `PASSWORD`, `CREDENTIAL`, `PRIVATE`).
- `get_configmap_refs()` / `get_secret_refs()` -- Enumerates ConfigMap and Secret names referenced by a pod (names only for secrets -- values are never read).
- `parse_command_args()` -- Parses CLI-style `--flag value` arguments into a structured dict.
- `create_component()` -- Builds a `Component` dataclass from a resource, tool name, version, and native config.

#### VLLMCollector (`vllm.py`)

Handles pods running vLLM. Detection checks container commands/args for `vllm` or `vllm.entrypoints`, and image names containing `vllm`. Extracts:

- **Model**: from `--model` or `--served-model-name` CLI flags.
- **Parallelism**: `--tensor-parallel-size`, `--pipeline-parallel-size` from flags; `VLLM_DP_SIZE`, `VLLM_EP_SIZE`, `VLLM_DP_LOCAL_SIZE`, `VLLM_NUM_WORKERS` from environment variables. `LLMDBENCH_VLLM_COMMON_DATA_LOCAL_PARALLELISM` and `LLMDBENCH_VLLM_COMMON_NUM_WORKERS_PARALLELISM` are also read as fallbacks for `data_local_parallel_size` and `num_workers` respectively.
- **GPU info**: count from resource requests/limits (`nvidia.com/gpu`), model name from the node's NVIDIA labels (cleaned of vendor/form-factor prefixes).
- **Role**: `HostType.PREFILL`, `DECODE`, or `REPLICA` determined by checking labels in priority order: `app.kubernetes.io/component` first, then `llm-d.ai/role`, then `llm-d.io/role` (legacy). A value of `"both"` maps to `REPLICA`.
- **Version**: extracted from image tag or `VLLM_VERSION` environment variable.
- Additional config: scheduler mode, KV cache settings, max model length, GPU memory utilisation, attention backend, v1 flag.

#### GAIECollector (`gaie.py`)

Handles two resource types:

- **InferencePool resources** -- Extracts plugin config, inference settings, scheduling policy, backend list (explicit or selector-based), and routing configuration. Tool name: `gaie`.
- **GAIE controller pods (EPP)** -- Detected via labels (`app.kubernetes.io/name=gaie`, `app.kubernetes.io/component` in `{epp, endpoint-picker}`, `inferencepool` label ending in `-epp`) or image name containing `gaie`, `epp`, or `inference-scheduler`. Extracts controller flags, env vars, and the full data of any referenced ConfigMaps. Tool name: `gaie-controller`.

#### GatewayCollector (`gateway.py`)

Dispatches on resource type:

- **OpenShift Route** -- Extracts host, path, TLS config, backend service reference, ingress status. Tool name: `openshift-route`.
- **Gateway** -- Extracts listeners (protocol, port, hostname, TLS, allowed routes), gateway class info (fetches GatewayClass resource for controller name). Tool name: `gateway-api`.
- **HTTPRoute** -- Extracts parent refs, hostnames, routing rules with matches/filters/backend refs. Tool name: `gateway-api-httproute`.

#### GenericCollector (`generic.py`)

Catch-all for resources without a specialised collector. Handles:

- **Services** -- type, clusterIP, ports, selector, session affinity, external name.
- **ConfigMaps** -- data keys only (not values), binary data keys, immutable flag.
- **Deployments** -- replicas, selector, strategy, images, ready/available/updated replica counts.
- **StatefulSets** -- replicas, service name, pod management policy, update strategy, volume claim templates.
- **Other resources** -- passes through the raw resource object.

The tool name is inferred from labels (`app.kubernetes.io/name`, `app`, or `component`) falling back to the lowercased resource kind.

> **Note:** The BFS traversal currently does not enqueue Deployments, StatefulSets, or ConfigMaps directly, so GenericCollector encounters those resource types only in tests or future enhancements. The capabilities are documented here for completeness.

## BFS traversal detail

The BFS in `StackTracer._trace_from_entry_point()` uses a visited set keyed by `"{kind}/{namespace}/{name}"` to prevent cycles. The dispatch table below shows what each resource type produces and what gets enqueued:

| Resource type | Collector | Downstream resources enqueued |
|---|---|---|
| `Route` | GatewayCollector | Backend Service (from `spec.to`) |
| `Gateway` | GatewayCollector | HTTPRoutes whose `parentRefs` reference this Gateway (cross-namespace search) |
| `HTTPRoute` | GatewayCollector | Backend refs: Services, InferencePools, or InferenceModels. If an InferencePool cannot be found, falls back to selecting pods labelled `llm-d.ai/inferenceServing: "true"` in the backend namespace. |
| `InferenceModel` | GenericCollector | InferencePool from `spec.poolRef.name` |
| `InferencePool` | GAIECollector | Backend pods/services from explicit backends, selector-matched pods, or `modelServers.matchLabels` pods; also follows `extensionRef` or `endpointPickerRef` to the EPP Service |
| `Service` | GenericCollector | (a) Gateway resource if selector contains `gateway.networking.k8s.io/gateway-name`; (b) HTTPRoutes referencing that Gateway; (c) Pods matching the service selector |
| `Pod` | VLLMCollector (if vLLM) > GAIECollector (if GAIE/EPP) > GenericCollector (fallback) | None (leaf node) |
| Other | GenericCollector | None (leaf node) |

For Services with a `gateway.networking.k8s.io/gateway-name` selector label, the tracer looks up the named Gateway (same namespace first, then all namespaces) and also finds HTTPRoutes in the service's namespace that reference that Gateway.

## Data models (`models/components.py`)

Three dataclasses:

- **`ComponentMetadata`** -- `namespace`, `name`, `kind`, `labels`, `annotations`.
- **`Component`** -- `metadata` (ComponentMetadata), `tool` (e.g. `"vllm"`, `"gaie"`, `"openshift-route"`), `tool_version`, `native` (dict with collector-specific raw configuration).
- **`DiscoveryResult`** -- `url`, `timestamp`, `cluster_info`, `components` list, `errors` list.

## Output (`output/`)

### OutputFormatter (`formatter.py`)

Supports six output formats:

| Format | Description |
|---|---|
| `summary` | Human-readable text grouping components by type with key details |
| `json` | Full discovery result as indented JSON |
| `yaml` | Full discovery result as YAML |
| `native` | Raw component configs keyed by `Kind/namespace/name`, as JSON |
| `native-yaml` | Same as `native` but as YAML |
| `benchmark-report` | Emits the `component` list from the benchmark report v0.2 schema (see below) |

An optional `--filter` flag filters components by kind (case-insensitive) or by tool name. Tool name matching compares the lowercased filter value against the stored tool name, so a filter must match the tool name's exact casing as stored (label-derived tool names preserve their original casing).

### Benchmark report conversion (`benchmark_report.py`)

Converts `DiscoveryResult` into the benchmark report v0.2 schema:

- **vLLM pods** are grouped by a key of (model, role, TP, PP, DP, DP-local, workers, EP, GPU model, GPU count). Each group becomes one `inference_engine` component dict with a `replicas` count. CLI args are parsed into a flag dict, env vars are included (minus redacted values), and a deterministic `cfg_id` (SHA-256 prefix) is computed from the standardized + native sections.
- **GAIE controller pods** become `generic` components with tool `request_router` and label `EPP`.
- **All other components** become `generic` components labelled by their `Kind/namespace/name`.

Two public functions are provided:
- `discovery_to_stack_components()` -- returns plain dicts (used internally by `OutputFormatter` for the `benchmark-report` format).
- `discovery_to_scenario_stack()` -- wraps those dicts as Pydantic `Component` objects for direct use in `BenchmarkReportV02.scenario.stack`.

## Security considerations

- **Environment variable redaction**: `BaseCollector._filter_env_vars()` replaces values with `<REDACTED>` for any env var whose name contains `TOKEN`, `KEY`, `SECRET`, `PASSWORD`, `CREDENTIAL`, or `PRIVATE`.
- **Secrets**: only Secret *names* are tracked via `get_secret_refs()`. Secret values are never read.
- **ConfigMaps**: `GenericCollector` reports data *keys* only, not values.
- **RBAC**: the tool requires only `get` and `list` verbs. It never creates, updates, or deletes resources. The provided `k8s/rbac.yaml` defines the minimum required ClusterRole.

## Deployment

### As a CLI

```bash
pip install -e ./llm_d_stack_discovery
llm-d-discover https://model.example.com/v1 --output-format summary
```

### As a Kubernetes Job

Apply RBAC and the Job manifest:

```bash
kubectl apply -f llm_d_stack_discovery/k8s/rbac.yaml
kubectl apply -f llm_d_stack_discovery/k8s/job.yaml
```

The Job runs with a dedicated ServiceAccount and ClusterRole granting read-only access to the resource types the tool queries (pods, services, configmaps, nodes, deployments, statefulsets, routes, gateways, httproutes, gatewayclasses, inferencepools, inferencemodels, securitycontextconstraints, clusterversions).

### Container image

Built from `Dockerfile` using `python:3.11-slim`. Installs `benchmark_report` (sibling package dependency), then the discovery tool's requirements and source. Runs as non-root user (UID 1000).

### Programmatic usage

```python
from llm_d_stack_discovery.discovery.utils import kube_connect
from llm_d_stack_discovery.discovery.tracer import StackTracer
from llm_d_stack_discovery.output.formatter import OutputFormatter
from llm_d_stack_discovery.output.benchmark_report import discovery_to_scenario_stack

api, k8s_client = kube_connect()
tracer = StackTracer(api, k8s_client)
result = tracer.trace("https://model.example.com/v1")

# Get a human-readable summary
print(OutputFormatter().format(result, format_type="summary"))

# Get Pydantic Component objects for benchmark reports
stack = discovery_to_scenario_stack(result)
```
