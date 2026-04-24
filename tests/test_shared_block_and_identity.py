"""Tests for the scenario-wide `shared:` block and per-stack identity rewrites.

Covers two related pieces of render_plans.py:

1. `shared:` merge — scenario-wide config applied to every stack before the
   per-stack overrides. Per-stack always wins.
2. `_resolve_per_stack_identity` — auto-suffixes shipped-default resource
   names (model PVC, download Job, EPP Secret) with the model_id_label so
   multi-stack scenarios don't race on the same Kubernetes resource.
   Skipped for single-stack scenarios to keep their names stable.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from llmdbenchmark.parser.render_plans import RenderPlans


@pytest.fixture
def renderer():
    """Bypass __init__ — we only need the pure logic under test."""
    logger = MagicMock()
    logger.log_warning = MagicMock()
    logger.log_info = MagicMock()
    r = RenderPlans.__new__(RenderPlans)
    r.logger = logger
    return r


class TestPerStackIdentity:
    """_resolve_per_stack_identity — auto-derived unique names."""

    def _base_values(self, label: str = "my-model") -> dict:
        # Mirrors defaults.yaml shape for the keys we care about.
        return {
            "model_id_label": label,
            "storage": {"modelPvc": {"name": "model-pvc", "size": "50Gi"}},
            "downloadJob": {"name": "download-model"},
            "inferenceExtension": {
                "monitoring": {
                    "secretName": "inference-gateway-sa-metrics-reader-secret",
                    "interval": "10s",
                },
            },
        }

    def test_single_stack_leaves_names_unchanged(self, renderer):
        """Single-stack: no collision risk, so don't churn resource names."""
        values = self._base_values()
        out = renderer._resolve_per_stack_identity(values, total_stacks=1)
        assert out["storage"]["modelPvc"]["name"] == "model-pvc"
        assert out["downloadJob"]["name"] == "download-model"
        assert (
            out["inferenceExtension"]["monitoring"]["secretName"]
            == "inference-gateway-sa-metrics-reader-secret"
        )

    def test_multi_stack_suffixes_default_names(self, renderer):
        """Multi-stack: download-Job + EPP secret get suffixed (PVC does not)."""
        values = self._base_values(label="qwen-07df-6b")
        out = renderer._resolve_per_stack_identity(values, total_stacks=2)
        assert out["downloadJob"]["name"] == "download-model-qwen-07df-6b"
        assert out["inferenceExtension"]["monitoring"]["secretName"] == (
            "inference-gateway-sa-metrics-reader-secret-qwen-07df-6b"
        )

    def test_multi_stack_model_pvc_is_always_shared(self, renderer):
        """Model PVC stays at its default so N stacks share one volume."""
        values = self._base_values(label="qwen-07df-6b")
        out = renderer._resolve_per_stack_identity(values, total_stacks=3)
        assert out["storage"]["modelPvc"]["name"] == "model-pvc"

    def test_multi_stack_preserves_explicit_override(self, renderer):
        """Explicit scenario overrides must not be rewritten."""
        values = self._base_values(label="qwen-07df-6b")
        values["inferenceExtension"]["monitoring"]["secretName"] = "custom-secret"
        out = renderer._resolve_per_stack_identity(values, total_stacks=2)
        assert out["inferenceExtension"]["monitoring"]["secretName"] == "custom-secret"
        # Unchanged paths still get the default rewrite
        assert out["downloadJob"]["name"] == "download-model-qwen-07df-6b"

    def test_missing_model_id_label_is_a_noop(self, renderer):
        """No label → nothing to suffix with; must not crash."""
        values = self._base_values(label="")
        out = renderer._resolve_per_stack_identity(values, total_stacks=5)
        # Names stay at their shipped defaults.
        assert out["storage"]["modelPvc"]["name"] == "model-pvc"
        assert out["downloadJob"]["name"] == "download-model"


class TestDeepMergeSharedBlock:
    """Merge order for the scenario `shared:` block — defaults < shared < stack."""

    def test_shared_overrides_defaults(self, renderer):
        defaults = {"gateway": {"className": "istio"}}
        shared = {"gateway": {"className": "agentgateway"}}
        stack = {"name": "pool-a"}

        # Simulate the merge chain in RenderPlans.eval / _process_stack
        merged = renderer.deep_merge(defaults, shared)
        merged = renderer.deep_merge(
            merged, {k: v for k, v in stack.items() if k != "name"}
        )
        assert merged["gateway"]["className"] == "agentgateway"

    def test_stack_overrides_shared(self, renderer):
        defaults = {"decode": {"replicas": 1}}
        shared = {"decode": {"replicas": 2}}
        stack = {"name": "pool-a", "decode": {"replicas": 5}}

        merged = renderer.deep_merge(defaults, shared)
        merged = renderer.deep_merge(
            merged, {k: v for k, v in stack.items() if k != "name"}
        )
        assert merged["decode"]["replicas"] == 5

    def test_shared_deep_merges_with_defaults(self, renderer):
        """Nested keys only present in one side must both survive the merge."""
        defaults = {"wva": {"enabled": False, "image": {"tag": "v0.5.0"}}}
        shared = {"wva": {"enabled": True}}
        stack = {"name": "pool-a"}

        merged = renderer.deep_merge(defaults, shared)
        merged = renderer.deep_merge(
            merged, {k: v for k, v in stack.items() if k != "name"}
        )
        # From shared
        assert merged["wva"]["enabled"] is True
        # From defaults — preserved because shared didn't touch image.tag
        assert merged["wva"]["image"]["tag"] == "v0.5.0"

    def test_treatment_overrides_shared_and_stack(self, renderer):
        """Full precedence chain: defaults → shared → stack → setup_overrides.

        Ensures DoE experiment treatments (applied as ``setup_overrides``)
        win over every earlier layer, so a sweep over a shared-block
        field actually takes effect on every stack.
        """
        defaults = {"decode": {"replicas": 1}}
        shared = {"decode": {"replicas": 2}}
        stack_a = {"name": "pool-a", "decode": {"replicas": 3}}
        stack_b = {"name": "pool-b"}  # inherits from shared
        treatment = {"decode": {"replicas": 5}}

        # Simulate per-stack merge chain for each stack with treatment applied
        # last (same order as RenderPlans._process_stack).
        for stack_cfg in (stack_a, stack_b):
            merged = renderer.deep_merge(defaults, shared)
            merged = renderer.deep_merge(
                merged, {k: v for k, v in stack_cfg.items() if k != "name"}
            )
            merged = renderer.deep_merge(merged, treatment)
            # Treatment always wins, no matter what shared/stack set.
            assert merged["decode"]["replicas"] == 5, (
                f"Stack {stack_cfg['name']}: treatment didn't win over layers"
            )


class TestSharedInfraStackIndex:
    """_resolve_shared_infra_stack_index — promote owner past standalone stacks."""

    @pytest.fixture
    def resolve(self):
        from llmdbenchmark.parser.render_plans import RenderPlans
        return RenderPlans._resolve_shared_infra_stack_index

    def test_all_modelservice_first_is_owner(self, resolve):
        siblings = [
            {"name": "pool-a", "standalone": False},
            {"name": "pool-b", "standalone": False},
        ]
        assert resolve(siblings) == 1

    def test_standalone_first_skips_to_modelservice(self, resolve):
        """Leading standalone stacks can't own shared infra — skip them."""
        siblings = [
            {"name": "pool-a", "standalone": True},
            {"name": "pool-b", "standalone": False},
        ]
        assert resolve(siblings) == 2

    def test_multiple_standalone_then_modelservice(self, resolve):
        siblings = [
            {"name": "a", "standalone": True},
            {"name": "b", "standalone": True},
            {"name": "c", "standalone": False},
        ]
        assert resolve(siblings) == 3

    def test_all_standalone_falls_back_to_one(self, resolve):
        """Edge case: every stack is standalone → no modelservice infra
        installs, so the index is moot but still deterministic."""
        siblings = [
            {"name": "a", "standalone": True},
            {"name": "b", "standalone": True},
        ]
        assert resolve(siblings) == 1


class TestHelmfileDeclaresRelease:
    """step_07_deploy_setup._helmfile_declares_release — YAML walk not substring."""

    @pytest.fixture
    def check(self):
        from llmdbenchmark.standup.steps.step_07_deploy_setup import DeploySetupStep
        return DeploySetupStep._helmfile_declares_release

    def _write(self, tmp_path, content):
        p = tmp_path / "helmfile.yaml"
        p.write_text(content, encoding="utf-8")
        return p

    def test_release_declared(self, check, tmp_path):
        p = self._write(tmp_path, """
releases:
  - name: infra-llmdbench
    chart: foo/bar
""")
        assert check(p, "infra-llmdbench") is True

    def test_release_absent(self, check, tmp_path):
        p = self._write(tmp_path, """
releases:
  - name: model-a-ms
    chart: foo/bar
""")
        assert check(p, "infra-llmdbench") is False

    def test_label_value_does_not_match(self, check, tmp_path):
        """Guards against the old substring-match false positive: a label
        value or similar 'name: X' occurrence must NOT be mistaken for a
        release declaration."""
        p = self._write(tmp_path, """
releases:
  - name: model-a-ms
    chart: foo/bar
    labels:
      name: infra-llmdbench
""")
        assert check(p, "infra-llmdbench") is False

    def test_empty_file(self, check, tmp_path):
        p = self._write(tmp_path, "")
        assert check(p, "infra-llmdbench") is False

    def test_missing_file(self, check, tmp_path):
        assert check(tmp_path / "nope.yaml", "infra-llmdbench") is False


class TestGatewayRoutesHealth:
    """BaseSmoketest._gateway_routes_health — skip /health when routing narrows."""

    @pytest.fixture
    def check(self):
        from llmdbenchmark.smoketests.base import BaseSmoketest
        return BaseSmoketest._gateway_routes_health

    def test_non_shared_always_routes(self, check):
        """Per-stack HTTPRoute is a single-backend catch-all — always routes /health."""
        assert check({}) is True
        assert check({"httpRoute": {"mode": "per-stack"}}) is True

    def test_shared_with_root_rewrite_routes(self, check):
        cfg = {"httpRoute": {"mode": "shared", "rewriteTo": "/"}}
        assert check(cfg) is True

    def test_shared_default_rewrite_routes(self, check):
        """rewriteTo absent → falls back to '/' → /health routes."""
        cfg = {"httpRoute": {"mode": "shared"}}
        assert check(cfg) is True

    def test_shared_with_v1_rewrite_does_not_route(self, check):
        """rewriteTo: /v1 narrows routing to /v1/* — /health won't match."""
        cfg = {"httpRoute": {"mode": "shared", "rewriteTo": "/v1"}}
        assert check(cfg) is False


class TestCliModelOverrideMultiStack:
    """_resolve_model — warn (once) when -m/--models is used in multi-stack."""

    @pytest.fixture
    def renderer(self):
        from llmdbenchmark.parser.render_plans import RenderPlans
        logger = MagicMock()
        logger.log_warning = MagicMock()
        logger.log_info = MagicMock()
        r = RenderPlans.__new__(RenderPlans)
        r.logger = logger
        r.cli_model = "meta-llama/Llama-3.2-3B"
        r.cli_stack_filter = []
        r.DEFAULT_NAMESPACE = "llmdbench"
        r._cli_model_multi_stack_warned = False
        return r

    def test_single_stack_no_warning(self, renderer):
        """-m on a single-stack scenario is a normal override — no warning."""
        values = {
            "model": {"name": "Qwen/Qwen3-32B"},
            "namespace": {"name": "ns"},
        }
        renderer._resolve_model(values, total_stacks=1)
        renderer.logger.log_warning.assert_not_called()

    def test_multi_stack_warns_once(self, renderer):
        """First stack emits the warning; subsequent stacks stay silent."""
        values = {
            "model": {"name": "Qwen/Qwen3-0.6B"},
            "namespace": {"name": "ns"},
        }
        renderer._resolve_model(values, total_stacks=2, stack_name="qwen3-06b")
        renderer._resolve_model(values, total_stacks=2, stack_name="llama-31-8b")
        # Warn exactly once — not once per stack.
        assert renderer.logger.log_warning.call_count == 1
        msg = renderer.logger.log_warning.call_args[0][0]
        assert "--stack" in msg
        assert "N copies of one model" in msg

    def test_multi_stack_still_overrides(self, renderer):
        """Warning doesn't block the override — current behavior preserved."""
        values = {
            "model": {"name": "Qwen/Qwen3-0.6B"},
            "namespace": {"name": "ns"},
        }
        out = renderer._resolve_model(values, total_stacks=2, stack_name="qwen3-06b")
        assert out["model"]["name"] == "meta-llama/Llama-3.2-3B"

    def test_no_cli_model_is_noop(self, renderer):
        """When -m is not set, _resolve_model must be a pure no-op."""
        renderer.cli_model = None
        values = {
            "model": {"name": "Qwen/Qwen3-0.6B"},
            "namespace": {"name": "ns"},
        }
        out = renderer._resolve_model(values, total_stacks=5)
        assert out["model"]["name"] == "Qwen/Qwen3-0.6B"
        renderer.logger.log_warning.assert_not_called()

    def test_stack_filter_scopes_override_to_matching_stack(self, renderer):
        """--stack NAME + -m MODEL → override only the named stack."""
        renderer.cli_stack_filter = ["qwen3-06b"]
        target_values = {"model": {"name": "orig"}, "namespace": {"name": "ns"}}
        out = renderer._resolve_model(
            target_values, total_stacks=2, stack_name="qwen3-06b"
        )
        assert out["model"]["name"] == "meta-llama/Llama-3.2-3B"
        renderer.logger.log_warning.assert_not_called()

    def test_stack_filter_leaves_non_matching_stack_alone(self, renderer):
        """--stack NAME + -m MODEL → sibling stacks preserve their model."""
        renderer.cli_stack_filter = ["qwen3-06b"]
        sibling_values = {"model": {"name": "orig-sibling"}, "namespace": {"name": "ns"}}
        out = renderer._resolve_model(
            sibling_values, total_stacks=2, stack_name="llama-31-8b"
        )
        assert out["model"]["name"] == "orig-sibling"
        renderer.logger.log_warning.assert_not_called()

    def test_broad_filter_still_warns(self, renderer):
        """--stack X,Y + -m MODEL (>1 in filter) → warn as if unscoped."""
        renderer.cli_stack_filter = ["qwen3-06b", "llama-31-8b"]
        values = {"model": {"name": "orig"}, "namespace": {"name": "ns"}}
        renderer._resolve_model(values, total_stacks=2, stack_name="qwen3-06b")
        renderer.logger.log_warning.assert_called_once()


class TestPrintEndpointsTable:
    """cli._print_endpoints_table — tolerates Path-typed specification_file."""

    def _mock_ctx(self, tmp_path, stacks_with_models):
        from unittest.mock import MagicMock
        ctx = MagicMock()
        ctx.namespace = "test-ns"
        rendered = []
        endpoints = {}
        for name, model, url in stacks_with_models:
            stack_dir = tmp_path / name
            stack_dir.mkdir(parents=True, exist_ok=True)
            (stack_dir / "config.yaml").write_text(
                f"model:\n  name: {model}\n", encoding="utf-8"
            )
            rendered.append(stack_dir)
            endpoints[name] = url
        ctx.rendered_stacks = rendered
        ctx.deployed_endpoints = endpoints
        return ctx

    def _capturing_logger(self):
        lines: list[str] = []
        logger = MagicMock = type("L", (), {})()
        logger.log_info = lambda msg: lines.append(str(msg))
        logger.log_warning = lambda msg: lines.append("WARN:" + str(msg))
        logger.log_plain = lambda msg: lines.append(str(msg))
        logger.line_break = lambda: lines.append("")
        return logger, lines

    def test_specification_file_as_posixpath(self, tmp_path, capsys):
        """Regression: PosixPath spec must not crash the table printer."""
        from pathlib import Path
        from llmdbenchmark.cli import _print_endpoints_table

        ctx = self._mock_ctx(tmp_path, [
            ("pool-a", "Qwen/Qwen3-0.6B", "http://gw:80/pool-a"),
        ])
        logger, lines = self._capturing_logger()

        # This is the real shape coming from RenderSpecification — a Path.
        args = type("A", (), {"specification_file": Path(
            "/abs/path/config/specification/guides/multi-model-wva.yaml.j2"
        )})()

        _print_endpoints_table(ctx, logger, args)  # must not raise

        # Table (logger) + copy-paste block (stdout) combined.
        combined = "\n".join(lines) + "\n" + capsys.readouterr().out
        assert "guides/multi-model-wva" in combined
        assert "http://gw:80/pool-a" in combined
        assert "Qwen/Qwen3-0.6B" in combined

    def test_specification_file_as_short_name(self, tmp_path, capsys):
        """Bare spec name (e.g. 'gpu') should flow through unchanged."""
        from llmdbenchmark.cli import _print_endpoints_table

        ctx = self._mock_ctx(tmp_path, [
            ("pool-a", "Qwen/Qwen3-0.6B", "http://gw:80/pool-a"),
        ])
        logger, lines = self._capturing_logger()
        args = type("A", (), {"specification_file": "guides/multi-model-wva"})()

        _print_endpoints_table(ctx, logger, args)
        combined = "\n".join(lines) + "\n" + capsys.readouterr().out
        assert "guides/multi-model-wva" in combined

    def test_no_endpoints_warns(self, tmp_path):
        """Empty endpoints dict → warn the user to stand up first."""
        from llmdbenchmark.cli import _print_endpoints_table

        from unittest.mock import MagicMock
        ctx = MagicMock()
        ctx.rendered_stacks = []
        ctx.deployed_endpoints = {}
        ctx.namespace = "test-ns"

        logger, lines = self._capturing_logger()
        args = type("A", (), {"specification_file": "guides/x"})()

        _print_endpoints_table(ctx, logger, args)
        assert any(
            "no endpoints" in line.lower() or "standup first" in line.lower()
            for line in lines
        )

    def test_copy_paste_block_goes_through_logger(self, tmp_path):
        """log_plain lines end up in every handler — including log files."""
        from llmdbenchmark.cli import _print_endpoints_table

        ctx = self._mock_ctx(tmp_path, [
            ("pool-a", "Qwen/Qwen3-0.6B", "http://gw:80/pool-a"),
        ])
        # Capturing logger records log_plain calls too so the assertion
        # below holds regardless of whether a real FileHandler is attached.
        plain_lines: list[str] = []
        logger, lines = self._capturing_logger()
        logger.log_plain = lambda msg: plain_lines.append(str(msg))

        args = type("A", (), {"specification_file": "guides/x"})()
        _print_endpoints_table(ctx, logger, args)

        # The command lines must flow through log_plain (not print),
        # so piped-to-file log output captures them.
        joined = "\n".join(plain_lines)
        assert "llmdbenchmark --spec" in joined
        assert "http://gw:80/pool-a" in joined
        assert "Qwen/Qwen3-0.6B" in joined


class TestRenderStackFilterValidation:
    """RenderPlans.eval() — --stack typos fail at render time, not later."""

    def _write_scenario(self, tmp_path, names):
        import yaml as _yaml
        stacks = [{"name": n, "model": {"name": f"m/{n}"}} for n in names]
        p = tmp_path / "scenario.yaml"
        p.write_text(_yaml.safe_dump({"scenario": stacks}), encoding="utf-8")
        return p

    def _renderer(self, tmp_path, scenario_path, cli_stack_filter):
        from llmdbenchmark.parser.render_plans import RenderPlans
        # Minimal instance; bypass __init__ and set only what eval() needs
        r = RenderPlans.__new__(RenderPlans)
        logger = MagicMock()
        logger.log_info = MagicMock()
        logger.log_warning = MagicMock()
        logger.log_error = MagicMock()
        logger.line_break = MagicMock()
        r.logger = logger
        r.defaults_file = tmp_path / "defaults.yaml"
        r.defaults_file.write_text("", encoding="utf-8")
        r.scenarios_file = scenario_path
        r.output_dir = tmp_path / "out"
        r.cli_stack_filter = cli_stack_filter
        return r

    def test_unknown_stack_fails_at_render(self, tmp_path):
        scenario = self._write_scenario(tmp_path, ["qwen3-06b", "llama-31-8b"])
        r = self._renderer(tmp_path, scenario, ["typo"])
        result = r.eval()
        assert result.has_errors
        err_msg = "\n".join(result.global_errors)
        assert "unknown stack" in err_msg.lower()
        assert "typo" in err_msg
        assert "qwen3-06b" in err_msg
        assert "llama-31-8b" in err_msg

    def test_valid_stack_passes_validation(self, tmp_path):
        scenario = self._write_scenario(tmp_path, ["qwen3-06b", "llama-31-8b"])
        r = self._renderer(tmp_path, scenario, ["qwen3-06b"])
        # Can't fully render without templates — but the unknown-stack
        # gate must NOT fire. Validate by checking global_errors absence
        # BEFORE template loading starts. We short-circuit by returning
        # early in the first stages; the filter validation is among the
        # first checks. If it DID fire, result would contain the "unknown"
        # error. Any later error (missing templates, etc.) is unrelated
        # to the filter and fine for this test's purpose.
        result = r.eval()
        for err in result.global_errors or []:
            assert "unknown stack" not in err.lower(), (
                f"Valid filter rejected: {err}"
            )

    def test_no_filter_is_noop(self, tmp_path):
        scenario = self._write_scenario(tmp_path, ["a", "b"])
        r = self._renderer(tmp_path, scenario, [])
        result = r.eval()
        for err in result.global_errors or []:
            assert "unknown stack" not in err.lower()


class TestParseSizeToGib:
    """step_04_model_namespace._parse_size_to_gib — warn-friendly parser."""

    @pytest.fixture
    def parse(self):
        from llmdbenchmark.standup.steps.step_04_model_namespace import (
            ModelNamespaceStep,
        )
        return ModelNamespaceStep._parse_size_to_gib

    def test_gibibytes(self, parse):
        assert parse("50Gi") == 50.0

    def test_mebibytes(self, parse):
        assert abs(parse("1024Mi") - 1.0) < 1e-9

    def test_tebibytes(self, parse):
        assert parse("1Ti") == 1024.0

    def test_si_gigabytes(self, parse):
        assert parse("1G") == 1.0

    def test_decimal(self, parse):
        assert parse("0.5Gi") == 0.5

    def test_empty_returns_zero(self, parse):
        assert parse("") == 0.0
        assert parse(None) == 0.0

    def test_unparseable_returns_zero(self, parse):
        assert parse("not-a-size") == 0.0


class TestRequiresPvcDownload:
    """Step 4 gating: PVC + download Job only for pvc/standalone stacks."""

    @pytest.fixture
    def step(self):
        """A ModelNamespaceStep instance — the method under test is pure."""
        from llmdbenchmark.standup.steps.step_04_model_namespace import (
            ModelNamespaceStep,
        )
        return ModelNamespaceStep()

    def test_pvc_protocol_needs_pvc(self, step):
        cfg = {"modelservice": {"uriProtocol": "pvc"}, "standalone": {"enabled": False}}
        assert step._requires_pvc_download(cfg) is True

    def test_standalone_mode_needs_pvc(self, step):
        """Standalone mode uses a PVC even when uriProtocol is non-pvc."""
        cfg = {"modelservice": {"uriProtocol": "hf"}, "standalone": {"enabled": True}}
        assert step._requires_pvc_download(cfg) is True

    def test_hf_protocol_skips_pvc(self, step):
        """hf uriProtocol means modelservice fetches at runtime — no PVC."""
        cfg = {"modelservice": {"uriProtocol": "hf"}, "standalone": {"enabled": False}}
        assert step._requires_pvc_download(cfg) is False

    def test_s3_protocol_skips_pvc(self, step):
        cfg = {"modelservice": {"uriProtocol": "s3"}, "standalone": {"enabled": False}}
        assert step._requires_pvc_download(cfg) is False

    def test_oci_protocol_skips_pvc(self, step):
        cfg = {"modelservice": {"uriProtocol": "oci"}, "standalone": {"enabled": False}}
        assert step._requires_pvc_download(cfg) is False


class TestComputeGatewayPathPrefix:
    """utilities.endpoint.compute_gateway_path_prefix."""

    def test_default_returns_empty(self):
        from llmdbenchmark.utilities.endpoint import compute_gateway_path_prefix
        cfg = {}
        assert compute_gateway_path_prefix(cfg, "pool-a") == ""

    def test_standalone_returns_empty(self):
        from llmdbenchmark.utilities.endpoint import compute_gateway_path_prefix
        cfg = {"httpRoute": {"mode": "shared", "pathPrefix": "/{stack.name}"}}
        assert compute_gateway_path_prefix(cfg, "pool-a", is_standalone=True) == ""

    def test_per_stack_mode_returns_empty(self):
        """httpRoute.mode: per-stack (or unset) → no prefix injection."""
        from llmdbenchmark.utilities.endpoint import compute_gateway_path_prefix
        cfg = {"httpRoute": {"mode": "per-stack", "pathPrefix": "/ignored"}}
        assert compute_gateway_path_prefix(cfg, "pool-a") == ""

    def test_shared_mode_substitutes_stack_name(self):
        from llmdbenchmark.utilities.endpoint import compute_gateway_path_prefix
        cfg = {"httpRoute": {"mode": "shared", "pathPrefix": "/{stack.name}"}}
        assert compute_gateway_path_prefix(cfg, "pool-a") == "/pool-a"

    def test_shared_mode_preserves_nested_prefix(self):
        from llmdbenchmark.utilities.endpoint import compute_gateway_path_prefix
        cfg = {"httpRoute": {"mode": "shared", "pathPrefix": "/{stack.name}/v1"}}
        assert compute_gateway_path_prefix(cfg, "pool-a") == "/pool-a/v1"

    def test_missing_stack_name_returns_empty(self):
        from llmdbenchmark.utilities.endpoint import compute_gateway_path_prefix
        cfg = {"httpRoute": {"mode": "shared", "pathPrefix": "/{stack.name}"}}
        assert compute_gateway_path_prefix(cfg, "") == ""


class TestParseEndpoint:
    """step_04_verify_model._parse_endpoint — host/port/prefix extraction."""

    @pytest.fixture
    def parse(self):
        from llmdbenchmark.run.steps.step_04_verify_model import VerifyModelStep
        return VerifyModelStep._parse_endpoint

    def test_plain_endpoint(self, parse):
        assert parse("http://10.0.0.1:80") == ("10.0.0.1", "80", "")

    def test_https_default_port(self, parse):
        assert parse("https://gw.example.com") == ("gw.example.com", "443", "")

    def test_http_default_port(self, parse):
        assert parse("http://gw.example.com") == ("gw.example.com", "80", "")

    def test_endpoint_with_prefix(self, parse):
        assert parse("http://10.0.0.1:80/pool-a") == ("10.0.0.1", "80", "/pool-a")

    def test_endpoint_with_multi_segment_prefix(self, parse):
        assert parse("http://10.0.0.1:80/pool-a/v1") == ("10.0.0.1", "80", "/pool-a/v1")

    def test_trailing_slash_stripped(self, parse):
        assert parse("http://10.0.0.1:80/pool-a/") == ("10.0.0.1", "80", "/pool-a")

    def test_root_only_is_empty_prefix(self, parse):
        assert parse("http://10.0.0.1:80/") == ("10.0.0.1", "80", "")
