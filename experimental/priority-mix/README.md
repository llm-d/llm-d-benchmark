# Experimental `priority-mix` Harness

This directory contains the experimental `priority-mix` harness and its workload profiles (`priority_mix`, `sanity`) for EPP flow-control validation.

## Overview

The `priority-mix` harness profile defines traffic classes with different `objective` values; the harness converts each objective to an `x-llm-d-inference-objective` request header. The target stack must define matching `InferenceObjective` resources, for example through `router.inferenceObjectives`, so those headers resolve to configured priority bands instead of falling back to priority `0`.

Streaming profiles also report per-traffic-class TTFT and TPOT so priority impact can be compared across classes. Each traffic class may override the global request with its own `request.prompt`, `request.promptTemplate`, `request.promptRepeat`, and `request.max_tokens`, which is useful for creating high-priority shared-prefix traffic and low-priority long-prompt cache pressure when evaluating priority-based KV eviction.

## Contents

- `priority-mix-llm-d-benchmark.sh`: Harness entry-point script.
- `priority_mix.py`: Load generator implementation.
- `priority-mix-analyze_results.sh`: Result validation script.
- `profiles/priority_mix.yaml.in`: Mixed-priority chat workload profile (6 req/s, 90s).
- `profiles/sanity.yaml.in`: Quick sanity mixed-priority chat profile (1 req/s, 10s).
