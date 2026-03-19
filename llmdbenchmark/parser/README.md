# llmdbenchmark.parser

Config parsing, Jinja2 template rendering, schema validation, and version/resource resolution. Transforms specification files and scenario YAML into fully-resolved, rendered Kubernetes manifests.

## Rendering Pipeline

1. **Specification rendering** (`RenderSpecification`) -- Render the specification Jinja2 template, parse the YAML output, and validate that all referenced filesystem paths exist.
2. **Plan rendering** (`RenderPlans`) -- For each stack in the scenario, merge defaults with scenario overrides, apply resource presets, resolve versions/resources/namespaces/models/monitoring/HF tokens, validate against the config schema, and render all `.j2` templates into YAML files.
3. **Config validation** (`validate_config`) -- Non-blocking Pydantic v2 validation of the merged config dict. Returns warnings without raising exceptions.

## Files

| File | Description |
|------|-------------|
| `__init__.py` | Empty package marker |
| `render_specification.py` | `RenderSpecification` -- Render the specification Jinja2 template, parse YAML, validate filesystem paths |
| `render_plans.py` | `RenderPlans` -- Full rendering pipeline: template loading, deep merge, resolver chain, Jinja2 rendering, YAML validation. Includes custom Jinja2 filters (`toyaml`, `tojson`, `b64encode`, `b64pad`, `indent`, `is_empty`) |
| `render_result.py` | `RenderResult` and `StackErrors` -- Error tracking and result types for the rendering pipeline |
| `config_schema.py` | Pydantic v2 config schema (`BenchmarkConfig`) with nested models for model, decode, prefill, vllmCommon, harness, parallelism, probes, resources, monitoring, and autoscaling |
| `version_resolver.py` | `VersionResolver` -- Resolve `"auto"` image tags via skopeo/podman and chart versions via `helm search repo`. Handles init container images, standalone images, WVA images, and gateway versions |
| `cluster_resource_resolver.py` | `ClusterResourceResolver` -- Resolve `"auto"` cluster resources (accelerator type, network RDMA/IB, GPU affinity labels) by scanning Kubernetes node capacities and labels |

## Resolver Chain

During plan rendering, the following resolvers execute in order on the merged values dict:

1. Resource preset application
2. Version resolution (image tags, chart versions)
3. Cluster resource resolution (accelerator, network, affinity, GPU labels)
4. Namespace resolution (CLI override or `"auto"` default)
5. Model resolution (CLI `--models` override)
6. Deploy method resolution (CLI `--methods` override)
7. Monitoring resolution (CLI `--monitoring` flag)
8. HuggingFace token auto-detection from environment
9. Config schema validation
