"""
CLI interface for config_explorer package
"""

import argparse
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

from config_explorer.capacity_planner import (
    get_model_info_from_hf,
    get_model_config_from_hf,
    model_memory_req,
    max_concurrent_requests,
    allocatable_kv_cache_memory,
    total_kv_cache_blocks,
    KVCacheDetail,
    find_possible_tp,
    gpus_required,
    per_gpu_model_memory_required,
)
from config_explorer.recommender.recommender import GPURecommender
from llm_optimizer.predefined.gpus import GPU_SPECS


def start_ui():
    """Start the Streamlit UI"""

    # Get the path to Capacity_Planner.py
    config_explorer_dir = Path(__file__).parent.parent.parent
    ui_file = config_explorer_dir / "Capacity_Planner.py"

    if not ui_file.exists():
        sys.exit(f"Error: Capacity_Planner.py not found at expected location: {ui_file}")

    print("Starting Config Explorer UI...")
    try:
        result = subprocess.run(["streamlit", "run", str(ui_file)])
        if result.returncode != 0:
            sys.exit(f"Error: Failed to start Streamlit UI (exit code {result.returncode}).")
    except FileNotFoundError:
        sys.exit(
            "Error: 'streamlit' command not found. Please install Streamlit and "
            "ensure it is available on your PATH."
        )
    except Exception as e:
        sys.exit(f"Error: Failed to start Streamlit UI: {e}")


def plan_capacity(args):
    """Run capacity planning analysis"""

    # Get HF token from environment if available
    hf_token = os.getenv("HF_TOKEN", None)

    try:
        # Fetch model information
        print(f"Fetching model information for {args.model}...")
        model_info = get_model_info_from_hf(args.model, hf_token)
        model_config = get_model_config_from_hf(args.model, hf_token)

        # Prepare result dictionary
        result = {
            "input_parameters": {
                "model": args.model,
            },
            "model_info": {
                "total_parameters": model_info.safetensors.total,
            },
        }

        # Calculate model memory requirement
        model_memory = model_memory_req(model_info, model_config)
        result["model_memory_gb"] = round(model_memory, 2)

        # Set max_model_len: use provided value or default from model's max context length
        if args.max_model_len:
            max_model_len = args.max_model_len
        else:
            from config_explorer.capacity_planner import max_context_len
            max_model_len = max_context_len(model_config)

        batch_size = args.batch_size or 1

        # Validate TP value if provided
        if args.tp:
            possible_tp = find_possible_tp(model_config)
            if args.tp not in possible_tp:
                sys.exit(f"Error: Invalid --tp value {args.tp}. Valid values for this model are: {possible_tp}")

        # Add parameters to input_parameters
        result["input_parameters"]["max_model_len"] = max_model_len
        result["input_parameters"]["batch_size"] = batch_size

        # Calculate KV cache details (always calculate with max_model_len)
        kv_cache_detail = KVCacheDetail(
            model_info,
            model_config,
            max_model_len,
            batch_size
        )

        result["kv_cache_detail"] = {
            "attention_type": kv_cache_detail.attention_type,
            "kv_data_type": kv_cache_detail.kv_data_type,
            "num_hidden_layers": kv_cache_detail.num_hidden_layers,
            "num_attention_heads": kv_cache_detail.num_attention_heads,
            "num_key_value_heads": kv_cache_detail.num_key_value_heads,
            "head_dimension": kv_cache_detail.head_dimension,
            "per_token_memory_bytes": kv_cache_detail.per_token_memory_bytes,
            "per_request_kv_cache_gb": round(kv_cache_detail.per_request_kv_cache_gb, 4),
            "kv_cache_size_gb": round(kv_cache_detail.kv_cache_size_gb, 2),
            "context_len": kv_cache_detail.context_len,
            "batch_size": kv_cache_detail.batch_size,
        }

        # Add MLA-specific details if applicable
        if kv_cache_detail.kv_lora_rank is not None:
            result["kv_cache_detail"]["kv_lora_rank"] = kv_cache_detail.kv_lora_rank
        if kv_cache_detail.qk_rope_head_dim is not None:
            result["kv_cache_detail"]["qk_rope_head_dim"] = kv_cache_detail.qk_rope_head_dim

        # Calculate GPU-related metrics if gpu_memory is provided
        if args.gpu_memory:
            gpu_memory_gb = args.gpu_memory

            # Set parallelism parameters
            tp = args.tp or 1
            pp = args.pp or 1
            dp = args.dp or 1
            gpu_mem_util = args.gpu_mem_util or 0.9

            # Add parallelism parameters to input_parameters
            result["input_parameters"]["tp"] = tp
            result["input_parameters"]["pp"] = pp
            result["input_parameters"]["dp"] = dp
            result["input_parameters"]["gpu_mem_util"] = gpu_mem_util

            # Calculate per-GPU model memory
            per_gpu_memory = per_gpu_model_memory_required(model_info, model_config, tp, pp)
            result["per_gpu_model_memory_gb"] = round(per_gpu_memory, 2)

            # Calculate total GPUs required
            total_gpus = gpus_required(tp, pp, dp)
            result["total_gpus_required"] = total_gpus

            # Calculate allocatable KV cache memory
            allocatable_kv = allocatable_kv_cache_memory(
                model_info, model_config,
                gpu_memory_gb, gpu_mem_util,
                tp, pp, dp,
                max_model_len=max_model_len,
                batch_size=batch_size
            )
            result["allocatable_kv_cache_memory_gb"] = round(allocatable_kv, 2)

            # Calculate max concurrent requests
            max_requests = max_concurrent_requests(
                model_info, model_config,
                max_model_len,
                gpu_memory_gb, gpu_mem_util,
                batch_size=batch_size,
                tp=tp, pp=pp, dp=dp
            )
            result["max_concurrent_requests"] = max_requests

            # Calculate total KV cache blocks (use default block_size of 16 if not provided)
            block_size = args.block_size or 16
            total_blocks = total_kv_cache_blocks(
                model_info, model_config,
                max_model_len,
                gpu_memory_gb, gpu_mem_util,
                batch_size,
                block_size,
                tp, pp, dp
            )
            result["total_kv_cache_blocks"] = int(total_blocks)
            # Always record the effective block_size used (including defaults)
            result["input_parameters"]["block_size"] = block_size

        # Find possible TP values
        if args.show_possible_tp:
            possible_tp = find_possible_tp(model_config)
            result["possible_tp_values"] = possible_tp

        # Output results
        if args.output:
            # Write to file
            output_path = Path(args.output)
            with open(output_path, 'w') as f:
                json.dump(result, f, indent=2)
            print(f"Results written to {output_path}")
        else:
            # Print to console
            print(json.dumps(result, indent=2))

        return result

    except Exception as e:
        if args.verbose:
            traceback.print_exc()
        sys.exit(f"Error during capacity planning: {str(e)}")


def estimate_performance(args):
    """Run GPU performance estimation and recommendation"""

    try:
        # Parse GPU-specific max_gpus if provided
        max_gpus_per_type = None
        if args.max_gpus_per_type:
            max_gpus_per_type = {}
            for item in args.max_gpus_per_type:
                try:
                    gpu_name, max_count = item.split(':')
                    max_gpus_per_type[gpu_name] = int(max_count)
                except ValueError:
                    sys.exit(f"Error: Invalid format for --max-gpus-per-type: {item}. Expected format: GPU_NAME:MAX_COUNT")

        # Parse GPU list if provided, otherwise use all GPUs from GPU_SPECS
        if args.gpu_list:
            gpu_list = [g.strip() for g in args.gpu_list.split(',')]
        else:
            gpu_list = sorted(list(GPU_SPECS.keys()))

        # Parse custom GPU costs if provided
        custom_gpu_costs = None
        if hasattr(args, 'custom_gpu_cost') and args.custom_gpu_cost:
            custom_gpu_costs = {}
            for item in args.custom_gpu_cost:
                try:
                    gpu_name, cost_str = item.split(':', 1)
                    custom_gpu_costs[gpu_name.strip()] = float(cost_str)
                except ValueError:
                    sys.exit(f"Error: Invalid cost format '{item}'. Use GPU_NAME:COST (e.g., H100:30.5)")

        print(f"Running performance estimation for {args.model}...")
        print(f"Analyzing {len(gpu_list)} GPU type(s)...")

        recommender = GPURecommender(
            model_id=args.model,
            input_len=args.input_len,
            output_len=args.output_len,
            max_gpus=args.max_gpus,
            max_gpus_per_type=max_gpus_per_type,
            gpu_list=gpu_list,
            max_ttft=args.max_ttft,
            max_itl=args.max_itl,
            max_latency=args.max_latency,
            custom_gpu_costs=custom_gpu_costs,
        )

        # Get results using the recommender's method
        gpu_results, failed_gpus = recommender.get_gpu_results()
        performance_summary = recommender.get_performance_summary(verbose=args.verbose)

        # Prepare result dictionary
        result = {
            "input_parameters": {
                "model": args.model,
                "input_len": args.input_len,
                "output_len": args.output_len,
                "max_gpus": args.max_gpus,
                "gpu_list": gpu_list,
            },
            "estimated_best_performance": performance_summary["estimated_best_performance"],
            "gpu_results": performance_summary["gpu_results"],
            "failed_gpus": failed_gpus,
        }

        # Add constraints if specified
        if max_gpus_per_type:
            result["input_parameters"]["max_gpus_per_type"] = max_gpus_per_type
        if args.max_ttft:
            result["input_parameters"]["max_ttft_ms"] = args.max_ttft
        if args.max_itl:
            result["input_parameters"]["max_itl_ms"] = args.max_itl
        if args.max_latency:
            result["input_parameters"]["max_latency_s"] = args.max_latency

        # Summary statistics
        result["summary"] = {
            "total_gpus_analyzed": len(gpu_list),
            "failed_gpus": len(failed_gpus),
        }

        # Output results
        if args.output:
            output_path = Path(args.output)
            with open(output_path, 'w') as f:
                json.dump(result, f, indent=2)
            print(f"Results written to {output_path}")
        elif hasattr(args, 'pretty') and args.pretty:
            # Pretty-printed human-readable format
            print("\n" + "="*80)
            print("GPU Performance Estimation Results")
            print("="*80)

            # Show conditional disclaimer based on whether custom costs are used
            if recommender.cost_manager.is_using_custom_costs():
                print(f"\n💡 Displaying custom costs")
            else:
                print(f"\n💡 Default costs are reference values for comparison purposes.")

            print("\n📊 Results sorted by cost (lowest to highest)")
            print("    Only showing GPUs that meet performance requirements")
            print("="*80)

            # Show lowest cost GPU
            if "lowest_cost" in performance_summary["estimated_best_performance"]:
                best_cost_info = performance_summary["estimated_best_performance"]["lowest_cost"]
                print(f"\n🏆 Best Value GPU: {best_cost_info['gpu']}")
                print(f"   Cost: ${best_cost_info['cost']:.2f}")
                if 'throughput_tps' in best_cost_info:
                    print(f"   Throughput: {best_cost_info['throughput_tps']:.2f} tokens/s")
                if 'ttft_ms' in best_cost_info:
                    print(f"   TTFT: {best_cost_info['ttft_ms']:.2f} ms")
                if 'itl_ms' in best_cost_info:
                    print(f"   ITL: {best_cost_info['itl_ms']:.2f} ms")

            # Show sorted results - only include GPUs with valid performance data
            sorted_results = recommender.get_results_sorted_by_cost()
            # Filter to only include GPUs that are in performance_summary (which excludes failed GPUs)
            valid_sorted_results = [
                (gpu_name, cost, result)
                for gpu_name, cost, result in sorted_results
                if gpu_name in performance_summary["gpu_results"]
            ]

            if valid_sorted_results:
                print(f"\n📋 All Qualifying GPUs ({len(valid_sorted_results)} total):")
                print("-" * 80)
                for idx, (gpu_name, cost, _) in enumerate(valid_sorted_results, 1):
                    gpu_data = performance_summary["gpu_results"][gpu_name]
                    best_config = gpu_data.get("best_latency", {})

                    print(f"\n{idx}. {gpu_name}")
                    print(f"   💰 Cost: ${cost:.2f}")

                    if best_config:
                        throughput = best_config.get("throughput_tps", "N/A")
                        ttft = best_config.get("ttft_ms", "N/A")
                        itl = best_config.get("itl_ms", "N/A")
                        num_gpus = gpu_data.get("num_gpus", "N/A")
                        tp = best_config.get("tp", "N/A")

                        print(f"   🚀 Throughput: {throughput} tokens/s")
                        print(f"   ⚡ TTFT: {ttft} ms | ITL: {itl} ms")
                        print(f"   🔧 Config: {num_gpus} GPU(s), TP={tp}")

            # Show failed GPUs - always display in pretty mode
            if failed_gpus:
                print(f"\n⚠️  Excluded GPUs ({len(failed_gpus)}):")
                print("-" * 80)

                if args.verbose:
                    # Verbose mode: show full error messages
                    for gpu_name, error in failed_gpus.items():
                        print(f"   • {gpu_name}: {error}")
                else:
                    # Non-verbose mode: group by error type
                    error_groups = {}
                    for gpu_name, error in failed_gpus.items():
                        if error not in error_groups:
                            error_groups[error] = []
                        error_groups[error].append(gpu_name)

                    for error, gpu_names in error_groups.items():
                        print(f"   • {error}:")
                        print(f"     {', '.join(gpu_names)}")

                print("\n   💡 Use --verbose flag to see detailed error information for each GPU")

            # Show message if no valid results
            if len(valid_sorted_results) == 0:
                print("\n⚠️  No GPUs met the performance requirements or had valid configurations.")
                print("    Try relaxing constraints or selecting different GPUs.")

            print("\n" + "="*80)
        else:
            # JSON output (default)
            print(json.dumps(result, indent=2))

        return result

    except Exception as e:
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(f"Error during performance estimation: {str(e)}")


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="Config Explorer CLI - Capacity planning and configuration tools for LLM deployment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start the Streamlit UI
  config-explorer start

  # Basic capacity planning (uses model's default max context length)
  config-explorer plan --model Qwen/Qwen3-32B

  # Plan with custom max model length
  config-explorer plan --model Qwen/Qwen3-32B --max-model-len 2048

  # Plan with GPU memory
  config-explorer plan --model Qwen/Qwen3-32B --gpu-memory 80

  # Plan with full parallelism configuration
  config-explorer plan --model Qwen/Qwen3-32B \\
    --gpu-memory 80 --max-model-len 8192 --batch-size 128 \\
    --tp 4 --pp 1 --dp 2 \\
    --output results.json

  # Show possible TP values for a model
  config-explorer plan --model Qwen/Qwen3-32B --show-possible-tp

  # GPU performance estimation and recommendation
  config-explorer estimate --model Qwen/Qwen3-32B --input-len 512 --output-len 128

  # Estimate with specific GPU list
  config-explorer estimate --model Qwen/Qwen3-32B --input-len 512 --output-len 128 \\
    --gpu-list H100,A100,L40

  # Estimate with performance constraints
  config-explorer estimate --model Qwen/Qwen3-32B --input-len 512 --output-len 128 \\
    --max-ttft 100 --max-itl 10 --max-latency 2.0

  # Estimate with GPU-specific limits
  config-explorer estimate --model Qwen/Qwen3-32B --input-len 512 --output-len 128 \\
    --max-gpus 4 --max-gpus-per-type H100:8 --max-gpus-per-type A100:4 \\
    --output estimate_results.json
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Start command
    subparsers.add_parser(
        'start',
        help='Start the Streamlit UI'
    )

    # Plan command
    plan_parser = subparsers.add_parser(
        'plan',
        help='Run capacity planning analysis'
    )

    # Model parameters
    plan_parser.add_argument(
        '--model',
        type=str,
        required=True,
        help='HuggingFace model ID (e.g., Qwen/Qwen3-32B, meta-llama/Llama-2-70b-hf)'
    )

    # GPU parameters
    plan_parser.add_argument(
        '--gpu-memory',
        type=int,
        help='GPU memory in GB (required for GPU-related calculations like allocatable KV cache)'
    )

    # Workload parameters
    plan_parser.add_argument(
        '--max-model-len',
        type=int,
        help='Maximum model context length in tokens (default: model\'s max_position_embeddings)'
    )

    plan_parser.add_argument(
        '--batch-size',
        type=int,
        default=1,
        help='Batch size for KV cache calculation (default: 1)'
    )

    # Parallelism parameters
    plan_parser.add_argument(
        '--tp',
        type=int,
        default=1,
        help='Tensor parallelism degree (default: 1)'
    )

    plan_parser.add_argument(
        '--pp',
        type=int,
        default=1,
        help='Pipeline parallelism degree (default: 1)'
    )

    plan_parser.add_argument(
        '--dp',
        type=int,
        default=1,
        help='Data parallelism degree (default: 1)'
    )

    # Memory parameters
    plan_parser.add_argument(
        '--gpu-mem-util',
        type=float,
        default=0.9,
        help='GPU memory utilization factor (default: 0.9)'
    )

    plan_parser.add_argument(
        '--block-size',
        type=int,
        help='KV cache block size (e.g., 16, 32)'
    )

    # Output options
    plan_parser.add_argument(
        '--output',
        '-o',
        type=str,
        help='Output file path for JSON results (prints to console if not specified)'
    )

    plan_parser.add_argument(
        '--show-possible-tp',
        action='store_true',
        help='Show possible tensor parallelism values for the model'
    )

    plan_parser.add_argument(
        '--verbose',
        '-v',
        action='store_true',
        help='Enable verbose output'
    )

    # Estimate command (GPU performance estimation and recommendation)
    estimate_parser = subparsers.add_parser(
        'estimate',
        help='Run GPU performance estimation and recommendation'
    )

    # Model parameters
    estimate_parser.add_argument(
        '--model',
        type=str,
        required=True,
        help='HuggingFace model ID (e.g., Qwen/Qwen3-32B, meta-llama/Llama-2-70b-hf)'
    )

    # Workload parameters
    estimate_parser.add_argument(
        '--input-len',
        type=int,
        required=True,
        help='Input sequence length in tokens'
    )

    estimate_parser.add_argument(
        '--output-len',
        type=int,
        required=True,
        help='Output sequence length in tokens'
    )

    # GPU parameters
    estimate_parser.add_argument(
        '--max-gpus',
        type=int,
        default=1,
        help='Default maximum number of GPUs to use for all GPU types (default: 1)'
    )

    estimate_parser.add_argument(
        '--max-gpus-per-type',
        type=str,
        action='append',
        help='GPU-specific max GPU limit in format GPU_NAME:MAX_COUNT (e.g., H100:8). Can be specified multiple times.'
    )

    estimate_parser.add_argument(
        '--gpu-list',
        type=str,
        help='Comma-separated list of GPU names to evaluate (e.g., H100,A100,L40). If not specified, evaluates all available GPUs.'
    )

    # Performance constraints
    estimate_parser.add_argument(
        '--max-ttft',
        type=float,
        help='Maximum time to first token constraint in milliseconds (ms)'
    )

    estimate_parser.add_argument(
        '--max-itl',
        type=float,
        help='Maximum inter-token latency constraint in milliseconds (ms)'
    )

    # Cost parameters
    estimate_parser.add_argument(
        '--custom-gpu-cost',
        type=str,
        action='append',
        help='Custom GPU cost in format GPU_NAME:COST (e.g., H100:30.5). Can be specified multiple times. Use any numbers for relative comparison (e.g., your actual $/hour or $/token pricing).'
    )

    estimate_parser.add_argument(
        '--max-latency',
        type=float,
        help='Maximum end-to-end latency constraint in seconds (s)'
    )

    # Output options
    estimate_parser.add_argument(
        '--output',
        '-o',
        type=str,
        help='Output file path for JSON results (prints to console if not specified)'
    )

    estimate_parser.add_argument(
        '--verbose',
        '-v',
        action='store_true',
        help='Enable verbose output with detailed results for all GPUs'
    )

    estimate_parser.add_argument(
        '--pretty',
        action='store_true',
        help='Output results in human-readable format instead of JSON (results sorted by cost for GPUs meeting performance requirements)'
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == 'start':
        start_ui()
    elif args.command == 'plan':
        plan_capacity(args)
    elif args.command == 'estimate':
        estimate_performance(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
