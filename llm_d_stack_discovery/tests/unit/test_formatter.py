"""Unit tests for OutputFormatter."""

import json
import io
import unittest

import yaml

from llm_d_stack_discovery.models.components import (
    Component,
    ComponentMetadata,
    DiscoveryResult,
)
from llm_d_stack_discovery.output.formatter import OutputFormatter


def _make_result():
    """Build a simple DiscoveryResult with 2 components."""
    vllm_component = Component(
        metadata=ComponentMetadata(
            namespace="default",
            name="vllm-pod-0",
            kind="Pod",
            labels={"app": "vllm"},
        ),
        tool="vllm",
        tool_version="v0.4.0",
        native={
            "vllm_config": {
                "model": "meta-llama/Llama-2-70b",
                "tensor_parallel_size": 8,
            },
            "role": "replica",
            "gpu": {"model": "A100-80GB", "count": 8},
            "pod": {"spec": {"containers": []}},
            "command": ["python"],
            "args": ["--model", "meta-llama/Llama-2-70b"],
            "environment": [{"name": "VLLM_USE_V1", "value": "true"}],
            "resources": {},
        },
    )

    service_component = Component(
        metadata=ComponentMetadata(
            namespace="default",
            name="my-service",
            kind="Service",
            labels={},
        ),
        tool="gateway-api",
        tool_version="v1.0.0",
        native={"spec": {"type": "ClusterIP", "ports": [{"port": 8000}]}},
    )

    return DiscoveryResult(
        url="https://example.com/v1",
        timestamp="2025-01-01T00:00:00Z",
        cluster_info={"platform": "kubernetes", "version": "v1.28.0"},
        components=[vllm_component, service_component],
        errors=[],
    )


class TestOutputFormatter(unittest.TestCase):
    """Test OutputFormatter."""

    def setUp(self):
        self.formatter = OutputFormatter()
        self.result = _make_result()

    def test_format_json(self):
        """JSON output is valid and has expected keys."""
        output = self.formatter.format(self.result, format_type="json")
        data = json.loads(output)
        self.assertIn("url", data)
        self.assertIn("components", data)
        self.assertEqual(len(data["components"]), 2)
        self.assertEqual(data["url"], "https://example.com/v1")

    def test_format_yaml(self):
        """YAML output is valid."""
        output = self.formatter.format(self.result, format_type="yaml")
        data = yaml.safe_load(output)
        self.assertIn("url", data)
        self.assertIn("components", data)
        self.assertEqual(len(data["components"]), 2)

    def test_format_summary(self):
        """Summary output contains header."""
        output = self.formatter.format(self.result, format_type="summary")
        self.assertIn("LLM-D Stack Discovery Summary", output)
        self.assertIn("example.com", output)

    def test_format_native(self):
        """Native output has discovery_metadata and components keys."""
        output = self.formatter.format(self.result, format_type="native")
        data = json.loads(output)
        self.assertIn("discovery_metadata", data)
        self.assertIn("components", data)
        self.assertIn("url", data["discovery_metadata"])

    def test_format_benchmark_report(self):
        """Benchmark report output is valid JSON list of component dicts."""
        output = self.formatter.format(self.result, format_type="benchmark-report")
        data = json.loads(output)
        self.assertIsInstance(data, list)
        self.assertTrue(len(data) > 0)
        for item in data:
            self.assertIn("standardized", item)
            self.assertIn("metadata", item)

    def test_filter_by_tool(self):
        """filter='vllm' returns only vLLM components."""
        output = self.formatter.format(
            self.result, format_type="json", filter_type="vllm"
        )
        data = json.loads(output)
        self.assertEqual(len(data["components"]), 1)
        self.assertEqual(data["components"][0]["tool"], "vllm")

    def test_filter_by_kind(self):
        """filter='Pod' returns only pods."""
        output = self.formatter.format(
            self.result, format_type="json", filter_type="Pod"
        )
        data = json.loads(output)
        self.assertEqual(len(data["components"]), 1)
        self.assertEqual(data["components"][0]["metadata"]["kind"], "Pod")

    def test_write_to_file(self):
        """output_file receives the formatted string."""
        buf = io.StringIO()
        output = self.formatter.format(self.result, format_type="json", output_file=buf)
        buf.seek(0)
        file_content = buf.read()
        self.assertIn("example.com", file_content)
        # The output string and file content should match
        self.assertEqual(json.loads(output), json.loads(file_content.strip()))


if __name__ == "__main__":
    unittest.main()
