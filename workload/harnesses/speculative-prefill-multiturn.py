#!/usr/bin/env python3
"""Multi-turn TTFT A/B runner for speculative prefill validation.

The runner simulates concurrent users, each running a multi-turn chat. It runs
the same workload twice: first without the speculative-prefill header, then with
``x-speculative-prefill: true``. The per-turn time to first streamed token
(TTFT) is summarized for both arms.

Turns are zero-indexed. Turn1 always asks for a long final answer so assistant1
is long enough to make turn2's speculative-prefill benefit visible.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics as stats
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import yaml


BASE_DEFAULT = "http://localhost:18080/v1/chat/completions"
SPECULATIVE_PREFILL_HEADER = "x-speculative-prefill"
LONG_ANSWER_TURN = 1

LOREM = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua ut enim ad minim "
    "veniam quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea "
    "commodo consequat duis aute irure dolor in reprehenderit voluptate"
).split()


def lorem(rng: random.Random, approx_tokens: int) -> str:
    word_count = max(1, int(approx_tokens * 0.75))
    return " ".join(rng.choice(LOREM) for _ in range(word_count))


def post_stream(
    url: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    use_speculative_prefill: bool,
    ignore_eos: bool,
    timeout: int,
) -> tuple[float | None, str]:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if ignore_eos:
        body["ignore_eos"] = True

    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    if use_speculative_prefill:
        request.add_header(SPECULATIVE_PREFILL_HEADER, "true")

    start = time.time()
    ttft_ms = None
    answer: list[str] = []
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw_line in response:
            line = raw_line.decode(errors="ignore").strip()
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            delta = event.get("choices", [{}])[0].get("delta", {})
            piece = delta.get("content") or delta.get("reasoning_content") or ""
            if piece and ttft_ms is None:
                ttft_ms = (time.time() - start) * 1000
            if delta.get("content"):
                answer.append(delta["content"])

    return ttft_ms, "".join(answer)


def run_user(args: argparse.Namespace, user_id: int, use_speculative_prefill: bool) -> list[dict[str, Any]]:
    rng = random.Random(args.seed + user_id)
    results = []
    messages = [{"role": "system", "content": f"You are assistant #{user_id}."}]

    for turn in range(args.num_turns):
        prompt = f"[u{user_id}t{turn}-{rng.randint(1000, 9999)}] " + lorem(rng, args.num_user_tokens)
        if turn == LONG_ANSWER_TURN:
            prompt += (
                f"\n\nFor this turn, write a detailed final answer of at least "
                f"{args.long_answer_words} words. Keep the final answer substantive, "
                "with numbered points and concrete details."
            )

        messages.append({"role": "user", "content": prompt})
        ttft_ms, answer = post_stream(
            args.url,
            args.model,
            messages,
            args.max_completion_tokens,
            use_speculative_prefill,
            args.ignore_eos,
            args.timeout,
        )
        messages.append({"role": "assistant", "content": answer})

        if ttft_ms is not None:
            results.append(
                {
                    "user_id": user_id,
                    "turn": turn,
                    "ttft_ms": ttft_ms,
                    "assistant_chars": len(answer),
                }
            )

        if turn < args.num_turns - 1 and args.mean_delay_ms > 0:
            delay_seconds = rng.expovariate(1.0 / (args.mean_delay_ms / 1000.0))
            time.sleep(delay_seconds)

    return results


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return float("nan")
    sorted_values = sorted(values)
    index = (len(sorted_values) - 1) * percent / 100
    lo = int(index)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (index - lo)


def summarize_turns(rows: list[dict[str, Any]]) -> dict[int, dict[str, float]]:
    by_turn: dict[int, list[float]] = {}
    for row in rows:
        by_turn.setdefault(row["turn"], []).append(row["ttft_ms"])

    return {
        turn: {
            "n": len(values),
            "mean": stats.mean(values),
            "p50": percentile(values, 50),
            "p95": percentile(values, 95),
            "p99": percentile(values, 99),
        }
        for turn, values in sorted(by_turn.items())
    }


def run_case(args: argparse.Namespace, use_speculative_prefill: bool, label: str) -> dict[str, Any]:
    print(
        f"\n===== {label} (users={args.num_users} turns={args.num_turns} "
        f"delay={args.mean_delay_ms}ms header={use_speculative_prefill}) =====",
        flush=True,
    )

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.num_users) as executor:
        futures = [
            executor.submit(run_user, args, user_id, use_speculative_prefill)
            for user_id in range(args.num_users)
        ]
        for future in futures:
            rows.extend(future.result())

    per_turn = summarize_turns(rows)
    print(f"  {'turn':>4} {'n':>4} {'p50':>8} {'p95':>8} {'p99':>8}  (ms)", flush=True)
    for turn, summary in per_turn.items():
        print(
            f"  {turn:>4} {summary['n']:>4.0f} {summary['p50']:>8.0f} "
            f"{summary['p95']:>8.0f} {summary['p99']:>8.0f}",
            flush=True,
        )

    follow_up_ttft = [row["ttft_ms"] for row in rows if row["turn"] >= 1]
    if follow_up_ttft:
        print(
            f"  turn>=2 combined: n={len(follow_up_ttft)} "
            f"p50={percentile(follow_up_ttft, 50):.0f} p95={percentile(follow_up_ttft, 95):.0f}",
            flush=True,
        )

    return {"label": label, "rows": rows, "per_turn": per_turn}


def compare_cases(baseline: dict[str, Any], treatment: dict[str, Any]) -> dict[str, Any]:
    baseline_turns = baseline["per_turn"]
    treatment_turns = treatment["per_turn"]
    turn_ids = sorted(set(baseline_turns) | set(treatment_turns))
    per_turn = {}

    print("\n===== A vs B per-turn TTFT p50 (ms) =====", flush=True)
    print(f"  {'turn':>4} {'A_p50':>8} {'B_p50':>8} {'delta':>8} {'speedup':>8}", flush=True)
    for turn in turn_ids:
        baseline_p50 = baseline_turns.get(turn, {}).get("p50", float("nan"))
        treatment_p50 = treatment_turns.get(turn, {}).get("p50", float("nan"))
        delta = baseline_p50 - treatment_p50
        speedup = baseline_p50 / treatment_p50 if treatment_p50 else float("nan")
        per_turn[turn] = {
            "baseline_p50": baseline_p50,
            "speculative_prefill_p50": treatment_p50,
            "delta": delta,
            "speedup": speedup,
        }
        print(
            f"  {turn:>4} {baseline_p50:>8.0f} {treatment_p50:>8.0f} "
            f"{delta:>+8.0f} {speedup:>7.2f}x",
            flush=True,
        )

    baseline_follow_up = [row["ttft_ms"] for row in baseline["rows"] if row["turn"] >= 1]
    treatment_follow_up = [row["ttft_ms"] for row in treatment["rows"] if row["turn"] >= 1]
    combined = {}
    if baseline_follow_up and treatment_follow_up:
        baseline_p50 = percentile(baseline_follow_up, 50)
        treatment_p50 = percentile(treatment_follow_up, 50)
        combined = {
            "baseline_p50": baseline_p50,
            "speculative_prefill_p50": treatment_p50,
            "speedup": baseline_p50 / treatment_p50,
        }
        print(
            f"\nturn>=2 p50: A={baseline_p50:.0f}ms  B={treatment_p50:.0f}ms  "
            f"speedup={combined['speedup']:.2f}x",
            flush=True,
        )

    return {"per_turn": per_turn, "turn_ge_2": combined}


def write_results(args: argparse.Namespace, result: dict[str, Any]) -> None:
    if not args.output:
        return
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"\nWrote results to {output_path}", flush=True)


def load_profile(args: argparse.Namespace, defaults: dict[str, Any]) -> None:
    if not args.profile:
        return

    profile_path = Path(args.profile)
    data = yaml.safe_load(profile_path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Profile must be a YAML mapping: {profile_path}")

    for key, value in data.items():
        attr = key.replace("-", "_")
        if not hasattr(args, attr):
            raise ValueError(f"Unknown profile key: {key}")
        if getattr(args, attr) == defaults.get(attr):
            setattr(args, attr, value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=BASE_DEFAULT)
    parser.add_argument("--model", default="Qwen/Qwen3-8B")
    parser.add_argument("--num-users", type=int, default=10)
    parser.add_argument("--num-turns", type=int, default=5)
    parser.add_argument("--num-user-tokens", type=int, default=128)
    parser.add_argument("--max-completion-tokens", type=int, default=256)
    parser.add_argument("--mean-delay-ms", type=int, default=5000)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--ignore-eos", action="store_true")
    parser.add_argument(
        "--long-answer-words",
        type=int,
        default=1200,
        help="target word count for the fixed turn1 long final answer",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", help="optional path for JSON results")
    parser.add_argument("--profile", help="optional YAML profile path")
    defaults = {action.dest: action.default for action in parser._actions}
    args = parser.parse_args()
    load_profile(args, defaults)
    return args


def main() -> None:
    args = parse_args()
    baseline = run_case(args, False, "A_baseline")
    treatment = run_case(args, True, "B_specprefill")
    comparison = compare_cases(baseline, treatment)
    write_results(
        args,
        {
            "args": vars(args),
            "baseline": baseline,
            "speculative_prefill": treatment,
            "comparison": comparison,
        },
    )


if __name__ == "__main__":
    main()