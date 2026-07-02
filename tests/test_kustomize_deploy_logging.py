"""Tests for the exact-command logging + secret scrubbing in
`KustomizeDeployStep._run_resolved` / `_scrub_secrets`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# The `llmdbenchmark.standup.steps` package pulls in `step_03_workload_monitoring`
# which requires the top-level `planner` package (installed by install.sh but not
# a declared pyproject dependency). Bypass the package __init__ and load
# `step_06_kustomize_deploy` directly — this is a unit test for its logging
# helpers, we don't need the whole standup pipeline.
_STEP_PATH = (
    Path(__file__).resolve().parent.parent
    / "llmdbenchmark"
    / "standup"
    / "steps"
    / "step_06_kustomize_deploy.py"
)
_spec = importlib.util.spec_from_file_location(
    "step_06_kustomize_deploy_isolated", _STEP_PATH
)
_module = importlib.util.module_from_spec(_spec)
sys.modules["step_06_kustomize_deploy_isolated"] = _module
_spec.loader.exec_module(_module)
KustomizeDeployStep = _module.KustomizeDeployStep


class TestScrubSecrets:
    """`_scrub_secrets` must replace any known secret env var value that
    appears in the input with `<redacted>`, and must leave placeholder /
    unset values alone."""

    def test_no_secret_env_leaves_text_untouched(self, monkeypatch):
        for var in KustomizeDeployStep._SECRET_ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        text = "helm install foo -f bar.yaml"
        assert KustomizeDeployStep._scrub_secrets(text) == text

    def test_placeholder_hf_token_is_ignored(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "HF_TOKEN_PLACEHOLDER")
        text = "kubectl create secret ... HF_TOKEN=HF_TOKEN_PLACEHOLDER"
        # Placeholder is intentionally excluded from scrubbing — no leak risk.
        assert KustomizeDeployStep._scrub_secrets(text) == text

    def test_empty_hf_token_is_ignored(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "")
        text = "kubectl apply -f manifest.yaml"
        assert KustomizeDeployStep._scrub_secrets(text) == text

    def test_real_hf_token_gets_redacted(self, monkeypatch):
        secret = "hf_realtoken_1234567890"
        monkeypatch.setenv("HF_TOKEN", secret)
        text = f"kubectl create secret HF_TOKEN={secret}"
        scrubbed = KustomizeDeployStep._scrub_secrets(text)
        assert secret not in scrubbed
        assert "<redacted>" in scrubbed

    def test_llmdbench_hf_token_gets_redacted(self, monkeypatch):
        for v in KustomizeDeployStep._SECRET_ENV_VARS:
            monkeypatch.delenv(v, raising=False)
        secret = "hf_llmdbench_9876"
        monkeypatch.setenv("LLMDBENCH_HF_TOKEN", secret)
        text = f"echo {secret}"
        assert secret not in KustomizeDeployStep._scrub_secrets(text)

    def test_huggingface_hub_token_gets_redacted(self, monkeypatch):
        for v in KustomizeDeployStep._SECRET_ENV_VARS:
            monkeypatch.delenv(v, raising=False)
        secret = "hf_hub_abc"
        monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", secret)
        text = f"kubectl set env HF={secret}"
        assert secret not in KustomizeDeployStep._scrub_secrets(text)

    def test_multiple_matches_all_redacted(self, monkeypatch):
        secret = "hf_multi"
        monkeypatch.setenv("HF_TOKEN", secret)
        text = f"cmd --a={secret} --b={secret}"
        scrubbed = KustomizeDeployStep._scrub_secrets(text)
        assert secret not in scrubbed
        assert scrubbed.count("<redacted>") == 2


class TestRunResolvedLogging:
    """`_run_resolved` must log the exact final invocation with a phase
    tag, honouring the `helm install` → `helm upgrade --install` rewrite
    and scrubbing secrets before printing."""

    def _make_context(self):
        ctx = MagicMock()
        ctx.logger.log_info = MagicMock()
        return ctx

    def _make_cmd(self):
        cmd = MagicMock()
        cmd.kube.return_value = MagicMock(success=True)
        cmd.helm.return_value = MagicMock(success=True)
        cmd.execute.return_value = MagicMock(success=True)
        return cmd

    def test_kubectl_command_logged_verbatim(self):
        ctx = self._make_context()
        cmd = self._make_cmd()
        KustomizeDeployStep._run_resolved(
            cmd,
            "kubectl apply -n ns -k /path/to/overlay/",
            check=False,
            context=ctx,
            phase="prerequisites",
        )
        ctx.logger.log_info.assert_called_once_with(
            "[prerequisites] kubectl apply -n ns -k /path/to/overlay/"
        )
        cmd.kube.assert_called_once_with(
            "apply", "-n", "ns", "-k", "/path/to/overlay/", check=False
        )

    def test_helm_install_rewritten_to_upgrade_install(self):
        ctx = self._make_context()
        cmd = self._make_cmd()
        KustomizeDeployStep._run_resolved(
            cmd,
            "helm install my-guide oci://foo -f bar.yaml -n ns",
            check=False,
            context=ctx,
            phase="router",
        )
        logged = ctx.logger.log_info.call_args[0][0]
        # The log must reflect the ACTUAL final invocation.
        assert logged.startswith("[router] helm upgrade --install ")
        assert "helm install " not in logged
        # And the cmd.helm arg list must match.
        cmd.helm.assert_called_once_with(
            "upgrade",
            "--install",
            "my-guide",
            "oci://foo",
            "-f",
            "bar.yaml",
            "-n",
            "ns",
            check=False,
        )

    def test_secrets_scrubbed_from_log(self, monkeypatch):
        secret = "hf_shouldnotleak"
        monkeypatch.setenv("HF_TOKEN", secret)
        ctx = self._make_context()
        cmd = self._make_cmd()
        KustomizeDeployStep._run_resolved(
            cmd,
            f"kubectl create secret generic hf --from-literal=HF_TOKEN={secret}",
            check=False,
            context=ctx,
            phase="prerequisites",
        )
        logged = ctx.logger.log_info.call_args[0][0]
        assert secret not in logged
        assert "<redacted>" in logged

    def test_no_truncation_of_long_helm_command(self):
        ctx = self._make_context()
        cmd = self._make_cmd()
        long_cmd = (
            "helm install optimized-baseline oci://ghcr.io/llm-d/charts/llm-d-router-standalone "
            "-f /home/runner/work/llm-d/llm-d/guides/recipes/router/base.values.yaml "
            "-f /home/runner/work/llm-d/llm-d/guides/optimized-baseline/router/"
            "optimized-baseline.values.yaml "
            "-n llm-d-nightly-optimized-baseline-gke-gpu --version v0.9.0"
        )
        KustomizeDeployStep._run_resolved(
            cmd, long_cmd, check=False, context=ctx, phase="router"
        )
        logged = ctx.logger.log_info.call_args[0][0]
        # The old code truncated at 120 chars. This one must not.
        assert len(logged) > 200
        assert "--version v0.9.0" in logged

    def test_silent_mode_when_context_missing(self):
        cmd = self._make_cmd()
        # No context, no phase — should not attempt to log at all.
        KustomizeDeployStep._run_resolved(cmd, "kubectl get ns", check=False)
        cmd.kube.assert_called_once_with("get", "ns", check=False)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
