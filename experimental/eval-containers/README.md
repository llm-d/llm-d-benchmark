# Experimental `eval-containers` Harness

This directory contains the experimental `eval-containers` harness and its workload profiles (`aider-polyglot`, `gaia`).

## Contents

- `eval-containers-llm-d-benchmark.sh`: Harness entry-point script (runs one agentic evaluation task per parallel pod).
- `profiles/aider-polyglot.yaml.in`: Exercism polyglot code-editing benchmark profile (225 tasks, offline).
- `profiles/gaia.yaml.in`: GAIA general-AI-assistant benchmark profile (165 tasks).

## Scenarios & Documentation

- Experimental scenarios: `config/scenarios/experimental/eval-containers-*.yaml`
- Experimental specifications: `config/specification/experimental/eval-containers-*.yaml.j2`
- Full documentation: [docs/agentic_eval.md](../../docs/agentic_eval.md)
