#!/usr/bin/env python3
"""Example usage of the LLM-D Stack Discovery Tool."""

import json
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from llm_d_stack_discovery.discovery.utils import kube_connect
from llm_d_stack_discovery.discovery.tracer import StackTracer
from llm_d_stack_discovery.output.formatter import OutputFormatter
from llm_d_stack_discovery.output.benchmark_report import (
    discovery_to_stack_components,
    discovery_to_scenario_stack,
)


def main():
    """Run basic discovery example."""
    # Example URL - replace with your actual endpoint
    url = "https://model.example.com/v1"

    print(f"Discovering stack configuration from: {url}")
    print("-" * 60)

    try:
        # Connect to Kubernetes
        print("Connecting to Kubernetes cluster...")
        api, k8s_client = kube_connect()

        # Create tracer and discover
        print("Starting discovery...")
        tracer = StackTracer(api, k8s_client)
        result = tracer.trace(url)

        # Format and display results
        formatter = OutputFormatter()

        # Show summary
        print("\n" + "=" * 60)
        print("DISCOVERY SUMMARY")
        print("=" * 60)
        summary = formatter.format(result, format_type="summary")
        print(summary)

        # Show component details
        print("\n" + "=" * 60)
        print("COMPONENT DETAILS")
        print("=" * 60)

        for component in result.components:
            print(
                f"\n--- {component.metadata.kind}: {component.metadata.namespace}/{component.metadata.name} ---"
            )

            if component.tool:
                print(f"Tool: {component.tool} v{component.tool_version}")

            if component.tool == "vllm":
                vllm_config = component.native.get("vllm_config", {})
                role = component.native.get("role", "replica")
                gpu = component.native.get("gpu", {})
                print(f"Model: {vllm_config.get('model', 'unknown')}")
                print(f"Role: {role}")
                print(f"GPUs: {gpu.get('count', 0)}x {gpu.get('model', 'unknown')}")
                tp = vllm_config.get("tensor_parallel_size", 1)
                pp = vllm_config.get("pipeline_parallel_size", 1)
                dp = vllm_config.get("data_parallel_size", 1)
                print(f"Parallelism: TP={tp}, PP={pp}, DP={dp}")

            # Show labels
            if component.metadata.labels:
                print(f"Labels: {json.dumps(component.metadata.labels, indent=2)}")

        # Export to file
        print("\n" + "=" * 60)
        print("EXPORTING RESULTS")
        print("=" * 60)

        # Export as JSON
        output_path = Path("discovery-result.json")
        with open(output_path, "w") as f:
            formatter.format(result, format_type="json", output_file=f)
        print(f"Results exported to: {output_path}")

        # Export benchmark report format using the conversion API
        report_path = Path("benchmark-report-stack.json")
        stack_dicts = discovery_to_stack_components(result)
        with open(report_path, "w") as f:
            json.dump(stack_dicts, f, indent=2, default=str)
        print(f"Benchmark report stack exported to: {report_path}")

        # Alternatively, get Pydantic objects for programmatic use
        stack_objects = discovery_to_scenario_stack(result)
        print(f"Created {len(stack_objects)} Pydantic Component objects")

    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
