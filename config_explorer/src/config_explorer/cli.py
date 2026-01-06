"""
CLI interface for config_explorer package
"""

import argparse
import json
import os
import subprocess
import sys
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


def start_ui():
    """Start the Streamlit UI"""

    # Get the path to Capacity_Planner.py
    config_explorer_dir = Path(__file__).parent.parent.parent
    ui_file = config_explorer_dir / "Capacity_Planner.py"

    if not ui_file.exists():
        print(f"Error: UI file not found at {ui_file}")
        sys.exit(1)

    print(f"Starting Config Explorer UI...")
    try:
        result = subprocess.run(["streamlit", "run", str(ui_file)])
        if result.returncode != 0:
            print(
                f"Error: Failed to start Streamlit UI (exit code {result.returncode})."
            )
            sys.exit(result.returncode)
    except FileNotFoundError:
        print(
            "Error: 'streamlit' command not found. Please install Streamlit and "
            "ensure it is available on your PATH."
        )
        sys.exit(1)
    except Exception as e:
        print(f"Error: Failed to start Streamlit UI: {e}")
        sys.exit(1)


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
                print(f"Error: Invalid --tp value {args.tp}. Valid values for this model are: {possible_tp}")
                sys.exit(1)

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
                tp, pp, dp
            )
            result["allocatable_kv_cache_memory_gb"] = round(allocatable_kv, 2)

            # Calculate max concurrent requests
            max_requests = max_concurrent_requests(
                model_info, model_config,
                max_model_len,
                gpu_memory_gb, gpu_mem_util,
                tp, pp, dp
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
        print(f"Error during capacity planning: {str(e)}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


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

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == 'start':
        start_ui()
    elif args.command == 'plan':
        plan_capacity(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
