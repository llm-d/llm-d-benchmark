"""Rendering the Agent-Rendered Run Command and the Benchmark Job Manifest.

Neither function calls Kubernetes. Both are pure: given a Recommendation
Output and Execution Facts, they produce data structures a caller may
submit, dump, or inspect.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

from llmdbenchmark.agent.facts import (
    AgentRenderedRunCommand,
    ExecutionFacts,
    RecommendationOutput,
    SecretEnvReference,
    SecretFileReference,
)

_LABEL_PREFIX = "llmdbenchmark.llm-d.ai"


def _slugify(value: str) -> str:
    """Legal Kubernetes label value: lowercase, alphanumerics and
    ``-_.``. ``Interactive Chat`` contains a space and is not a legal
    label value on its own."""
    slug = re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-")
    return slug or "unknown"


def _argv(
    recommendation: RecommendationOutput,
    execution_facts: ExecutionFacts,
) -> list[str]:
    """The single owner of canonical argv ordering. ``--spec`` and
    ``--workspace`` precede ``run`` because both are root flags
    (cli.py:1783-1787, cli.py:1770-1810), matching the repo's own
    copy-paste block at cli.py:1076-1080."""
    selected = recommendation.selected
    argv = [
        "llmdbenchmark",
        "--spec",
        selected.specification,
        "--workspace",
        execution_facts.benchmark_workspace_volume.mount_path,
        "run",
        "--namespace",
        execution_facts.namespace,
        "--endpoint-url",
        execution_facts.endpoint_url,
        "--model",
        execution_facts.model,
        "--harness",
        selected.harness,
        "--workload",
        selected.workload_profile,
        "--analyze",
    ]
    if execution_facts.benchmark_monitoring_override:
        argv.append("--monitoring")
    return argv


def render_run_command(
    recommendation: RecommendationOutput,
    execution_facts: ExecutionFacts,
) -> AgentRenderedRunCommand:
    """Render the Agent-Rendered Run Command."""
    argv = _argv(recommendation, execution_facts)
    return AgentRenderedRunCommand(argv=argv, rendered=shlex.join(argv))


def render_benchmark_job_manifest(
    recommendation: RecommendationOutput,
    execution_facts: ExecutionFacts,
    run_command: AgentRenderedRunCommand,
) -> dict[str, Any]:
    """Render the Benchmark Job Manifest: a plain ``batch/v1`` Job dict.

    ``ttlSecondsAfterFinished`` is deliberately absent -- that omission is
    Benchmark Artifact Retention, so results are not silently garbage
    collected before an operator can inspect them.
    """
    name = f"llmdbench-agent-{execution_facts.benchmark_session_id}"
    workspace = execution_facts.benchmark_workspace_volume

    volumes: list[dict[str, Any]] = [
        {
            "name": "benchmark-workspace",
            "persistentVolumeClaim": {"claimName": workspace.claim_name},
        }
    ]
    volume_mounts: list[dict[str, Any]] = [
        {"name": "benchmark-workspace", "mountPath": workspace.mount_path}
    ]
    env: list[dict[str, Any]] = []

    for index, secret_ref in enumerate(execution_facts.benchmark_secret_references):
        if isinstance(secret_ref, SecretEnvReference):
            env.append(
                {
                    "name": secret_ref.env_var,
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": secret_ref.secret_name,
                            "key": secret_ref.secret_key,
                        }
                    },
                }
            )
        elif isinstance(secret_ref, SecretFileReference):
            volume_name = f"benchmark-secret-{index}"
            volumes.append(
                {
                    "name": volume_name,
                    "secret": {"secretName": secret_ref.secret_name},
                }
            )
            volume_mounts.append(
                {
                    "name": volume_name,
                    "mountPath": secret_ref.mount_path,
                    "readOnly": True,
                }
            )

    container: dict[str, Any] = {
        "name": "benchmark-runner",
        "image": execution_facts.benchmark_runner_image,
        "command": ["llmdbenchmark"],
        "args": run_command.argv[1:],
        "volumeMounts": volume_mounts,
    }
    if env:
        container["env"] = env

    pod_spec: dict[str, Any] = {
        "restartPolicy": "Never",
        "containers": [container],
        "volumes": volumes,
    }
    if execution_facts.benchmark_runner_auth:
        pod_spec["serviceAccountName"] = execution_facts.benchmark_runner_auth

    manifest: dict[str, Any] = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": execution_facts.namespace,
            "labels": {
                f"{_LABEL_PREFIX}/benchmark-session-id": (
                    execution_facts.benchmark_session_id
                ),
                f"{_LABEL_PREFIX}/recommendation-id": recommendation.recommendation_id,
                f"{_LABEL_PREFIX}/harness": recommendation.selected.harness,
                f"{_LABEL_PREFIX}/workload-intent": _slugify(
                    str(recommendation.workload_intent)
                ),
            },
            "annotations": {
                f"{_LABEL_PREFIX}/recommendation-map-version": (
                    recommendation.recommendation_map_version
                ),
                f"{_LABEL_PREFIX}/specification": recommendation.selected.specification,
                f"{_LABEL_PREFIX}/workload-intent": str(recommendation.workload_intent),
                f"{_LABEL_PREFIX}/agent-rendered-run-command": run_command.rendered,
            },
        },
        "spec": {
            "backoffLimit": 0,
            "template": {"spec": pod_spec},
        },
    }
    return manifest
