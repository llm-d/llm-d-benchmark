# llm-d Results Store: Submission Policy

This policy governs the submission, review, and publication of AI inference benchmarks in the **llm-d Results Store**. This policy does not govern the use of the published benchmarks or claims made about the benchmarks outside of the llm-d Results Store.

The results store is designed to be the **authoritative open repository for AI inference performance data**, providing a standardized, vendor-neutral repository of validated, reproducible, and queryable benchmarks.

## 1. Core Principles

To maintain the integrity, neutrality, and utility of the llm-d Results Store, the submission and publication process is grounded in the following principles:

### 1.1 Separation of Data and Narrative Claims

**The Numbers vs. The Words:** The results store is strictly a repository for **numerical benchmark results and configurations**, not marketing claims or narrative statements.

**Scope of Approval:** The review and publication process only certifies that the numerical data is **valid, complete, and reproducible** under the described configurations. It does **not** endorse or approve any narrative claims, interpretations, or comparison summaries built on top of the data.

**Independent Claims Process:** Narrative claims (e.g., *"Optimization ABC shows up to 50% higher P90 latency over standard services"*) must be evaluated and approved via organization-specific product marketing or disclosure processes. Those processes can easily reference published benchmarks in the results store using their unique **Run UID**.

### 1.2 Mandatory Attribution

**Explicit Ownership:** Every submission must clearly identify the individual who ran the benchmark and the organization they represent.

**No Anonymous Submissions:** To prevent gaming, bias, or unverified claims, anonymous or un-attributable results will be rejected.

### 1.3 Verifiability & Reproducibility

**Provide Evidence:** Every submission must include raw log files, cluster configurations, and system metrics verifying the execution.

**Replication Recipes:** The stack deployment recipe, model configurations, and workload generator options must be fully documented to allow other community members or automated agents to reproduce the run.

## 2. Governance and Decision Making

The technical governance of the **llm-d Results Store** follows the hierarchical technical governance structure of the `llm-d-benchmark` repository:

- A community of **contributors** who run benchmarks and submit them using the submission flow supported by the results store CLI.  
- A body of **core maintainers** who own the results store, define the validation pipelines, and have final authority over benchmark review, verification, and promotion.  
- A **lead core maintainer** who is the catch-all decision maker when consensus cannot be reached by core maintainers.

The list of current core maintainers can be found in the `llm-d-benchmark` repository's [OWNERS](https://github.com/llm-d/llm-d-benchmark/blob/main/OWNERS) file.

### 2.1 Lead Core Maintainer

When core maintainers cannot come to a consensus, the Lead Core Maintainer is expected to settle the debate and make executive decisions. The Lead Core Maintainer is also responsible for confirming or removing core maintainers.

- **Lead Maintainer:** [Marcio Silva](https://github.com/maugustosilva)

### 2.2 Decision Making

Submissions are categorized to ensure efficiency, consistency, and clarity in the review process:

- **Uncontroversial Submissions:** Benchmark runs that conform to standard workload profiles, pass all automated verification agents, include complete attribution/reproducibility artifacts, and exhibit expected performance characteristics are accepted and promoted by default.  
- **Controversial Submissions:** Submissions that feature anomalous/extreme results, utilize customized run rules, or include non-standard configuration modifications are evaluated on a case-by-case basis under the subjective judgment of the core maintainers. Core maintainers reserve the right to request additional verification runs or reject submissions that compromise comparability or reproducibility.

## 3. Submission Tiers

Submissions to the results store are categorized into two tiers, depending on the level of reproducibility and evidence provided:

**Tier 1: Experimental Benchmark Results**

Submission to this tier is intended to facilitate peer review and discussion.

Requirements: Conforms to report v0.2 schema, contains basic hardware/model metadata, and passes basic automated sanity checks.

Storage Location: gs://llm-d-benchmarks-staging/

**Tier 2: Fully Reproducible Benchmark Results (Golden)**

Requirements: Conforms to report v0.2 (or more recent)  schema + contains infrastructure manifests (e.g. Helm, Kubernetes configs) + exact replication scripts + passes rigorous human and agent review.

Storage Location: gs://llm-d-benchmarks/

## 4. Attribution & Verification Metadata

To ensure full transparency, every benchmark report must include the following metadata in the run block:

```yaml
run:
  uid: "c6bc210e-5a82-4bf8-b57f-d52b310e3032"
  user: "jane.doe@organization.com"
  description: "Optimization review for prefix caching with kimi-k2"
  keywords: ["prefix-cache", "lws", "vllm"]
  submitter:
    name: "Jane Doe"
    email: "jane.doe@organization.com"
    organization: "Acme AI Corp"
    github_handle: "janedoe-acme"
  evidence:
    harness_logs_uri: "gs://acme-benchmark-logs/runs/c6bc210e/harness.log"
    system_metrics_uri: "gs://acme-benchmark-logs/runs/c6bc210e/prometheus_metrics.json"
```

## 5. Referencing and Citing Benchmarks

Approved benchmarks in the results store must be cited using a standardized format to ensure claims can be independently audited.

### 5.1 Citation Format

When publishing narrative claims or referencing benchmarks internally or externally, include the following citation details:

"([Submitter Organization]), [Month] [Year], llm-d Results Store (Run UID: <run_uid>, URI: gs://llm-d-benchmarks/default/<scenario>/<model>/<hardware>/<run_uid>)"

### 5.2 Citation Example

"Using prefix-aware routing with vLLM on 8x TPU7x for code generation workloads improves P90 TTFT latency by 45% compared to the standard round-robin routing baseline (Source: Jane Doe (Acme AI Corp), June 2026, llm-d Results Store Run UID: c6bc210e)."