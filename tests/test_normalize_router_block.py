"""Tests for `RenderPlans._normalize_router_block`.

The router block in scenarios uses the llm-d-router chart's native `router.*`
keys directly. The 12_router-values.yaml.j2 template is a pass-through; all
benchmark-specific transformations (HF token env injection, zmq port
expansion, image resolution, modelServers defaults, providerConfig lift,
epponly port 80, tokenizer.modelName fallback) happen here.

The most important contract this module pins: **a scenario can set any
chart field under `router:` and it survives to the rendered YAML.** A
regression here breaks override-by-treatment, so the override-survival
test below is exhaustive across the upstream chart's shape.

Upstream chart values reference:
  https://github.com/llm-d/llm-d-router/blob/main/config/charts/routerlib/values.yaml
  https://github.com/llm-d/llm-d-router/blob/main/config/charts/llm-d-router-standalone/values.yaml
  https://github.com/llm-d/llm-d-router/blob/main/config/charts/llm-d-router-gateway/values.yaml
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from llmdbenchmark.parser.render_plans import RenderPlans


@pytest.fixture
def normalize():
    r = RenderPlans.__new__(RenderPlans)
    r.logger = MagicMock()
    return r._normalize_router_block.__get__(r)


@pytest.fixture
def toyaml():
    return RenderPlans._toyaml_filter


# ---------------------------------------------------------------------------
# YAML rendering: multi-line strings use literal block style (`|`)
# ---------------------------------------------------------------------------


class TestToYamlLiteralBlock:
    """Pin that multi-line strings (e.g. embedded ConfigMap content like
    ``router.epp.pluginsCustomConfig.<filename>``) render as YAML literal
    blocks (``|``) instead of double-quoted scalars with ``\\n`` escapes.

    Regression guard: the pre-router-migration template hand-emitted
    ``: |`` for ``pluginsCustomConfig`` so the rendered artifact stayed
    readable. The new pass-through template relies on the toyaml filter
    to preserve that shape -- without the literal-block representer,
    every plugin config blob collapses to a single double-quoted line
    full of escape sequences, which makes captured router-values.yaml
    artifacts unreviewable (despite being functionally equivalent --
    Helm's toYaml re-serializes server-side either way)."""

    def test_multiline_string_uses_literal_block(self, toyaml):
        value = {
            "pluginsCustomConfig": {
                "custom.yaml": (
                    "apiVersion: llm-d.ai/v1alpha1\n"
                    "kind: EndpointPickerConfig\n"
                    "plugins:\n"
                    "- type: queue-scorer\n"
                ),
            },
        }
        out = toyaml(value)
        assert "custom.yaml: |" in out
        assert "\\n" not in out
        assert "apiVersion: llm-d.ai/v1alpha1" in out
        assert "kind: EndpointPickerConfig" in out

    def test_single_line_string_stays_unquoted(self, toyaml):
        """Single-line strings should NOT be forced into literal block style."""
        value = {"image": {"tag": "v0.0.1", "repository": "ghcr.io/llm-d/x"}}
        out = toyaml(value)
        assert "tag: v0.0.1" in out
        assert "tag: |" not in out

    def test_multiline_string_roundtrips(self, toyaml):
        """Output parses back to the same string (modulo trailing-newline
        normalization). YAML's ``|`` style strips a single trailing
        newline; that's fine for ConfigMap embedding."""
        import yaml as _yaml

        original = "line one\nline two\n  indented\nline four"
        value = {"key": original}
        out = toyaml(value)
        parsed = _yaml.safe_load(out)
        assert parsed["key"] == original


# ---------------------------------------------------------------------------
# Override survival: ANY field a scenario sets under `router:` must reach
# the rendered output unchanged.
# ---------------------------------------------------------------------------


class TestOverrideSurvival:
    """Catch the silent-drop class of bug. Each chart field a user might
    override must round-trip through _normalize_router_block."""

    def _base(self) -> dict:
        return {
            "gateway": {"className": "istio"},
            "model": {"name": "meta-llama/Llama-3.1-8B"},
            "model_id_label": "llama-31-8b",
            "labels": {"inferenceServing": "true"},
            "decode": {"vllm": {"servicePort": 8000}},
            "images": {
                "routerEndpointPicker": {
                    "repository": "ghcr.io/llm-d/llm-d-router-endpoint-picker-dev",
                    "tag": "v0.0.1",
                    "pullPolicy": "IfNotPresent",
                },
            },
            "huggingface": {"enabled": False},
        }

    @pytest.mark.parametrize(
        "path,value",
        [
            # EPP fields the old enumerated template dropped
            ("router.epp.affinity", {"nodeAffinity": {"required": "x"}}),
            ("router.epp.tolerations", [{"key": "gpu", "operator": "Exists"}]),
            ("router.epp.parsers", ["openai-parser", "anthropic-parser"]),
            ("router.epp.grpcHealthPort", 9003),
            ("router.epp.metricsDataSource.scheme", "https"),
            ("router.epp.metricsDataSource.path", "/custom/metrics"),
            ("router.epp.metricsDataSource.insecureSkipVerify", False),
            ("router.epp.nodeSelector.kubernetes.io/arch", "amd64"),
            ("router.epp.serviceAccount.name", "my-sa"),
            ("router.epp.podLabels.app", "my-epp"),
            ("router.epp.podAnnotations.sidecar.istio.io/inject", "false"),
            ("router.epp.securityContext.runAsNonRoot", True),
            ("router.epp.securityContext.runAsUser", 65532),
            ("router.epp.priorityClassName", "system-cluster-critical"),
            ("router.epp.terminationGracePeriodSeconds", 30),
            # Tokenizer fields the old template dropped wholesale
            ("router.tokenizer.image.registry", "my-registry.io"),
            ("router.tokenizer.image.repository", "vllm/tok"),
            ("router.tokenizer.image.tag", "v1.2"),
            ("router.tokenizer.image.pullPolicy", "Always"),
            ("router.tokenizer.port", 9000),
            ("router.tokenizer.resources.requests.cpu", "2"),
            ("router.tokenizer.resources.requests.memory", "4Gi"),
            # Tracing -- entire block was dropped before
            ("router.tracing.enabled", True),
            ("router.tracing.otelExporterEndpoint", "http://otel-collector:4317"),
            ("router.tracing.sampling.sampler", "always_on"),
            ("router.tracing.sampling.samplerArg", "0.5"),
            # InferencePool fields beyond failureMode
            ("router.inferencePool.create", True),
            ("router.inferencePool.failureMode", "FailClose"),
            # ModelServers fields beyond targetPorts/matchLabels
            ("router.modelServers.type", "sglang"),
            ("router.modelServers.protocol", "grpc"),
            # Monitoring fields beyond prometheus.enabled
            ("router.monitoring.provider.name", "gmp"),
            ("router.monitoring.provider.gmp.autopilot", True),
            ("router.monitoring.prometheus.extraLabels.team", "inference"),
            # Standalone-only proxy fields beyond args/resources
            ("router.proxy.enabled", True),
            ("router.proxy.proxyType", "envoy"),
            ("router.proxy.mode", "service"),
            ("router.proxy.replicas", 3),
            ("router.proxy.failOpen", False),
            # LatencyPredictor: entire block beyond `enabled` was lossy
            ("router.latencyPredictor.enabled", True),
            ("router.latencyPredictor.trainingServer.port", 9999),
            ("router.latencyPredictor.predictionServers.count", 4),
            ("router.latencyPredictor.eppEnv.LATENCY_MAX_SAMPLE_SIZE", "20000"),
        ],
    )
    def test_user_set_field_survives(self, normalize, path, value):
        values = self._base()
        cur = values
        parts = path.split(".")
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value

        out = normalize(values)

        cur = out
        for part in parts:
            assert isinstance(cur, dict), (
                f"path '{path}' broke at '{part}'; parent is {type(cur).__name__}"
            )
            assert part in cur, f"path '{path}' was dropped at '{part}'"
            cur = cur[part]
        assert cur == value, f"path '{path}' was mutated: {value!r} -> {cur!r}"

    def test_router_inferenceObjectives_list_survives(self, normalize):
        """List fields shouldn't be erased by setdefault dict logic."""
        values = self._base()
        values["router"] = {
            "inferenceObjectives": [
                {"name": "high", "priority": 5},
                {"name": "low", "priority": 1},
            ],
        }
        out = normalize(values)
        assert out["router"]["inferenceObjectives"] == [
            {"name": "high", "priority": 5},
            {"name": "low", "priority": 1},
        ]


# ---------------------------------------------------------------------------
# HF token env injection
# ---------------------------------------------------------------------------


class TestHfTokenInjection:
    def test_no_hf_when_disabled(self, normalize):
        values = {"huggingface": {"enabled": False}}
        out = normalize(values)
        assert out["router"]["epp"].get("env") is None

    def test_hf_appended_when_enabled(self, normalize):
        values = {
            "huggingface": {
                "enabled": True,
                "secretName": "my-secret",
                "tokenKey": "MY_TOKEN",
            },
        }
        out = normalize(values)
        env = out["router"]["epp"]["env"]
        assert len(env) == 1
        assert env[0]["name"] == "HF_TOKEN"
        assert env[0]["valueFrom"]["secretKeyRef"]["name"] == "my-secret"
        assert env[0]["valueFrom"]["secretKeyRef"]["key"] == "MY_TOKEN"

    def test_user_env_preserved_when_hf_appended(self, normalize):
        """User-supplied env entries must NOT be dropped when HF_TOKEN is added."""
        values = {
            "huggingface": {"enabled": True},
            "router": {
                "epp": {
                    "env": [
                        {"name": "CUSTOM", "value": "x"},
                        {"name": "OTHER", "value": "y"},
                    ],
                },
            },
        }
        out = normalize(values)
        env = out["router"]["epp"]["env"]
        names = [e["name"] for e in env]
        assert "CUSTOM" in names
        assert "OTHER" in names
        assert "HF_TOKEN" in names

    def test_hf_idempotent(self, normalize):
        """User already added HF_TOKEN -> normalize doesn't duplicate."""
        values = {
            "huggingface": {"enabled": True},
            "router": {
                "epp": {
                    "env": [
                        {
                            "name": "HF_TOKEN",
                            "valueFrom": {
                                "secretKeyRef": {"name": "custom", "key": "token"}
                            },
                        }
                    ],
                },
            },
        }
        out = normalize(values)
        env = out["router"]["epp"]["env"]
        hf_entries = [e for e in env if e["name"] == "HF_TOKEN"]
        assert len(hf_entries) == 1
        assert hf_entries[0]["valueFrom"]["secretKeyRef"]["name"] == "custom"


# ---------------------------------------------------------------------------
# zmqPort expansion (benchmark-only knob -> chart-native port arrays)
# ---------------------------------------------------------------------------


class TestZmqPortExpansion:
    def test_expands_into_both_arrays(self, normalize):
        values = {"router": {"epp": {"zmqPort": 5557}}}
        out = normalize(values)
        assert "zmqPort" not in out["router"]["epp"]
        container_ports = out["router"]["epp"]["extraContainerPorts"]
        assert any(
            p["name"] == "zmq" and p["containerPort"] == 5557 for p in container_ports
        )
        service_ports = out["router"]["extraServicePorts"]
        assert any(
            p["name"] == "zmq" and p["port"] == 5557 and p["targetPort"] == 5557
            for p in service_ports
        )

    def test_user_extraContainerPorts_preserved(self, normalize):
        values = {
            "router": {
                "epp": {
                    "zmqPort": 5557,
                    "extraContainerPorts": [
                        {"name": "metrics", "containerPort": 8200, "protocol": "TCP"},
                    ],
                },
            },
        }
        out = normalize(values)
        names = [p["name"] for p in out["router"]["epp"]["extraContainerPorts"]]
        assert "metrics" in names
        assert "zmq" in names

    def test_user_extraServicePorts_preserved(self, normalize):
        values = {
            "router": {
                "epp": {"zmqPort": 5557},
                "extraServicePorts": [
                    {
                        "name": "custom",
                        "port": 9999,
                        "targetPort": 9999,
                        "protocol": "TCP",
                    },
                ],
            },
        }
        out = normalize(values)
        names = [p["name"] for p in out["router"]["extraServicePorts"]]
        assert "custom" in names
        assert "zmq" in names


# ---------------------------------------------------------------------------
# epponly: port 80 -> 8081 auto-add
# ---------------------------------------------------------------------------


class TestEpponlyPort:
    def test_epponly_adds_http_service_port(self, normalize):
        values = {"gateway": {"className": "epponly"}}
        out = normalize(values)
        service_ports = out["router"].get("extraServicePorts") or []
        http = [p for p in service_ports if p.get("name") == "http"]
        assert len(http) == 1
        assert http[0]["port"] == 80
        assert http[0]["targetPort"] == 8081

    def test_non_epponly_does_not_add_port(self, normalize):
        for cls in ("istio", "gke", "agentgateway"):
            values = {"gateway": {"className": cls}}
            out = normalize(values)
            ports = out["router"].get("extraServicePorts") or []
            assert not any(p.get("name") == "http" for p in ports), (
                f"unexpected http port added for gateway.className={cls}"
            )


# ---------------------------------------------------------------------------
# Tokenizer modelName fallback
# ---------------------------------------------------------------------------


class TestTokenizerModelName:
    def test_fallback_to_model_name(self, normalize):
        values = {
            "model": {"name": "Qwen/Qwen3-32B"},
            "router": {"tokenizer": {"enabled": True}},
        }
        out = normalize(values)
        assert out["router"]["tokenizer"]["modelName"] == "Qwen/Qwen3-32B"

    def test_user_modelName_preserved(self, normalize):
        values = {
            "model": {"name": "Qwen/Qwen3-32B"},
            "router": {"tokenizer": {"enabled": True, "modelName": "custom/Model"}},
        }
        out = normalize(values)
        assert out["router"]["tokenizer"]["modelName"] == "custom/Model"

    def test_no_fallback_when_disabled(self, normalize):
        values = {
            "model": {"name": "Qwen/Qwen3-32B"},
            "router": {"tokenizer": {"enabled": False}},
        }
        out = normalize(values)
        assert "modelName" not in out["router"]["tokenizer"]


# ---------------------------------------------------------------------------
# EPP image resolution
# ---------------------------------------------------------------------------


class TestEppImage:
    def test_chart_native_image_from_catalog(self, normalize):
        values = {
            "images": {
                "routerEndpointPicker": {
                    "repository": "ghcr.io/llm-d/llm-d-router-endpoint-picker-dev",
                    "tag": "v0.0.1",
                    "pullPolicy": "IfNotPresent",
                },
            },
        }
        out = normalize(values)
        img = out["router"]["epp"]["image"]
        assert img["registry"] == "ghcr.io/llm-d"
        assert img["repository"] == "llm-d-router-endpoint-picker-dev"
        assert img["tag"] == "v0.0.1"
        assert img["pullPolicy"] == "IfNotPresent"

    def test_explicit_router_epp_image_full_override_wins(self, normalize):
        """User can fully bypass the catalog by setting all four image fields."""
        values = {
            "images": {
                "routerEndpointPicker": {"repository": "ghcr.io/llm-d/x", "tag": "v1"},
            },
            "router": {
                "epp": {
                    "image": {
                        "registry": "explicit.io",
                        "repository": "my-img",
                        "tag": "main",
                        "pullPolicy": "Never",
                    },
                },
            },
        }
        out = normalize(values)
        assert out["router"]["epp"]["image"]["registry"] == "explicit.io"
        assert out["router"]["epp"]["image"]["tag"] == "main"
        assert out["router"]["epp"]["image"]["pullPolicy"] == "Never"

    def test_partial_router_epp_image_override_merges_with_catalog(self, normalize):
        """Regression guard: a tag-only override on router.epp.image must
        merge with the catalog so the rendered helm values still carry a
        non-empty registry and repository. The chart's _deployment.yaml
        renders ``{{ registry }}/{{ repository }}:{{ tag }}``, so a
        partial override losing the other fields would render
        ``/:custom-tag`` which is an invalid image reference."""
        values = {
            "images": {
                "routerEndpointPicker": {
                    "repository": "ghcr.io/llm-d/llm-d-router-endpoint-picker-dev",
                    "tag": "v0.0.1",
                    "pullPolicy": "IfNotPresent",
                },
            },
            "router": {"epp": {"image": {"tag": "my-custom-tag"}}},
        }
        out = normalize(values)
        img = out["router"]["epp"]["image"]
        assert img["tag"] == "my-custom-tag"
        assert img["registry"] == "ghcr.io/llm-d"
        assert img["repository"] == "llm-d-router-endpoint-picker-dev"
        assert img["pullPolicy"] == "IfNotPresent"


# ---------------------------------------------------------------------------
# modelServers defaults
# ---------------------------------------------------------------------------


class TestModelServersDefaults:
    def test_matchLabels_default_from_labels_and_model_id(self, normalize):
        values = {
            "labels": {"inferenceServing": "true"},
            "model_id_label": "qwen3-32b-xyz",
        }
        out = normalize(values)
        labels = out["router"]["modelServers"]["matchLabels"]
        assert labels["llm-d.ai/inferenceServing"] == "true"
        assert labels["llm-d.ai/model"] == "qwen3-32b-xyz"

    def test_user_matchLabels_preserved(self, normalize):
        values = {
            "labels": {"inferenceServing": "true"},
            "model_id_label": "x",
            "router": {"modelServers": {"matchLabels": {"app": "vllm-custom"}}},
        }
        out = normalize(values)
        assert out["router"]["modelServers"]["matchLabels"] == {"app": "vllm-custom"}

    def test_targetPorts_default_from_decode(self, normalize):
        values = {"decode": {"vllm": {"servicePort": 8000}}}
        out = normalize(values)
        assert out["router"]["modelServers"]["targetPorts"] == [{"number": 8000}]

    def test_user_targetPorts_preserved(self, normalize):
        values = {
            "decode": {"vllm": {"servicePort": 8000}},
            "router": {"modelServers": {"targetPorts": [{"number": 9999}]}},
        }
        out = normalize(values)
        assert out["router"]["modelServers"]["targetPorts"] == [{"number": 9999}]


# ---------------------------------------------------------------------------
# Flags / verbosity
# ---------------------------------------------------------------------------


class TestFlagsAndVerbosity:
    def test_user_flags_win(self, normalize):
        values = {"router": {"epp": {"verbosity": "1", "flags": {"v": "5"}}}}
        out = normalize(values)
        assert out["router"]["epp"]["flags"] == {"v": "5"}
        assert "verbosity" not in out["router"]["epp"]

    def test_verbosity_materializes_into_flags(self, normalize):
        values = {"router": {"epp": {"verbosity": "3"}}}
        out = normalize(values)
        assert out["router"]["epp"]["flags"] == {"v": "3"}
        assert "verbosity" not in out["router"]["epp"]

    def test_metrics_scrape_bumps_v_to_4(self, normalize):
        values = {
            "monitoring": {"metricsScrapeEnabled": True},
            "router": {"epp": {"verbosity": "1"}},
        }
        out = normalize(values)
        assert out["router"]["epp"]["flags"] == {"v": "4"}


# ---------------------------------------------------------------------------
# providerConfig lift to root-level provider.<gw_class>
# ---------------------------------------------------------------------------


class TestProviderConfigLift:
    def test_istio_lifts_providerConfig_to_root(self, normalize):
        values = {
            "gateway": {"className": "istio"},
            "router": {
                "inferencePool": {
                    "providerConfig": {
                        "destinationRule": {"host": "x", "trafficPolicy": {}}
                    },
                },
            },
        }
        out = normalize(values)
        assert out["provider"]["name"] == "istio"
        assert out["provider"]["istio"]["destinationRule"]["host"] == "x"
        assert "providerConfig" not in out["router"]["inferencePool"]

    def test_gke_lifts_providerConfig_to_root(self, normalize):
        values = {
            "gateway": {"className": "gke"},
            "router": {
                "inferencePool": {
                    "providerConfig": {"some": "gke-config"},
                },
            },
        }
        out = normalize(values)
        assert out["provider"]["name"] == "gke"
        assert out["provider"]["gke"] == {"some": "gke-config"}

    def test_epponly_does_not_lift(self, normalize):
        """epponly uses the standalone chart, which doesn't have a provider block."""
        values = {
            "gateway": {"className": "epponly"},
            "router": {
                "inferencePool": {"providerConfig": {"x": 1}},
            },
        }
        out = normalize(values)
        assert "provider" not in out
        assert "providerConfig" not in out["router"]["inferencePool"]

    def test_existing_root_provider_block_merges_with_lift(self, normalize):
        """When the user sets a root-level `provider:` AND providerConfig
        contributes a disjoint key, both survive."""
        values = {
            "gateway": {"className": "istio"},
            "provider": {"istio": {"destinationRule": {"host": "user-host"}}},
            "router": {
                "inferencePool": {
                    "providerConfig": {
                        "destinationRule": {"trafficPolicy": {"new": "stuff"}},
                    },
                },
            },
        }
        out = normalize(values)
        assert out["provider"]["istio"]["destinationRule"]["host"] == "user-host"
        assert out["provider"]["istio"]["destinationRule"]["trafficPolicy"] == {
            "new": "stuff"
        }

    def test_root_provider_wins_on_conflict_with_providerConfig(self, normalize):
        """When the user sets BOTH a root-level `provider.<gw>.X` and a
        conflicting `router.inferencePool.providerConfig.X`, the
        root-level explicit value is the more specific intent and must
        win. This guards against `_resolve_inference_pool_host`'s
        auto-fill clobbering a user-supplied host."""
        values = {
            "gateway": {"className": "istio"},
            "provider": {"istio": {"destinationRule": {"host": "user-explicit-host"}}},
            "router": {
                "inferencePool": {
                    "providerConfig": {
                        "destinationRule": {"host": "auto-resolved-host"},
                    },
                },
            },
        }
        out = normalize(values)
        assert (
            out["provider"]["istio"]["destinationRule"]["host"] == "user-explicit-host"
        )
