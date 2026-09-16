# Proposal: selective convergence with BenchFlow

## Status

Design proposal. This document is intended to guide follow-up implementation
PRs; it does not add a runtime dependency on BenchFlow, Tekton, Kueue, or
MLflow.

## Summary

`llm-d-benchmark` should remain the core, harness-independent benchmarking
framework for llm-d. It already provides declarative scenarios, a complete
standup/run/teardown lifecycle, DoE treatments, multiple benchmark harnesses,
Prometheus analysis, and the portable Benchmark Report format.

BenchFlow contributes a useful control-plane layer around those operations.
The recommended direction is selective convergence: adopt compatible
contracts and optional adapters, rather than merging the two codebases or
making BenchFlow's infrastructure mandatory.

The highest-value additions are:

1. An immutable, fully resolved execution plan.
2. First-class AIPerf trace workloads, including Weka, Mooncake, and agentic
   traces.
3. Explicit execution identity, resource isolation, and setup-key locking.
4. Pluggable execution backends with optional GPU-aware scheduling.
5. Provenance and artifact publishing integrations, including MLflow.

## Current-state comparison

| Capability | `llm-d-benchmark` today | BenchFlow contribution | Direction |
| --- | --- | --- | --- |
| Declarative configuration and lifecycle | Strong scenario and DoE model | Experiment/profile model | Keep the existing model; add a resolution boundary |
| Matrix execution | Existing setup/run treatments and matrix expansion | Explicit Cartesian matrix and child execution model | Align concepts; do not replace the DoE engine |
| Harnesses and reports | Multiple harnesses and canonical Benchmark Report | Toolbox runners and MLflow-centric results | Keep the report as the canonical interchange format |
| Resolved execution contract | Configuration is captured in workspaces | Immutable `RunPlan` | Add a first-class resolved plan |
| Execution | Local/Kubernetes-oriented lifecycle | Asynchronous Tekton execution | Add optional backends behind an executor interface |
| GPU admission | Capacity validation and bounded parallelism | Kueue queues and GPU-aware admission | Add an optional scheduler adapter |
| Trace replay | Harness-dependent and experimental multi-turn support | AIPerf trace profiles and dataset handling | Promote trace replay to a workload abstraction |
| Metrics and artifacts | Prometheus analysis, workspace artifacts, GPU fields in reports | DCGM bootstrap, artifact capture, MLflow upload | Normalize collection and add optional publishers |
| Isolation | Workspace and execution state | Execution-scoped resources and setup locking | Make identity and cleanup invariants explicit |
| Multi-cluster execution | Deployment flexibility | Management and target cluster split | Defer until the executor contract is stable |

## Proposed architecture

```text
Experiment/configuration
        |
        v
  Resolver and validator
        |
        v
 Immutable RunPlan (JSON/YAML artifact)
        |
        +------------------+
        |                  |
     Scheduler          Executor
   local/bounded      local | Job | Kueue | Tekton
        |                  |
        +--------+---------+
                 |
       setup -> benchmark -> collect -> cleanup
                 |
                 v
         Canonical Benchmark Report
                 |
       filesystem | MLflow | other stores
```

The RunPlan is the boundary between user intent and execution. It must be
deterministic, serializable, and sufficient to understand or reproduce a run
without re-resolving mutable defaults. It should include, where applicable:

- source revisions and timestamps;
- model, llm-d, vLLM, harness, and image references (including digests when
  available);
- target cluster, accelerator type/count, replicas, and parallelism;
- rendered deployment and scheduler/EPP configuration;
- benchmark profile, parameters, random seed, dataset/trace identity, and
  replay mode;
- metrics configuration and collection intervals;
- execution identity, resource requests, priority, and artifact locations.

The existing workspace remains valuable as the detailed artifact bundle. The
RunPlan is the small, immutable index and input contract for that bundle.

## Workload and trace model

Workloads should expose a common abstraction with at least these variants:

```yaml
workload:
  type: aiperf-trace
  trace:
    type: weka
    name: cc-traces-weka-062126
    source: s3://example/traces.jsonl
    replay_mode: fixed_schedule
```

The initial trace adapters should cover the profiles already exercised in
BenchFlow: Weka, Mooncake, and Mooncake tool-agent/agentic traces. A custom
AIPerf trace should remain possible without adding a new framework type for
every dataset. The resolved plan and Benchmark Report should retain trace
type, source/name, request/session counts, seed, and replay mode.

This should integrate with the existing harness abstraction rather than
introduce a second benchmark runner. AIPerf-specific argument construction,
dataset caching, and result normalization belong in the AIPerf integration.

## Execution and scheduling

The executor contract should represent a run as named stages with explicit
ownership of resources and artifacts. The first implementation can continue
to use the current local lifecycle. Future adapters may submit the same
RunPlan to a Kubernetes Job, Tekton PipelineRun, or Kueue-managed workload.

Kueue must be optional. A run should be schedulable with a requested GPU
budget and priority when Kueue is installed, while local execution should
continue to work without it. Matrix execution should support a bounded
parallel strategy such as:

```yaml
execution:
  strategy: parallel
  max_parallel: 4
  gpu_budget: 16
  priority: low
```

The scheduler must report admission, execution, and terminal status in a
stable format. Backend-specific objects (PipelineRuns, Jobs, queues) should
not leak into the core Benchmark Report.

### Execution identity and setup locking

Every treatment execution should have one stable identity used for:

- workspace and artifact paths;
- Kubernetes labels, names, and resource ownership;
- logs, reports, and cleanup selection;
- matrix parent/child relationships.

Cleanup must select resources by that identity and never by a broad shared
name. A setup key should optionally serialize runs that depend on the same
mutable platform setup, while allowing compatible workload treatments to
share a deployed setup. The key should include the deployment-relevant
inputs, such as model, accelerator, software revisions, topology, and EPP
configuration.

## Results, metrics, and artifacts

The Benchmark Report remains the canonical, harness-independent result. It
already models inference, workload, and GPU/system metrics; follow-up work
should standardize collection of GPU utilization, memory, power, temperature,
and bandwidth time series when DCGM is available.

Artifact publishing should be a separate concern from report generation. A
run should be able to retain locally:

- the RunPlan and rendered manifests;
- the Benchmark Report and native harness output;
- Prometheus and GPU metric exports;
- plots, logs, and workload/trace metadata.

An optional MLflow publisher can then record the RunPlan, report, parameters,
tags, metrics, and artifact bundle. MLflow should be an additional backend,
not a replacement for the portable report or filesystem workspace.

Sensitive values must be redacted before publishing manifests, pod
descriptions, command lines, or environment metadata.

## Phased implementation

### Phase 1: contracts and immediately useful workloads

- Define and validate the RunPlan schema and resolver output.
- Record immutable plan, source revision, seed, image references, and
  execution identity in every workspace.
- Add normalized AIPerf trace workload configuration for Weka, Mooncake, and
  agentic traces.
- Add trace provenance to the Benchmark Report or its versioned extension.
- Make resource ownership and cleanup identity explicit.
- Standardize optional DCGM time-series collection where the exporter exists.

### Phase 2: scale-out execution

- Add bounded parallel treatment execution while preserving existing DoE
  semantics.
- Add setup-key locking and compatible setup reuse.
- Implement a Kueue adapter for GPU-aware admission and priority.
- Add a Kubernetes Job adapter if it provides value independently of Tekton.

### Phase 3: control-plane integrations

- Add a Tekton adapter that submits a RunPlan and observes stage results.
- Add MLflow result and artifact publishing.
- Add management-cluster/remote-target execution.
- Consider a controller only after the executor and status contracts have
  proven stable.

## Explicit non-goals

- Do not replace the existing DoE engine with BenchFlow's matrix code.
- Do not replace Benchmark Report with MLflow records.
- Do not make Tekton, Kueue, MLflow, or platform bootstrap operators required
  dependencies of the core CLI.
- Do not copy BenchFlow's complete bootstrap stack into this repository.
- Do not merge BenchFlow's platform-specific deployment implementations when
  an existing llm-d-benchmark lifecycle already covers the use case.

## Acceptance criteria for follow-up implementation

- Resolving the same input and pinned source context produces byte-stable
  RunPlan output, apart from explicitly documented timestamps.
- A RunPlan can be inspected or executed without re-reading user defaults.
- A Weka, Mooncake, or agentic trace run records enough provenance to identify
  the exact trace and replay mode.
- Two concurrent treatments cannot clean up or overwrite one another's
  resources or artifacts.
- Core local execution works with none of the optional control-plane services
  installed.
- Optional backends expose the same stage/status/artifact contract.
- Existing Benchmark Report validation and current lifecycle/DoE tests remain
  passing.

## Reference implementation

This proposal is informed by the BenchFlow implementation at
[`albertoperdomo2/benchflow@aa28cb4`](https://github.com/albertoperdomo2/benchflow/tree/aa28cb45dc7a86c0a6512af789e41d29d9503bb5), in particular its
resolved plan contracts, matrix expansion, orchestration, Kueue integration,
artifact/MLflow publishers, metrics collection, and AIPerf runner. The
reference is used for design comparison; it is not proposed as a wholesale
code import.
