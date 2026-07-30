"""Cluster-free smoketest probes for the no-Kubernetes (nok8s) deployment.

The stack is plain containers on the host, so there is no Service IP, no pod
to exec into and no route to look up.  These two probes talk straight to the
Envoy front door over HTTP, using the same rendered
``34_nok8s-containers.yaml`` launch spec ``step_06_nok8s_deploy`` deployed
from, so the endpoint probed and the model asserted provably match what was
launched.
"""

from pathlib import Path

import requests
import yaml

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.smoketests.report import CheckResult, SmoketestReport

_SPEC_PREFIX = "34_nok8s-containers"
_TIMEOUT = 15


def _read_spec(stack_path: Path) -> tuple[str, str, str]:
    """Return (endpoint, model, error) from the rendered nok8s launch spec."""
    for spec_file in sorted(stack_path.glob(f"{_SPEC_PREFIX}*")):
        spec = yaml.safe_load(spec_file.read_text(encoding="utf-8")) or {}
        endpoint = str(spec.get("endpoint") or "").rstrip("/")
        model = str(spec.get("model") or "")
        if not endpoint or not model:
            return "", "", f"{spec_file} is missing 'endpoint' and/or 'model'"
        return endpoint, model, ""
    return "", "", f"No {_SPEC_PREFIX}*.yaml found in {stack_path}"


def _spec_failure(name: str, error: str) -> SmoketestReport:
    report = SmoketestReport()
    report.add(CheckResult(name, False, message=error))
    return report


def health_check(context: ExecutionContext, stack_path: Path) -> SmoketestReport:
    """Probe ``/v1/models`` on the nok8s Envoy front door."""
    report = SmoketestReport()
    endpoint, model, error = _read_spec(stack_path)
    if error:
        return _spec_failure("nok8s_container_spec", error)

    url = f"{endpoint}/v1/models"
    if context.dry_run:
        context.logger.log_info(f"[dry-run] would GET {url} expecting model {model}")
        return report

    context.logger.log_info(f"Checking {url}...")
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
    except requests.RequestException as e:
        report.add(
            CheckResult(
                "nok8s_models_endpoint",
                False,
                message=f"{url} unreachable: {e}",
            )
        )
        return report

    if resp.status_code != 200:
        report.add(
            CheckResult(
                "nok8s_models_endpoint",
                False,
                expected="200",
                actual=str(resp.status_code),
                message=f"{url} returned HTTP {resp.status_code}: {resp.text[:200]}",
            )
        )
        return report

    try:
        body = resp.json()
    except ValueError:
        report.add(
            CheckResult(
                "nok8s_models_endpoint",
                False,
                message=f"{url} returned a non-JSON body: {resp.text[:200]}",
            )
        )
        return report

    report.add(
        CheckResult("nok8s_models_endpoint", True, message=f"{url} returned 200")
    )

    served = [d.get("id") for d in body.get("data", []) if isinstance(d, dict)]
    if model in served:
        report.add(
            CheckResult("nok8s_model_served", True, message=f"{model} is served")
        )
    else:
        report.add(
            CheckResult(
                "nok8s_model_served",
                False,
                expected=model,
                actual=", ".join(str(s) for s in served) or "(none)",
                message=f"{model} not served, {url} lists: {served}",
            )
        )
    return report


def inference_test(context: ExecutionContext, stack_path: Path) -> SmoketestReport:
    """Send one completion request through the nok8s Envoy front door."""
    report = SmoketestReport()
    endpoint, model, error = _read_spec(stack_path)
    if error:
        return _spec_failure("nok8s_container_spec", error)

    url = f"{endpoint}/v1/completions"
    payload = {"model": model, "prompt": "Hello", "max_tokens": 16}
    if context.dry_run:
        context.logger.log_info(f"[dry-run] would POST {payload} to {url}")
        return report

    context.logger.log_info(f"Running sample inference against {url}...")
    try:
        resp = requests.post(url, json=payload, timeout=_TIMEOUT)
    except requests.RequestException as e:
        report.add(
            CheckResult("nok8s_inference", False, message=f"{url} unreachable: {e}")
        )
        return report

    if resp.status_code != 200:
        report.add(
            CheckResult(
                "nok8s_inference",
                False,
                expected="200",
                actual=str(resp.status_code),
                message=f"{url} returned HTTP {resp.status_code}: {resp.text[:200]}",
            )
        )
        return report

    try:
        choices = resp.json().get("choices") or []
    except ValueError:
        report.add(
            CheckResult(
                "nok8s_inference",
                False,
                message=f"{url} returned a non-JSON body: {resp.text[:200]}",
            )
        )
        return report

    text = choices[0].get("text", "") if choices else ""
    if not text:
        report.add(
            CheckResult(
                "nok8s_inference",
                False,
                message=f"{url} returned no generated text: {resp.text[:200]}",
            )
        )
        return report

    context.logger.log_info(f"Generated: {text[:120]}")
    report.add(
        CheckResult("nok8s_inference", True, message=f"{model} responded via {url}")
    )
    return report
