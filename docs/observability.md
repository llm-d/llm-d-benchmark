## Observability

### Benchmark-Built-In Metrics

The benchmark collects metrics automatically during runs when `metricsScrapeEnabled: true` is set in the scenario config. This includes:

- **vLLM Prometheus metrics** — KV cache, request queues, prefix cache, NIXL transfers, preemptions (117+ metrics scraped every 15s)
- **Replica status** — Desired vs ready vs available replica counts per model per role
- **Pod startup times** — Creation-to-Ready duration per pod per node
- **EPP metrics** — Endpoint Picker dispatch latency, routing scores, request distribution

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
