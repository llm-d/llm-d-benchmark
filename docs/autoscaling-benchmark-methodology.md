# Compare autoscaling benchmark runs

Use this worksheet to compare a live autoscaler with a fixed replica pool under the same offered
traffic. For wiring and calibration, use the [inflight](inflight-autoscaling.md) or
[token-aware](token-aware-autoscaling.md) integration guide.

## Record the comparison before running it

| Field | What to record |
| --- | --- |
| Stack | Benchmark commit, scenario, resolved images, model revision, accelerator type and count, and parallelism. |
| Scaling | Live signal and thresholds, replica bounds and scaling periods. For the fixed arm, record the replica count for each role and how you chose it. |
| Workload | Harness and profile, effective overrides, dataset or prefix-cache policy, input/output token lengths, and offered request rate and duration per stage. |
| Success criteria | The latency percentile and threshold, error budget, and measurement window for each comparison. |
| Repetitions | How many pairs to run, their order, and the initial replica, queue and cache conditions each run must meet. |

Keep the model, hardware, router, workload and monitoring settings the same within a pair. A staged
load can show how the pool ramps up and drains. A separate sharp step can measure how it reacts to
a burst. Report stage rates and durations, even when using a stock profile. Give the low-load
phases enough time for the configured scale-down policy to act.

If the fixed pool is sized from an observed `demand.peak`, record the pilot run and trigger settings
that produced it. If you choose the count in advance, record its capacity or resource-budget basis.
Keep that count fixed throughout the measurement. The [serving-capacity guide](serving-capacity.md)
explains how to measure the requests per second a replica can sustain for a particular workload
while meeting the time-to-first-token (TTFT) objective.

## Check that the runs are comparable

- Start each repetition on a freshly stood-up stack with the same initial conditions. Apply any
  required per-signal calibration again. A prior run can leave queued requests, warm caches, stale
  signal values, or more ready replicas than the next run was supposed to start with.
- Verify replica state and the intended scale target before each run. When using the
  [KEDA dry-run configuration](../config/README.md#autoscaling-dry-run-eppkedasaturationdryrun)
  for the fixed arm, KEDA writes decisions to a shadow target. Confirm that the serving replica
  count stays fixed and label any shadow decisions separately from serving replicas.
- Use `llmdbenchmark run --monitoring` to collect the signals and replica counts. Wait for reports
  to finish, check that the time windows cover the same workload stages, and verify that the scrape
  contains the series needed for the comparison. Mark any missing series as unavailable.
- Record any excluded run and its reason, such as a failed monitoring scrape or the wrong
  starting state. Keep request failures from a valid run in its results.

## Report each pair

Report results by stage and repetition. Use the same metric definitions, units, aggregation, and
measurement windows for both arms. Keep latency percentiles separate by stage and show their spread
across repetitions.

| Result | Where to look |
| --- | --- |
| TTFT, inter-token latency and end-to-end latency at p50, p90 and p95 when available | [v0.2 benchmark reports](analysis.md#output-directory-structure) for each stage. |
| Offered and completed requests per second, output tokens per second, total and failed requests | Workload stages for offered rate and [benchmark reports](../benchmark-report/README.md) for observed results. State the denominator for failure rates. |
| Average and peak replica counts, GPU-hours | [Replica time series](token-aware-autoscaling.md#reading-a-run). For provisioned GPU-hours, integrate the actual allocated GPU count across the comparison window and state that window. Report GPU-hours as unavailable if the allocation history is missing. |
| Queue depth and KV cache utilization at p50 and p95, where collected | [Monitoring output](metrics_collection.md). State whether each statistic uses a pool-level series or an aggregation of per-pod series. |
| Peak demand, observed autoscaler decision and provisioned replicas | `autoscaling-sizing.json` for runs that emit the [KEDA sizing report](../config/README.md#autoscaling-dry-run-eppkedasaturationdryrun). Demand is the replica count the trigger requests before scaling policy, within its configured bounds. |

Align offered load, scaling signal, replica counts, and TTFT on a shared timeline where the sampled
data allows. Label repetitions and arms with `--run-description` and `--run-keywords`, and keep the
run IDs or report paths with the summary so reviewers can find the original measurements. Remove
private identifiers from any reports prepared for public sharing.
