"""Unit tests for collectors."""

# pylint: disable=duplicate-code,protected-access

import unittest
from typing import Optional
from unittest.mock import Mock

import pykube
from llmdbenchmark.analysis.benchmark_report.schema_v0_2_components import HostType

from llm_d_stack_discovery.discovery.collectors.base import BaseCollector
from llm_d_stack_discovery.discovery.collectors.vllm import VLLMCollector
from llm_d_stack_discovery.models.components import Component


class _ConcreteCollector(BaseCollector):
    """Minimal concrete subclass of BaseCollector for testing."""

    def collect(self, resource: pykube.objects.APIObject) -> Optional[Component]:
        return None


class TestBaseCollector(unittest.TestCase):
    """Test BaseCollector functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.api = Mock()
        self.collector = _ConcreteCollector(self.api)

    def test_filter_env_vars(self):
        """Test environment variable filtering."""
        env_vars = [
            {"name": "MODEL_NAME", "value": "llama-2"},
            {"name": "HF_TOKEN", "value": "hf_abcdef123456"},
            {"name": "API_KEY", "value": "sk-1234567890"},
            {"name": "PORT", "value": "8000"},
            {"name": "SECRET_PASSWORD", "value": "supersecret"},
        ]

        filtered = self.collector._filter_env_vars(env_vars)

        # Check that sensitive values are redacted
        for env in filtered:
            if (
                "TOKEN" in env["name"]
                or "KEY" in env["name"]
                or "SECRET" in env["name"]
            ):
                self.assertEqual(env["value"], "<REDACTED>")
            else:
                self.assertNotEqual(env["value"], "<REDACTED>")

    def test_parse_command_args(self):
        """Test command and argument parsing."""
        command = ["python", "-m", "vllm.entrypoints.openai.api_server"]
        args = [
            "--model",
            "meta-llama/Llama-2-70b",
            "--tensor-parallel-size",
            "8",
            "--port",
            "8000",
            "--gpu-memory-utilization",
            "0.95",
            "--enable-prefix-caching",
        ]

        parsed = self.collector.parse_command_args(command, args)

        self.assertEqual(parsed["command"], "python")
        self.assertEqual(parsed["flags"]["model"], "meta-llama/Llama-2-70b")
        self.assertEqual(parsed["flags"]["tensor-parallel-size"], "8")
        self.assertEqual(parsed["flags"]["port"], "8000")
        self.assertEqual(parsed["flags"]["gpu-memory-utilization"], "0.95")
        self.assertEqual(parsed["flags"]["enable-prefix-caching"], True)


class TestVLLMCollector(unittest.TestCase):
    """Test VLLMCollector functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.api = Mock()
        self.collector = VLLMCollector(self.api)

    def test_is_vllm_pod(self):
        """Test vLLM pod detection."""
        # Create vLLM pod
        vllm_pod = Mock()
        vllm_pod.obj = {
            "spec": {
                "containers": [
                    {
                        "command": [
                            "python",
                            "-m",
                            "vllm.entrypoints.openai.api_server",
                        ],
                        "image": "vllm/vllm-openai:latest",
                    }
                ]
            }
        }

        # Create non-vLLM pod
        other_pod = Mock()
        other_pod.obj = {
            "spec": {
                "containers": [
                    {
                        "command": ["nginx"],
                        "image": "nginx:latest",
                    }
                ]
            }
        }

        self.assertTrue(self.collector._is_vllm_pod(vllm_pod))
        self.assertFalse(self.collector._is_vllm_pod(other_pod))

    def test_determine_role(self):
        """Test role determination from various label sources."""
        vllm_config = {}

        cases = [
            # app.kubernetes.io/component labels
            (
                {"app.kubernetes.io/component": "prefill-engine"},
                HostType.PREFILL,
                "k8s component prefill",
            ),
            (
                {"app.kubernetes.io/component": "decode-engine"},
                HostType.DECODE,
                "k8s component decode",
            ),
            # llm-d.ai/role labels (current production)
            (
                {"llm-d.ai/role": "prefill"},
                HostType.PREFILL,
                "llm-d.ai prefill",
            ),
            (
                {"llm-d.ai/role": "decode"},
                HostType.DECODE,
                "llm-d.ai decode",
            ),
            (
                {"llm-d.ai/role": "both"},
                HostType.REPLICA,
                "llm-d.ai both",
            ),
            # llm-d.io/role labels (legacy backward compat)
            (
                {"llm-d.io/role": "prefill"},
                HostType.PREFILL,
                "llm-d.io prefill",
            ),
            (
                {"llm-d.io/role": "decode"},
                HostType.DECODE,
                "llm-d.io decode",
            ),
            (
                {"llm-d.io/role": "both"},
                HostType.REPLICA,
                "llm-d.io both",
            ),
            # llm-d.ai takes precedence over llm-d.io when both present
            (
                {"llm-d.ai/role": "prefill", "llm-d.io/role": "decode"},
                HostType.PREFILL,
                "llm-d.ai takes precedence",
            ),
            # No role labels -> REPLICA
            (
                {},
                HostType.REPLICA,
                "no labels",
            ),
        ]

        for labels, expected, description in cases:
            with self.subTest(description):
                pod = Mock()
                pod.obj = {"metadata": {"labels": labels}}
                self.assertEqual(
                    self.collector._determine_role(pod, vllm_config),
                    expected,
                )

    def test_clean_gpu_model_name(self):
        """Test GPU model name cleaning."""
        test_cases = [
            ("NVIDIA-A100-SXM4-80GB", "A100-80GB"),
            ("NVIDIA-H100-SXM5-94GB", "H100-94GB"),
            ("Tesla-V100-SXM2-32GB", "V100-32GB"),
            ("NVIDIA-RTX-4090", "RTX-4090"),
            ("A100-PCIE-40GB", "A100-40GB"),
        ]

        for input_name, expected in test_cases:
            result = self.collector._clean_gpu_model_name(input_name)
            self.assertEqual(result, expected)

    def test_extract_vllm_config_with_non_numeric_values(self):
        """Pass 'auto' as tensor-parallel-size, verify it defaults to 1."""
        parsed_args = {
            "command": "python",
            "flags": {
                "model": "test-model",
                "tensor-parallel-size": "auto",
                "pipeline-parallel-size": "auto",
                "port": "invalid",
                "gpu-memory-utilization": "auto",
                "max-model-len": "dynamic",
                "max-num-seqs": "unlimited",
                "max-num-batched-tokens": "N/A",
            },
            "positional": [],
        }
        pod_info = {"env": []}

        pod = Mock()
        pod.obj = {"metadata": {"labels": {}}, "spec": {"containers": []}}

        config = self.collector._extract_vllm_config(parsed_args, pod_info, pod)

        self.assertIsNone(config["tensor_parallel_size"])
        self.assertIsNone(config["pipeline_parallel_size"])
        self.assertEqual(config["port"], 8000)
        self.assertIsNone(config["gpu_memory_utilization"])
        self.assertIsNone(config["max_model_len"])
        self.assertIsNone(config["max_num_seqs"])
        self.assertIsNone(config["max_num_batched_tokens"])


if __name__ == "__main__":
    unittest.main()
