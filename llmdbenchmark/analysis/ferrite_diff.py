"""Pairwise diff of two ferrite-matrix runs (python vs ferrite, or any two
otherwise-identical workspace dirs).

Walks two run-workspace directory trees, locates each run's vllm-benchmark
JSON output + its matching ``run_metadata.yaml``, joins on the profile name
(``harness_workload``), and emits a per-shape delta table similar in shape to
the tables the project lead has been pasting in Slack.

Usage::

    python -m llmdbenchmark.analysis.ferrite_diff \\
        --python-runs /tmp/ferrite-matrix-7b-python \\
        --ferrite-runs /tmp/ferrite-matrix-7b-ferrite \\
        [--metric e2el|tpot|ttft|tput|reqs] \\
        [--regime latency|throughput|serve_sat|serve_sustained]

Output is a Markdown-style table on stdout. Pipe through ``column -t`` for a
plain-text grid, or capture with ``> diff.md`` for direct paste into Slack /
GitHub.

Design notes:
  - vLLM's ``benchmark_serving.py`` writes one JSON per run via ``--save-result``;
    the harness moves these into the per-run results dir. We discover them by
    glob, then pair via ``run_metadata.yaml``'s ``harness_workload`` field.
  - When a profile is present in only one of the two trees, it's reported as
    "unmatched" and skipped from the delta table -- the delta is meaningless
    without both endpoints.
  - When a profile has multiple runs in one tree (re-runs, different timestamps),
    the most recent JSON (by ``date`` field, falling back to file mtime) wins.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover -- pyyaml is a benchmark-repo dep
    print("ERROR: PyYAML not installed; pip install pyyaml", file=sys.stderr)
    sys.exit(1)


# Metrics we surface, mapped to the vllm-benchmark JSON field name and a
# direction indicator (lower=better vs higher=better) used for the % delta sign.
METRICS = {
    "e2el": {"field": "mean_e2el_ms", "label": "e2el ms", "lower_better": True},
    "tpot": {"field": "mean_tpot_ms", "label": "TPOT ms", "lower_better": True},
    "ttft": {"field": "mean_ttft_ms", "label": "TTFT ms", "lower_better": True},
    "tput": {
        "field": "total_token_throughput",
        "label": "tok/s",
        "lower_better": False,
    },
    "reqs": {"field": "request_throughput", "label": "req/s", "lower_better": False},
}


@dataclass
class RunResult:
    profile: str  # harness_workload, e.g. "ferrite_t1_lat_bs1_in2048_out128.yaml"
    metrics: dict  # parsed JSON contents
    json_path: Path
    metadata_path: Path

    @property
    def regime(self) -> str:
        # Filename-derived regime tag from the matrix generator. Examples:
        #   ferrite_t1_lat_..., ferrite_t2_tput_..., ferrite_t3_srv16_...
        # Strip prefix + tier, take next token.
        stem = self.profile.removesuffix(".yaml").removesuffix(".yaml.in")
        parts = stem.split("_")
        # ferrite, t1|t2|t3, regime, ...
        if len(parts) >= 3 and parts[0] == "ferrite":
            return parts[2]
        return "unknown"


def _load_run_results(root: Path) -> dict[str, RunResult]:
    """Return profile-name -> RunResult, picking the most recent run when
    multiple match (re-runs are not pairwise-deterministic — we collapse
    on the latest)."""
    out: dict[str, RunResult] = {}
    candidates: dict[str, list[RunResult]] = defaultdict(list)

    # Each per-pod results dir contains run_metadata.yaml + a vllm-bench *.json.
    for meta_path in root.rglob("run_metadata.yaml"):
        try:
            meta = yaml.safe_load(meta_path.read_text()) or {}
        except yaml.YAMLError:
            continue
        profile = (meta.get("harness_workload") or "").strip()
        if not profile or not profile.startswith("ferrite_"):
            continue
        # Pair the metadata to the vllm JSON in the same dir.
        json_files = [
            p
            for p in meta_path.parent.glob("*.json")
            if p.name
            != "run_metadata.json"  # belt-and-braces -- if anyone ever writes a meta json
        ]
        if not json_files:
            continue
        # vllm dumps a single JSON per run; if multiple, take newest by mtime.
        json_path = max(json_files, key=lambda p: p.stat().st_mtime)
        try:
            metrics = json.loads(json_path.read_text())
        except (json.JSONDecodeError, ValueError):
            continue
        candidates[profile].append(
            RunResult(
                profile=profile,
                metrics=metrics,
                json_path=json_path,
                metadata_path=meta_path,
            )
        )

    for profile, runs in candidates.items():
        # Pick the latest by metrics["date"] string if available, else mtime.
        runs.sort(
            key=lambda r: (r.metrics.get("date") or "", r.json_path.stat().st_mtime)
        )
        out[profile] = runs[-1]
    return out


def _format_pct(py_val: float, fer_val: float, lower_better: bool) -> str:
    if py_val == 0:
        return "  n/a "
    delta = (fer_val - py_val) / py_val * 100
    # Sign-flip the displayed delta when "lower is better" so a negative
    # number always means "ferrite improved." This matches the project lead's table
    # convention.
    if lower_better:
        sign = "−" if delta < 0 else "+"
        return f"{sign}{abs(delta):5.1f}%"
    sign = "+" if delta >= 0 else "−"
    return f"{sign}{abs(delta):5.1f}%"


def _emit_table(
    pairs: list[tuple[str, RunResult, RunResult]],
    metric_keys: list[str],
    out=sys.stdout,
) -> None:
    if not pairs:
        print("(no paired runs to compare)", file=out)
        return

    # Column layout: profile + (py, fer, delta) per metric.
    headers = ["profile"]
    for k in metric_keys:
        m = METRICS[k]
        headers.extend([f"py {m['label']}", f"fer {m['label']}", "Δ"])

    rows: list[list[str]] = [headers]
    for profile, py_run, fer_run in pairs:
        row = [profile.removesuffix(".yaml")]
        for k in metric_keys:
            field = METRICS[k]["field"]
            py_val = py_run.metrics.get(field)
            fer_val = fer_run.metrics.get(field)
            if py_val is None or fer_val is None:
                row.extend(["—", "—", "—"])
                continue
            row.append(f"{py_val:>9.2f}")
            row.append(f"{fer_val:>9.2f}")
            row.append(_format_pct(py_val, fer_val, METRICS[k]["lower_better"]))
        rows.append(row)

    # Compute column widths.
    widths = [max(len(r[i]) for r in rows) for i in range(len(headers))]

    def _line(row: list[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))

    print(_line(rows[0]), file=out)
    print("  ".join("-" * w for w in widths), file=out)
    for r in rows[1:]:
        print(_line(r), file=out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--python-runs",
        required=True,
        type=Path,
        help="Workspace root for the python-vLLM run.",
    )
    p.add_argument(
        "--ferrite-runs",
        required=True,
        type=Path,
        help="Workspace root for the ferrite run.",
    )
    p.add_argument(
        "--metric",
        action="append",
        choices=list(METRICS.keys()),
        help="Metric to include (repeatable). Default: e2el,tpot,ttft,tput,reqs.",
    )
    p.add_argument(
        "--regime",
        choices=["latency", "tput", "throughput", "srvsat", "srv16", "all"],
        default="all",
        help="Filter rows by regime tag in the profile name.",
    )
    p.add_argument(
        "--unmatched",
        action="store_true",
        help="Also list profiles present in only one tree.",
    )
    args = p.parse_args(argv)

    metric_keys = args.metric or ["e2el", "tpot", "ttft", "tput", "reqs"]

    py_runs = _load_run_results(args.python_runs)
    fer_runs = _load_run_results(args.ferrite_runs)

    if not py_runs and not fer_runs:
        print(
            f"No ferrite_* runs found under either {args.python_runs} or "
            f"{args.ferrite_runs}",
            file=sys.stderr,
        )
        return 1

    common = sorted(set(py_runs) & set(fer_runs))
    py_only = sorted(set(py_runs) - set(fer_runs))
    fer_only = sorted(set(fer_runs) - set(py_runs))

    pairs: list[tuple[str, RunResult, RunResult]] = []
    for profile in common:
        py_r, fer_r = py_runs[profile], fer_runs[profile]
        if args.regime != "all":
            if py_r.regime != args.regime:
                continue
        pairs.append((profile, py_r, fer_r))

    print("# Ferrite vs python vLLM diff")
    print(f"# Python runs:  {args.python_runs}  ({len(py_runs)} profiles)")
    print(f"# Ferrite runs: {args.ferrite_runs}  ({len(fer_runs)} profiles)")
    print(
        f"# Paired:       {len(pairs)}; py-only: {len(py_only)}; "
        f"ferrite-only: {len(fer_only)}"
    )
    print()
    _emit_table(pairs, metric_keys)

    if args.unmatched and (py_only or fer_only):
        print()
        print("# Unmatched profiles")
        for pr in py_only:
            print(f"  python-only:  {pr}")
        for pr in fer_only:
            print(f"  ferrite-only: {pr}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
