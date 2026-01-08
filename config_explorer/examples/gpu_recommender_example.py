#!/usr/bin/env python3
"""
Example usage of the GPURecommender class from config_explorer package.

This script demonstrates how to use the GPURecommender to find optimal GPUs
for running LLM inference with various configurations and constraints.
"""

import json
import os
import traceback
from config_explorer.recommender.recommender import GPURecommender


def example_basic_usage():
    """Basic usage: Analyze all GPUs for a model"""
    print("=" * 80)
    print("Example 1: Basic GPU Recommendation")
    print("=" * 80)

    recommender = GPURecommender(
        model_id="Qwen/Qwen-7B",
        input_len=512,
        output_len=128,
        max_gpus=1,
    )

    # Get results
    gpu_results, failed_gpus = recommender.get_gpu_results()

    print(f"\nAnalyzed GPUs: {len(gpu_results)}")
    print(f"Failed GPUs: {len(failed_gpus)}")

    # Get best GPU recommendations
    best_throughput = recommender.get_gpu_with_highest_throughput()
    if best_throughput:
        print(f"\nBest GPU for throughput: {best_throughput[0]}")
        print(f"  Throughput: {best_throughput[1]:.2f} tokens/s")

    best_ttft = recommender.get_gpu_with_lowest_ttft()
    if best_ttft:
        print(f"\nBest GPU for TTFT: {best_ttft[0]}")
        print(f"  TTFT: {best_ttft[1]:.2f} ms")


def example_specific_gpus():
    """Analyze only specific GPUs"""
    print("\n" + "=" * 80)
    print("Example 2: Analyze Specific GPUs")
    print("=" * 80)

    recommender = GPURecommender(
        model_id="Qwen/Qwen-7B",
        input_len=1024,
        output_len=256,
        max_gpus=1,
        gpu_list=["H100", "A100", "L40"],  # Only analyze these GPUs
    )

    gpu_results, failed_gpus = recommender.get_gpu_results()

    print(f"\nRequested GPUs: H100, A100, L40")
    print(f"Successful: {len(gpu_results)}")
    print(f"Failed: {len(failed_gpus)}")

    if failed_gpus:
        print("\nFailed GPUs:")
        for gpu_name, error_msg in failed_gpus.items():
            print(f"  {gpu_name}: {error_msg}")


def example_with_constraints():
    """Use performance constraints"""
    print("\n" + "=" * 80)
    print("Example 3: GPU Recommendation with Performance Constraints")
    print("=" * 80)

    recommender = GPURecommender(
        model_id="Qwen/Qwen-7B",
        input_len=512,
        output_len=128,
        max_gpus=2,
        gpu_list=["H100", "A100", "L40", "L4"],
        max_ttft=100.0,      # Maximum TTFT: 100ms
        max_itl=10.0,        # Maximum ITL: 10ms
        max_latency=2.0,     # Maximum E2E latency: 2s
    )

    gpu_results, failed_gpus = recommender.get_gpu_results()

    print("\nConstraints:")
    print(f"  Max TTFT: 100ms")
    print(f"  Max ITL: 10ms")
    print(f"  Max E2E Latency: 2s")

    print(f"\nGPUs meeting constraints: {len(gpu_results)}")
    print(f"GPUs not meeting constraints: {len(failed_gpus)}")

    if gpu_results:
        print("\nGPUs that passed:")
        for gpu_name in gpu_results.keys():
            print(f"  - {gpu_name}")


def example_multi_gpu_configs():
    """Use different max GPU counts for different GPU types"""
    print("\n" + "=" * 80)
    print("Example 4: Different Max GPU Counts per GPU Type")
    print("=" * 80)

    recommender = GPURecommender(
        model_id="Qwen/Qwen3-32B",
        input_len=512,
        output_len=128,
        max_gpus=2,  # Default for GPUs not specified below
        max_gpus_per_type={
            "H100": 8,  # Allow up to 8 H100 GPUs
            "A100": 4,  # Allow up to 4 A100 GPUs
            "L40": 2,   # Allow up to 2 L40 GPUs
        },
        gpu_list=["H100", "A100", "L40"],
    )

    gpu_results, failed_gpus = recommender.get_gpu_results()

    print("\nGPU Configuration:")
    print(f"  H100: up to 8 GPUs")
    print(f"  A100: up to 4 GPUs")
    print(f"  L40: up to 2 GPUs")

    print(f"\nSuccessful configurations: {len(gpu_results)}")

    # Get performance summary
    summary = recommender.get_performance_summary(verbose=False)

    if "estimated_best_performance" in summary:
        print("\nBest Performance Recommendations:")
        for metric, data in summary["estimated_best_performance"].items():
            print(f"  {metric}: {data['gpu']}")


def example_detailed_analysis():
    """Get detailed performance analysis with verbose mode"""
    print("\n" + "=" * 80)
    print("Example 5: Detailed Performance Analysis (Verbose Mode)")
    print("=" * 80)

    recommender = GPURecommender(
        model_id="Qwen/Qwen-7B",
        input_len=512,
        output_len=128,
        max_gpus=1,
        gpu_list=["H100"],  # Just analyze H100 for detailed output
    )

    _, failed_gpus = recommender.get_gpu_results()
    if failed_gpus:
        print("\nFailed GPUs during detailed analysis:")
        for gpu_name, error_msg in failed_gpus.items():
            print(f"  {gpu_name}: {error_msg}")

    # Get detailed summary with verbose=True for concurrency analysis
    summary = recommender.get_performance_summary(verbose=True)

    if "H100" in summary["gpu_results"]:
        h100_data = summary["gpu_results"]["H100"]

        print("\nH100 Performance Details:")

        if "best_latency" in h100_data:
            print("\n  Best Latency (Concurrency=1):")
            bl = h100_data["best_latency"]
            print(f"    Throughput: {bl.get('throughput_tps', 'N/A')} tokens/s")
            print(f"    TTFT: {bl.get('ttft_ms', 'N/A')} ms")
            print(f"    ITL: {bl.get('itl_ms', 'N/A')} ms")
            print(f"    E2E Latency: {bl.get('e2e_latency_s', 'N/A')} s")
            print(f"    Prefill Memory Bound: {bl.get('prefill_is_memory_bound', 'N/A')}")
            print(f"    Decode Memory Bound: {bl.get('decode_is_memory_bound', 'N/A')}")

        if "best_output_throughput" in h100_data:
            print("\n  Best Throughput (Optimal Concurrency):")
            bt = h100_data["best_output_throughput"]
            print(f"    Optimal Concurrency: {bt.get('optimal_concurrency', 'N/A')}")
            print(f"    Throughput: {bt.get('throughput_tps', 'N/A')} tokens/s")
            print(f"    TTFT: {bt.get('ttft_ms', 'N/A')} ms")
            print(f"    ITL: {bt.get('itl_ms', 'N/A')} ms")

        if "total_memory_gb" in h100_data:
            print("\n  Memory Information:")
            print(f"    Total Memory: {h100_data['total_memory_gb']} GB")
            print(f"    Model Memory: {h100_data.get('model_memory_gb', 'N/A')} GB")
            print(f"    KV Cache Memory: {h100_data.get('kv_cache_memory_gb', 'N/A')} GB")


def example_export_to_json():
    """Export results to JSON file"""
    print("\n" + "=" * 80)
    print("Example 6: Export Results to JSON")
    print("=" * 80)

    recommender = GPURecommender(
        model_id="Qwen/Qwen-7B",
        input_len=512,
        output_len=128,
        max_gpus=1,
        gpu_list=["H100", "A100"],
    )

    gpu_results, failed_gpus = recommender.get_gpu_results()
    summary = recommender.get_performance_summary(verbose=True)

    # Prepare export data
    export_data = {
        "model": "Qwen/Qwen-7B",
        "workload": {
            "input_len": 512,
            "output_len": 128,
        },
        "estimated_best_performance": summary["estimated_best_performance"],
        "gpu_results": summary["gpu_results"],
        "failed_gpus": failed_gpus,
    }

    # Write to file
    output_file = "gpu_recommendation_results.json"
    with open(output_file, 'w') as f:
        json.dump(export_data, f, indent=2)

    print(f"\nResults exported to: {output_file}")
    print(f"File size: {os.path.getsize(output_file)} bytes")


def example_restrictive_constraints():
    """Handle failed results due to overly restrictive constraints"""
    print("\n" + "=" * 80)
    print("Example 7: Handling Failed Results with Restrictive Constraints")
    print("=" * 80)

    # Use extremely restrictive constraints that no GPU can meet
    recommender = GPURecommender(
        model_id="Qwen/Qwen3-32B",  # Large model
        input_len=2048,              # Long input
        output_len=512,              # Long output
        max_gpus=1,                  # Only 1 GPU allowed
        gpu_list=["L4", "L40", "A100", "H100"],
        max_ttft=1.0,                # Extremely low: 1ms TTFT
        max_itl=0.5,                 # Extremely low: 0.5ms ITL
        max_latency=0.1,             # Extremely low: 0.1s total latency
    )

    print("\nTesting with VERY restrictive constraints:")
    print(f"  Model: Qwen/Qwen3-32B (large model)")
    print(f"  Input length: 2048 tokens")
    print(f"  Output length: 512 tokens")
    print(f"  Max GPUs: 1")
    print(f"  Max TTFT: 1ms (extremely restrictive)")
    print(f"  Max ITL: 0.5ms (extremely restrictive)")
    print(f"  Max E2E Latency: 0.1s (extremely restrictive)")

    gpu_results, failed_gpus = recommender.get_gpu_results()

    print(f"\n{'='*60}")
    print(f"Results:")
    print(f"{'='*60}")
    print(f"GPUs that met constraints: {len(gpu_results)}")
    print(f"GPUs that failed constraints: {len(failed_gpus)}")

    if failed_gpus:
        print(f"\n{'Failed GPUs and Reasons:'}")
        print(f"{'-'*60}")
        for gpu_name, error_msg in failed_gpus.items():
            print(f"\n  {gpu_name}:")
            # Wrap long error messages
            if len(error_msg) > 50:
                print(f"    {error_msg[:50]}...")
                print(f"    {error_msg[50:]}")
            else:
                print(f"    {error_msg}")

    if not gpu_results:
        print(f"\n{'='*60}")
        print("⚠️  NO GPUs could meet these constraints!")
        print(f"{'='*60}")
        print("\nRecommendations:")
        print("  1. Relax the performance constraints")
        print("  2. Increase max_gpus to allow tensor parallelism")
        print("  3. Reduce input/output sequence lengths")
        print("  4. Consider a smaller model")
        print("  5. Use more powerful GPUs (e.g., H100 instead of L4)")
    else:
        print(f"\n✓ {len(gpu_results)} GPU(s) met the constraints")
        summary = recommender.get_performance_summary(verbose=False)
        if "estimated_best_performance" in summary and summary["estimated_best_performance"]:
            print("\nBest performers:")
            for metric, data in summary["estimated_best_performance"].items():
                print(f"  {metric}: {data['gpu']}")


def example_comparison():
    """Compare performance across multiple models"""
    print("\n" + "=" * 80)
    print("Example 8: Compare Multiple Models")
    print("=" * 80)

    models = ["Qwen/Qwen-7B", "Qwen/Qwen-14B"]
    gpu_list = ["H100", "A100"]

    results = {}

    for model in models:
        print(f"\nAnalyzing {model}...")

        recommender = GPURecommender(
            model_id=model,
            input_len=512,
            output_len=128,
            max_gpus=1,
            gpu_list=gpu_list,
        )

        gpu_results, failed_gpus = recommender.get_gpu_results()
        best_throughput = recommender.get_gpu_with_highest_throughput()

        results[model] = {
            "successful_gpus": len(gpu_results),
            "best_gpu": best_throughput[0] if best_throughput else None,
            "best_throughput": best_throughput[1] if best_throughput else None,
        }

    print("\n" + "-" * 80)
    print("Comparison Summary:")
    print("-" * 80)
    for model, data in results.items():
        print(f"\n{model}:")
        print(f"  Compatible GPUs: {data['successful_gpus']}/{len(gpu_list)}")
        print(f"  Best GPU: {data['best_gpu']}")
        print(f"  Best Throughput: {data['best_throughput']:.2f} tokens/s" if data['best_throughput'] else "  Best Throughput: N/A")


def main():
    """Run all examples"""
    print("\n")
    print("=" * 80)
    print("GPU Recommender Examples")
    print("=" * 80)
    print("\nThese examples demonstrate various ways to use the GPURecommender class")
    print("from the config_explorer package.")
    print("\nNote: Set HF_TOKEN environment variable if analyzing gated models.")
    print("=" * 80)

    try:
        # Run all examples
        example_basic_usage()
        example_specific_gpus()
        example_with_constraints()
        example_multi_gpu_configs()
        example_detailed_analysis()
        example_export_to_json()
        example_restrictive_constraints()
        example_comparison()

        print("\n" + "=" * 80)
        print("All examples completed successfully!")
        print("=" * 80 + "\n")

    except Exception as e:
        print(f"\nError running examples: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
