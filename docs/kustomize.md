# Kustomize deploy method

`-t kustomize` deploys an upstream **llm-d guide** by running the commands
parsed from that guide's `README.md`, instead of rendering the
`modelservice`/`standalone` templates. It is implemented in
`llmdbenchmark/standup/steps/step_06_kustomize_deploy.py` and
`llmdbenchmark/kustomize/`.

## Key principle

Under kustomize the deployment is defined **entirely** by the guide's own
manifests. The normal scenario/CLI/experiment merge chain does **not** reach
it. Specifically ignored: `-m/--models`, `model.*`, decode/prefill replicas,
parallelism, resources, gateway, and all other scenario/CLI tuning. DoE
`experiment` **setup** sweeps also do not alter a kustomize deploy (only
run/workload treatments still apply). `model.name` is only recorded in the
`standup-parameters` ConfigMap as metadata.

The **only** way to change a kustomize deployment is the `kustomize.*` keys
below.

## Enabling

```bash
llmdbenchmark --spec guides/optimized-baseline standup -t kustomize -p NS
```

`-t kustomize` sets `kustomize.enabled: true` (and disables the other
methods). Equivalently, set `kustomize.enabled: true` in the scenario. If
`kustomize.enabled` is false the step is a no-op.

## Config reference (`kustomize:` block)

| Key | Default | Effect |
|---|---|---|
| `enabled` | `false` | Must be true (or `-t kustomize`) for the step to deploy. |
| `guideName` | — (required) | Guide dir under `guides/<name>` in the llm-d repo. |
| `repoPath` | `""` | Local llm-d clone to use. Falls back to `--llmd-repo-path`; empty ⇒ clone `https://github.com/llm-d/llm-d.git` into `workspace/llm-d`. |
| `repoRef` | `"main"` | Git ref used when cloning. |
| `acceleratorBackend` | `"gpu/vllm"` | Swaps `modelserver/gpu/vllm` → `modelserver/<backend>` in guide paths. |
| `gaieVersion` | README `GAIE_VERSION` or `v1.5.0` | GAIE CRD bundle version substituted into README commands. |
| `routerChartVersion` | README `ROUTER_CHART_VERSION` or `v0` | llm-d-router chart version. |
| `monitoring` | `false` | Also applies `guides/recipes/modelserver/components/monitoring`. |
| `deployTimeout` | `900` | Pod-readiness wait (seconds). |
| `patches` | `[]` | Inline strategic-merge patches (modelserver). See below. |
| `overlayPath` | `""` | Directory overlay (modelserver). See below. |
| `extraHelmValues` | `[]` | `-f <file>` appended to the router/GAIE helm command. |
| `extraHelmSets` | `{}` | `--set k=v` appended to the router/GAIE helm command. |
| `guideVariableOverrides` | `{}` | Override/fill the guide README's `${VAR}` values (cannot add new variables). |

## Two scopes

- **Modelserver** (the workload pods): `patches`, `overlayPath`.
- **Router / GAIE** (the helm release the guide README installs):
  `extraHelmValues`, `extraHelmSets`.
- **README placeholders**: `guideVariableOverrides`.

`patches`/`overlayPath` only take effect when `patches` is non-empty or
`overlayPath` is an existing directory; the generated wrapper
`kustomization.yaml` (base = the guide's modelserver dir) is written to
`workspace/setup/kustomize-overlay/` and applied with `kubectl apply -k`.

## Examples

```yaml
kustomize:
  enabled: true
  guideName: "optimized-baseline"

  # patches → modelserver. Strategic-merge, matched by
  # apiVersion + kind + metadata.name against the guide's base.
  patches:
    - patch: |                       # override the guide's replica count
        apiVersion: apps/v1
        kind: Deployment
        metadata: { name: decode }
        spec: { replicas: 4 }
    - patch: |                       # gated models: inject HF token env
        apiVersion: apps/v1
        kind: Deployment
        metadata: { name: decode }
        spec:
          template:
            spec:
              containers:
                - name: modelserver
                  env:
                    - name: HF_TOKEN
                      valueFrom:
                        secretKeyRef:
                          name: llm-d-hf-token
                          key: HF_TOKEN

  # overlayPath → modelserver. If the dir contains kustomization.yaml it
  # is added as a kustomize component; otherwise every *.yaml in it is
  # applied as a patch file. Combinable with `patches`.
  overlayPath: "/abs/path/my-overlay"

  # extraHelmValues / extraHelmSets → router/GAIE helm release ONLY.
  # Keys are passed straight through to helm and therefore must match the
  # chart's values schema. With the llm-d-router-{standalone,gateway}-dev
  # charts the EPP replica knob lives at `router.epp.replicas`
  # (previously `inferenceExtension.replicas` on the old GAIE chart).
  extraHelmValues: ["/abs/path/gaie-values.yaml"]
  extraHelmSets:
    router.epp.replicas: "2"

  # guideVariableOverrides → override/fill ${VAR} tokens the guide
  # README already uses (cannot introduce new variables).
  guideVariableOverrides:
    SOME_README_VAR: "value"
```

## Caveats

- Valid patch targets (`metadata.name`, e.g. `decode`, `prefill`) and valid
  `extraHelmValues`/`extraHelmSets` keys depend on the **upstream guide** —
  read `guides/<guideName>/modelserver/<backend>/` and the helm step in
  `guides/<guideName>/README.md` in the llm-d repo. They are not arbitrary.
- `guideVariableOverrides` only affects `${VAR}` tokens that literally
  appear in that guide's README; it cannot introduce new variables.
  Precedence (`llmdbenchmark/kustomize/variable_resolver.py`):
  README-declared defaults < `guideVariableOverrides` < forced
  `GUIDE_NAME` / `NAMESPACE` / `GAIE_VERSION` / `ROUTER_CHART_VERSION`
  (those four cannot be overridden).
- The deployed model is whatever the guide pins; change it via a `patches`
  entry against the resource that carries it, not via `-m`/`model.name`.
- **Multi-model (multi-stack) is NOT supported with kustomize.** The
  deployment is keyed entirely on `guideName` (resources + the
  `{guideName}-epp` endpoint) with no per-stack/per-model uniquification
  (unlike modelservice's per-stack identity resolution), so multiple stacks
  would collide on the same guide resources. Use the `modelservice` method
  (e.g. the `multi-model-wva` scenario) for multi-model; keep kustomize
  scenarios single-stack.
