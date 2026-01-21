"""Cost management for GPU recommendations"""
import json
from pathlib import Path
from typing import Dict, Optional


class CostManager:
    """Manages GPU cost data with support for defaults and user overrides"""
    
    def __init__(self, custom_costs: Optional[Dict[str, float]] = None):
        """
        Initialize cost manager
        
        Args:
            custom_costs: Optional dict mapping GPU names to custom costs ($/hour)
            
        Raises:
            ValueError: If custom_costs contains invalid values
        """
        self.default_costs = self._load_default_costs()
        
        # Validate custom costs
        if custom_costs:
            for gpu_name, cost in custom_costs.items():
                if cost is not None and (not isinstance(cost, (int, float)) or cost < 0):
                    raise ValueError(f"Invalid cost for {gpu_name}: {cost}. Cost must be a non-negative number.")
        
        self.custom_costs = custom_costs or {}
    
    def get_cost(self, gpu_name: str, num_gpus: int = 1) -> Optional[float]:
        """
        Get cost for GPU configuration
        
        Args:
            gpu_name: Name of the GPU
            num_gpus: Number of GPUs
            
        Returns:
            Total cost per hour or None if cost not available
        """
        # Check custom costs first
        if gpu_name in self.custom_costs:
            custom_cost = self.custom_costs[gpu_name]
            if custom_cost is not None:
                return custom_cost * num_gpus
        
        # Fall back to default costs
        if gpu_name in self.default_costs:
            return self.default_costs[gpu_name]["cost_per_hour"] * num_gpus
        
        return None
    
    def get_all_costs(self) -> Dict[str, float]:
        """
        Get all GPU costs (custom overrides defaults)
        
        Returns:
            Dict mapping GPU names to cost per hour
        """
        costs = {}
        
        # Start with defaults (skip non-GPU entries like _disclaimer)
        for gpu_name, data in self.default_costs.items():
            if isinstance(data, dict) and "cost_per_hour" in data:
                costs[gpu_name] = data["cost_per_hour"]
        
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
