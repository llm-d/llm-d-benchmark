# tektonc — A Minimal Template Expander for Tekton Pipelines

`tektonc` is a lightweight command-line tool that helps authors write **reusable Tekton pipeline templates** using
a small extension to standard Tekton YAML.

It is designed for the [`llm-d-benchmark`](https://llm-d.ai) repository, where multiple model, workload, inference-scheduler, and other platform configuration variants need to be expressed cleanly without duplicating boilerplate.

---

## ✨ Purpose

Tekton already provides a powerful foundation for modular and reproducible orchestration:
- **Modularity** — reusable `Task` definitions and `Step`-level composition.  
- **Precedence & dependencies** — control flow through `runAfter` relationships.  
- **Parallelism** — automatic execution of independent tasks.  
- **Failure tolerance** — built-in retries and error handling.  
- **Cleanup & teardown** — handled elegantly using `finally` blocks.

However, in complex `llm-d` benchmarking workflows, you often have a base pipeline structure that needs to repeat the same sequence of tasks for several **models**, **workload variants**, or **inference configurations**.

Manually authoring these combinations quickly leads to large, repetitive, and error-prone YAML.

`tektonc` solves this problem by introducing a **single, minimal construct** for compile-time expansion, enabling high-level loops and parameter sweeps while keeping everything 100% Tekton-compatible.

```yaml
loopName: <id>
foreach:
  domain:
    var1: [a, b, c]
    var2: [x, y]
tasks:
  - name: ...
    runAfter: ...
```

Everything else remains **pure Tekton** — `tektonc` only handles structured expansion.

---

## 🧩 Overview

### Input
1. A Jinja-based Tekton pipeline template (`pipeline.yaml.j2`)
2. A simple YAML file of template values (`values.yaml`)

### Output
A **flat, valid Tekton pipeline YAML** ready for `kubectl apply` or `tkn pipeline start`.

### Example

**Template (`pipeline.yaml.j2`):**

```yaml
apiVersion: tekton.dev/v1
kind: Pipeline
metadata:
  name: {{ pipeline_name }}
spec:
  params:
    - name: message
      type: string
  tasks:
    - name: print-start
      taskRef: { name: echo }
      params:
        - name: text
          value: "Starting pipeline {{ pipeline_name }}"

    - loopName: per-model
      foreach:
        domain:
          modelRef: {{ models|tojson }}
      tasks:
        - name: "process-{{ modelRef|dns }}"
          taskRef: { name: process-model }
          runAfter: [ print-start ]
          params:
            - { name: model, value: "{{ modelRef }}" }
```

**Values (`values.yaml`):**

```yaml
pipeline_name: demo-pipeline
models: ["llama-7b", "qwen-2.5-7b"]
```

Run:

```bash
tektonc -t pipeline.yaml.j2 -f values.yaml -o build/pipeline.yaml
```

Result (`build/pipeline.yaml`):

```yaml
apiVersion: tekton.dev/v1
kind: Pipeline
metadata:
  name: demo-pipeline
spec:
  params:
  - name: message
    type: string
  tasks:
  - name: print-start
    taskRef:
      name: echo
    params:
    - name: text
      value: Starting pipeline demo-pipeline
  - name: process-llama-7b
    taskRef:
      name: process-model
    runAfter:
    - print-start
    params:
    - { name: model, value: llama-7b }
  - name: process-qwen-2-5-7b
    taskRef:
      name: process-model
    runAfter:
    - print-start
    params:
    - { name: model, value: qwen-2-5-7b }
```

---

## 🚀 Capabilities

- **Single construct** — only `loopName + foreach + tasks`
- **Nested loops** — define inner/outer iterations naturally
- **Native Tekton** — all fields (`retries`, `when`, `workspaces`, etc.) pass through unchanged
- **Finally blocks** — support the same loop semantics for teardown/cleanup
- **Deterministic expansion** — Cartesian product enumeration of domains
- **Safe** — Jinja variables (`{{ }}`) resolved at compile-time; Tekton params (`$(params.xxx)`) left untouched

---

## 🧠 When to Use It

Use `tektonc` when you need to:
- generate a Tekton pipeline for benchmarking `llm-d` configurations,
- run configuration sweeps or inference experiments,
- keep YAML human-readable while supporting complex graph expansions.

---

## 🛠️ Installation

```bash
pip install -r requirements.txt
```

Then test it:

```bash
python3 tektonc.py -t tektoncsample/quickstart/pipeline.yaml.j2 -f tektoncsample/quickstart/values.yaml --explain
```

---

## 📘 Command Reference

```
tektonc -t TEMPLATE -f VALUES [-o OUTPUT] [--explain]
```

| Flag | Description |
|------|--------------|
| `-t, --template` | Path to Jinja template file (`pipeline.yaml.j2`) |
| `-f, --values` | Path to YAML/JSON file containing template variables |
| `-o, --out` | Output file (default: stdout) |
| `--explain` | Print an easy-to-read table of task names and dependencies |

---

## 🤝 Contributing

- Keep new features minimal and Tekton-native.
- Avoid adding new syntax unless absolutely necessary.
- Open PRs against the `llm-d-benchmark` repo with clear examples under `tektoncsample/`.

---

**In short:**  
`tektonc` makes Tekton authoring for `llm-d-benchmark` scalable — without inventing a new DSL.  
It keeps templates clean, YAML valid, and expansion predictable.
