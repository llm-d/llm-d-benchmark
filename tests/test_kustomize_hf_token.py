"""Tests for the HF-token fail-fast logic in step_06_kustomize_deploy.

Three behaviours are covered:

1. Pre-existing Secret short-circuits without requiring an env token.
2. No Secret + no env token returns the exact, user-facing error.
3. No Secret + env token set creates the Secret via a temp manifest
   file -- never via ``--from-literal=`` on the kubectl command line
   (the token must not appear in any captured kubectl args).
"""

from __future__ import annotations

import base64
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

# ``llmdbenchmark.standup.steps.__init__`` eagerly imports a sibling
# (``step_03_workload_monitoring``) that transitively depends on a
# ``planner`` package not installed in this test environment.  Stub it
# permissively (any attribute resolves to a no-op callable) so the
# import chain reaches ``step_06_kustomize_deploy``.
if "planner.capacity_planner" not in sys.modules:

    class _PermissiveModule(types.ModuleType):
        def __getattr__(self, name: str):  # type: ignore[override]
            return type(name, (), {})

    sys.modules.setdefault("planner", _PermissiveModule("planner"))
    sys.modules["planner.capacity_planner"] = _PermissiveModule(
        "planner.capacity_planner"
    )

from llmdbenchmark.standup.steps.step_06_kustomize_deploy import (  # noqa: E402
    KustomizeDeployStep,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeResult:
    """Stand-in for ``CommandResult`` covering only the fields we use."""

    success: bool = True
    stdout: str = ""
    stderr: str = ""


@dataclass
class FakeCommandExecutor:
    """Capture every ``kube(*args)`` invocation and dispatch by verb.

    ``get_handler`` is invoked for ``("get", "secret", …)`` calls and
    must return a ``FakeResult``.  ``apply_handler`` is invoked for
    ``("apply", "-f", <tmpfile>, …)`` calls.  Both default to success
    so individual tests only override the bit they care about.
    """

    get_handler: Any = field(default_factory=lambda: lambda *a, **k: FakeResult(True))
    apply_handler: Any = field(default_factory=lambda: lambda *a, **k: FakeResult(True))
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = field(default_factory=list)
    apply_manifests: list[dict] = field(default_factory=list)

    def kube(self, *args: str, **kwargs: Any) -> FakeResult:
        self.calls.append((tuple(args), dict(kwargs)))
        if args and args[0] == "get":
            return self.get_handler(*args, **kwargs)
        if args and args[0] == "apply":
            # Capture the rendered manifest so tests can verify the
            # token was base64-encoded into Secret.data correctly.
            for i, tok in enumerate(args):
                if tok == "-f" and i + 1 < len(args):
                    path = Path(args[i + 1])
                    if path.exists():
                        self.apply_manifests.append(
                            yaml.safe_load(path.read_text(encoding="utf-8"))
                        )
                    break
            return self.apply_handler(*args, **kwargs)
        return FakeResult(True)


def _fake_context() -> Any:
    """Minimal stand-in for ``ExecutionContext`` with a mocked logger."""
    ctx = MagicMock()
    ctx.logger = MagicMock()
    return ctx


def _flatten_call_args(cmd: FakeCommandExecutor) -> str:
    """All kubectl args ever captured, joined for token-leak scanning."""
    return " ".join(str(a) for args, _ in cmd.calls for a in args)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _clear_hf_env(monkeypatch):
    """Strip every HF-token env var so each test starts clean."""
    for var in ("HF_TOKEN", "LLMDBENCH_HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)


class TestEnsureHfTokenSecret:
    """Cover the three branches of ``_ensure_hf_token_secret``."""

    def test_pre_existing_secret_succeeds_without_env_token(self, monkeypatch):
        """Externally managed Secret -- no env token required."""
        _clear_hf_env(monkeypatch)
        cmd = FakeCommandExecutor(
            get_handler=lambda *a, **k: FakeResult(success=True),
        )
        ctx = _fake_context()

        result = KustomizeDeployStep._ensure_hf_token_secret(cmd, ctx, "my-ns")

        assert result is None, (
            "Pre-existing Secret should make the helper succeed without an env token."
        )
        # No `apply` was attempted -- only the single get.
        verbs = [args[0] for args, _ in cmd.calls]
        assert verbs == ["get"], verbs

    def test_missing_secret_and_no_token_returns_error_with_namespace(
        self, monkeypatch
    ):
        """The exact user-facing error message."""
        _clear_hf_env(monkeypatch)
        cmd = FakeCommandExecutor(
            get_handler=lambda *a, **k: FakeResult(success=False),
        )
        ctx = _fake_context()

        result = KustomizeDeployStep._ensure_hf_token_secret(cmd, ctx, "my-cool-ns")

        assert result is not None
        # The opening line, with the namespace populated.
        assert (
            "HF_TOKEN is not set and Secret 'llm-d-hf-token' does not exist "
            "in namespace my-cool-ns" in result
        )
        assert "A HF_TOKEN is now required for well-lit-path guides" in result
        # Both remediation paths are spelled out.
        assert "Export HF_TOKEN" in result
        assert "LLMDBENCH_HF_TOKEN" in result
        assert "HUGGING_FACE_HUB_TOKEN" in result
        assert "kubectl create secret generic llm-d-hf-token" in result
        assert "-n my-cool-ns" in result
        # No apply was attempted -- we bailed before creating.
        verbs = [args[0] for args, _ in cmd.calls]
        assert verbs == ["get"], verbs

    def test_env_token_creates_secret_via_temp_manifest(self, monkeypatch):
        """Token comes from env, Secret applied from a temp file."""
        token = "hf_unique_test_value_DO_NOT_LOG_ME"
        monkeypatch.setenv("HF_TOKEN", token)
        monkeypatch.delenv("LLMDBENCH_HF_TOKEN", raising=False)
        monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)

        cmd = FakeCommandExecutor(
            get_handler=lambda *a, **k: FakeResult(success=False),
            apply_handler=lambda *a, **k: FakeResult(success=True),
        )
        ctx = _fake_context()

        result = KustomizeDeployStep._ensure_hf_token_secret(cmd, ctx, "ns42")

        assert result is None, "Creation should succeed."
        # An apply was attempted with -f <tmp> and the right namespace.
        verbs = [args[0] for args, _ in cmd.calls]
        assert verbs == ["get", "apply"], verbs

        apply_args = cmd.calls[1][0]
        assert apply_args[0] == "apply"
        assert "-f" in apply_args
        assert "--namespace" in apply_args
        assert "ns42" in apply_args

        # The rendered Secret manifest was correct: base64(token) under
        # data.HF_TOKEN, type Opaque, named llm-d-hf-token.
        assert len(cmd.apply_manifests) == 1
        manifest = cmd.apply_manifests[0]
        assert manifest["kind"] == "Secret"
        assert manifest["type"] == "Opaque"
        assert manifest["metadata"]["name"] == "llm-d-hf-token"
        assert manifest["metadata"]["namespace"] == "ns42"
        expected_b64 = base64.b64encode(token.encode("utf-8")).decode("ascii")
        assert manifest["data"]["HF_TOKEN"] == expected_b64

    def test_token_never_appears_in_any_captured_command_arg(self, monkeypatch, capsys):
        """The HF token value must never reach the kubectl argv (which
        ``CommandExecutor._run_once`` logs to disk).  Belt-and-braces:
        also assert it never reaches stdout/stderr captured by pytest."""
        token = "hf_unique_canary_value_zzz_DO_NOT_LEAK"
        monkeypatch.setenv("HF_TOKEN", token)
        monkeypatch.delenv("LLMDBENCH_HF_TOKEN", raising=False)
        monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)

        cmd = FakeCommandExecutor(
            get_handler=lambda *a, **k: FakeResult(success=False),
            apply_handler=lambda *a, **k: FakeResult(success=True),
        )
        ctx = _fake_context()

        KustomizeDeployStep._ensure_hf_token_secret(cmd, ctx, "ns42")

        joined = _flatten_call_args(cmd)
        assert token not in joined, (
            f"HF token leaked into captured kubectl args: {joined!r}"
        )
        # And not into anything pytest captured from stdout/stderr.
        captured = capsys.readouterr()
        assert token not in captured.out
        assert token not in captured.err
        # And not into anything sent to the (mocked) logger.
        for call in ctx.logger.method_calls:
            args_str = " ".join(str(a) for a in call.args)
            assert token not in args_str, (
                f"HF token leaked into a logger call: {call!r}"
            )

    def test_apply_failure_returns_error_redacted_of_token(self, monkeypatch):
        """If kubectl somehow echoes the token in stderr, we scrub it."""
        token = "hf_canary_token_for_redaction"
        monkeypatch.setenv("HF_TOKEN", token)

        # Synthesise a stderr that contains the token to exercise the
        # belt-and-braces redaction.
        fake_stderr = f"the server rejected token {token}: 401 Unauthorized"
        cmd = FakeCommandExecutor(
            get_handler=lambda *a, **k: FakeResult(success=False),
            apply_handler=lambda *a, **k: FakeResult(success=False, stderr=fake_stderr),
        )
        ctx = _fake_context()

        result = KustomizeDeployStep._ensure_hf_token_secret(cmd, ctx, "ns42")

        assert result is not None
        assert token not in result, f"Token leaked into the surfaced error: {result!r}"
        assert "<redacted>" in result
        assert "Failed to create HF token secret" in result

    def test_env_token_fallback_order(self, monkeypatch):
        """LLMDBENCH_HF_TOKEN is honoured when HF_TOKEN is unset."""
        token = "hf_from_llmdbench_var"
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.setenv("LLMDBENCH_HF_TOKEN", token)
        monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)

        cmd = FakeCommandExecutor(
            get_handler=lambda *a, **k: FakeResult(success=False),
            apply_handler=lambda *a, **k: FakeResult(success=True),
        )
        ctx = _fake_context()

        result = KustomizeDeployStep._ensure_hf_token_secret(cmd, ctx, "ns42")

        assert result is None
        expected_b64 = base64.b64encode(token.encode("utf-8")).decode("ascii")
        assert cmd.apply_manifests[0]["data"]["HF_TOKEN"] == expected_b64

    def test_temp_manifest_file_is_deleted_after_apply(self, monkeypatch):
        """No stale temp file with a base64 token left on disk."""
        monkeypatch.setenv("HF_TOKEN", "hf_temp_file_cleanup_test")

        captured_path: list[str] = []

        def apply_handler(*args, **kwargs):
            for i, tok in enumerate(args):
                if tok == "-f" and i + 1 < len(args):
                    captured_path.append(args[i + 1])
                    break
            return FakeResult(success=True)

        cmd = FakeCommandExecutor(
            get_handler=lambda *a, **k: FakeResult(success=False),
            apply_handler=apply_handler,
        )
        ctx = _fake_context()

        KustomizeDeployStep._ensure_hf_token_secret(cmd, ctx, "ns42")

        assert captured_path, "Apply was not invoked with -f <path>"
        assert not Path(captured_path[0]).exists(), (
            "Temp manifest file was not unlinked after apply -- the "
            "base64 token would persist on disk."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
