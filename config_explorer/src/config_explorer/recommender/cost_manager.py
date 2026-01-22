"""Cost management for GPU recommendations"""
import json
from pathlib import Path
from typing import Dict, Optional, Literal


CostUnit = Literal["per_hour", "per_1m_tokens"]


class CostManager:
    """Manages GPU cost data with support for defaults and user overrides"""

    def __init__(
        self,
        custom_costs: Optional[Dict[str, float]] = None,
        cost_unit: CostUnit = "per_hour"
    ):
        """
        Initialize cost manager

        Args:
            custom_costs: Optional dict mapping GPU names to custom costs
            cost_unit: Unit for costs - either "per_hour" ($/hour) or "per_1m_tokens" ($/1M tokens)

        Raises:
            ValueError: If custom_costs contains invalid values or cost_unit is invalid
        """
        if cost_unit not in ["per_hour", "per_1m_tokens"]:
            raise ValueError(f"Invalid cost_unit: {cost_unit}. Must be 'per_hour' or 'per_1m_tokens'")

        self.cost_unit = cost_unit
        self.default_costs = self._load_default_costs()

        # Validate custom costs
        if custom_costs:
            for gpu_name, cost in custom_costs.items():
                if cost is not None and (not isinstance(cost, (int, float)) or cost < 0):
                    raise ValueError(f"Invalid cost for {gpu_name}: {cost}. Cost must be a non-negative number.")

        self.custom_costs = custom_costs or {}

        # Track if any custom costs were provided
        self.has_custom_costs = bool(custom_costs and any(v is not None for v in custom_costs.values()))

    def get_cost(self, gpu_name: str, num_gpus: int = 1) -> Optional[float]:
        """
        Get cost for GPU configuration

        Args:
            gpu_name: Name of the GPU
            num_gpus: Number of GPUs

        Returns:
            Total cost (in the configured unit) or None if cost not available
        """
        # Check custom costs first
        if gpu_name in self.custom_costs:
            custom_cost = self.custom_costs[gpu_name]
            if custom_cost is not None:
                return custom_cost * num_gpus

        # Fall back to default costs
        if gpu_name in self.default_costs:
            cost_field = f"cost_{self.cost_unit}"
            if cost_field in self.default_costs[gpu_name]:
                return self.default_costs[gpu_name][cost_field] * num_gpus

        return None

    def get_all_costs(self) -> Dict[str, float]:
        """
        Get all GPU costs (custom overrides defaults)

        Returns:
            Dict mapping GPU names to cost (in the configured unit)
        """
        costs = {}
        cost_field = f"cost_{self.cost_unit}"

        # Start with defaults (skip non-GPU entries like _disclaimer, _cost_units)
        for gpu_name, data in self.default_costs.items():
            if isinstance(data, dict) and cost_field in data:
                costs[gpu_name] = data[cost_field]

        # Override with custom costs (filter out None values)
        for gpu_name, cost in self.custom_costs.items():
            if cost is not None:
                costs[gpu_name] = cost

        return costs

    def has_cost(self, gpu_name: str) -> bool:
        """
        Check if cost data is available for a GPU

        Args:
            gpu_name: Name of the GPU

        Returns:
            True if cost data is available, False otherwise
        """
        return gpu_name in self.custom_costs or gpu_name in self.default_costs

    def get_cost_unit_label(self) -> str:
        """
        Get human-readable label for the current cost unit

        Returns:
            Label string (e.g., "$/hour" or "$/1M tokens")
        """
        if self.cost_unit == "per_hour":
            return "/hour"
        elif self.cost_unit == "per_1m_tokens":
            return "/1M tokens"
        return self.cost_unit

    def is_using_custom_costs(self) -> bool:
        """
        Check if any custom costs are being used

        Returns:
            True if custom costs were provided, False if using only defaults
        """
        return self.has_custom_costs

    def _load_default_costs(self) -> Dict[str, Dict]:
        """
        Load default costs from JSON file

        Returns:
            Dict mapping GPU names to cost data dictionaries
        """
        # Navigate from recommender module to config_explorer root
        cost_file = Path(__file__).parent.parent.parent.parent / "gpu_costs.json"

        if cost_file.exists():
            with open(cost_file, 'r') as f:
                return json.load(f)

        return {}
