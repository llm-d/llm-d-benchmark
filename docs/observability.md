## Observability

### CLI Monitoring Flags

The `--monitoring` and `--no-monitoring` flags provide CLI control over monitoring behavior:

```bash
# Enable monitoring: creates PodMonitors at standup, enables metrics scraping during run
llmdbenchmark standup -s <scenario> --monitoring
llmdbenchmark run -f   # -f is shorthand for --monitoring

# Disable monitoring: skips PodMonitor and GAIE ServiceMonitor creation
# Use when the cluster lacks Prometheus CRDs (e.g. GKE without GMP enabled)
llmdbenchmark standup -s <scenario> --no-monitoring

# No flag: scenario defaults apply (PodMonitors created, but no metrics scraping during run)
llmdbenchmark standup -s <scenario>
llmdbenchmark run     # no metrics scraping
```

See [config/README.md — Monitoring and Metrics](../config/README.md#monitoring-and-metrics) for full configuration reference.

### Benchmark-Built-In Metrics

The benchmark collects metrics automatically during runs when `metricsScrapeEnabled: true` is set in the scenario config (or when `--monitoring` / `-f` is passed on the CLI). This includes:

- **vLLM Prometheus metrics** — KV cache usage, GPU/CPU cache and memory, request queues, prefix cache hit rates, NIXL KV transfers, preemptions (scraped every 15s)
- **EPP Prometheus metrics** — Pool-level gauges (KV cache utilization, queue size, ready pods), scheduler/plugin/request duration histograms, token distributions, P/D decision counters
- **GPU/System metrics** — DCGM GPU utilization/power/memory (requires DCGM exporter), container memory/CPU/network via cAdvisor
- **Replica status** — Desired vs ready vs available replica counts per model per role
- **Pod startup times** — Creation-to-Ready duration per pod per node
- **EPP log-derived metrics** — Dispatch latency, endpoint scores, request distribution, per-plugin filter/scorer latencies

Results are written to the `metrics/` directory within each experiment's results and integrated into the benchmark report under `results.observability`.

See [Metrics Collection](metrics_collection.md) for the full technical reference (configuration, data flow, collected metrics, file formats).

### Prometheus, Grafana & Dashboards

For cluster-level monitoring with Prometheus and Grafana (dashboards, alerts, PromQL queries), refer to the upstream llm-d monitoring documentation:

- [Observability and Monitoring in llm-d](https://github.com/llm-d/llm-d/tree/main/docs/monitoring) — Setup guides, PodMonitor configuration, platform-specific instructions
- [Example PromQL Queries](https://github.com/llm-d/llm-d/blob/main/docs/monitoring/example-promQL-queries.md) — Ready-to-use queries for vLLM, EPP, prefix caching, and P/D disaggregation metrics
- [Grafana Dashboards](https://github.com/llm-d/llm-d/tree/main/docs/monitoring/grafana/dashboards) — Community dashboards (vLLM overview, failure/saturation indicators, diagnostic drill-down, KV cache performance, P/D coordinator)

### Distributed Tracing

The benchmark can **configure** OpenTelemetry tracing on deployed modelservice pods by adding a `tracing:` block to your scenario YAML (endpoint, sampling rate, service names). However, it does not deploy a tracing backend (OTel Collector, Jaeger) or collect/analyze traces as part of benchmark results.

For tracing backend setup and instrumentation details, refer to the upstream docs:

- [Distributed Tracing Guide](https://github.com/llm-d/llm-d/blob/main/docs/monitoring/tracing/README.md) — OTel Collector + Jaeger setup, per-component configuration

### Examples

These plots, automatically generated, were used to showcase the difference between a baseline `vLLM` deployment and `llm-d` (for models Llama 4 Scout and Llama 3.1 70B):

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img alt="vllm vs llm-d comparison" src="./images/scenarios_1_2_3_comparison.png" width=100%>
  </picture>
</p>
