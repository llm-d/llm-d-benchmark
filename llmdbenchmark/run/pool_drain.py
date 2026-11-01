"""Drain check before a serving-capacity (R) measurement.

Leftover in-flight requests from a previous run read as capacity the pool does
not have: stage 0 of the rate ladder measures the leftover queue, not the pod.
On a stack without admission control (the router's flow-control gate off) an
overloaded run leaves vLLM's internal queue draining for minutes -- measured on
a live cluster, the retry's slowest stage read p90 TTFT of 412 s while the
previous run's requests (600 s timeouts) drained, and the ladder was rejected
as "no knee". With flow control on the router bounds the pod's in-flight work,
so back-to-back runs do not poison each other; without it, they do.

``make calibrate-serving-capacity`` therefore waits, before the ladder starts,
for every model-server pod's running+waiting requests to reach zero:

  python -m llmdbenchmark.run.pool_drain check --namespace <ns> [--timeout 600]

Exit codes: 0 when the pool drained (or in-flight could not be read -- the
check degrades to a warning rather than blocking on a metrics-access problem),
1 when requests are still in flight after the timeout. The in-flight count is
read from each pod's own vLLM /metrics endpoint over ``kubectl exec``; a pod
whose metrics cannot be read is skipped, and the count is only trusted when at
least one pod answered.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

# One request served by vLLM's own metrics endpoint, run inside the pod: sums
# vllm:num_requests_running and vllm:num_requests_waiting. The metrics port is
# the pod's own VLLM_METRICS_PORT (8080 as a fallback for stacks that do not
# set it).
PROBE = (
    "import os,urllib.request;"
    "m=urllib.request.urlopen("
    '"http://localhost:"+os.environ.get("VLLM_METRICS_PORT","8080")+"/metrics",'
    "timeout=5).read().decode();"
    "print(int(sum(float(l.split()[1]) for l in m.splitlines()"
    'if l.startswith(("vllm:num_requests_running","vllm:num_requests_waiting")))))'
)

MODEL_POD_LABEL = "llm-d.ai/inferenceServing=true"
POLL_SECONDS = 10


def _run(argv: list[str], timeout: int = 30) -> str | None:
    """Run kubectl, returning stdout or None when it failed or timed out."""
    try:
        r = subprocess.run(
            argv, check=False, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return None
    return r.stdout if r.returncode == 0 else None


def model_pods(kube, namespace: str) -> list[str]:
    out = kube(
        [
            "kubectl",
            "get",
            "pods",
            "-n",
            namespace,
            "-l",
            MODEL_POD_LABEL,
            "--field-selector=status.phase=Running",
            "-o",
            "name",
        ]
    )
    return out.split() if out else []


def inflight(kube, namespace: str) -> int | None:
    """Running+waiting requests summed over the stack's model-server pods.

    None when no pod's metrics could be read (exec failure, no vllm container,
    wedge): unknown, not zero.
    """
    total, readable = 0, False
    for pod in model_pods(kube, namespace):
        out = kube(
            [
                "kubectl",
                "exec",
                "-n",
                namespace,
                pod,
                "-c",
                "vllm",
                "--",
                "python3",
                "-c",
                PROBE,
            ]
        )
        if out is None or not out.strip().lstrip("-").isdigit():
            continue
        readable = True
        total += int(out.strip())
    return total if readable else None


def check(
    kube, namespace: str, timeout_s: int, poll_s: int = POLL_SECONDS, sleep=time.sleep
) -> int:
    """Wait for the pool to drain; 0 drained-or-unknown, 1 timed out."""
    deadline = time.monotonic() + timeout_s
    last = None
    while True:
        last = inflight(kube, namespace)
        if last is None:
            print(
                "⚠️  could not read in-flight requests from the model pods "
                "(no vllm /metrics access); skipping the drain check"
            )
            return 0
        if last == 0:
            print("🚰 pool drained: 0 running+waiting requests")
            return 0
        if time.monotonic() >= deadline:
            break
        print(
            f"⏳ {last} request(s) still in flight; waiting for the pool to drain "
            f"({int(deadline - time.monotonic())}s left)"
        )
        sleep(poll_s)
    print(
        f"❌ {last} request(s) still in flight after {timeout_s}s. Leftover queue "
        "from a previous run reads as missing capacity in the ladder's first "
        "stage: restart the model pods, or raise DRAIN_TIMEOUT_S"
    )
    return 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="wait for running+waiting requests to reach 0")
    c.add_argument("--namespace", required=True)
    c.add_argument(
        "--timeout", type=int, default=600, help="seconds to wait (default 600)"
    )
    o = ap.parse_args()
    if o.cmd == "check":
        sys.exit(check(_run, o.namespace, o.timeout))


if __name__ == "__main__":
    main()
