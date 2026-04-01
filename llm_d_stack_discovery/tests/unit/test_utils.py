"""Unit tests for discovery utilities."""

import unittest
from unittest.mock import Mock

from llm_d_stack_discovery.discovery.utils import (
    parse_endpoint_url,
    get_pod_containers,
)


class TestUtils(unittest.TestCase):
    """Test utility functions."""

    def test_parse_endpoint_url(self):
        """Test URL parsing."""
        # Test HTTPS with standard port
        result = parse_endpoint_url("https://model.example.com/v1")
        self.assertEqual(result["scheme"], "https")
        self.assertEqual(result["hostname"], "model.example.com")
        self.assertEqual(result["port"], 443)
        self.assertEqual(result["path"], "/v1")

        # Test HTTP with custom port
        result = parse_endpoint_url("http://localhost:8080/v1/completions")
        self.assertEqual(result["scheme"], "http")
        self.assertEqual(result["hostname"], "localhost")
        self.assertEqual(result["port"], 8080)
        self.assertEqual(result["path"], "/v1/completions")

        # Test without path
        result = parse_endpoint_url("https://api.example.com")
        self.assertEqual(result["path"], "")

    def test_get_pod_containers(self):
        """Test extracting container info from pod."""
        # Create mock pod
        pod = Mock()
        pod.obj = {
            "spec": {
                "containers": [
                    {
                        "name": "vllm",
                        "image": "vllm/vllm-openai:v0.3.0",
                        "command": [
                            "python",
                            "-m",
                            "vllm.entrypoints.openai.api_server",
                        ],
                        "args": ["--model", "meta-llama/Llama-2-70b", "--port", "8000"],
                        "env": [
                            {"name": "HF_TOKEN", "value": "secret"},
                            {"name": "VLLM_USE_V1", "value": "true"},
                        ],
                        "resources": {
                            "limits": {"nvidia.com/gpu": "8"},
                            "requests": {"nvidia.com/gpu": "8"},
                        },
                        "ports": [{"containerPort": 8000, "protocol": "TCP"}],
                    }
                ]
            }
        }

        containers = get_pod_containers(pod)

        self.assertEqual(len(containers), 1)
        container = containers[0]
        self.assertEqual(container["name"], "vllm")
        self.assertEqual(container["image"], "vllm/vllm-openai:v0.3.0")
        self.assertEqual(
            container["command"], ["python", "-m", "vllm.entrypoints.openai.api_server"]
        )
        self.assertEqual(
            container["args"], ["--model", "meta-llama/Llama-2-70b", "--port", "8000"]
        )
        self.assertEqual(len(container["env"]), 2)
        self.assertEqual(container["resources"]["limits"]["nvidia.com/gpu"], "8")


if __name__ == "__main__":
    unittest.main()
