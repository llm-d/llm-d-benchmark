"""Helm cannot reinstall over a record it cannot list, and refuses to
adopt a secret an old release owns. Both fail the next standup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import llmdbenchmark.teardown.steps.step_01_uninstall_helm as step_01
from llmdbenchmark.teardown.steps.step_01_uninstall_helm import (
    UninstallHelmStep,
    _release_name_from_record,
)


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
    release_records: str = ""
    annotated_secrets: str = ""
    fail_deletes: set[str] = field(default_factory=set)
    fail_label_deletes: bool = False
    deleted: list[tuple[str, ...]] = field(default_factory=list)
    queries: list[tuple[str, ...]] = field(default_factory=list)

    def kube(self, *args: str, **_: Any) -> _StubResult:
        if args[0] == "delete":
            self.deleted.append(args)
            if "-l" in args and self.fail_label_deletes:
                return _StubResult(success=False, stderr="boom")
            name = args[2]
            if name in self.fail_deletes:
                return _StubResult(success=False, stderr="boom")
            return _StubResult()
        if args[0] == "get":
            self.queries.append(args)
            if "--field-selector" in args:
                return _StubResult(stdout=self.release_records)
            if "release-name" in " ".join(args):
                return _StubResult(stdout=self.annotated_secrets)
        return _StubResult()

    def helm(self, *args: str, **_: Any) -> _StubResult:
        return _StubResult()


@dataclass
class _StubContext:
    logger: _StubLogger = field(default_factory=_StubLogger)
    stack_filter: list[str] | None = None
    deep_clean: bool = False


_MODEL = "qwen-qwe-6ed7b204-wen3-32b"
_REL = f"{_MODEL}-router"


def _run(
    cmd: _StubCmd,
    seen: set[str] | frozenset | None = frozenset(),
    ctx: _StubContext | None = None,
) -> list[str]:
    errors: list[str] = []
    UninstallHelmStep()._delete_blocking_secrets(
        cmd,
        ctx or _StubContext(),
        "nikita-dev",
        "llmdbench",
        [_MODEL],
        seen,
        errors,
    )
    cmd.errors = errors
    # the fixed-name secret can be deleted by both the annotated loop and
    # the unconditional fallback; it is idempotent, so compare names once
    return sorted({a[2] for a in cmd.deleted if a[0] == "delete" and a[1] == "secret"})


def test_release_name_from_record():
    assert _release_name_from_record(f"sh.helm.release.v1.{_REL}.v12") == _REL
    assert _release_name_from_record("sh.helm.release.v1.rel.v1") == "rel"
    assert _release_name_from_record("sh.helm.release.v1.rel") == "rel"
    assert _release_name_from_record("sh.helm.release.v1.") == ""
    assert _release_name_from_record("some-other-secret") == ""


def test_jsonpath_args_are_shell_quoted():
    # kube() runs through a shell; an unquoted template loses its spaces
    # and backslashes and the query silently returns nothing
    for query in (step_01._RELEASE_RECORDS_QUERY, step_01._ANNOTATED_SECRETS_QUERY):
        assert query.startswith("'") and query.endswith("'")
        assert " " in query
    assert "meta\\.helm\\.sh/release-name" in step_01._ANNOTATED_SECRETS_QUERY

    cmd = _StubCmd()
    _run(cmd)

    records_query = cmd.queries[0][cmd.queries[0].index("-o") + 1]
    annotated_query = cmd.queries[1][cmd.queries[1].index("-o") + 1]
    assert records_query == step_01._RELEASE_RECORDS_QUERY
    assert annotated_query == step_01._ANNOTATED_SECRETS_QUERY


def test_stale_release_record_is_deleted():
    cmd = _StubCmd(
        release_records=(
            f"sh.helm.release.v1.{_REL}.v2\n"
            "sh.helm.release.v1.foreign-model-router.v1\n"
        )
    )

    deleted = _run(cmd)

    assert f"sh.helm.release.v1.{_REL}.v2" in deleted
    assert "sh.helm.release.v1.foreign-model-router.v1" not in deleted


def test_record_of_release_helm_list_reported_is_kept():
    # deleting its record would orphan every object the release owns
    cmd = _StubCmd(release_records=f"sh.helm.release.v1.{_REL}.v1\n")

    deleted = _run(cmd, seen={_REL})

    assert f"sh.helm.release.v1.{_REL}.v1" not in deleted


def test_helm_list_failure_skips_record_deletion():
    # seen=None means the listing failed; no record is safe to touch
    cmd = _StubCmd(
        release_records=f"sh.helm.release.v1.{_REL}.v1\n",
        annotated_secrets=f"cross-ns-secret {_REL} other-namespace\n",
    )

    deleted = _run(cmd, seen=None)

    assert f"sh.helm.release.v1.{_REL}.v1" not in deleted
    assert deleted == ["inference-gateway-sa-metrics-reader-secret"]


def test_annotated_secret_of_our_release_is_deleted():
    cmd = _StubCmd(
        annotated_secrets=(
            f"inference-gateway-sa-metrics-reader-secret {_REL} nikita-dev\n"
            f"foreign-secret foreign-model-router nikita-dev\n"
            f"cross-ns-secret {_REL} other-namespace\n"
        )
    )

    deleted = _run(cmd)

    assert deleted == ["inference-gateway-sa-metrics-reader-secret"]


def test_fixed_name_secret_gated_on_partial_teardown():
    # a partial teardown may share the namespace with a live run
    cmd = _StubCmd()
    deleted = _run(cmd, ctx=_StubContext(stack_filter=["other-stack"]))
    assert "inference-gateway-sa-metrics-reader-secret" not in deleted

    cmd = _StubCmd()
    deleted = _run(cmd, ctx=_StubContext(deep_clean=True))
    assert "inference-gateway-sa-metrics-reader-secret" in deleted


def test_delete_failure_records_error():
    cmd = _StubCmd(
        release_records=f"sh.helm.release.v1.{_REL}.v2\n",
        fail_deletes={f"sh.helm.release.v1.{_REL}.v2"},
    )

    _run(cmd)

    assert any("sh.helm.release.v1" in e for e in cmd.errors)


def test_wedged_label_delete_failure_leaves_release_unseen(monkeypatch):
    # the record delete failed, so the name-based fallback must still see it
    monkeypatch.setattr(
        step_01,
        "list_helm_releases",
        lambda cmd, ns, statuses: [
            {"name": _REL, "status": "uninstalling", "chart": "llm-d-router-1.0.0"}
        ],
    )
    cmd = _StubCmd(fail_label_deletes=True)
    errors: list[str] = []

    seen = UninstallHelmStep()._uninstall_releases(
        cmd, _StubContext(), "nikita-dev", "llmdbench", [_MODEL], errors
    )

    assert seen == set()
