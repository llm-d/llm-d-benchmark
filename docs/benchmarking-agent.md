# Benchmarking Agent (Agent Core)

`llmdbenchmark/agent/` is the first slice of a Benchmarking Agent MVP for
Run-Only Benchmark Sessions, tracked in
[#1058](https://github.com/llm-d/llm-d-benchmark/issues/1058). It is a
library-level Python package: four pure functions plus one versioned YAML
map. There is no CLI subcommand, no FastAPI service, no database, and no
Kubernetes client anywhere in the package. Everything it produces is a
plain Python object, a `dict`, or a file the caller writes -- submitting a
rendered manifest to a real cluster, watching it, and collecting its
results is out of scope for this slice and is left to whatever already
does that today (`llmdbenchmark run`, or a caller's own client).

## Why a separate package

The Agent Core exists to answer three questions offline, without a
cluster: given a Workload Intent, what should be run; given Execution
Facts, what would that run look like as a command and a Job manifest; and
given the benchmark-report v0.2 output of a run, did it clear a set of
SLO Gates. None of that requires touching `llmdbenchmark.cli`,
`llmdbenchmark.standup`, or the global `config` singleton, and importing
those modules pulls in `planner` (installed only by `install.sh`, not a
declared dependency), so the package deliberately imports nothing from
that chain.

## Inputs

### Structured Recommendation Facts

`RecommendationFacts` has exactly two fields: `workload_intent` (one of
`Interactive Chat`, `Long-Context Generation`, `Batch Throughput`, the PRD
values verbatim) and `prefix_reuse` (bool, default `False`). Nothing else
is in scope of mapping -- this is deliberate: `RecommendationOverrides` and
`ExecutionFacts` are separate models so that neither an override nor an
endpoint detail can silently change which Recommendation Map row is
selected.

### Recommendation Overrides

`specification`, `harness`, `workload_profile`, `slo_gate_percentile`
(`p95` default, `p99` available). Any override degrades Recommendation
Confidence by one step, because an overridden field is no longer
attested by the Recommendation Map's own fit.

### Execution Facts

Everything needed to render a run command and a Job manifest:
`benchmark_session_id`, `endpoint_url`, `model`, `namespace`,
`benchmark_runner_image` (required, no default -- no harness image in this
repository is documented to contain the `llmdbenchmark` CLI, so the
caller must confront that explicitly), `benchmark_workspace_volume`
(PVC claim name and mount path), `benchmark_runner_auth` (a service
account name; `None` means the namespace default), `benchmark_secret_references`
(env or file references, never a value), `benchmark_monitoring_override`.

`ExecutionFacts` is never a parameter of `recommend()`. This is enforced
by the function's signature, not by a runtime check.

### Benchmark Secret References

A `BenchmarkSecretReference` is a discriminated union of
`SecretEnvReference` (`secret_name`, `secret_key`, `env_var`) and
`SecretFileReference` (`secret_name`, `mount_path`). Neither variant has a
field that can hold a secret value, so constructing one with a `value` or
`password` key is a `pydantic.ValidationError`, not something a filter has
to catch downstream.

## The four artifacts

Calling `recommend()`, `render_run_command()`,
`render_benchmark_job_manifest()`, and (optionally) `score_slo_goodput()`
in sequence produces everything `write_agent_session_workspace()` writes
to `<session_root>/<Benchmark Session ID>/`:

- `recommendation.yaml` -- the `RecommendationOutput`: selected
  specification/harness/workload profile, Recommendation ID,
  Recommendation Map Version, Recommendation Confidence, rationale,
  expected artifacts, and the Agent Static Validation diagnostics.
- `run-command.txt` / `run-command.json` -- the Agent-Rendered Run
  Command, as a copy-pasteable string and as an argv list.
- `benchmark-job.yaml` -- the `batch/v1` Job manifest.
- `slo-goodput.yaml` -- only written when a `SloGoodput` is supplied.

`session_root` has no default. The PRD is explicit that it is not the
workspace root, but does not name a path, so the caller supplies one.

## Worked example: Interactive Chat

```python
from pathlib import Path

from llmdbenchmark.agent import (
    ExecutionFacts,
    RecommendationFacts,
    WorkloadIntent,
    WorkspaceVolume,
    recommend,
    render_benchmark_job_manifest,
    render_run_command,
    write_agent_session_workspace,
)

recommendation = recommend(RecommendationFacts(workload_intent=WorkloadIntent.INTERACTIVE_CHAT))

execution_facts = ExecutionFacts(
    benchmark_session_id="chat-001",
    endpoint_url="http://qwen-endpoint.default.svc.cluster.local",
    model="Qwen/Qwen3-0.6B",
    namespace="benchmarks",
    benchmark_runner_image="ghcr.io/example/llmdbenchmark-runner:0.7.0",
    benchmark_workspace_volume=WorkspaceVolume(claim_name="benchmark-workspace"),
)

run_command = render_run_command(recommendation, execution_facts)
manifest = render_benchmark_job_manifest(recommendation, execution_facts, run_command)

write_agent_session_workspace(
    Path("/var/lib/agent-sessions"), execution_facts, recommendation, run_command, manifest
)
```

`recommendation.selected` is
`{specification: "guides/optimized-baseline", harness: "inference-perf",
workload_profile: "chatbot_synthetic.yaml", slo_gate_percentile: "p95"}`.
The rendered command is:

```
llmdbenchmark --spec guides/optimized-baseline --workspace /workspace run \
  --namespace benchmarks --endpoint-url http://qwen-endpoint.default.svc.cluster.local \
  --model Qwen/Qwen3-0.6B --harness inference-perf --workload chatbot_synthetic.yaml --analyze
```

`--spec` and `--workspace` come before `run` because both are root-level
flags (`llmdbenchmark/cli.py:1783-1787`), matching the repository's own
example at `llmdbenchmark/cli.py:1076-1080`. `--analyze` is always
emitted: the in-container analyzer already writes benchmark-report v0.2
files regardless, but `--analyze` also gates step 12
(`step_12_analyze_results.py:27-31`), which re-converts locally and adds
the cross-treatment CSV -- it is not, contrary to an earlier draft of this
feature's PRD, what causes v0.2 reports to exist at all. `--monitoring`
is added only when `benchmark_monitoring_override` is set; its absence is
already the code default (`interface/run.py:203-207`).

The manifest carries both identities as separate label values --
`llmdbenchmark.llm-d.ai/benchmark-session-id` and
`llmdbenchmark.llm-d.ai/recommendation-id` -- plus a
`llmdbenchmark.llm-d.ai/workload-intent` label (slugified, since
`Interactive Chat` is not a legal label value) and the verbatim string as
an annotation. The container's `args` is `run_command.argv[1:]`, the same
list sliced, so the two artifacts cannot drift apart. There is no
`ttlSecondsAfterFinished` on the Job: that omission is Benchmark Artifact
Retention, so a completed run's results are not garbage collected before
anyone looks at them.

## The `inference-scheduling` rename

The PRD's Recommendation Map default names an `inference-scheduling`
guide. That specification does not exist in this repository; it was
renamed upstream to `optimized-baseline`
(`README.md:104`, `util/test-scenarios.sh:91`). `recommendation_map.yaml`
resolves both the Interactive Chat and Batch Throughput rows to
`guides/optimized-baseline` and records the rename in a comment. If a
maintainer wants the literal name `inference-scheduling` to resolve, the
fix is an alias specification file under `config/specification/`, not a
change to this package.

## Recommendation Confidence

`recommendation_confidence` (`high` / `medium` / `low`) describes how well
a Recommendation Map row fits the Structured Recommendation Facts. It is
not an estimate of the probability that a run passes its SLO Gates --
those are two independent questions, and only `score_slo_goodput()`
answers the second one, and only when the caller supplies gates.

## SLO Gates: no built-in thresholds

`score_slo_goodput()` takes a list of `SloGate` (`metric`, `threshold`,
`units`) supplied by the caller. **No threshold value exists anywhere in
this repository** -- not in the schema, not in a workload profile, not in
a scenario file -- and the PRD names neither a metric set nor a number.
Shipping an invented default was judged the single highest-probability
rejection trigger for this PR, so this slice ships none:
`score_slo_goodput()` scores nothing until a caller supplies gates.

**Open question for maintainers:** what is the default gate set (which
`SloMetric` members, at what threshold, at what percentile) per Workload
Intent? Until that is decided, callers must supply their own.

Scoring reads only `results.request_performance.aggregate` from
benchmark-report v0.2 (via `import_benchmark_report`, never the committed
`br_v0_2_json_schema.json`, which is missing `SessionPerformance` and
several `Units` members and would reject valid reports). Every level of
that path may be absent in a real report, so a missing percentile or a
missing `aggregate.throughput` produces a diagnostic (`missing_percentile`,
`missing_throughput`), never an interpolated or derived number. Reports
whose `version` field is absent, `"0.2.1"`, or anything else produce
`missing_report_version`, `version_superset`, or
`unsupported_report_version` respectively; `"0.2.1"` is accepted and
scored as an additive superset of `"0.2"` because nothing in the
converter pipeline currently emits it and rejecting it outright seemed
more likely to surprise a future caller than accepting it.

**This is gate-at-percentile plus the passing reports' throughput, not
per-request SLO attainment.** benchmark-report v0.2 stores aggregates
only; true per-request goodput would need `per_request_lifecycle_metrics.json`,
which is out of scope for this slice. `slo_goodput_output_token_rate` is
the maximum `output_token_rate.mean` among the reports that passed all
gates -- one function's choice of aggregation, not something the PRD
specifies, and flagged here as a second open question.

## Run-Only Benchmark RBAC (documented, not rendered)

This slice does not render a `Role`/`RoleBinding`. The permission set a
Run-Only Benchmark Session actually needs, namespace-scoped:

- `configmaps`: `get`, `list`, `create`, `patch` -- even in run-only mode,
  `_store_run_parameters_configmap` applies the
  `llm-d-benchmark-run-parameters` ConfigMap unless `--dry-run`
  (`llmdbenchmark/cli.py:1171-1273`).
- `pods`, `pods/log`, `pods/exec` -- the harness workload today is a bare
  `Pod` (`config/templates/jinja/20_harness_pod.yaml.j2`); the future Job
  wraps the same container.
- `jobs` -- to submit and watch the `batch/v1` Job this package renders.

**Open question for maintainers:** do you want this rendered as YAML in a
later slice, or is documentation-only sufficient for the MVP?

## What this slice deliberately does not do

- No Load Sweeps, Token-Shape Sweeps, or Run-Treatment Sweep generation.
  The override key needed to vary load is harness-specific (`load.stages[N].rate`
  for inference-perf, `rate` for guidellm, `max-concurrency` for
  vllm-benchmark) and a wrong key silently no-ops through
  `profile_renderer.apply_overrides`. No field on any model in this slice
  names a sweep, so a later slice can add one without a migration.
- No rendered Run-Treatment RBAC YAML (see above).
- No Kubernetes API calls of any kind. `import llmdbenchmark.agent` never
  puts `kubernetes` in `sys.modules`.
- No CLI subcommand. There is no `Command` enum entry and no
  `llmdbenchmark/interface/agent.py`.
- No numeric SLO thresholds (see above).

## Testing

`tests/test_agent_recommend.py`, `tests/test_agent_render.py`, and
`tests/test_agent_score.py` are offline, exercise only the public API, and
pass under `python -m pytest tests/test_agent_*.py -q`. They do not
exercise a live cluster, a GPU, or a model endpoint, and none of that is
claimed. `python -m pytest tests/ -x -q` does not currently complete on
this checkout for reasons unrelated to this package
(`tests/test_kube_helpers_pod_failures.py` hangs after #1630); this PR
does not attempt to fix that.
