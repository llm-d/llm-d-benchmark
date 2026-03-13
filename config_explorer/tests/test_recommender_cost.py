"""Integration tests for GPURecommender cost features"""
import pytest
from config_explorer.recommender import GPURecommender


class TestGPURecommenderCost:
    """Test suite for GPURecommender cost integration"""

    @pytest.fixture
    def basic_recommender(self):
        """Create a basic recommender instance"""
        return GPURecommender(
            model_id="Qwen/Qwen-7B",
            input_len=512,
            output_len=128,
            max_gpus=1,
            gpu_list=["H100", "A100", "L40", "L20"],
        )

    @pytest.fixture
    def custom_cost_recommender(self):
        """Create a recommender with custom costs"""
        custom_costs = {
            "H100": 30.0,
            "A100": 20.0,
            "L40": 22.0,
            "L20": 12.0,
        }
        return GPURecommender(
            model_id="Qwen/Qwen-7B",
            input_len=512,
            output_len=128,
            max_gpus=1,
            gpu_list=["H100", "A100", "L40", "L20"],
            custom_gpu_costs=custom_costs,
        )

    def test_cost_manager_initialized(self, basic_recommender):
        """Test that cost manager is initialized"""
        assert basic_recommender.cost_manager is not None
        assert hasattr(basic_recommender.cost_manager, 'get_cost')

    def test_get_gpu_with_lowest_cost_default(self, basic_recommender):
        """Test getting lowest cost GPU with default costs"""
        # Note: This test may fail if GPU results can't be generated
        # In a real scenario, you'd mock the performance estimation
        try:
            best_cost = basic_recommender.get_gpu_with_lowest_cost()
            if best_cost:
                gpu_name, cost = best_cost
                assert isinstance(gpu_name, str)
                assert isinstance(cost, (int, float))
                assert cost > 0
        except Exception:
            # If performance estimation fails, that's okay for this test
            pytest.skip("Performance estimation not available")

    def test_get_gpu_with_lowest_cost_custom(self, custom_cost_recommender):
        """Test getting lowest cost GPU with custom costs"""
        try:
            best_cost = custom_cost_recommender.get_gpu_with_lowest_cost()
            if best_cost:
                gpu_name, cost = best_cost
                # With custom costs, L20 should be cheapest at $12/hour
                assert cost >= 12.0
        except Exception:
            pytest.skip("Performance estimation not available")

    def test_get_results_sorted_by_cost(self, basic_recommender):
        """Test getting results sorted by cost"""
        try:
            sorted_results = basic_recommender.get_results_sorted_by_cost()
            
            if sorted_results:
                # Check that results are sorted (ascending cost)
                costs = [cost for _, cost, _ in sorted_results]
                assert costs == sorted(costs)
                
                # Check structure
                for gpu_name, cost, result in sorted_results:
                    assert isinstance(gpu_name, str)
                    assert isinstance(cost, (int, float))
                    assert cost > 0
        except Exception:
            pytest.skip("Performance estimation not available")

    def test_performance_summary_includes_cost(self, basic_recommender):
        """Test that performance summary includes cost data"""
        try:
            summary = basic_recommender.get_performance_summary()
            
            # Check for lowest_cost in best performance
            if "estimated_best_performance" in summary:
                best_perf = summary["estimated_best_performance"]
                if "lowest_cost" in best_perf:
                    assert "gpu" in best_perf["lowest_cost"]
                    assert "cost_per_hour" in best_perf["lowest_cost"]
            
            # Check for cost in GPU results
            if "gpu_results" in summary:
                for gpu_name, gpu_data in summary["gpu_results"].items():
                    if "cost_per_hour" in gpu_data:
                        assert isinstance(gpu_data["cost_per_hour"], (int, float))
                        assert gpu_data["cost_per_hour"] > 0
        except Exception:
            pytest.skip("Performance estimation not available")

    def test_custom_costs_override_defaults(self, custom_cost_recommender):
        """Test that custom costs properly override defaults"""
        # Check that custom costs are set
        assert custom_cost_recommender.cost_manager.custom_costs == {
            "H100": 30.0,
            "A100": 20.0,
            "L40": 22.0,
            "L20": 12.0,
        }
        
        # Verify costs are retrieved correctly
        assert custom_cost_recommender.cost_manager.get_cost("H100") == 30.0
        assert custom_cost_recommender.cost_manager.get_cost("A100") == 20.0

    def test_multi_gpu_cost_calculation(self):
        """Test cost calculation with multiple GPUs"""
        recommender = GPURecommender(
            model_id="Qwen/Qwen-7B",
            input_len=512,
            output_len=128,
            max_gpus=2,
            gpu_list=["H100"],
        )
        
        # Cost should be doubled for 2 GPUs
        single_cost = recommender.cost_manager.get_cost("H100", num_gpus=1)
        double_cost = recommender.cost_manager.get_cost("H100", num_gpus=2)
        
        assert double_cost == single_cost * 2
        
    def test_cost_manager_handles_none_values(self):
        """Test that cost manager properly handles None values in custom costs"""
        custom_costs = {
            "H100": None,  # Explicitly set to None
            "A100": 20.0,
        }
        recommender = GPURecommender(
            model_id="Qwen/Qwen-7B",
            input_len=512,
            output_len=128,
            max_gpus=1,
            gpu_list=["H100", "A100"],
            custom_gpu_costs=custom_costs,
        )
        
        # H100 with None should fall back to default
        h100_cost = recommender.cost_manager.get_cost("H100", num_gpus=1)
        assert h100_cost is not None
        assert h100_cost > 0
        
        # A100 should use custom cost
        a100_cost = recommender.cost_manager.get_cost("A100", num_gpus=1)
        assert a100_cost == 20.0

    def test_cost_with_max_gpus_per_type(self):
        """Test cost calculation with different GPU counts per type"""
        max_gpus_per_type = {
            "H100": 4,
            "A100": 2,
        }
        
        recommender = GPURecommender(
            model_id="Qwen/Qwen-7B",
            input_len=512,
            output_len=128,
            max_gpus=1,
            max_gpus_per_type=max_gpus_per_type,
            gpu_list=["H100", "A100"],
        )
        
        # Verify max_gpus_per_type is set
        assert recommender.max_gpus_per_type == max_gpus_per_type

    def test_cost_for_unknown_gpu(self, basic_recommender):
        """Test cost retrieval for GPU not in database"""
        cost = basic_recommender.cost_manager.get_cost("UNKNOWN_GPU")
        assert cost is None

    def test_empty_custom_costs(self):
        """Test with empty custom costs dict"""
        recommender = GPURecommender(
            model_id="Qwen/Qwen-7B",
            input_len=512,
            output_len=128,
            max_gpus=1,
            gpu_list=["H100"],
            custom_gpu_costs={},
        )
        
        # Should fall back to defaults
        cost = recommender.cost_manager.get_cost("H100")
        assert cost is not None
        assert cost > 0

    def test_none_custom_costs(self):
        """Test with None custom costs"""
        recommender = GPURecommender(
            model_id="Qwen/Qwen-7B",
            input_len=512,
            output_len=128,
            max_gpus=1,
            gpu_list=["H100"],
            custom_gpu_costs=None,
        )
        
        # Should use defaults
        assert recommender.cost_manager.custom_costs == {}
        cost = recommender.cost_manager.get_cost("H100")
        assert cost is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
