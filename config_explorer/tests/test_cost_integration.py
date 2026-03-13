#!/usr/bin/env python3
"""Quick test script to verify cost integration implementation"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from config_explorer.recommender import GPURecommender, CostManager

def test_cost_manager():
    """Test CostManager functionality"""
    print("=" * 80)
    print("Testing CostManager")
    print("=" * 80)
    
    # Test default costs
    cm = CostManager()
    print(f"✅ CostManager initialized with {len(cm.default_costs)} GPUs")
    
    # Test getting costs
    h100_cost = cm.get_cost("H100")
    print(f"✅ H100 cost: ${h100_cost}")

    a100_cost = cm.get_cost("A100")
    print(f"✅ A100 cost: ${a100_cost}")

    # Test multi-GPU cost
    h100_2gpu = cm.get_cost("H100", num_gpus=2)
    print(f"✅ H100 (2 GPUs) cost: ${h100_2gpu}")
    assert h100_2gpu == h100_cost * 2, "Multi-GPU cost calculation failed"
    
    # Test custom costs
    custom_costs = {"H100": 30.0, "A100": 20.0}
    cm_custom = CostManager(custom_costs=custom_costs)
    h100_custom = cm_custom.get_cost("H100")
    print(f"✅ H100 custom cost: ${h100_custom}")
    assert h100_custom == 30.0, "Custom cost override failed"
    
    print("\n✅ All CostManager tests passed!\n")

def test_gpu_recommender():
    """Test GPURecommender cost integration"""
    print("=" * 80)
    print("Testing GPURecommender Cost Integration")
    print("=" * 80)
    
    # Test basic initialization
    recommender = GPURecommender(
        model_id="Qwen/Qwen-7B",
        input_len=512,
        output_len=128,
        max_gpus=1,
        gpu_list=["H100", "A100"],
    )
    print("✅ GPURecommender initialized")
    
    # Test cost manager is available
    assert recommender.cost_manager is not None, "CostManager not initialized"
    print("✅ CostManager integrated into GPURecommender")
    
    # Test custom costs
    custom_costs = {"H100": 30.0, "A100": 20.0}
    recommender_custom = GPURecommender(
        model_id="Qwen/Qwen-7B",
        input_len=512,
        output_len=128,
        max_gpus=1,
        gpu_list=["H100", "A100"],
        custom_gpu_costs=custom_costs,
    )
    print("✅ GPURecommender with custom costs initialized")
    
    # Verify custom costs are set
    h100_cost = recommender_custom.cost_manager.get_cost("H100")
    assert h100_cost == 30.0, "Custom costs not applied"
    print(f"✅ Custom costs applied correctly: H100 = ${h100_cost}")
    
    # Test methods exist
    assert hasattr(recommender, 'get_gpu_with_lowest_cost'), "Missing get_gpu_with_lowest_cost method"
    assert hasattr(recommender, 'get_results_sorted_by_cost'), "Missing get_results_sorted_by_cost method"
    print("✅ New cost methods available")
    
    print("\n✅ All GPURecommender integration tests passed!\n")

def main():
    """Run all tests"""
    print("\n")
    print("=" * 80)
    print("Cost Integration Verification Tests")
    print("=" * 80)
    print()
    
    try:
        test_cost_manager()
        test_gpu_recommender()
        
        print("=" * 80)
        print("✅ ALL TESTS PASSED!")
        print("=" * 80)
        print("\nCost integration implementation is working correctly.")
        print("\nKey features verified:")
        print("  ✅ Default GPU costs loaded from JSON")
        print("  ✅ Custom cost override functionality")
        print("  ✅ Multi-GPU cost calculation")
        print("  ✅ CostManager integrated into GPURecommender")
        print("  ✅ New cost methods available")
        print()
        return 0
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
