"""Unit tests for benchmark report conversion layer."""

import unittest

from benchmark_report.schema_v0_2 import Component as ReportComponent
from benchmark_report.schema_v0_2_components import HostType

from llm_d_stack_discovery.models.components import (
    Component,
    ComponentMetadata,
    DiscoveryResult,
)
from llm_d_stack_discovery.output.benchmark_report import (
    discovery_to_stack_components,
    discovery_to_scenario_stack,
    _cfg_id,
    _extract_vllm_serve_tokens,
    _resolve_env_ref,
    _resolve_model_name,
)


def _make_vllm_component(
    name="vllm-pod-0",
    namespace="default",
    model="meta-llama/Llama-2-70b",
    role=HostType.REPLICA,
    tp=8,
    pp=1,
    dp=1,
    ep=1,
    gpu_model="A100-80GB",
    gpu_count=8,
    tool_version="v0.4.0",
):
    """Helper to create a vLLM discovery component."""
    return Component(
        metadata=ComponentMetadata(
            namespace=namespace,
            name=name,
            kind="Pod",
            labels={"app": "vllm"},
        ),
        tool="vllm",
        tool_version=tool_version,
        native={
            "vllm_config": {
                "model": model,
                "tensor_parallel_size": tp,
                "pipeline_parallel_size": pp,
                "data_parallel_size": dp,
                "expert_parallel_size": ep,
            },
            "role": role,
            "gpu": {
                "model": gpu_model,
                "count": gpu_count,
            },
            "args": [
                "--model",
                model,
                "--tensor-parallel-size",
                str(tp),
                "--enable-prefix-caching",
            ],
            "environment": [
                {"name": "VLLM_USE_V1", "value": "true"},
            ],
        },
    )


def _make_generic_component(
    name="my-service",
    namespace="default",
    kind="Service",
    tool="gateway-api",
    tool_version="v1.0.0",
):
    """Helper to create a generic discovery component."""
    return Component(
        metadata=ComponentMetadata(
            namespace=namespace,
            name=name,
            kind=kind,
            labels={},
        ),
        tool=tool,
        tool_version=tool_version,
        native={"spec": {"type": "ClusterIP"}},
    )


class TestVLLMConversion(unittest.TestCase):
    """Test vLLM component to InferenceEngine conversion."""

    def test_single_vllm_to_inference_engine(self):
        """Test converting a single vLLM pod to InferenceEngine."""
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[_make_vllm_component()],
        )

        dicts = discovery_to_stack_components(result)

        self.assertEqual(len(dicts), 1)
        d = dicts[0]
        self.assertEqual(d["standardized"]["kind"], "inference_engine")
        self.assertEqual(d["standardized"]["tool"], "vllm")
        self.assertEqual(d["standardized"]["tool_version"], "v0.4.0")
        self.assertEqual(d["standardized"]["role"], HostType.REPLICA)
        self.assertEqual(d["standardized"]["replicas"], 1)
        self.assertEqual(d["standardized"]["model"]["name"], "meta-llama/Llama-2-70b")
        self.assertEqual(d["standardized"]["accelerator"]["model"], "A100-80GB")
        self.assertEqual(d["standardized"]["accelerator"]["count"], 8)
        self.assertEqual(d["standardized"]["accelerator"]["parallelism"]["tp"], 8)
        self.assertEqual(d["standardized"]["accelerator"]["parallelism"]["pp"], 1)

    def test_prefill_role(self):
        """Test vLLM with prefill role."""
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[_make_vllm_component(role=HostType.PREFILL)],
        )
        dicts = discovery_to_stack_components(result)
        self.assertEqual(dicts[0]["standardized"]["role"], HostType.PREFILL)

    def test_decode_role(self):
        """Test vLLM with decode role."""
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[_make_vllm_component(role=HostType.DECODE)],
        )
        dicts = discovery_to_stack_components(result)
        self.assertEqual(dicts[0]["standardized"]["role"], HostType.DECODE)

    def test_native_section(self):
        """Test that native section has args, envars, and config."""
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[_make_vllm_component()],
        )
        dicts = discovery_to_stack_components(result)
        native = dicts[0]["native"]
        self.assertIn("args", native)
        self.assertIn("envars", native)
        self.assertIn("config", native)
        # Check that args were parsed from the CLI
        self.assertEqual(native["args"]["model"], "meta-llama/Llama-2-70b")
        # Boolean flags have None value
        self.assertIsNone(native["args"]["enable-prefix-caching"])
        # Check envars
        self.assertEqual(native["envars"]["VLLM_USE_V1"], "true")
        # config is None (vllm_config is derived, not a config file)
        self.assertIsNone(native["config"])


class TestGenericConversion(unittest.TestCase):
    """Test generic component conversion."""

    def test_generic_component(self):
        """Test converting a non-vLLM component to Generic."""
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[_make_generic_component()],
        )

        dicts = discovery_to_stack_components(result)

        self.assertEqual(len(dicts), 1)
        d = dicts[0]
        self.assertEqual(d["standardized"]["kind"], "generic")
        self.assertEqual(d["standardized"]["tool"], "gateway-api")
        self.assertEqual(d["standardized"]["tool_version"], "v1.0.0")
        self.assertEqual(d["metadata"]["label"], "Service/default/my-service")

    def test_generic_without_tool(self):
        """Test generic component without explicit tool uses kind."""
        comp = _make_generic_component(tool=None)
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[comp],
        )
        dicts = discovery_to_stack_components(result)
        self.assertEqual(dicts[0]["standardized"]["tool"], "service")


class TestReplicaAggregation(unittest.TestCase):
    """Test replica aggregation for identical vLLM pods."""

    def test_three_identical_pods_become_three_replicas(self):
        """Test that 3 identical vLLM pods are aggregated to replicas=3."""
        pods = [_make_vllm_component(name=f"vllm-pod-{i}") for i in range(3)]
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=pods,
        )

        dicts = discovery_to_stack_components(result)

        self.assertEqual(len(dicts), 1)
        self.assertEqual(dicts[0]["standardized"]["replicas"], 3)

    def test_different_roles_are_separate(self):
        """Test that pods with different roles are not aggregated."""
        prefill = _make_vllm_component(name="prefill-0", role=HostType.PREFILL)
        decode = _make_vllm_component(name="decode-0", role=HostType.DECODE)

        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[prefill, decode],
        )

        dicts = discovery_to_stack_components(result)

        self.assertEqual(len(dicts), 2)
        roles = {d["standardized"]["role"] for d in dicts}
        self.assertEqual(roles, {HostType.PREFILL, HostType.DECODE})
        for d in dicts:
            self.assertEqual(d["standardized"]["replicas"], 1)

    def test_different_models_are_separate(self):
        """Test that pods with different models are not aggregated."""
        pod_a = _make_vllm_component(name="pod-a", model="model-a")
        pod_b = _make_vllm_component(name="pod-b", model="model-b")

        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[pod_a, pod_b],
        )

        dicts = discovery_to_stack_components(result)
        self.assertEqual(len(dicts), 2)

    def test_mixed_vllm_and_generic(self):
        """Test that vLLM and generic components coexist."""
        vllm = _make_vllm_component()
        svc = _make_generic_component()

        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[vllm, svc],
        )

        dicts = discovery_to_stack_components(result)
        self.assertEqual(len(dicts), 2)

        kinds = {d["standardized"]["kind"] for d in dicts}
        self.assertEqual(kinds, {"inference_engine", "generic"})


class TestCfgId(unittest.TestCase):
    """Test cfg_id determinism."""

    def test_same_config_same_hash(self):
        """Test that same configuration produces same hash."""
        std = {"kind": "inference_engine", "tool": "vllm"}
        native = {"args": {"model": "llama"}}

        id1 = _cfg_id(std, native)
        id2 = _cfg_id(std, native)
        self.assertEqual(id1, id2)

    def test_different_config_different_hash(self):
        """Test that different configuration produces different hash."""
        std = {"kind": "inference_engine", "tool": "vllm"}
        native_a = {"args": {"model": "llama"}}
        native_b = {"args": {"model": "mistral"}}

        id_a = _cfg_id(std, native_a)
        id_b = _cfg_id(std, native_b)
        self.assertNotEqual(id_a, id_b)


class TestPydanticValidation(unittest.TestCase):
    """Test that generated dicts pass Pydantic validation."""

    def test_vllm_validates_as_component(self):
        """Test vLLM dict validates as a schema_v0_2.Component."""
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[_make_vllm_component()],
        )

        dicts = discovery_to_stack_components(result)
        # This should not raise
        component = ReportComponent(**dicts[0])
        self.assertEqual(component.standardized.kind, "inference_engine")
        self.assertEqual(component.standardized.role, HostType.REPLICA)
        self.assertEqual(component.standardized.replicas, 1)

    def test_generic_validates_as_component(self):
        """Test generic dict validates as a schema_v0_2.Component."""
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[_make_generic_component()],
        )

        dicts = discovery_to_stack_components(result)
        # This should not raise
        component = ReportComponent(**dicts[0])
        self.assertEqual(component.standardized.kind, "generic")

    def test_discovery_to_scenario_stack(self):
        """Test the high-level conversion produces valid Pydantic objects."""
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[
                _make_vllm_component(name="p-0", role=HostType.PREFILL),
                _make_vllm_component(name="p-1", role=HostType.PREFILL),
                _make_vllm_component(name="d-0", role=HostType.DECODE),
                _make_generic_component(),
            ],
        )

        stack = discovery_to_scenario_stack(result)
        self.assertEqual(len(stack), 3)  # 2 prefill grouped, 1 decode, 1 generic

        # Find the prefill component
        prefill = [
            c
            for c in stack
            if hasattr(c.standardized, "role")
            and c.standardized.role == HostType.PREFILL
        ]
        self.assertEqual(len(prefill), 1)
        self.assertEqual(prefill[0].standardized.replicas, 2)


class TestDpLocalAndWorkers(unittest.TestCase):
    """Test dp_local and workers in parallelism dict."""

    def test_dp_local_and_workers_in_parallelism(self):
        """Verify dp_local/workers appear in standardized.accelerator.parallelism."""
        comp = _make_vllm_component()
        comp.native["vllm_config"]["data_local_parallel_size"] = 2
        comp.native["vllm_config"]["num_workers"] = 4

        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[comp],
        )

        dicts = discovery_to_stack_components(result)
        parallelism = dicts[0]["standardized"]["accelerator"]["parallelism"]
        self.assertEqual(parallelism["dp_local"], 2)
        self.assertEqual(parallelism["workers"], 4)

    def test_dp_local_defaults_to_one(self):
        """When absent, dp_local and workers default to 1."""
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[_make_vllm_component()],
        )

        dicts = discovery_to_stack_components(result)
        parallelism = dicts[0]["standardized"]["accelerator"]["parallelism"]
        self.assertEqual(parallelism["dp_local"], 1)
        self.assertEqual(parallelism["workers"], 1)

    def test_grouping_includes_dp_local_and_workers(self):
        """Pods differing only in dp_local are NOT grouped together."""
        pod_a = _make_vllm_component(name="pod-a")
        pod_a.native["vllm_config"]["data_local_parallel_size"] = 1

        pod_b = _make_vllm_component(name="pod-b")
        pod_b.native["vllm_config"]["data_local_parallel_size"] = 2

        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[pod_a, pod_b],
        )

        dicts = discovery_to_stack_components(result)
        self.assertEqual(len(dicts), 2)

    def test_pydantic_validation_with_dp_local(self):
        """Generated dict with dp_local/workers passes Component validation."""
        comp = _make_vllm_component()
        comp.native["vllm_config"]["data_local_parallel_size"] = 2
        comp.native["vllm_config"]["num_workers"] = 3

        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[comp],
        )

        dicts = discovery_to_stack_components(result)
        report_component = ReportComponent(**dicts[0])
        self.assertEqual(
            report_component.standardized.accelerator.parallelism.dp_local, 2
        )
        self.assertEqual(
            report_component.standardized.accelerator.parallelism.workers, 3
        )


class TestGAIEControllerConversion(unittest.TestCase):
    """Test GAIE controller to request_router conversion."""

    def _make_gaie_controller_component(self):
        return Component(
            metadata=ComponentMetadata(
                namespace="default",
                name="gaie-controller-0",
                kind="Pod",
                labels={"app.kubernetes.io/name": "gaie"},
            ),
            tool="gaie-controller",
            tool_version="v1.3.0",
            native={
                "pod": {},
                "component_type": "gaie-controller",
                "controller_config": {
                    "leader_elect": True,
                    "metrics_addr": ":8080",
                    "watch_namespace": "model-ns",
                },
                "command": ["./gaie-controller"],
                "args": [
                    "--leader-elect",
                    "--metrics-bind-address",
                    ":8080",
                    "--health-probe-bind-address",
                    ":8081",
                ],
                "environment": [
                    {"name": "GAIE_NAMESPACE", "value": "model-ns"},
                    {"name": "GAIE_RECONCILE_INTERVAL", "value": "10s"},
                    {"name": "SECRET_TOKEN", "value": "<REDACTED>"},
                ],
                "resources": {},
            },
        )

    def test_gaie_controller_becomes_request_router(self):
        """gaie-controller component produces tool='request_router'."""
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[self._make_gaie_controller_component()],
        )
        dicts = discovery_to_stack_components(result)
        self.assertEqual(len(dicts), 1)
        self.assertEqual(dicts[0]["standardized"]["tool"], "request_router")
        self.assertEqual(dicts[0]["standardized"]["kind"], "generic")

    def test_gaie_controller_label_is_epp(self):
        """Label is 'EPP' for gaie-controller components."""
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[self._make_gaie_controller_component()],
        )
        dicts = discovery_to_stack_components(result)
        self.assertEqual(dicts[0]["metadata"]["label"], "EPP")

    def test_gaie_controller_args_populated(self):
        """gaie-controller native args are parsed from the token list."""
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[self._make_gaie_controller_component()],
        )
        dicts = discovery_to_stack_components(result)
        args = dicts[0]["native"]["args"]

        self.assertIsNotNone(args)
        self.assertIsNone(args["leader-elect"])  # boolean flag
        self.assertEqual(args["metrics-bind-address"], ":8080")
        self.assertEqual(args["health-probe-bind-address"], ":8081")

    def test_gaie_controller_envars_populated(self):
        """gaie-controller native envars are populated (redacted excluded)."""
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[self._make_gaie_controller_component()],
        )
        dicts = discovery_to_stack_components(result)
        envars = dicts[0]["native"]["envars"]

        self.assertIsNotNone(envars)
        self.assertEqual(envars["GAIE_NAMESPACE"], "model-ns")
        self.assertEqual(envars["GAIE_RECONCILE_INTERVAL"], "10s")
        self.assertNotIn("SECRET_TOKEN", envars)

    def test_gaie_controller_config_is_none(self):
        """gaie-controller native config is None (derived, not a manifest)."""
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[self._make_gaie_controller_component()],
        )
        dicts = discovery_to_stack_components(result)
        self.assertIsNone(dicts[0]["native"]["config"])


class TestExtractVllmServeTokens(unittest.TestCase):
    """Test _extract_vllm_serve_tokens helper."""

    def test_empty_list(self):
        """Empty list returns empty list."""
        self.assertEqual(_extract_vllm_serve_tokens([]), [])

    def test_normal_args_returned_as_is(self):
        """Normal CLI args are returned unchanged."""
        args = ["--model", "llama", "--port", "8000"]
        self.assertEqual(_extract_vllm_serve_tokens(args), args)

    def test_single_flag_returned_as_is(self):
        """Single flag is returned unchanged."""
        args = ["--enable-prefix-caching"]
        self.assertEqual(_extract_vllm_serve_tokens(args), args)

    def test_shell_script_parsed(self):
        """Shell script with vllm serve command is parsed into tokens."""
        script = (
            "source env.sh; vllm serve /models/llama \\\n"
            "--host 0.0.0.0 \\\n"
            "--port $PORT \\\n"
            "--disable-log-requests \n"
        )
        tokens = _extract_vllm_serve_tokens([script])
        self.assertEqual(tokens[0], "/models/llama")
        self.assertIn("--host", tokens)
        self.assertIn("0.0.0.0", tokens)
        self.assertIn("--port", tokens)
        self.assertIn("$PORT", tokens)
        self.assertIn("--disable-log-requests", tokens)

    def test_no_vllm_serve_returned_as_is(self):
        """Args without vllm serve command are returned unchanged."""
        args = ["python3 some_script.py --flag value"]
        self.assertEqual(_extract_vllm_serve_tokens(args), args)


class TestResolveEnvRef(unittest.TestCase):
    """Test _resolve_env_ref helper."""

    def test_no_vars(self):
        """String with no env vars is returned unchanged."""
        self.assertEqual(_resolve_env_ref("hello", {}), "hello")

    def test_dollar_var(self):
        """$VAR reference is resolved from lookup."""
        self.assertEqual(_resolve_env_ref("$MY_PORT", {"MY_PORT": "8080"}), "8080")

    def test_braced_var(self):
        """${VAR} reference is resolved from lookup."""
        self.assertEqual(_resolve_env_ref("${MY_PORT}", {"MY_PORT": "8080"}), "8080")

    def test_unknown_var_kept(self):
        """Unknown $VAR reference is kept as-is."""
        self.assertEqual(_resolve_env_ref("$UNKNOWN", {}), "$UNKNOWN")

    def test_empty_value(self):
        """Empty string is returned as empty string."""
        self.assertEqual(_resolve_env_ref("", {}), "")

    def test_none_value(self):
        """None input returns None."""
        self.assertIsNone(_resolve_env_ref(None, {}))

    def test_mixed_text_and_var(self):
        """Inline $VAR reference embedded in text is resolved correctly."""
        self.assertEqual(
            _resolve_env_ref("prefix-$VAR-suffix", {"VAR": "mid"}),
            "prefix-mid-suffix",
        )


class TestShellScriptArgsParsing(unittest.TestCase):
    """Test end-to-end benchmark report args from shell script pod args."""

    def _make_shell_script_component(self):
        """Create a vLLM component whose args are a single shell script string."""
        script = (
            "python3 /setup/preprocess/set_llmdbench_environment.py; "
            "source $HOME/llmdbench_env.sh; "
            "vllm serve /model-cache/models/meta-llama/Llama-3.1-8B-Instruct \\\n"
            "--host 0.0.0.0 \\\n"
            "--served-model-name meta-llama/Llama-3.1-8B-Instruct \\\n"
            "--port $VLLM_INFERENCE_PORT \\\n"
            "--block-size $VLLM_BLOCK_SIZE \\\n"
            "--max-model-len $VLLM_MAX_MODEL_LEN \\\n"
            "--tensor-parallel-size $VLLM_TENSOR_PARALLELISM \\\n"
            "--gpu-memory-utilization $VLLM_ACCELERATOR_MEM_UTIL \\\n"
            "--kv-transfer-config "
            '\'{"kv_connector":"NixlConnector","kv_role":"kv_both"}\' \\\n'
            "--disable-log-requests \\\n"
            "--disable-uvicorn-access-log \\\n"
            "--no-enable-prefix-caching \n"
        )
        return Component(
            metadata=ComponentMetadata(
                namespace="model-ns",
                name="prefill-pod-0",
                kind="Pod",
                labels={"app": "vllm"},
            ),
            tool="vllm",
            tool_version="v0.8.5",
            native={
                "vllm_config": {
                    "model": "meta-llama/Llama-3.1-8B-Instruct",
                    "tensor_parallel_size": 1,
                    "pipeline_parallel_size": 1,
                    "data_parallel_size": 1,
                },
                "role": HostType.PREFILL,
                "gpu": {"model": "H100-80GB", "count": 1},
                "args": [script],
                "environment": [
                    {"name": "VLLM_INFERENCE_PORT", "value": "8000"},
                    {"name": "VLLM_BLOCK_SIZE", "value": "16"},
                    {"name": "VLLM_MAX_MODEL_LEN", "value": "4096"},
                    {"name": "VLLM_TENSOR_PARALLELISM", "value": "1"},
                    {"name": "VLLM_ACCELERATOR_MEM_UTIL", "value": "0.95"},
                    {"name": "HF_TOKEN", "value": "<REDACTED>"},
                ],
            },
        )

    def test_shell_script_args_parsed(self):
        """Shell script args are parsed and env vars resolved."""
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[self._make_shell_script_component()],
        )
        dicts = discovery_to_stack_components(result)
        args = dicts[0]["native"]["args"]

        self.assertIsNotNone(args)
        self.assertEqual(args["host"], "0.0.0.0")
        self.assertEqual(args["served-model-name"], "meta-llama/Llama-3.1-8B-Instruct")
        # Env var references resolved
        self.assertEqual(args["port"], "8000")
        self.assertEqual(args["block-size"], "16")
        self.assertEqual(args["max-model-len"], "4096")
        self.assertEqual(args["tensor-parallel-size"], "1")
        self.assertEqual(args["gpu-memory-utilization"], "0.95")
        # config is None (vllm_config is derived, not a config file)
        self.assertIsNone(dicts[0]["native"]["config"])

    def test_boolean_flags_are_none(self):
        """Boolean flags (no value) produce None."""
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[self._make_shell_script_component()],
        )
        dicts = discovery_to_stack_components(result)
        args = dicts[0]["native"]["args"]

        self.assertIsNone(args["disable-log-requests"])
        self.assertIsNone(args["disable-uvicorn-access-log"])
        self.assertIsNone(args["no-enable-prefix-caching"])

    def test_kv_transfer_config_json(self):
        """JSON-valued flag is preserved correctly."""
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[self._make_shell_script_component()],
        )
        dicts = discovery_to_stack_components(result)
        args = dicts[0]["native"]["args"]

        kv_config = args["kv-transfer-config"]
        self.assertIn("NixlConnector", kv_config)
        self.assertIn("kv_both", kv_config)

    def test_envars_populated(self):
        """Envars dict populated (redacted values excluded)."""
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[self._make_shell_script_component()],
        )
        dicts = discovery_to_stack_components(result)
        envars = dicts[0]["native"]["envars"]

        self.assertEqual(envars["VLLM_INFERENCE_PORT"], "8000")
        self.assertNotIn("HF_TOKEN", envars)

    def test_pydantic_validation(self):
        """Shell-script-parsed component passes Pydantic validation."""
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[self._make_shell_script_component()],
        )
        dicts = discovery_to_stack_components(result)
        component = ReportComponent(**dicts[0])
        self.assertEqual(component.standardized.kind, "inference_engine")


class TestModelNameResolution(unittest.TestCase):
    """Test model name fallback chain for InferenceEngine components."""

    def test_model_from_positional_arg(self):
        """Model extracted from positional arg when vllm_config['model'] is None."""
        comp = Component(
            metadata=ComponentMetadata(
                namespace="default",
                name="vllm-pod-0",
                kind="Pod",
                labels={},
            ),
            tool="vllm",
            tool_version="v0.8.0",
            native={
                "vllm_config": {
                    "model": None,
                    "tensor_parallel_size": 1,
                    "pipeline_parallel_size": 1,
                    "data_parallel_size": 1,
                },
                "role": HostType.REPLICA,
                "gpu": {"model": "A100-80GB", "count": 1},
                "args": [
                    "vllm serve /models/meta-llama/Llama-3.1-8B-Instruct "
                    "--port 8000 --host 0.0.0.0"
                ],
                "environment": [],
            },
        )
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[comp],
        )
        dicts = discovery_to_stack_components(result)
        self.assertEqual(
            dicts[0]["standardized"]["model"]["name"],
            "/models/meta-llama/Llama-3.1-8B-Instruct",
        )

    def test_model_from_flag_in_args(self):
        """Model extracted from --model flag when vllm_config['model'] is None."""
        comp = Component(
            metadata=ComponentMetadata(
                namespace="default",
                name="vllm-pod-0",
                kind="Pod",
                labels={},
            ),
            tool="vllm",
            tool_version="v0.8.0",
            native={
                "vllm_config": {
                    "model": None,
                    "tensor_parallel_size": 1,
                    "pipeline_parallel_size": 1,
                    "data_parallel_size": 1,
                },
                "role": HostType.REPLICA,
                "gpu": {"model": "A100-80GB", "count": 1},
                "args": [
                    "--model",
                    "meta-llama/Llama-3.1-8B-Instruct",
                    "--port",
                    "8000",
                ],
                "environment": [],
            },
        )
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[comp],
        )
        dicts = discovery_to_stack_components(result)
        self.assertEqual(
            dicts[0]["standardized"]["model"]["name"],
            "meta-llama/Llama-3.1-8B-Instruct",
        )

    def test_model_unknown_fallback(self):
        """Model is 'unknown' when no source has it."""
        comp = Component(
            metadata=ComponentMetadata(
                namespace="default",
                name="vllm-pod-0",
                kind="Pod",
                labels={},
            ),
            tool="vllm",
            tool_version="v0.8.0",
            native={
                "vllm_config": {"model": None},
                "role": HostType.REPLICA,
                "gpu": {"model": "A100-80GB", "count": 1},
                "args": [],
                "environment": [],
            },
        )
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[comp],
        )
        dicts = discovery_to_stack_components(result)
        self.assertEqual(dicts[0]["standardized"]["model"]["name"], "unknown")

    def test_model_positional_with_env_var(self):
        """Positional model with env var reference is resolved."""
        comp = Component(
            metadata=ComponentMetadata(
                namespace="default",
                name="vllm-pod-0",
                kind="Pod",
                labels={},
            ),
            tool="vllm",
            tool_version="v0.8.0",
            native={
                "vllm_config": {"model": None},
                "role": HostType.REPLICA,
                "gpu": {"model": "A100-80GB", "count": 1},
                "args": ["vllm serve $MODEL_PATH --port 8000"],
                "environment": [
                    {"name": "MODEL_PATH", "value": "/models/llama"},
                ],
            },
        )
        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[comp],
        )
        dicts = discovery_to_stack_components(result)
        self.assertEqual(dicts[0]["standardized"]["model"]["name"], "/models/llama")

    def test_grouping_distinguishes_positional_models(self):
        """Pods with different positional models are not grouped together."""

        def _make_pod(name, model_path):
            return Component(
                metadata=ComponentMetadata(
                    namespace="default",
                    name=name,
                    kind="Pod",
                    labels={},
                ),
                tool="vllm",
                tool_version="v0.8.0",
                native={
                    "vllm_config": {
                        "model": None,
                        "tensor_parallel_size": 1,
                        "pipeline_parallel_size": 1,
                        "data_parallel_size": 1,
                    },
                    "role": HostType.REPLICA,
                    "gpu": {"model": "A100-80GB", "count": 1},
                    "args": [f"vllm serve {model_path} --port 8000"],
                    "environment": [],
                },
            )

        result = DiscoveryResult(
            url="https://example.com/v1",
            timestamp="2025-01-01T00:00:00",
            components=[
                _make_pod("pod-a", "/models/model-a"),
                _make_pod("pod-b", "/models/model-b"),
            ],
        )
        dicts = discovery_to_stack_components(result)
        self.assertEqual(len(dicts), 2)

    def test_resolve_model_name_direct(self):
        """Direct unit test of _resolve_model_name helper."""
        comp = Component(
            metadata=ComponentMetadata(
                namespace="default",
                name="pod",
                kind="Pod",
                labels={},
            ),
            tool="vllm",
            tool_version="v0.8.0",
            native={
                "vllm_config": {"model": "explicit-model"},
                "args": ["--model", "fallback-model"],
                "environment": [],
            },
        )
        # Source 1 wins when present
        self.assertEqual(_resolve_model_name(comp), "explicit-model")

        # Source 2 wins when vllm_config["model"] is None
        comp.native["vllm_config"]["model"] = None
        self.assertEqual(_resolve_model_name(comp), "fallback-model")

        # Default when nothing available
        comp.native["args"] = []
        self.assertEqual(_resolve_model_name(comp), "unknown")
        self.assertEqual(_resolve_model_name(comp, default=""), "")


if __name__ == "__main__":
    unittest.main()
