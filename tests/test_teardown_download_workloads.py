"""Normal teardown deletes the model download Job and DaemonSet.

Standup creates a download Job (PVC mode) or a download DaemonSet (hostPath
mode) for every deploy method, but a modelservice-only teardown kept only
resources matching ``MODELSERVICE_PATTERNS``, which do not cover them -- so
both outlived the teardown, the DaemonSet pinning a sleeping pod per node.

Behavior under test:
- With modelservice deployed, the download Job and DaemonSet are deleted,
  under the shipped names, their per-stack suffixed forms, and names a
  scenario configures.
- Other Jobs/DaemonSets that match no pattern are still left alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from llmdbenchmark.teardown.steps.step_03_delete_resources import DeleteResourcesStep


class _StubLogger:
    def log_info(self, *_: Any, **__: Any) -> None:
        pass

    def log_warning(self, *_: Any, **__: Any) -> None:
        pass

    def log_error(self, *_: Any, **__: Any) -> None:
        pass


@dataclass
class _StubResult:
    success: bool = True
    stdout: str = ""
    stderr: str = ""


@dataclass
class _StubCmd:
    """Lists *resources* for the namespace query and records the deletes."""

    resources: list[str]
    deleted: list[str] = field(default_factory=list)

    def kube(self, *args: str, **_: Any) -> _StubResult:
        if args[0] == "delete":
            self.deleted.append(args[-1])
            return _StubResult()
        if args[0] == "get" and "-o" in args and args[args.index("-o") + 1] == "name":
            if "-l" in args or "--no-headers" in args:
                return _StubResult()  # prism-protected lookup / prune probe
            return _StubResult(stdout="\n".join(self.resources))
        return _StubResult()


@dataclass
class _StubContext:
    rendered_stacks: list[Path]
    deployed_methods: list[str] = field(default_factory=lambda: ["modelservice"])
    is_openshift: bool = False
    deep_clean: bool = False
    remove_prism: bool = False
    logger: _StubLogger = field(default_factory=_StubLogger)


def _stack(tmp_path: Path, name: str, cfg: dict) -> Path:
    stack = tmp_path / name
    stack.mkdir()
    base = {"huggingface": {"secretName": "llm-d-hf-token"}}
    (stack / "config.yaml").write_text(yaml.safe_dump({**base, **cfg}))
    return stack


def _teardown(
    tmp_path: Path, resources: list[str], cfg: dict | None = None
) -> list[str]:
    stack = _stack(tmp_path, "s1", cfg or {})
    cmd = _StubCmd(resources=resources)
    DeleteResourcesStep()._normal_clean(cmd, _StubContext([stack]), ["bench"], [])
    return cmd.deleted


def test_modelservice_teardown_deletes_download_job_and_daemonset(tmp_path):
    deleted = _teardown(
        tmp_path,
        [
            "job.batch/download-model",
            "daemonset.apps/download-model-ds",
            "job.batch/download-model-qwen-qwe-bfc074e9",
            "daemonset.apps/download-model-ds-qwen-qwe-bfc074e9",
        ],
    )
    assert sorted(deleted) == [
        "daemonset.apps/download-model-ds",
        "daemonset.apps/download-model-ds-qwen-qwe-bfc074e9",
        "job.batch/download-model",
        "job.batch/download-model-qwen-qwe-bfc074e9",
    ]


def test_configured_download_names_are_deleted(tmp_path):
    cfg = {
        "downloadJob": {"name": "fetch-weights"},
        "storage": {"hostPath": {"daemonSetName": "stage-weights"}},
    }
    deleted = _teardown(
        tmp_path, ["job.batch/fetch-weights", "daemonset.apps/stage-weights"], cfg
    )
    assert sorted(deleted) == [
        "daemonset.apps/stage-weights",
        "job.batch/fetch-weights",
    ]


def test_unrelated_jobs_and_daemonsets_are_kept(tmp_path):
    deleted = _teardown(
        tmp_path,
        ["job.batch/someone-elses-job", "daemonset.apps/node-exporter"],
    )
    assert deleted == []
