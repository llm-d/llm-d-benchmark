#!/usr/bin/env python3
"""
Test suite for config_explorer CLI
"""

import subprocess
import json
import pytest


def run_cli(*args):
    """Run CLI command and return result"""
    cmd = ["config-explorer"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result


def parse_cli_json_output(stdout):
    """Parse JSON output from CLI, removing any prefixed messages"""
    # CLI outputs "Fetching model information..." before JSON
    # Split by newline and skip the first line(s) until we find JSON
    lines = stdout.split("\n")

    # Find where JSON starts (first line with '{')
    json_start = 0
    for i, line in enumerate(lines):
        if line.strip().startswith('{'):
            json_start = i
            break

    # Join remaining lines and parse
    json_output = "\n".join(lines[json_start:])
    return json.loads(json_output)


class TestHelp:
    """Test help and usage commands"""

    def test_help(self):
        """Test help command"""
        result = run_cli("--help")
        assert result.returncode == 0
        assert "config-explorer" in result.stdout.lower() or "config explorer" in result.stdout.lower()

    def test_plan_help(self):
        """Test plan help"""
        result = run_cli("plan", "--help")
        assert result.returncode == 0
        assert "--model" in result.stdout

    def test_start_help(self):
        """Test start help"""
        result = run_cli("start", "--help")
        assert result.returncode == 0


class TestBasicPlan:
    """Test basic capacity planning functionality"""

    def test_basic_plan_with_defaults(self):
        """Test basic capacity planning with default max_model_len"""
        result = run_cli("plan", "--model", "Qwen/Qwen2.5-3B")

        assert result.returncode == 0

        data = parse_cli_json_output(result.stdout)
        assert "model_memory_gb" in data
        assert "input_parameters" in data
        assert "kv_cache_detail" in data
        assert data["input_parameters"]["model"] == "Qwen/Qwen2.5-3B"
        assert "max_model_len" in data["input_parameters"]
        assert data["input_parameters"]["max_model_len"] > 0
        assert "batch_size" in data["input_parameters"]
        assert data["input_parameters"]["batch_size"] == 1

    def test_plan_with_custom_max_model_len(self):
        """Test capacity planning with explicit context length"""
        result = run_cli(
            "plan",
            "--model", "Qwen/Qwen2.5-3B",
            "--max-model-len", "8192"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)
        assert "kv_cache_detail" in data
        assert data["kv_cache_detail"]["context_len"] == 8192
        assert data["input_parameters"]["max_model_len"] == 8192

    def test_plan_with_batch_size(self):
        """Test capacity planning with custom batch size"""
        result = run_cli(
            "plan",
            "--model", "Qwen/Qwen2.5-3B",
            "--batch-size", "32"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)
        assert data["input_parameters"]["batch_size"] == 32
        assert data["kv_cache_detail"]["batch_size"] == 32


class TestGPUCalculations:
    """Test GPU-related calculations"""

    def test_plan_with_gpu_memory(self):
        """Test capacity planning with GPU memory"""
        result = run_cli(
            "plan",
            "--model", "Qwen/Qwen2.5-3B",
            "--gpu-memory", "80",
            "--max-model-len", "8192"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)
        assert "max_concurrent_requests" in data
        assert "total_kv_cache_blocks" in data
        assert "allocatable_kv_cache_memory_gb" in data
        assert "per_gpu_model_memory_gb" in data
        assert "total_gpus_required" in data
        assert data["input_parameters"]["tp"] == 1
        assert data["input_parameters"]["pp"] == 1
        assert data["input_parameters"]["dp"] == 1
        assert data["input_parameters"]["gpu_mem_util"] == 0.9
        assert data["input_parameters"]["block_size"] == 16  # Default block size

    def test_plan_with_parallelism(self):
        """Test capacity planning with tensor, pipeline, and data parallelism"""
        result = run_cli(
            "plan",
            "--model", "Qwen/Qwen2.5-3B",
            "--gpu-memory", "80",
            "--tp", "2",
            "--pp", "1",
            "--dp", "2"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)
        assert data["input_parameters"]["tp"] == 2
        assert data["input_parameters"]["pp"] == 1
        assert data["input_parameters"]["dp"] == 2
        assert data["total_gpus_required"] == 4  # tp * pp * dp

    def test_plan_with_custom_block_size(self):
        """Test capacity planning with custom block size"""
        result = run_cli(
            "plan",
            "--model", "Qwen/Qwen2.5-3B",
            "--gpu-memory", "80",
            "--block-size", "32"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)
        assert "total_kv_cache_blocks" in data
        assert data["input_parameters"]["block_size"] == 32

    def test_plan_with_gpu_mem_util(self):
        """Test capacity planning with custom GPU memory utilization"""
        result = run_cli(
            "plan",
            "--model", "Qwen/Qwen2.5-3B",
            "--gpu-memory", "80",
            "--gpu-mem-util", "0.85"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)
        assert data["input_parameters"]["gpu_mem_util"] == 0.85


class TestTPValidation:
    """Test tensor parallelism validation"""

    def test_show_possible_tp(self):
        """Test showing possible TP values"""
        result = run_cli(
            "plan",
            "--model", "Qwen/Qwen2.5-3B",
            "--show-possible-tp"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)
        assert "possible_tp_values" in data
        assert isinstance(data["possible_tp_values"], list)
        assert len(data["possible_tp_values"]) > 0
        assert 1 in data["possible_tp_values"]

    def test_valid_tp_value(self):
        """Test with a valid TP value"""
        result = run_cli(
            "plan",
            "--model", "Qwen/Qwen2.5-3B",
            "--gpu-memory", "80",
            "--tp", "4"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)
        assert data["input_parameters"]["tp"] == 4

    def test_invalid_tp_value(self):
        """Test with an invalid TP value"""
        result = run_cli(
            "plan",
            "--model", "Qwen/Qwen2.5-3B",
            "--tp", "11"
        )

        assert result.returncode != 0
        assert "Invalid --tp value" in result.stdout or "Invalid --tp value" in result.stderr


class TestOutputFormats:
    """Test output format and file writing"""

    def test_output_to_file(self, tmp_path):
        """Test writing output to file"""
        output_file = tmp_path / "results.json"

        result = run_cli(
            "plan",
            "--model", "Qwen/Qwen2.5-3B",
            "--output", str(output_file)
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"
        assert output_file.exists()

        with open(output_file, 'r') as f:
            data = json.load(f)
            assert "model_memory_gb" in data
            assert "input_parameters" in data

    def test_json_output_format(self):
        """Test that output is valid JSON"""
        result = run_cli("plan", "--model", "Qwen/Qwen2.5-3B")

        assert result.returncode == 0, f"Failed: {result.stderr}"

        # Should be able to parse as JSON
        data = parse_cli_json_output(result.stdout)
        assert isinstance(data, dict)


class TestKVCacheDetails:
    """Test KV cache detail calculations"""

    def test_kv_cache_always_present(self):
        """Test that KV cache details are always calculated"""
        result = run_cli("plan", "--model", "Qwen/Qwen2.5-3B")

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)
        assert "kv_cache_detail" in data
        kv_detail = data["kv_cache_detail"]

        # Check all expected fields
        assert "attention_type" in kv_detail
        assert "kv_data_type" in kv_detail
        assert "num_hidden_layers" in kv_detail
        assert "num_attention_heads" in kv_detail
        assert "num_key_value_heads" in kv_detail
        assert "head_dimension" in kv_detail
        assert "per_token_memory_bytes" in kv_detail
        assert "per_request_kv_cache_gb" in kv_detail
        assert "kv_cache_size_gb" in kv_detail
        assert "context_len" in kv_detail
        assert "batch_size" in kv_detail

    def test_kv_cache_with_different_context_lengths(self):
        """Test KV cache scales with context length"""
        result_short = run_cli(
            "plan",
            "--model", "Qwen/Qwen2.5-3B",
            "--max-model-len", "2048"
        )
        result_long = run_cli(
            "plan",
            "--model", "Qwen/Qwen2.5-3B",
            "--max-model-len", "8192"
        )

        assert result_short.returncode == 0
        assert result_long.returncode == 0

        data_short = parse_cli_json_output(result_short.stdout)
        data_long = parse_cli_json_output(result_long.stdout)

        # Longer context should require more KV cache
        assert data_long["kv_cache_detail"]["kv_cache_size_gb"] > \
               data_short["kv_cache_detail"]["kv_cache_size_gb"]


class TestErrorHandling:
    """Test error handling and validation"""

    def test_missing_model(self):
        """Test error when model is not specified"""
        result = run_cli("plan")
        assert result.returncode != 0

    def test_invalid_model(self):
        """Test error with non-existent model"""
        result = run_cli("plan", "--model", "invalid/model-name-that-does-not-exist-12345")
        assert result.returncode != 0

    def test_verbose_flag(self):
        """Test verbose flag with an error"""
        result = run_cli(
            "plan",
            "--model", "invalid/model-that-does-not-exist",
            "--verbose"
        )
        assert result.returncode != 0
        # Verbose should show traceback
        assert len(result.stderr) > 0 or len(result.stdout) > 0


class TestModelInfo:
    """Test model information output"""

    def test_model_info_present(self):
        """Test that model info is included in output"""
        result = run_cli("plan", "--model", "Qwen/Qwen2.5-3B")

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)
        assert "model_info" in data
        assert "total_parameters" in data["model_info"]
        assert data["model_info"]["total_parameters"] > 0


class TestIntegration:
    """Integration tests with multiple parameters"""

    def test_full_deployment_planning(self):
        """Test complete deployment planning scenario"""
        result = run_cli(
            "plan",
            "--model", "Qwen/Qwen2.5-3B",
            "--gpu-memory", "80",
            "--max-model-len", "8192",
            "--batch-size", "64",
            "--tp", "2",
            "--pp", "1",
            "--dp", "2",
            "--block-size", "32",
            "--gpu-mem-util", "0.85"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)

        # Verify all parameters are recorded
        assert data["input_parameters"]["max_model_len"] == 8192
        assert data["input_parameters"]["batch_size"] == 64
        assert data["input_parameters"]["tp"] == 2
        assert data["input_parameters"]["pp"] == 1
        assert data["input_parameters"]["dp"] == 2
        assert data["input_parameters"]["block_size"] == 32
        assert data["input_parameters"]["gpu_mem_util"] == 0.85

        # Verify calculations
        assert data["total_gpus_required"] == 4
        assert "max_concurrent_requests" in data
        assert "total_kv_cache_blocks" in data
        assert "allocatable_kv_cache_memory_gb" in data


class TestEstimateCommand:
    """Test GPU performance estimation command"""

    def test_estimate_help(self):
        """Test estimate help command"""
        result = run_cli("estimate", "--help")
        assert result.returncode == 0
        assert "--model" in result.stdout
        assert "--input-len" in result.stdout
        assert "--output-len" in result.stdout

    def test_basic_estimate(self):
        """Test basic performance estimation"""
        result = run_cli(
            "estimate",
            "--model", "Qwen/Qwen-7B",
            "--input-len", "512",
            "--output-len", "128"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)
        assert "input_parameters" in data
        assert "estimated_best_performance" in data
        assert "gpu_results" in data
        assert "summary" in data

        # Check input parameters
        assert data["input_parameters"]["model"] == "Qwen/Qwen-7B"
        assert data["input_parameters"]["input_len"] == 512
        assert data["input_parameters"]["output_len"] == 128
        assert data["input_parameters"]["max_gpus"] == 1
        assert "gpu_list" in data["input_parameters"]
        assert len(data["input_parameters"]["gpu_list"]) > 0

    def test_estimate_with_specific_gpus(self):
        """Test estimation with specific GPU list"""
        result = run_cli(
            "estimate",
            "--model", "Qwen/Qwen-7B",
            "--input-len", "512",
            "--output-len", "128",
            "--gpu-list", "H100,A100"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)
        assert data["input_parameters"]["gpu_list"] == ["H100", "A100"]

        # Check that results only include specified GPUs (or failures)
        all_gpus = set(data["gpu_results"].keys()) | set(data["failed_gpus"].keys())
        assert all_gpus.issubset({"H100", "A100"})

    def test_estimate_with_max_gpus(self):
        """Test estimation with custom max GPUs"""
        result = run_cli(
            "estimate",
            "--model", "Qwen/Qwen-7B",
            "--input-len", "512",
            "--output-len", "128",
            "--max-gpus", "4",
            "--gpu-list", "H100"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)
        assert data["input_parameters"]["max_gpus"] == 4

    def test_estimate_with_max_gpus_per_type(self):
        """Test estimation with GPU-specific limits"""
        result = run_cli(
            "estimate",
            "--model", "Qwen/Qwen-7B",
            "--input-len", "512",
            "--output-len", "128",
            "--max-gpus-per-type", "H100:8",
            "--max-gpus-per-type", "A100:4",
            "--gpu-list", "H100,A100"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)
        assert "max_gpus_per_type" in data["input_parameters"]
        assert data["input_parameters"]["max_gpus_per_type"]["H100"] == 8
        assert data["input_parameters"]["max_gpus_per_type"]["A100"] == 4

    def test_estimate_with_ttft_constraint(self):
        """Test estimation with TTFT constraint"""
        result = run_cli(
            "estimate",
            "--model", "Qwen/Qwen-7B",
            "--input-len", "512",
            "--output-len", "128",
            "--max-ttft", "100.0",
            "--gpu-list", "H100"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)
        assert data["input_parameters"]["max_ttft_ms"] == 100.0

    def test_estimate_with_itl_constraint(self):
        """Test estimation with ITL constraint"""
        result = run_cli(
            "estimate",
            "--model", "Qwen/Qwen-7B",
            "--input-len", "512",
            "--output-len", "128",
            "--max-itl", "10.0",
            "--gpu-list", "H100"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)
        assert data["input_parameters"]["max_itl_ms"] == 10.0

    def test_estimate_with_latency_constraint(self):
        """Test estimation with E2E latency constraint"""
        result = run_cli(
            "estimate",
            "--model", "Qwen/Qwen-7B",
            "--input-len", "512",
            "--output-len", "128",
            "--max-latency", "2.0",
            "--gpu-list", "H100"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)
        assert data["input_parameters"]["max_latency_s"] == 2.0

    def test_estimate_with_all_constraints(self):
        """Test estimation with all performance constraints"""
        result = run_cli(
            "estimate",
            "--model", "Qwen/Qwen-7B",
            "--input-len", "512",
            "--output-len", "128",
            "--max-ttft", "100.0",
            "--max-itl", "10.0",
            "--max-latency", "2.0",
            "--gpu-list", "H100"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)
        assert data["input_parameters"]["max_ttft_ms"] == 100.0
        assert data["input_parameters"]["max_itl_ms"] == 10.0
        assert data["input_parameters"]["max_latency_s"] == 2.0

    def test_estimate_output_structure(self):
        """Test that estimate output has correct structure"""
        result = run_cli(
            "estimate",
            "--model", "Qwen/Qwen-7B",
            "--input-len", "512",
            "--output-len", "128",
            "--gpu-list", "H100"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)

        # Check main sections
        assert "input_parameters" in data
        assert "estimated_best_performance" in data
        assert "gpu_results" in data
        assert "failed_gpus" in data
        assert "summary" in data

        # Check summary structure
        assert "total_gpus_analyzed" in data["summary"]
        assert "failed_gpus" in data["summary"]

    def test_estimate_gpu_results_structure(self):
        """Test GPU results have correct structure"""
        result = run_cli(
            "estimate",
            "--model", "Qwen/Qwen-7B",
            "--input-len", "512",
            "--output-len", "128",
            "--gpu-list", "H100"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)

        # If H100 succeeded, check its structure
        if "H100" in data["gpu_results"]:
            gpu_data = data["gpu_results"]["H100"]

            # Should have best_latency config
            if "best_latency" in gpu_data:
                best_latency = gpu_data["best_latency"]
                assert "optimal_concurrency" in best_latency
                assert best_latency["optimal_concurrency"] == 1
                assert "throughput_tps" in best_latency
                assert "ttft_ms" in best_latency
                assert "itl_ms" in best_latency
                assert "e2e_latency_s" in best_latency

            # Should have best_output_throughput config
            if "best_output_throughput" in gpu_data:
                best_throughput = gpu_data["best_output_throughput"]
                assert "optimal_concurrency" in best_throughput
                assert "throughput_tps" in best_throughput

    def test_estimate_performance_recommendations(self):
        """Test that performance recommendations are present"""
        result = run_cli(
            "estimate",
            "--model", "Qwen/Qwen-7B",
            "--input-len", "512",
            "--output-len", "128",
            "--gpu-list", "H100,A100"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)

        # Check estimated_best_performance section
        estimated_best_perf = data["estimated_best_performance"]

        # At least some recommendations should be present if any GPUs succeeded
        if len(data["gpu_results"]) > 0:
            possible_keys = ["highest_throughput", "lowest_ttft", "lowest_itl", "lowest_e2e_latency"]
            assert any(key in estimated_best_perf for key in possible_keys)

    def test_estimate_verbose_mode(self):
        """Test verbose mode includes concurrency analysis"""
        result = run_cli(
            "estimate",
            "--model", "Qwen/Qwen-7B",
            "--input-len", "512",
            "--output-len", "128",
            "--gpu-list", "H100",
            "--verbose"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)

        # In verbose mode, should have concurrency_analysis if available
        if "H100" in data["gpu_results"]:
            gpu_data = data["gpu_results"]["H100"]
            # May or may not have concurrency_analysis depending on the result structure
            # but verbose mode should at least show detailed results
            assert "best_latency" in gpu_data or "best_output_throughput" in gpu_data

    def test_estimate_output_to_file(self, tmp_path):
        """Test writing estimate output to file"""
        output_file = tmp_path / "estimate_results.json"

        result = run_cli(
            "estimate",
            "--model", "Qwen/Qwen-7B",
            "--input-len", "512",
            "--output-len", "128",
            "--gpu-list", "H100",
            "--output", str(output_file)
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"
        assert output_file.exists()

        with open(output_file, 'r') as f:
            data = json.load(f)
            assert "input_parameters" in data
            assert "estimated_best_performance" in data
            assert "gpu_results" in data

    def test_estimate_missing_required_args(self):
        """Test error when required arguments are missing"""
        # Missing model
        result = run_cli("estimate", "--input-len", "512", "--output-len", "128")
        assert result.returncode != 0

        # Missing input-len
        result = run_cli("estimate", "--model", "Qwen/Qwen-7B", "--output-len", "128")
        assert result.returncode != 0

        # Missing output-len
        result = run_cli("estimate", "--model", "Qwen/Qwen-7B", "--input-len", "512")
        assert result.returncode != 0

    def test_estimate_gpu_list_with_spaces(self):
        """Test GPU list with spaces is accepted and trimmed"""
        result = run_cli(
            "estimate",
            "--model", "Qwen/Qwen-7B",
            "--input-len", "512",
            "--output-len", "128",
            "--gpu-list", "H100, A100, L40"  # With spaces - should be trimmed
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)
        # Spaces should be stripped
        assert "H100" in data["input_parameters"]["gpu_list"]
        assert "A100" in data["input_parameters"]["gpu_list"]
        assert "L40" in data["input_parameters"]["gpu_list"]

    def test_estimate_invalid_max_gpus_per_type_format(self):
        """Test error with invalid max-gpus-per-type format"""
        result = run_cli(
            "estimate",
            "--model", "Qwen/Qwen-7B",
            "--input-len", "512",
            "--output-len", "128",
            "--max-gpus-per-type", "H100:invalid"
        )

        assert result.returncode != 0
        assert "Invalid format" in result.stdout or "Invalid format" in result.stderr

    def test_estimate_with_large_model(self):
        """Test estimation with a large model that may fail on some GPUs"""
        result = run_cli(
            "estimate",
            "--model", "Qwen/Qwen2.5-3B",
            "--input-len", "512",
            "--output-len", "128",
            "--gpu-list", "L20,H100"  # L20 likely to fail, H100 likely to succeed
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        data = parse_cli_json_output(result.stdout)

        # Should have both successful and failed GPUs
        total_analyzed = len(data["gpu_results"]) + len(data["failed_gpus"])
        assert total_analyzed == 2
        assert data["summary"]["total_gpus_analyzed"] == 2

    def test_estimate_json_output_validity(self):
        """Test that estimate output is valid JSON"""
        result = run_cli(
            "estimate",
            "--model", "Qwen/Qwen-7B",
            "--input-len", "512",
            "--output-len", "128",
            "--gpu-list", "H100"
        )

        assert result.returncode == 0, f"Failed: {result.stderr}"

        # Should be able to parse as JSON
        data = parse_cli_json_output(result.stdout)
        assert isinstance(data, dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
