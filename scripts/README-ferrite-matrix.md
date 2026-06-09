# Ferrite-vs-python comparison matrix

Tooling to benchmark [vllm-rs / ferrite](https://github.com/llm-d/llm-d-vllm-rs)
against python vLLM through the standard llm-d-benchmark pipeline. Produces
side-by-side delta tables matching the format the project lead has been pasting
in Slack.

---

## TL;DR

```bash
# 1. Generate / regenerate the matrix profile YAMLs (idempotent)
python3 scripts/generate-ferrite-matrix.py

# 2. Run the python baseline (Tier 1 sanity, ~20 cells, ~30 min on H100)
scripts/run-ferrite-matrix.sh \
    --tier tier1 --scenario ferrite-7b --ns my-py-ns \
    --workspace ~/data/ferrite-matrix-7b-py

# 3. Run ferrite (same matrix, different image)
scripts/run-ferrite-matrix.sh \
    --tier tier1 --scenario ferrite-7b --ns my-fer-ns \
    --image-repo <ferrite-registry> --image-tag <ferrite-tag> \
    --workspace ~/data/ferrite-matrix-7b-fer

# 4. Diff
python3 -m llmdbenchmark.analysis.ferrite_diff \
    --python-runs  ~/data/ferrite-matrix-7b-py \
    --ferrite-runs ~/data/ferrite-matrix-7b-fer
```

---

## What's measured

The wrapping harness invokes `vllm bench serve` for every cell. vLLM's serve
benchmark drives a controlled request stream against the deployed endpoint
and writes a JSON file per run via `--save-result`. Fields captured per cell:

| JSON field | Meaning | Lower-better? |
|---|---|---|
| `mean_ttft_ms`, `median_ttft_ms`, `p99_ttft_ms` | Time-to-first-token (ms) | yes |
| `mean_tpot_ms`, `median_tpot_ms`, `p99_tpot_ms` | Time-per-output-token (ms) | yes |
| `mean_itl_ms`, `p99_itl_ms` | Inter-token-latency (ms) | yes |
| `mean_e2el_ms`, `median_e2el_ms`, `p99_e2el_ms` | End-to-end request latency (ms) | yes |
| `request_throughput` | Requests/second | no |
| `output_throughput` | Generated tokens/second | no |
| `total_token_throughput` | Input + output tokens/second | no |
| `completed` / `duration` | Request count and wall-clock | informational |

The diff analyzer surfaces five of these by default (`e2el`, `tpot`, `ttft`,
`tput`, `reqs`); the rest sit in the JSON for ad-hoc analysis.

### Why no `vllm bench latency` / `vllm bench throughput`

Those modes are **offline** — they load the model in the benchmark process
and don't accept `--base-url`. They can't target the deployed vLLM server
that llm-d-benchmark provisions. The matrix expresses the project lead's
latency/throughput regimes via `serve` shapes:

| Reference regime | serve config |
|---|---|
| `bench latency BS=1 in=N out=M` | `max-concurrency=1, num-prompts=4, in=N, out=M` (sequential, e2el) |
| `bench latency BS=8 in=N out=M` | `max-concurrency=8, num-prompts=16, request-rate=inf` |
| `bench throughput in=N out=M` | `max-concurrency=64, num-prompts=200, request-rate=inf` (saturated tput) |
| `bench serve sustained` | `max-concurrency=64, num-prompts=200, request-rate=16` |

Numbers are directly comparable to the project lead's tables modulo negligible HTTP
overhead inside the same pod network.

---

## The matrix (92 cells)

### Tier 1 — sanity / replicate the project lead's reference tables (20 cells)

Run against **Qwen2.5-7B-Instruct** (fp16) only. Goal: confirm
llm-d-benchmark numbers match the project lead's hand-run `vllm bench serve` numbers
within ~5%. If they don't, fix the harness wrapping before proceeding.

| Profile | regime | input | output | max-conc | num-prompts | request-rate |
|---|---|---:|---:|---:|---:|---:|
| `ferrite_t1_lat_bs1_in1024_out128` | latency BS=1 | 1024 | 128 | 1 | 4 | (sequential) |
| `ferrite_t1_lat_bs1_in2048_out128` | latency BS=1 | 2048 | 128 | 1 | 4 | (sequential) |
| `ferrite_t1_lat_bs1_in2048_out512` | latency BS=1 | 2048 | 512 | 1 | 4 | (sequential) |
| `ferrite_t1_lat_bs1_in4096_out128` | latency BS=1 | 4096 | 128 | 1 | 4 | (sequential) |
| `ferrite_t1_lat_bs1_in4096_out512` | latency BS=1 | 4096 | 512 | 1 | 4 | (sequential) |
| `ferrite_t1_lat_bs1_in8192_out128` | latency BS=1 | 8192 | 128 | 1 | 4 | (sequential) |
| `ferrite_t1_lat_bs8_in512_out128`  | latency BS=8 |  512 | 128 | 8 | 16 | inf |
| `ferrite_t1_lat_bs32_in128_out128` | latency BS=32 | 128 | 128 | 32 | 64 | inf |
| `ferrite_t1_tput_in{1024,2048,4096}_out{128,512}` | throughput | 1024/2048/4096 | 128/512 | 64 | 200 | inf |
| `ferrite_t1_srv16_in{1024,2048,4096}_out{128,512}` | serve sustained | 1024/2048/4096 | 128/512 | 64 | 200 | 16 r/s |

### Tier 2 — full perf-region sweep (42 cells per model)

Run against each of **Qwen2.5-3B-Instruct**, **Qwen2.5-7B-Instruct**, and
**Qwen2.5-14B-Instruct** (fp16). 42 cells × 3 models × 2 stacks (python +
ferrite) = 252 runs total.

Cartesian product of:

| Sweep dimension | Values |
|---|---|
| Latency: BS | 1, 8, 32 |
| Latency: input length | 1024, 2048, 4096, 8192 |
| Latency: output length | 128, 512 |
| Throughput: input length | 1024, 2048, 4096 |
| Throughput: output length | 128, 512 |
| Throughput: max-concurrency | 64 (saturated) |
| Throughput: num-prompts | 200 |
| Serve saturated: same shapes as throughput, rate=inf |
| Serve sustained: same shapes as throughput, rate=16 r/s |

Per model: 24 latency + 6 throughput + 6 serve-sat + 6 serve-sustained = 42 cells.

### Tier 3 — FP8 + MoE characterization (30 cells)

| Scenario | Model | Cells |
|---|---|---|
| `ferrite-7b-fp8` | RedHatAI/Qwen2.5-7B-Instruct-FP8-dynamic | 18 (latency BS={1,8} × in={1024,2048,4096} × out={128,512}) + 6 (throughput) = 24 |
| `ferrite-mixtral` | mistralai/Mixtral-8x7B-Instruct-v0.1 (MoE, TP=2) | 8 (latency BS={1,8} × in={1024,4096} × out={128,512}) + 4 (serve sat) = 12 |

Tier 3 is tighter than Tier 2 because MoE runs are slower (expert routing +
larger active params) and FP8 is mostly to confirm the dense-FP8 vs dense-fp16
delta.

### Coverage map

|  | 3B fp16 | 7B fp16 | 14B fp16 | 7B FP8 | Mixtral MoE |
|---|---:|---:|---:|---:|---:|
| Tier 1 | — | 20 | — | — | — |
| Tier 2 | 42 | 42 | 42 | — | — |
| Tier 3 | — | — | — | 24 | 12 (effective set after dedup) |

Total cells × 2 stacks (py + fer) ≈ **360 runs** across all tiers and models.
Single-H100, ~2 min/cell median, ~12 hours wall-clock to run everything once.

---

## How to run

### Prereqs

- OpenShift access (e.g. pokprod) with a namespace you can deploy into
- HuggingFace token in a secret named `huggingface-secret` (key `HF_TOKEN`) in
  that namespace, for gated models
- `llmdbenchmark` CLI installed (`pip install -e .` from repo root)
- `yq`, `python3` ≥ 3.10, `bash` ≥ 4 on PATH

### Per-scenario invocations

The matrix is driven by **scenario × tier**. Each scenario is one model; each
tier is a subset of profiles. Usage:

```bash
scripts/run-ferrite-matrix.sh \
    --tier {tier1|tier2|tier3} \
    [--scenario ferrite-{3b,7b,14b,7b-fp8,mixtral} | --all-fp16-scenarios] \
    --ns <namespace> \
    [--workspace <dir>]                    # default: ~/data/ferrite-matrix-<scenario>
    [--image-repo <repo> --image-tag <tag>] # ferrite swap
    [--skip-standup]                       # reuse a deployed pod
    [--dry-run]
```

### Recommended order of operations (one stack at a time)

```bash
# Stage 1 - sanity. Confirm our numbers match the reference. ~30 min on H100.
scripts/run-ferrite-matrix.sh \
    --tier tier1 --scenario ferrite-7b --ns ferrite-py-sanity \
    --workspace ~/data/ferrite-7b-py-tier1

# Run the diff against the project lead's reference numbers (manual eyeball).
# If anything is >5% off, debug the harness wrapping, not ferrite.

# Stage 2 - full sweep across 3B/7B/14B fp16. ~6h on H100.
for model in ferrite-3b ferrite-7b ferrite-14b; do
    scripts/run-ferrite-matrix.sh \
        --tier tier2 --scenario "$model" --ns "ferrite-py-${model}" \
        --workspace "~/data/${model}-py-tier2"
done

# Stage 3 - FP8 + MoE. ~3h on H100.
scripts/run-ferrite-matrix.sh \
    --tier tier3 --scenario ferrite-7b-fp8 --ns ferrite-py-fp8 \
    --workspace ~/data/ferrite-7b-fp8-py
scripts/run-ferrite-matrix.sh \
    --tier tier3 --scenario ferrite-mixtral --ns ferrite-py-moe \
    --workspace ~/data/ferrite-mixtral-py

# Repeat all three stages with --image-repo/--image-tag pinned to ferrite.
# Use --skip-standup if you reuse the same namespaces (saves ~5 min per scenario).
```

### Pairwise diff

After running both stacks for a scenario:

```bash
python3 -m llmdbenchmark.analysis.ferrite_diff \
    --python-runs  ~/data/ferrite-7b-py-tier2 \
    --ferrite-runs ~/data/ferrite-7b-fer-tier2
```

CLI flags:

```
--python-runs PATH     Workspace root of the python vLLM run (required)
--ferrite-runs PATH    Workspace root of the ferrite run (required)
--metric KEY           Metrics column(s) to include. Repeatable. Default:
                       e2el,tpot,ttft,tput,reqs.
                       Choices: e2el, tpot, ttft, tput, reqs.
--regime KEY           Filter rows by regime tag from the profile name.
                       Choices: latency, tput, throughput, srvsat, srv16, all.
--unmatched            Also list profiles present in only one tree.
```

---

## Expected output

### From `run-ferrite-matrix.sh`

```
============================================================
Ferrite matrix run
  Tier:        tier1 (20 profiles)
  Scenarios:   ferrite-7b
  Namespace:   ferrite-py-sanity
============================================================

----- Scenario: ferrite-7b (workspace: ~/data/ferrite-7b-py-tier1) -----
==> standup ferrite-7b
[llmdbenchmark output ...]
==> run ferrite-7b / ferrite_t1_lat_bs1_in1024_out128.yaml
[harness pod runs vllm bench serve, JSON saved]
==> run ferrite-7b / ferrite_t1_lat_bs1_in2048_out128.yaml
...
============================================================
Matrix complete: 20 cells, 0 failed
============================================================
```

### From `ferrite_diff.py`

```
# Ferrite vs python vLLM diff
# Python runs:  /Users/.../ferrite-7b-py-tier1  (20 profiles)
# Ferrite runs: /Users/.../ferrite-7b-fer-tier1 (20 profiles)
# Paired:       20; py-only: 0; ferrite-only: 0

profile                                py e2el ms  fer e2el ms   Δ        py TPOT ms  fer TPOT ms   Δ        py TTFT ms  fer TTFT ms   Δ        py tok/s     fer tok/s    Δ        py req/s     fer req/s    Δ
-------------------------------------  ----------  -----------  -------  ----------  -----------  -------  ----------  -----------  -------  ----------  ----------  -------  ----------  ----------  -------
ferrite_t1_lat_bs1_in1024_out128         1284.30     1414.20    +10.1%       9.85      10.83      +9.9%      147.20     169.50      +15.2%       100.20     91.05       −9.1%       0.78       0.71      −9.0%
ferrite_t1_lat_bs1_in2048_out128         1374.10     1599.30    +16.4%      10.10      11.65      +15.3%     220.40     258.80      +17.4%        93.10     80.05       −14.0%      0.73       0.63      −14.1%
ferrite_t1_tput_in1024_out128           21458.00    20100.00     −6.3%      28.45      26.10      −8.3%      152.30     149.70      −1.7%      26100.00    27500.00     +5.4%      45.20      48.10     +6.4%
...
```

`Δ` columns: negative for **lower-better** metrics (e2el, ttft, tpot) means
**ferrite is faster**. Positive for **higher-better** metrics (req/s, tok/s)
means ferrite is faster. Sign convention matches the project lead's reference tables.

---

## Editing the matrix

Single source of truth is `scripts/generate-ferrite-matrix.py`. To add or
remove cells, edit `TIER_1`, `_tier2_cells()`, or `_tier3_cells()` and re-run:

```bash
python3 scripts/generate-ferrite-matrix.py
```

The script is idempotent and wipes stale `ferrite_*.yaml.in` files in the
profiles directory, so renamed cells don't leave orphans behind.

To add a new model, copy one of the existing scenario files
(`config/scenarios/guides/ferrite-7b.yaml`) plus its spec j2, edit the model
block, and reference it via `--scenario ferrite-<your-name>`.

---

## What this does NOT cover

- **`vllm bench latency` / `vllm bench throughput`** — offline modes,
  unsupported by the deployed-server pattern. The harness has the dispatch
  hooks (`executable: benchmark_latency.py` → `vllm bench latency`) but no
  scenario currently uses them.
- **Multi-GPU non-MoE** — TP=1 in all scenarios except Mixtral (TP=2).
- **Startup time** — benchmarked separately by the project lead; not exercised here.
- **L40s / sm89-specific shapes** — the matrix is hardware-agnostic but the
  shape choices target H100 perf gaps. Add an L40s scenario if needed.
- **Nightly CI** — explicitly out of scope per project lead.
