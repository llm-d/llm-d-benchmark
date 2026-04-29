"""vLLM component collector."""

import logging
import re
from typing import Any, Dict, Optional

import pykube

from llmdbenchmark.analysis.benchmark_report.schema_v0_2_components import HostType

from .base import BaseCollector
from ..utils import get_node_info
from ...models.components import Component

logger = logging.getLogger(__name__)


class VLLMCollector(BaseCollector):
    """Collector for vLLM inference engine components."""

    @staticmethod
    def _safe_int(value, default=None):
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _safe_float(value, default=None):
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    def collect(self, resource: pykube.Pod) -> Optional[Component]:
        """Collect vLLM configuration from a pod.

        Args:
            resource: Kubernetes pod running vLLM

        Returns:
            Component object or None if not a vLLM pod
        """
        pod = resource
        # Check if this is a vLLM pod
        if not self._is_vllm_pod(pod):
            return None

        try:
            self.get_metadata(pod)
            pod_info = self.extract_pod_info(pod)

            # Parse vLLM command arguments
            parsed_args = self.parse_command_args(
                pod_info.get("command", []), pod_info.get("args", [])
            )

            # Extract vLLM configuration
            vllm_config = self._extract_vllm_config(parsed_args, pod_info, pod)

            # Determine role from labels or configuration
            role = self._determine_role(pod, vllm_config)

            # Get GPU information
            gpu_info = self._get_gpu_info(pod, pod_info)

            # Build native configuration with all details
            native = {
                "pod": pod.obj,
                "vllm_config": vllm_config,
                "command": pod_info.get("command"),
                "args": pod_info.get("args"),
                "environment": pod_info.get("env"),
                "resources": pod_info.get("resources"),
                "role": role,
                "gpu": gpu_info,
                "node": pod_info.get("node_name"),
                "image": pod_info.get("image"),
            }

            return self.create_component(
                resource=pod,
                tool="vllm",
                tool_version=self._get_vllm_version(pod_info),
                native=native,
            )

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Failed to collect vLLM config from pod %s: %s", pod.name, e)
            return None

    def _is_vllm_pod(self, pod: pykube.Pod) -> bool:
        """Check if a pod is running vLLM.

        Args:
            pod: Kubernetes pod

        Returns:
            True if this is a vLLM pod
        """
        containers = pod.obj.get("spec", {}).get("containers", [])

        for container in containers:
            # Check command
            command = container.get("command", [])
            args = container.get("args", [])
            full_command = " ".join(command + args)

            # Look for vLLM indicators
            if any(
                indicator in full_command for indicator in ["vllm", "vllm.entrypoints"]
            ):
                return True

            # Check image name
            image = container.get("image", "")
            if "vllm" in image.lower():
                return True

        return False

    def _extract_vllm_config(
        self,
        parsed_args: Dict[str, Any],
        pod_info: Dict[str, Any],
        pod: pykube.Pod,  # pylint: disable=unused-argument
    ) -> Dict[str, Any]:
        """Extract vLLM configuration from pod info.

        Args:
            parsed_args: Parsed command arguments
            pod_info: Pod information
            pod: Pod object

        Returns:
            Dict with vLLM configuration
        """
        config = {}
        flags = parsed_args.get("flags", {})

        # Extract model
        config["model"] = flags.get("model") or flags.get("served-model-name")

        # Extract parallelism settings
        config["tensor_parallel_size"] = self._safe_int(
            flags.get("tensor-parallel-size", 1)
        )
        config["pipeline_parallel_size"] = self._safe_int(
            flags.get("pipeline-parallel-size", 1)
        )
        config["worker_use_ray"] = flags.get("worker-use-ray", False)

        # Extract scheduling settings
        config["scheduler_mode"] = flags.get("scheduler-mode", "default")
        config["num_scheduler_steps"] = self._safe_int(
            flags.get("num-scheduler-steps", 1)
        )

        # Extract KV cache settings
        config["kv_cache_dtype"] = flags.get("kv-cache-dtype", "auto")
        config["enable_prefix_caching"] = "enable-prefix-caching" in flags

        # Check environment variables for additional settings
        for env in pod_info.get("env", []):
            name = env.get("name", "")
            value = env.get("value", "")

            if value == "<REDACTED>":
                continue

            if name == "VLLM_DP_SIZE":
                config["data_parallel_size"] = self._safe_int(value, 1)
            elif name == "VLLM_EP_SIZE":
                config["expert_parallel_size"] = self._safe_int(value, 1)
            elif name == "VLLM_DP_LOCAL_SIZE":
                config["data_local_parallel_size"] = self._safe_int(value, 1)
            elif name == "VLLM_NUM_WORKERS":
                config["num_workers"] = self._safe_int(value, 1)
            elif name == "LLMDBENCH_VLLM_COMMON_DATA_LOCAL_PARALLELISM":
                config.setdefault("data_local_parallel_size", self._safe_int(value, 1))
            elif name == "LLMDBENCH_VLLM_COMMON_NUM_WORKERS_PARALLELISM":
                config.setdefault("num_workers", self._safe_int(value, 1))
            elif name == "VLLM_ATTENTION_BACKEND":
                config["attention_backend"] = value
            elif name == "VLLM_USE_V1":
                config["use_v1"] = value.lower() == "true"

        # Extract port
        config["port"] = self._safe_int(flags.get("port", 8000), 8000)

        # Extract max model len
        if "max-model-len" in flags:
            config["max_model_len"] = self._safe_int(flags["max-model-len"])

        # Extract GPU memory utilization
        if "gpu-memory-utilization" in flags:
            config["gpu_memory_utilization"] = self._safe_float(
                flags["gpu-memory-utilization"]
            )

        # Extract other important flags
        if "max-num-seqs" in flags:
            config["max_num_seqs"] = self._safe_int(flags["max-num-seqs"])

        if "max-num-batched-tokens" in flags:
            config["max_num_batched_tokens"] = self._safe_int(
                flags["max-num-batched-tokens"]
            )

        return config

    def _determine_role(
        self,
        pod: pykube.Pod,
        vllm_config: Dict[str, Any],  # pylint: disable=unused-argument
    ) -> HostType:
        """Determine the role of a vLLM instance.

        Args:
            pod: Kubernetes pod
            vllm_config: vLLM configuration

        Returns:
            HostType enum value (PREFILL, DECODE, or REPLICA)
        """
        labels = pod.obj.get("metadata", {}).get("labels", {})

        # Check labels first
        if "app.kubernetes.io/component" in labels:
            component = labels["app.kubernetes.io/component"].lower()
            if "prefill" in component:
                return HostType.PREFILL
            if "decode" in component:
                return HostType.DECODE

        # Check llm-d specific labels (both .ai and legacy .io domains)
        for label_key in ("llm-d.ai/role", "llm-d.io/role"):
            if label_key in labels:
                role = labels[label_key].lower()
                if role == "prefill":
                    return HostType.PREFILL
                if role == "decode":
                    return HostType.DECODE
                if role == "both":
                    return HostType.REPLICA

        # Default to replica
        return HostType.REPLICA

    def _clean_gpu_model_name(self, raw_name: str) -> str:
        """Clean GPU model name to a standardized short form.

        Strips vendor prefixes (NVIDIA-, Tesla-) and middle segments
        (SXM4, SXM5, SXM2, PCIE) to retain model + memory.

        Args:
            raw_name: Raw GPU product name from node labels

        Returns:
            Cleaned GPU model name (e.g., "A100-80GB")
        """
        # Remove common vendor prefixes
        name = re.sub(r"^(NVIDIA|Tesla)-", "", raw_name)

        # Remove form-factor/interconnect segments
        name = re.sub(r"-?(SXM[0-9]*|PCIE)-?", "-", name)

        # Clean up any double dashes or leading/trailing dashes
        name = re.sub(r"-+", "-", name).strip("-")

        return name

    def _get_gpu_info(
        self,
        pod: pykube.Pod,  # pylint: disable=unused-argument
        pod_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Extract GPU information.

        Args:
            pod: Kubernetes pod
            pod_info: Pod information

        Returns:
            Dict with GPU information
        """
        gpu_info = {}

        # Get GPU count from resources
        resources = pod_info.get("resources", {})
        requests = resources.get("requests", {})
        limits = resources.get("limits", {})

        if "nvidia.com/gpu" in requests:
            gpu_info["count"] = self._safe_int(requests["nvidia.com/gpu"], 0)
        elif "nvidia.com/gpu" in limits:
            gpu_info["count"] = self._safe_int(limits["nvidia.com/gpu"], 0)
        else:
            gpu_info["count"] = 0

        # Get GPU model from node
        node_name = pod_info.get("node_name")
        if node_name:
            node_info = get_node_info(self.api, node_name)
            if node_info and node_info.get("gpu"):
                node_gpu = node_info["gpu"]
                raw_product = node_gpu.get("product", "unknown")
                gpu_info["model"] = self._clean_gpu_model_name(raw_product)
                gpu_info["memory"] = node_gpu.get("memory")

        return gpu_info

    def _get_vllm_version(self, pod_info: Dict[str, Any]) -> str:
        """Extract vLLM version from pod info.

        Args:
            pod_info: Pod information

        Returns:
            vLLM version string
        """
        # Check image tag
        image = pod_info.get("image", "")
        if ":" in image:
            tag = image.split(":")[-1]
            # Extract version from tag if it looks like a version
            if re.match(r"v?\d+\.\d+", tag):
                return tag

        # Check environment variables
        for env in pod_info.get("env", []):
            if env.get("name") == "VLLM_VERSION":
                value = env.get("value", "")
                if value != "<REDACTED>":
                    return value

        return "unknown"
