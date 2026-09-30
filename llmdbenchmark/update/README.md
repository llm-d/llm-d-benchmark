# llmdbenchmark.update

Changes knobs on a stack that is already stood up, re-applying only the components the change affects.

## Why update

Changing one vLLM flag or one EPP plugin parameter used to mean `teardown` + `standup`: model weights downloaded again, PVCs recreated, CRDs and the gateway reinstalled. `update` renders the same plan and re-applies only the steps that own the changed config, so a vLLM knob restarts the serving pods and an EPP knob restarts the endpoint picker. Step 06 also re-applies the gateway provider and the shared `infra-{release}` release; with unchanged values helm changes nothing there.

It does not add any restart logic of its own. `helmfile apply` is `helm upgrade --install`, and both the vLLM args and the `VLLM_*` env vars live in the pod template, so changing them rolls the pods. The EPP plugins config is a ConfigMap, but the upstream router chart stamps a `checksum/config` annotation onto the EPP pod template, so helm rolls the EPP too. `update` only decides *which releases to re-apply*, then waits until the rollout is done -- not only until some pod is Ready, because the old pod stays Ready while its replacement starts.

## Usage

```bash
# Change a vLLM knob -- restarts decode/prefill only
llmdbenchmark --spec gpu update -p my-namespace --set decode.replicas=4

# Change an EPP knob -- restarts the endpoint picker only
llmdbenchmark --spec gpu update -p my-namespace --set router.epp.replicas=2

# Skip the smoketest that runs at the end
llmdbenchmark --spec gpu update -p my-namespace --set decode.replicas=4 --skip-smoketest

# Preview without changing the cluster (the stored flags are still read)
llmdbenchmark --spec gpu update -p my-namespace --set decode.replicas=4 --dry-run

# Re-apply a component directly, without inferring it from --set
llmdbenchmark --spec gpu update -p my-namespace --component epp
```

`--set` goes after the subcommand. Global options such as `--spec` may go before it or after. `-p` is required unless `--no-reuse-invocation` is passed (see [Flag continuity](#flag-continuity)).

A rolling update starts the new pod before it stops the old one, so it needs room for one more pod. On a full GPU pool the new pod stays Pending until the wait times out, and the timeout message names it.

## Which components a change touches

`update` maps the **top-level** key of every `--set` path to the standup steps that own the matching resources. Overrides always target the top-level path: `router.epp.pluginsConfigFile`, never `modelservice.router.epp.pluginsConfigFile`.

| Change | Components | Steps |
|---|---|---|
| `dra`, `extraObjects`, `modelArtifacts`, `modelservice`, `multinode`, `resourcePresets` | vllm | 6, 8 |
| `decode`, `prefill` | fma, vllm | 5, 6, 8 |
| `accelerator`, `affinity`, `common`, `control`, `httpRoute`, `routing`, `schedulerName` | standalone, vllm | 5, 6, 8 |
| `annotations`, `labels`, `vllmCommon` | standalone, fma, vllm | 5, 6, 8 |
| `images` | standalone, vllm, epp | 5, 6, 7, 8 |
| `router` | epp | 6, 7 |
| `standalone` | standalone, fma, vllm, epp | 5, 6, 7, 8 |
| `fma` | fma | 5, 6, 8 |
| `prism` | prism | 9 |
| `chartVersions`, `helmRepositories` | vllm, epp, infra | 6, 7, 8 |
| `monitoring` | monitoring, standalone, vllm, epp | 3, 5, 6, 7, 8 |
| `keda`, `wva` | monitoring, fma, vllm | 3, 5, 6, 8 |
| `eppKedaSaturation` | monitoring, vllm | 3, 6, 8 |
| `openshiftMonitoring` | monitoring | 3, 6, 8 |
| `gateway` | admin, infra, vllm, epp | 2, 6, 7, 8 |
| `gatewayProviders` | admin, infra | 2, 6 |
| `lws` | admin | 2 |
| `serviceAccount` | namespace | 4 |
| `serviceAccountOverride` | namespace, vllm | 4, 6, 8 |
| `huggingface` | namespace, standalone, fma, vllm, epp | 4, 5, 6, 7, 8 |
| `model` | namespace, standalone, fma, vllm, epp | 4, 5, 6, 7, 8 |
| `storage` | namespace, prism, vllm | 4, 6, 8, 9 |
| `downloadJob` | namespace | 4 |
| `namespace` | admin, namespace, infra, vllm, epp | 2, 4, 6, 7, 8 |
| `gatewayApiCrd` | admin | 2 |
| `release` | vllm, epp, infra | 6, 7, 8 |
| `kustomize` | kustomize | 5 |
| `nok8s` | nok8s | 5 |

The table is `KEY_COMPONENTS` in `__init__.py`, written out; `tests/test_update_scope.py` checks that every top-level key of `defaults.yaml` is in it. Steps are listed before they are pruned to the deploy method in use: steps 06-08 only run for modelservice and step 05 only for the other methods, so on a pure `fma` stack the `fma` row runs step 5 alone.

Step 06 is always added alongside 07 or 08. It writes the values files (`infra.yaml`, `router-values.yaml`, `ms-values.yaml`) that the helmfile references by relative path; every invocation renders into a fresh workspace, so without step 06 those files do not exist and helmfile cannot resolve them.

Components whose steps the active deploy method never runs are dropped, so a modelservice stack is not told it updated `standalone`.

The flags that change what gets rendered count the same way as a `--set` of the key they set: `-m` as `model`, `-r` as `release`, `--gateway-class` as `gateway`, `-a`/`-b` as `affinity`/`annotations`, `--wva`, `--epp-keda-saturation`, `--monitoring` and `--prism` as their keys, and `--no-pvc`/`--pvc` as `storage`. `-t` switches the deploy method, so it is refused like `kustomize`. `--full-infra` restarts nothing. A flag only counts when it differs from what the original standup used.

### Shared infrastructure

`gateway`, `gatewayProviders`, `monitoring`, `openshiftMonitoring`, `keda`, `eppKedaSaturation`, `wva`, `lws`, `serviceAccount`, `serviceAccountOverride` and `huggingface` re-run a global step that touches cluster-scoped or shared resources. They are applied with a warning naming what is being re-run -- but the ones that reach the `admin` or `namespace` component also need `--force`, see below.

### Changes that are refused

These cannot be applied to a live stack in place, and are refused unless `--force` is passed:

| Change | Why |
|---|---|
| `model`, `-m` | the weights must be downloaded again |
| `storage`, `--no-pvc`/`--pvc` | PVCs cannot be resized or rebound in place |
| `namespace` | every resource moves, orphaning the current namespace |
| `gatewayApiCrd` | re-applies cluster-scoped CRDs, affecting every tenant |
| `release`, `-r` | renames every helm release, orphaning the installed ones |
| `kustomize`, `nok8s`, `-t` | switches to a different deploy path |
| any change that reaches the `namespace` component (`downloadJob`, `serviceAccount`, `serviceAccountOverride`, `huggingface`, ...) | step 04 recreates PVCs, secrets and the weight-download job |
| any change that reaches the `admin` component (`gateway`, `gatewayProviders`, `lws`, ...) | step 02 re-applies cluster-scoped CRDs |
| `--component namespace`, `admin`, `kustomize` or `nok8s` | the same reasons |
| `-s` with a step of those components (2, 4, and 5 for kustomize/nok8s) | the same reasons |

The gate looks at where a change lands, not at which key named it, so no spelling of a change gets past it. Prefer `teardown` then `standup` for these. `--force` applies them anyway, and the result may differ from a clean standup.

### Changes that restart nothing

`harness`, `experiment` and `dataAccess` are read by the run phase; `description` is metadata; `idleCleanup` drives a standalone CronJob. `update` warns and, when nothing else is in scope, re-applies nothing. It still reads and records the flags.

An override whose top-level key is not in any of these groups is refused, because it is either a typo or a key nobody has classified yet.

## Flag continuity

`update` re-renders the whole plan from the specification and the scenario. A flag the original standup passed and this invocation omits would therefore revert that knob.

To avoid that, standup records its render-affecting flags in the in-cluster `llm-d-benchmark-standup-invocation` Secret, and `update` reads them back from the `-p` namespace (the harness one, for `-p infra,harness`) and reuses them. Anything passed on this invocation wins over the persisted value, every reused value is logged, and only a value that differs from the stored one counts as a change -- typing a standup flag again is harmless. An on/off flag can be turned off with its `--no-...` form (`--pvc` for `--no-pvc`). `--no-wva` with `LLMDBENCH_WVA=true` (and the same for `--no-epp-keda-saturation`) contradict each other, so `update` warns and exits without doing anything. `LLMDBENCH_SET` counts as `--set`.

It is a Secret because a `--set` value may carry a token. The deploy metadata for the benchmark report stays in the `llm-d-benchmark-standup-parameters` ConfigMap.

```bash
kubectl get secret llm-d-benchmark-standup-invocation -n <ns> -o json | jq '.data | map_values(@base64d)'
```

- Without `-p`, `update` cannot find the Secret and stops, rather than reverting the flags. When the read fails (no connection, no access), it stops too.
- When the Secret does not exist (a stack stood up by an older version), `update` warns that continuity cannot be guaranteed and proceeds with what was passed.
- `--no-reuse-invocation` renders from this invocation alone. The stored flags are still read, only to tell what changed.
- `--cluster-config` is stored as an absolute path. If that file is not readable on the machine running `update`, its values are not applied and a warning says so -- pass `--cluster-config` again.
- Global options such as `-i/--non-admin` are not stored: pass them again.
- Only `update` reuses the stored flags. A later `run`, `smoketest` or `teardown` renders from its own flags, so pass the changed `--set` there too if those phases read it.

Flags given with `--stack` describe those stacks only. `update` reuses them only when it covers the same stacks or fewer; otherwise it warns and renders from this invocation alone. An update that covers fewer stacks than the stored flags is not recorded, because one set of flags cannot hold a change for some stacks only: use a stack-scoped `--set 'NAME:key=value'` without `--stack` to keep it.

## Several deployments in one namespace

A multi-model scenario puts every stack in the same namespace. They share the
`infra-{release}` helm release, but each owns its own `{model_id_label}-ms` and
`{model_id_label}-router`, so updates can be scoped to one of them.

A change with no stack selector applies to **every** stack, which restarts
their serving pods too. `update` warns when that is about to happen. Scope it
either way:

```bash
# only this stack's steps run
llmdbenchmark --spec multi update -p ns --stack llama-31-8b --set decode.replicas=4

# only this stack's value changes
llmdbenchmark --spec multi update -p ns --set 'llama-31-8b:decode.replicas=4'
```

`--stack` is the stronger of the two: it stops the sibling's releases from being
re-applied at all, rather than re-applying them unchanged. A stack-scoped `--set`
for a stack that `--stack` leaves out is refused, because it would not be applied.

The deployed-stack check runs per stack, not per namespace. A sibling being up
does not make a missing stack look deployed -- updating that one would install
its two releases while the steps the scope skips (namespace, PVCs, weights) stay
missing, so it is refused with the stack named. For modelservice it looks for the
stack's `-ms`/`-router` release; for standalone and fma for the stack's own
Deployment; for kustomize for the pods of the stack's guide.

## When `--set` cannot express the change

`--set` takes a dotted path to a scalar, so two shapes are out of reach:

- **A value inside a multi-line string.** The EPP plugins document lives in
  `router.epp.pluginsCustomConfig."<file>.yaml"` as one literal block, so
  `turnPriorityTimeWeight` has no path of its own. Passing the whole block also
  fails when it contains an unquoted comma (a comment inside the block is
  enough): the `--set` splitter breaks pairs there.
- **An element of a list.** `vllmCommon.volumes` is a list, and a dotted path
  that descends into one is refused rather than silently replacing it.

Use `--cluster-config <file>` for both. It is deep-merged over the scenario with
the same precedence as `--set` (and below it), takes whole blocks and lists
verbatim, and is recorded by path, so later updates reuse it.

A `--cluster-config` file does **not** by itself say which component changed:
the scope is read only from the `--set` pairs and flags this invocation changes,
so neither a reused file nor a flag the original standup passed can scope a run,
or refuse one, over a key nobody asked to change. Name the component
explicitly:

```bash
# weight lives inside a literal block -> whole block in a file, scope by hand
llmdbenchmark --spec guides/turn-priority-fairness update -p ns \
  --cc ~/.llmdbench/turn-priority-weight.yaml --component epp
```

Paired with a `--set`, the file needs no `--component`: the `--set` sets the
scope and the file rides along.

Note the file is reused **by path**: rewrite it in place to change the value, and
keep it readable from wherever the next update runs.

## Notes

- `-s/--step` overrides the inferred steps entirely, for when you know exactly which step you want. It may be given alone.
- `update` refuses to run when a stack in scope is not deployed: updating a stack that was never stood up would half-create it. When the deployed stacks cannot be listed at all, it warns and goes on.
- An update that changes the EPP waits for the new EPP pod in step 07, because step 08, which waits for it on a standup, may not be in scope.
