# llmdbenchmark.parser

Config parsing, Jinja2 template rendering, schema validation, and version/resource resolution. Transforms specification files and scenario YAML into fully-resolved, rendered Kubernetes manifests.

## Rendering Pipeline

### 1. Specification Rendering (`RenderSpecification`)

Render the specification Jinja2 template (`.yaml.j2`), parse the resulting YAML, and validate that all referenced filesystem paths (template directories, scenario files, defaults files) exist and are non-empty.

```python
class RenderSpecification:
    def __init__(self, specification_file: Path, base_dir: Path | None = None, logger=None): ...
    def eval(self) -> dict[str, Any]: ...  # Render, parse, validate, return config dict
```

The specification template receives `base_dir` as a Jinja2 variable, allowing relative path resolution. The rendered output is written to the plan directory.

### 2. Plan Rendering (`RenderPlans`)

For each stack in the scenario, merge defaults with scenario overrides, apply the full resolver chain, validate against the config schema, and render all `.j2` templates into YAML files.

```python
class RenderPlans:
    def __init__(self, template_dir, defaults_file, scenarios_file, output_dir,
                 logger=None, version_resolver=None, cluster_resource_resolver=None,
                 cli_namespace=None, cli_model=None, cli_methods=None,
                 cli_monitoring=False, setup_overrides=None): ...
    def eval(self) -> RenderResult: ...    # Run full rendering pipeline
    def deep_merge(self, base, override) -> dict: ...  # Recursive dict merge
```

#### Deep Merge

`deep_merge()` recursively merges two dicts. Override values take precedence. `None` values in the override dict are skipped (YAML keys with no value do not clobber defaults). Returns a new dict.

#### Template Loading

Templates are loaded from `.j2` files in the template directory. Files prefixed with `_` (e.g. `_macros.j2`) are treated as partials/macros and are not rendered directly -- their content is prepended to every rendered template. Output filenames strip the `.j2` extension.

#### Custom Jinja2 Filters

| Filter | Description |
|--------|-------------|
| `indent(width, first=False)` | Indent text by specified width |
| `toyaml(indent=0)` | Convert Python object to YAML string |
| `tojson` | Convert to compact JSON |
| `is_empty` | Check if value is None, empty string, empty dict, or empty list |
| `default_if_empty(default)` | Return default if value is empty |
| `b64pad` | Ensure base64 string has proper padding (fixes K8s Secret errors) |
| `b64encode` | Base64-encode a plain-text string |

#### Per-Stack Processing

For each stack in the scenario:

1. Merge defaults with the optional top-level `shared:` block (scenario-wide
   settings applied to every stack), then with stack-specific overrides:
   `defaults -> shared -> stack`.
2. Apply setup overrides (from DoE experiment treatments) if present.
3. Apply resource preset (if `resourcePreset` is set in the config).
4. Run the resolver chain (see below).
5. Validate against the Pydantic config schema.
6. Inject the scenario-wide sibling summary (`siblingStacks`) and this
   stack's 1-indexed `stackIndex` into the Jinja values so templates can
   emit cross-stack constructs (e.g. a shared HTTPRoute with N backendRefs)
   or gate cluster-scoped resources on `stackIndex == 1` to avoid races.
7. Render all templates with the merged values.
8. Write `config.yaml` with the fully-resolved config (JSON round-trip strips YAML anchors).
9. Validate all generated YAML files for syntax.

### 3. Config Schema Validation (`config_schema.py`)

Non-blocking Pydantic v2 validation of the merged config dict. Returns a list of warning strings -- never raises exceptions.

```python
def validate_config(merged_values: dict, render_logger=None) -> list[str]: ...
```

The root model `BenchmarkConfig` uses `extra="allow"` so unmodeled top-level keys pass through. Nested section models use `extra="forbid"` to catch typos within modeled sections.

Modeled sections:

| Model | Scope |
|-------|-------|
| `ModelConfig` | Model name, path, HuggingFace ID, size, maxModelLen, gpuMemoryUtilization |
| `DecodeConfig` / `PrefillConfig` | Deployment config (replicas, autoscaling, parallelism, resources, probes, vllm, monitoring) |
| `VllmCommonConfig` | Shared vLLM config (ports, KV transfer, KV events, flags, volumes) |
| `HarnessConfig` | Harness name, profile, executable, resources, timeout |
| `ParallelismConfig` | data, dataLocal, tensor, workers parallelism settings |

## Resolver Chain

During plan rendering, the following resolvers execute in order on the merged values dict:

1. **Resource preset application** -- Merge named resource preset into decode/prefill configs.
2. **Version resolution** -- Resolve `"auto"` image tags and chart versions.
3. **Image override logging** (`_log_image_overrides`) -- When a scenario pins an image to a non-auto tag, the renderer logs the override (e.g. `Image override: vllm pinned to us.icr.io/...:v1.1.1`).
4. **Cluster resource resolution** -- Resolve `"auto"` accelerator, network, affinity, and GPU labels.
5. **Namespace resolution** -- Apply CLI `--namespace` override or resolve `"auto"` to default `"llmdbench"`. Supports comma-separated `deploy,harness,wva` format.
6. **Model resolution** -- Apply CLI `--models` override.
7. **Model ID label resolution** (`_resolve_model_id_label`) -- Compute `model_id_label` from the model name using the hashed format `{first8}-{sha256_8}-{last8}`. This label is used in all templates for Kubernetes resource naming.
8. **Per-stack identity resolution** (`_resolve_per_stack_identity`) -- Multi-stack scenarios (N >= 2) only. Auto-suffix shipped-default resource names (`storage.modelPvc.name`, `downloadJob.name`, `inferenceExtension.monitoring.secretName`) with `-{model_id_label}` so each stack gets unique names and Helm releases / PVCs don't collide in a shared namespace. Explicit overrides are preserved. See `_STACK_SCOPED_DEFAULTS` for the full list.
9. **Custom command conflict warning** -- Warns when CLI `--models` won't propagate into hardcoded `customCommand` values.
10. **Deploy method resolution** -- Apply CLI `--methods` override (`standalone` or `modelservice`). Only one may be active.
11. **Monitoring resolution** -- Apply CLI `--monitoring` flag. Enables PodMonitor and metrics scraping.
12. **HuggingFace token auto-detection** -- Detect HF token from `HF_TOKEN` or `HUGGING_FACE_HUB_TOKEN` env vars when the configured token is a sentinel value (`REPLACE_TOKEN` or empty).
13. **Config schema validation** -- Non-blocking Pydantic validation.

## Version Resolver (`version_resolver.py`)

Resolves `"auto"` values for image tags and chart versions.

```python
class VersionResolver:
    def resolve_all(self, values: dict) -> dict: ...
    def resolve_image_tag(self, registry, repository) -> str: ...
    def resolve_chart_version(self, chart_name, repo_url=None) -> str: ...
    def has_unresolved(self, values: dict) -> list[str]: ...
```

Image tag resolution order: skopeo `list-tags` then podman `search --list-tags`.

Chart version resolution order: `helm search repo`, then for repo URLs: OCI uses `helm show chart`, traditional repos temporarily add/search/remove.

Resolved fields: `images.*.tag`, `standalone.image.tag`, `wva.image.tag`, `chartVersions.*`, `gateway.version` (from istio version), and init container images with `:auto` suffix across decode/prefill/standalone.

## Cluster Resource Resolver (`cluster_resource_resolver.py`)

Resolves `"auto"` cluster resource values by scanning Kubernetes node capacities and labels.

```python
class ClusterResourceResolver:
    def resolve_all(self, values: dict) -> dict: ...
    def has_unresolved(self, values: dict) -> list[str]: ...
```

Connects lazily via `kube_connect()` on first call. Node scan results are cached after the first call.

Resolved fields:

| Config Path | Resolution |
|-------------|------------|
| `accelerator.resource` | First detected GPU resource key from node capacities (nvidia.com/gpu, amd.com/gpu, habana.ai/gaudi, etc.) |
| `vllmCommon.networkResource` | First detected RDMA/IB resource (rdma/rdma_shared_device_a, etc.). Cleared if none found (templates skip network section). |
| `vllmCommon.networkNr` | Set to `"1"` when network resource found, `""` otherwise |
| `affinity.nodeSelector` | Built from GPU product labels (e.g. `{"nvidia.com/gpu.product": "NVIDIA-H100-80GB-HBM3"}`) |
| `*.acceleratorType.labelValue` | GPU product label key and value for decode/prefill/standalone |

After resolution, `_propagate_network_to_methods()` copies `vllmCommon` network settings to per-method sections (decode, prefill, standalone) when their values are `"auto"` or empty.

In dry-run mode, unresolved fields produce warnings instead of errors.

## RenderResult (`render_result.py`)

```python
@dataclass
class StackErrors:
    render_errors: list[str]       # Jinja2 template errors
    yaml_errors: list[str]         # YAML validation errors
    missing_fields: list[str]      # Missing required fields
    validation_warnings: list[str] # Config schema warnings

@dataclass
class RenderResult:
    global_errors: list[str]       # Errors not tied to a specific stack
    stacks: dict[str, StackErrors] # Per-stack error accumulators
    rendered_paths: list[Path]     # Successfully rendered stack directories
```

## Files

```
parser/
+-- __init__.py                    -- Empty package marker
+-- render_specification.py        -- RenderSpecification
+-- render_plans.py                -- RenderPlans (full pipeline)
+-- render_result.py               -- RenderResult, StackErrors
+-- config_schema.py               -- Pydantic v2 config schema
+-- version_resolver.py            -- VersionResolver
+-- cluster_resource_resolver.py   -- ClusterResourceResolver
```
