"""Unit tests for CostManager"""
import pytest
import json
import tempfile
from pathlib import Path
from config_explorer.recommender.cost_manager import CostManager


class TestCostManager:
    """Test suite for CostManager class"""

    def test_init_default(self):
        """Test initialization with default costs"""
        manager = CostManager()
        assert manager.default_costs is not None
        assert isinstance(manager.default_costs, dict)
        assert manager.custom_costs == {}
        assert not hasattr(manager, 'cost_unit')  # Verify cost_unit is removed

    def test_init_with_custom_costs(self):
        """Test initialization with custom costs"""
        custom = {"H100": 30.0, "A100": 20.0}
        manager = CostManager(custom_costs=custom)
        assert manager.custom_costs == custom

    def test_get_cost_default(self):
        """Test getting cost from default database"""
        manager = CostManager()

        # Test known GPU
        cost = manager.get_cost("H100", num_gpus=1)
        assert cost is not None
        assert cost > 0

        # Test multi-GPU
        cost_multi = manager.get_cost("H100", num_gpus=2)
        assert cost_multi == cost * 2

    def test_get_cost_custom_override(self):
        """Test that custom costs override defaults"""
        custom = {"H100": 30.0}
        manager = CostManager(custom_costs=custom)

        cost = manager.get_cost("H100", num_gpus=1)
        assert cost == 30.0

    def test_get_cost_unknown_gpu(self):
        """Test getting cost for unknown GPU"""
        manager = CostManager()
        cost = manager.get_cost("UNKNOWN_GPU", num_gpus=1)
        assert cost is None

    def test_get_all_costs(self):
        """Test getting all costs"""
        manager = CostManager()
        all_costs = manager.get_all_costs()

        assert isinstance(all_costs, dict)
        assert len(all_costs) > 0
        assert "H100" in all_costs

    def test_get_all_costs_with_custom(self):
        """Test that get_all_costs includes custom overrides"""
        custom = {"H100": 30.0, "CUSTOM_GPU": 50.0}
        manager = CostManager(custom_costs=custom)

        all_costs = manager.get_all_costs()
        assert all_costs["H100"] == 30.0
        assert all_costs["CUSTOM_GPU"] == 50.0

    def test_has_cost(self):
        """Test checking if cost data exists"""
        manager = CostManager()

        assert manager.has_cost("H100") is True
        assert manager.has_cost("UNKNOWN_GPU") is False

    def test_has_cost_custom(self):
        """Test has_cost with custom costs"""
        custom = {"CUSTOM_GPU": 50.0}
        manager = CostManager(custom_costs=custom)

        assert manager.has_cost("CUSTOM_GPU") is True

    def test_multi_gpu_cost_calculation(self):
        """Test cost calculation for multiple GPUs"""
        manager = CostManager()

        single_cost = manager.get_cost("H100", num_gpus=1)
        double_cost = manager.get_cost("H100", num_gpus=2)
        quad_cost = manager.get_cost("H100", num_gpus=4)

        assert double_cost == single_cost * 2
        assert quad_cost == single_cost * 4

    def test_zero_gpus(self):
        """Test cost calculation with zero GPUs"""
        manager = CostManager()
        cost = manager.get_cost("H100", num_gpus=0)
        assert cost == 0

    def test_negative_gpus(self):
        """Test cost calculation with negative GPUs (edge case)"""
        manager = CostManager()
        cost = manager.get_cost("H100", num_gpus=-1)
        # Should still calculate (negative cost)
        assert cost is not None

    def test_default_costs_structure(self):
        """Test that default costs have expected structure"""
        manager = CostManager()

        for gpu_name, data in manager.default_costs.items():
            # Skip non-GPU entries like _disclaimer
            if not isinstance(data, dict):
                continue
            if "cost" not in data:
                continue

            assert "cost" in data
            assert "source" in data
            assert isinstance(data["cost"], (int, float))
            assert data["cost"] >= 0

    def test_custom_costs_override_all(self):
        """Test that custom costs can override multiple GPUs"""
        custom = {
            "H100": 30.0,
            "A100": 20.0,
            "L40": 25.0,
        }
        manager = CostManager(custom_costs=custom)

        assert manager.get_cost("H100") == 30.0
        assert manager.get_cost("A100") == 20.0
        assert manager.get_cost("L40") == 25.0

    def test_empty_custom_costs(self):
        """Test with empty custom costs dict"""
        manager = CostManager(custom_costs={})

        # Should fall back to defaults
        cost = manager.get_cost("H100")
        assert cost is not None
        assert cost > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
