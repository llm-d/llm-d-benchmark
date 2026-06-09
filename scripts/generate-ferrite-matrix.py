#!/usr/bin/env python3
"""Generate vllm-benchmark profile YAMLs for the ferrite-vs-python comparison
matrix.

Single source of truth for what shapes we benchmark across the three tiers
the project lead laid out:

    Tier 1 -- sanity / replicate the project lead's reference tables (~20 cells)
    Tier 2 -- full perf-region sweep across 3B/7B/14B (per-model: ~42 cells)
    Tier 3 -- FP8 + MoE characterization (~32 + ~10 cells)

All cells run as ``vllm bench serve`` because the offline ``latency`` and
``throughput`` modes don't take ``--base-url`` and so can't target the vLLM
server llm-d-benchmark provisions. ``serve`` reports TTFT, TPOT, e2el, and
total tokens/sec, which covers everything the project lead's latency + throughput tables
expose, modulo negligible HTTP overhead inside the same pod network.

Run::

    python3 scripts/generate-ferrite-matrix.py

Files land at::

    workload/profiles/vllm-benchmark/ferrite-matrix/<tier>/<name>.yaml.in

Each profile YAML is a minimal vllm-benchmark profile (executable +
benchmark_serving.py args). The wrapping harness substitutes
``REPLACE_ENV_*`` placeholders at run time.

To add a cell, edit the relevant tier list below and re-run. The script is
deterministic and idempotent -- re-running overwrites the same files.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Profiles must live as direct children of the harness directory: step_05 of
# the runner only iterates one level deep when copying profiles into a stack's
# render dir. We flatten and use a `ferrite_<tier>_...` prefix instead of
# nesting under sub-directories.
PROFILES_DIR = REPO_ROOT / "workload" / "profiles" / "vllm-benchmark"
MATRIX_PREFIX = "ferrite_"
MANIFEST_PATH = PROFILES_DIR / "ferrite-matrix-manifest.yaml"


# ---------------------------------------------------------------------------
# Matrix definition
# ---------------------------------------------------------------------------
#
# A "cell" is one profile YAML = one harness invocation. Fields:
#
#   name              file stem -- becomes the profile filename
#   regime            sanity|latency|throughput|serve_sat|serve_sustained
#                     (informational; the harness always runs `vllm bench serve`)
#   input_len         random-input-len
#   output_len        random-output-len
#   max_concurrency   max-concurrency (also serves as "BS" knob)
#   num_prompts       num-prompts (warmup uses 3; main run uses this)
#   request_rate      request-rate (None => omitted; saturate-as-fast-as-possible)
#
# Standardized non-shape args are appended in `_render` so we don't repeat them.


def _make_cell(
    name: str,
    regime: str,
    input_len: int,
    output_len: int,
    max_concurrency: int,
    num_prompts: int,
    request_rate: int | float | None = None,
) -> dict:
    return {
        "name": name,
        "regime": regime,
        "input_len": input_len,
        "output_len": output_len,
        "max_concurrency": max_concurrency,
        "num_prompts": num_prompts,
        "request_rate": request_rate,
    }


# Tier 1 -- replicate the project lead's reference tables for Qwen2.5-7B on H100.
# Goal: confirm llm-d-benchmark numbers match the project lead's hand-run vllm bench numbers
# within ~5%. If they don't, fix the harness wrapping before running anything else.
#
# Mix of latency-style (BS=1, sequential) and throughput/serve-style cells from
# the project lead's reference tables. Output length pinned to 128 or 512 (matches his tables).
TIER_1: list[dict] = [
    # BS=1 latency probes -- map to max_concurrency=1, num_prompts=4 so we get
    # 4 sequential single-request samples (warmup is the 1st; we average the
    # rest). the project lead's tables typically run 3-4 iterations per shape.
    _make_cell("t1_lat_bs1_in1024_out128", "latency", 1024, 128, 1, 4),
    _make_cell("t1_lat_bs1_in2048_out128", "latency", 2048, 128, 1, 4),
    _make_cell("t1_lat_bs1_in2048_out512", "latency", 2048, 512, 1, 4),
    _make_cell("t1_lat_bs1_in4096_out128", "latency", 4096, 128, 1, 4),
    _make_cell("t1_lat_bs1_in4096_out512", "latency", 4096, 512, 1, 4),
    _make_cell("t1_lat_bs1_in8192_out128", "latency", 8192, 128, 1, 4),
    # Batched latency (BS=8 / BS=32 shapes)
    _make_cell(
        "t1_lat_bs8_in512_out128", "latency", 512, 128, 8, 16, request_rate="inf"
    ),
    _make_cell(
        "t1_lat_bs32_in128_out128", "latency", 128, 128, 32, 64, request_rate="inf"
    ),
    # Throughput-saturated (throughput-target shapes)
    _make_cell(
        "t1_tput_in1024_out128", "throughput", 1024, 128, 64, 200, request_rate="inf"
    ),
    _make_cell(
        "t1_tput_in1024_out512", "throughput", 1024, 512, 64, 200, request_rate="inf"
    ),
    _make_cell(
        "t1_tput_in2048_out128", "throughput", 2048, 128, 64, 200, request_rate="inf"
    ),
    _make_cell(
        "t1_tput_in2048_out512", "throughput", 2048, 512, 64, 200, request_rate="inf"
    ),
    _make_cell(
        "t1_tput_in4096_out128", "throughput", 4096, 128, 64, 200, request_rate="inf"
    ),
    _make_cell(
        "t1_tput_in4096_out512", "throughput", 4096, 512, 64, 200, request_rate="inf"
    ),
    # Serve sustained (the rate=16 r/s reference table)
    _make_cell(
        "t1_srv16_in1024_out128", "serve_sustained", 1024, 128, 64, 200, request_rate=16
    ),
    _make_cell(
        "t1_srv16_in1024_out512", "serve_sustained", 1024, 512, 64, 200, request_rate=16
    ),
    _make_cell(
        "t1_srv16_in2048_out128", "serve_sustained", 2048, 128, 64, 200, request_rate=16
    ),
    _make_cell(
        "t1_srv16_in2048_out512", "serve_sustained", 2048, 512, 64, 200, request_rate=16
    ),
    _make_cell(
        "t1_srv16_in4096_out128", "serve_sustained", 4096, 128, 64, 200, request_rate=16
    ),
    _make_cell(
        "t1_srv16_in4096_out512", "serve_sustained", 4096, 512, 64, 200, request_rate=16
    ),
]


# Tier 2 -- full perf-region sweep. Shape-only (model is varied via scenario YAML).
# Generated programmatically so changing the matrix is one place.
def _tier2_cells() -> list[dict]:
    cells: list[dict] = []

    # Latency sweep: BS={1,8,32} x in={1024,2048,4096,8192} x out={128,512}
    for bs in (1, 8, 32):
        for in_len in (1024, 2048, 4096, 8192):
            for out_len in (128, 512):
                # BS=1 sequential -> rate omitted; BS>1 saturate.
                rate = None if bs == 1 else "inf"
                num_prompts = max(bs * 2, 8)
                cells.append(
                    _make_cell(
                        f"t2_lat_bs{bs}_in{in_len}_out{out_len}",
                        "latency",
                        in_len,
                        out_len,
                        bs,
                        num_prompts,
                        rate,
                    )
                )

    # Throughput sweep: in={1024,2048,4096} x out={128,512}, max-concurrency=64.
    for in_len in (1024, 2048, 4096):
        for out_len in (128, 512):
            cells.append(
                _make_cell(
                    f"t2_tput_in{in_len}_out{out_len}",
                    "throughput",
                    in_len,
                    out_len,
                    64,
                    200,
                    request_rate="inf",
                )
            )

    # Serve saturated: same shapes, rate=inf, narrower BS to surface tail behavior.
    for in_len in (1024, 2048, 4096):
        for out_len in (128, 512):
            cells.append(
                _make_cell(
                    f"t2_srvsat_in{in_len}_out{out_len}",
                    "serve_sat",
                    in_len,
                    out_len,
                    64,
                    200,
                    request_rate="inf",
                )
            )

    # Serve sustained at 16 r/s, same shapes.
    for in_len in (1024, 2048, 4096):
        for out_len in (128, 512):
            cells.append(
                _make_cell(
                    f"t2_srv16_in{in_len}_out{out_len}",
                    "serve_sustained",
                    in_len,
                    out_len,
                    64,
                    200,
                    request_rate=16,
                )
            )

    return cells


TIER_2: list[dict] = _tier2_cells()


# Tier 3 -- FP8 + MoE characterization. Smaller matrix because runs are slower
# (MoE in particular). FP8 mirrors the 7B fp16 sweep so the FP8-vs-fp16 delta is
# directly readable; MoE is a tighter sweep focused on representative shapes.
def _tier3_cells() -> list[dict]:
    cells: list[dict] = []

    # FP8 -- mirrors 7B fp16 sweep at a representative subset.
    for bs in (1, 8):
        for in_len in (1024, 2048, 4096):
            for out_len in (128, 512):
                rate = None if bs == 1 else "inf"
                num_prompts = max(bs * 2, 8)
                cells.append(
                    _make_cell(
                        f"t3_fp8_lat_bs{bs}_in{in_len}_out{out_len}",
                        "latency",
                        in_len,
                        out_len,
                        bs,
                        num_prompts,
                        rate,
                    )
                )
    for in_len in (1024, 2048, 4096):
        for out_len in (128, 512):
            cells.append(
                _make_cell(
                    f"t3_fp8_tput_in{in_len}_out{out_len}",
                    "throughput",
                    in_len,
                    out_len,
                    64,
                    200,
                    request_rate="inf",
                )
            )

    # MoE -- tighter; runs are slow because of expert routing + larger params.
    for bs in (1, 8):
        for in_len in (1024, 4096):
            for out_len in (128, 512):
                rate = None if bs == 1 else "inf"
                num_prompts = max(bs * 2, 8)
                cells.append(
                    _make_cell(
                        f"t3_moe_lat_bs{bs}_in{in_len}_out{out_len}",
                        "latency",
                        in_len,
                        out_len,
                        bs,
                        num_prompts,
                        rate,
                    )
                )
    for in_len in (1024, 4096):
        for out_len in (128, 512):
            cells.append(
                _make_cell(
                    f"t3_moe_srvsat_in{in_len}_out{out_len}",
                    "serve_sat",
                    in_len,
                    out_len,
                    64,
                    100,
                    request_rate="inf",
                )
            )

    return cells


TIER_3: list[dict] = _tier3_cells()


TIERS: dict[str, list[dict]] = {
    "tier1": TIER_1,
    "tier2": TIER_2,
    "tier3": TIER_3,
}


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
#
# Each profile is a vllm-benchmark .yaml.in file. The fields land verbatim as
# CLI args to `vllm bench serve` after the harness substitutes the
# REPLACE_ENV_* placeholders.


def _render(cell: dict) -> str:
    lines = [
        "# Auto-generated by scripts/generate-ferrite-matrix.py -- do not edit by hand.",
        "# Edit the matrix in the generator and re-run.",
        f"# Regime: {cell['regime']}",
        "executable: benchmark_serving.py",
        "model: REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL",
        "base-url: REPLACE_ENV_LLMDBENCH_HARNESS_STACK_ENDPOINT_URL",
        "dataset-name: random",
        f"random-input-len: {cell['input_len']}",
        f"random-output-len: {cell['output_len']}",
        f"max-concurrency: {cell['max_concurrency']}",
        f"num-prompts: {cell['num_prompts']}",
    ]
    if cell["request_rate"] is not None:
        lines.append(f"request-rate: {cell['request_rate']}")
    lines.extend(
        [
            # Percentile menu matching what the project lead's tables surface.
            'percentile-metrics: "ttft,tpot,itl,e2el"',
            'metric-percentiles: "0.1,1,5,10,25,75,90,95,99,99.9"',
            "ignore-eos: none",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    # Wipe stale auto-generated profiles so renames in the matrix don't leave
    # orphans behind. We're the sole writer of files matching MATRIX_PREFIX.
    for stale in PROFILES_DIR.glob(f"{MATRIX_PREFIX}*.yaml.in"):
        stale.unlink()

    summary: list[tuple[str, int]] = []
    for tier_name, cells in TIERS.items():
        for cell in cells:
            # Filename: ferrite_<cell.name>.yaml.in (cell.name already begins
            # with t1_/t2_/t3_ so the tier is encoded there).
            out = PROFILES_DIR / f"{MATRIX_PREFIX}{cell['name']}.yaml.in"
            out.write_text(_render(cell))
        summary.append((tier_name, len(cells)))

    # Emit a manifest for the wrapper script to consume.
    manifest_lines = [
        "# Auto-generated by scripts/generate-ferrite-matrix.py.",
        "# Tier -> list of harness.experimentProfile values (path under",
        "# workload/profiles/vllm-benchmark/ - just the basename here since",
        "# step_05 of the runner only iterates one level deep).",
    ]
    for tier_name, cells in TIERS.items():
        manifest_lines.append(f"{tier_name}:")
        for cell in cells:
            manifest_lines.append(f"  - {MATRIX_PREFIX}{cell['name']}.yaml")
    MANIFEST_PATH.write_text("\n".join(manifest_lines) + "\n")

    print("Generated profiles:")
    for tier_name, count in summary:
        print(f"  {tier_name}: {count} cells")
    print(f"Output dir: {PROFILES_DIR.relative_to(REPO_ROOT)}")
    print(f"Manifest:   {MANIFEST_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
