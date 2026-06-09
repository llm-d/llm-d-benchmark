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
| `extraHelmValues` | `[]` | `-f <file>` appended to the router helm command. |
| `extraHelmSets` | `{}` | `--set k=v` appended to the router helm command. |
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

  # extraHelmValues / extraHelmSets → router helm release ONLY.
  # Keys are passed straight through to helm and therefore must match the
  # chart's values schema. With the llm-d-router-{standalone,gateway}-dev
  # charts the EPP replica knob lives at `router.epp.replicas`
  # (previously `inferenceExtension.replicas` on the old GAIE chart).
  extraHelmValues: ["/abs/path/router-values.yaml"]
  extraHelmSets:
    router.epp.replicas: "2"

  # guideVariableOverrides → override/fill ${VAR} tokens the guide
  # README already uses (cannot introduce new variables).
  guideVariableOverrides:
    SOME_README_VAR: "value"
```

## Using `HF_TOKEN` with kustomize

Most llm-d guides serve gated models (Llama, Qwen, etc.) and need a
HuggingFace token to pull weights. Under kustomize the wiring has two
halves — the **Secret** and the **patch that mounts it into the pod**.

### 1. Provide the token before standup

Export the token in the shell that runs `llmdbenchmark`:

```bash
export HF_TOKEN=hf_XXXXXXXXXXXXXXXXXXXX
```

Equivalent fallback env vars (checked in order, first one wins) — see
[`_ensure_hf_token_secret` in `step_06_kustomize_deploy.py`](../llmdbenchmark/standup/steps/step_06_kustomize_deploy.py):

| Variable | Notes |
|---|---|
| `HF_TOKEN` | Plain HuggingFace convention. Recommended. |
| `LLMDBENCH_HF_TOKEN` | Project-prefixed; useful in CI where many vars are namespaced. |
| `HUGGING_FACE_HUB_TOKEN` | Alternative HF convention. |

If none of those is set, step 06 logs `No HF_TOKEN in environment — skipping
secret creation (gated models will fail)` and moves on. The standup will
still complete, but the modelserver pod will crash on first download with
a 401 from HuggingFace.

### 2. What the benchmark does for you

During the kustomize standup, after the guide's prerequisites run but
before the router/modelserver kustomize applies, step 06 calls
`_ensure_hf_token_secret`. It:

1. Checks whether a Secret named `llm-d-hf-token` already exists in the
   target namespace.
2. If not, creates it with a single key `HF_TOKEN` whose value is the
   token you exported:
   ```bash
   kubectl create secret generic llm-d-hf-token \
     --from-literal=HF_TOKEN=$HF_TOKEN \
     --namespace <NAMESPACE>
   ```

This is **idempotent** — re-running standup against a namespace that
already has the secret is a no-op. It does **not** rotate the value.
If you want to rotate, delete the Secret first:

```bash
kubectl delete secret llm-d-hf-token -n <NAMESPACE>
```

### 3. Wire the Secret into the modelserver pod

A Secret in the namespace doesn't do anything on its own — the container
serving vLLM has to *mount* it as an env var. Most upstream guides do
**not** include that env in their modelserver manifests (they expect the
user to add it), so you supply it via `kustomize.patches`:

```yaml
kustomize:
  enabled: true
  guideName: "optimized-baseline"

  patches:
    - patch: |
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: decode                 # ← guide-specific; some guides
                                       #   call this `prefill` or other
        spec:
          template:
            spec:
              containers:
                - name: modelserver    # ← guide-specific container name
                  env:
                    - name: HF_TOKEN
                      valueFrom:
                        secretKeyRef:
                          name: llm-d-hf-token
                          key: HF_TOKEN
```

The patch above is exactly what
[`config/scenarios/guides/optimized-baseline.yaml`](../config/scenarios/guides/optimized-baseline.yaml)
ships with, and is what makes Qwen3-32B pull successfully under
kustomize mode.

**For disaggregated guides (prefill + decode):** add a second `patch:`
entry with `metadata.name: prefill`. Both pods need the env var; a
single strategic-merge patch only matches one Deployment name.

### 4. Putting it all together — full standup command

```bash
export HF_TOKEN=hf_XXXXXXXXXXXXXXXXXXXX

llmdbenchmark \
  --spec config/scenarios/guides/optimized-baseline.yaml \
  standup -t kustomize -p my-llm-d-ns
```

What happens, in order:

1. Step 06 parses `guides/optimized-baseline/README.md` from the cloned
   `llm-d` repo.
2. Runs the guide's prereq commands (namespace creation, gateway provider).
3. Creates `llm-d-hf-token` in `my-llm-d-ns` from `$HF_TOKEN`.
4. Runs the guide's router helm install (the EPP / gateway).
5. Builds the overlay wrapper that takes the guide's modelserver
   kustomize dir as `resources:` and adds your `patches:` (including
   the HF-token env injection) as `patches:`.
6. `kubectl apply -k <wrapper-dir>` — the decode pod comes up with
   `HF_TOKEN` set, downloads weights, starts vLLM.

### Troubleshooting

- **Pod logs show `401 Client Error: Unauthorized`** — the Secret was
  not mounted into the container. Check:
  - `kubectl get secret llm-d-hf-token -n <ns>` exists.
  - `kubectl get deploy decode -n <ns> -o yaml | grep -A4 env:` shows
    the `HF_TOKEN` env entry. If not, your `patches[].patch` didn't
    apply — verify `metadata.name` matches the guide's Deployment name
    (it's `decode` for optimized-baseline; check
    `guides/<guideName>/modelserver/<backend>/` in the llm-d repo for
    others).
- **Standup log says `No HF_TOKEN in environment`** — you forgot to
  export the variable in the shell that ran `llmdbenchmark`. CI users
  typically wire this through `LLMDBENCH_HF_TOKEN` so the project
  namespace prefix groups it with the other settings.
- **The upstream guide README *also* tries to create the Secret** and
  the parser explodes substituting `<your HuggingFace token>` into the
  command — see the next section on skip markers; that block should be
  wrapped on the upstream guide.

## Skipping a block in the upstream guide

Sometimes the upstream guide README adds a setup step that the
benchmark already handles itself, or one that hard-codes a placeholder
the parser will mangle. The classic example is the HuggingFace token: 
`step_06_kustomize_deploy` creates it from `$HF_TOKEN` in
[`_ensure_hf_token_secret`](../llmdbenchmark/standup/steps/step_06_kustomize_deploy.py),
so if a guide also writes a `kubectl create secret generic llm-d-hf-token …`
block in its Prerequisites we end up either duplicating the work or —
worse — substituting an unresolved placeholder like `<your HuggingFace token>`
into a shell command and getting a `your: No such file or directory`
error at runtime.

To opt a block out, wrap it on the **upstream guide README** with our
HTML-comment markers:

```` markdown
- [Create the `llm-d-hf-token` secret in your target namespace …](../../helpers/hf-token.md) to pull models.

<!-- llm-d-cicd:skip start -->
  ```bash
  export HF_TOKEN=<your HuggingFace token>
  kubectl create secret generic llm-d-hf-token \
    --from-literal="HF_TOKEN=${HF_TOKEN}" \
    --namespace "${NAMESPACE}" \
    --dry-run=client -o yaml | kubectl apply -f -
  ```
<!-- llm-d-cicd:skip end -->
````

Effect:

- GitHub renders the README unchanged — HTML comments are invisible, so
  a human walking through the guide still sees the bash block and can
  copy-paste it.
- The benchmark's parser
  ([`llmdbenchmark/kustomize/readme_parser.py`](../llmdbenchmark/kustomize/readme_parser.py))
  drops every fenced block between the markers: **no `GuideCommand` is
  emitted and no `export VAR=…` is harvested into the variable table**.
  The block becomes invisible to `step_06_kustomize_deploy` — no
  duplicate secret, no broken substitution.

Notes:

- Markers are matched case-insensitively and tolerate inner whitespace
  (`<!--LLM-D-CICD:Skip Start-->`, `<!--  llm-d-cicd : skip  start  -->`
  both work).
- They must sit **outside** a fenced code block — a `<!-- llm-d-cicd:skip start -->`
  line that appears between `` ```bash `` and `` ``` `` is treated as
  literal bash, not as a directive. The natural placement is on its own
  markdown line immediately before / after the fence.
- A skip region can span multiple fenced blocks and intervening prose;
  every fenced block inside it is dropped.
- Heading and `<details>` tracking continue to update inside a skip
  region, so blocks that follow the region still land in the correct
  phase (e.g. opening a region under `## Prerequisites` and closing it
  under `## Deploy the Model Server` works as expected).
- If the closing marker is missing, the parser skips to end-of-file.
  Treat that as a bug in the guide, not a feature.

When **not** to use a skip marker — if the block is wrong for *everyone*
(humans included), fix it upstream instead. Skip markers are for blocks
that are correct for a human reader but redundant or harmful for our
automation.

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
- The parser obeys `<!-- llm-d-cicd:skip start -->` / `<!-- llm-d-cicd:skip end -->`
  markers in the upstream guide README — wrapped bash blocks are
  dropped entirely (no commands, no harvested variables). See
  [Skipping a block in the upstream guide](#skipping-a-block-in-the-upstream-guide).
