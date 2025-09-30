# Resource requirement for benchmarking

llm-d benchmark by default requests 16 CPUs in the benchmark launcher pod as defined by `LLMDBENCH_HARNESS_CPU_NR` in [run.md](./run.md#use) Depending on the Kubernetes cluster you run this on, you can change it to a different number based on the machines you have available to you.

## How to configure the resources needed for the benchmark?

Benchmarking is very CPU intensive and both the CPU clock speed and the number of cores matter. If you are using one of the multi-process harnesses like inference-perf, you can reach a higher concurrency and as a result higher load when you have more vCPUs / cores. If you are using a single process harness like vllm-benchmark, CPU clock speed will help with handling more concurrent requests, but by default you will be limited by the number of asyncio threads that the CPU can handle without slowing down.

Based on the harness you are running, set the appropriate CPUs. For detailed information on how multi-process load generation works, refer to this [load generation guide](https://github.com/kubernetes-sigs/inference-perf/blob/main/docs/loadgen.md).