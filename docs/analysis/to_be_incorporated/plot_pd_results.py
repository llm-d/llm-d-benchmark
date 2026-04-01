import json
import glob
import numpy as np
import matplotlib.pyplot as plt
import os

def load_and_average_metrics(directory):
    """Load all JSON files in a directory and calculate average metrics."""
    json_files = glob.glob(os.path.join(directory, "*.json"))
    print(f"Found {len(json_files)} JSON files in {directory}")
    
    metrics = {
        'mean_ttft_ms': [],
        'p95_ttft_ms': [],
        'mean_itl_ms': [],
        'p95_itl_ms': []
    }
    
    for file in json_files:
        with open(file, 'r') as f:
            data = json.load(f)
            for metric in metrics.keys():
                metrics[metric].append(data[metric])
    
    # Calculate averages
    averages = {k: np.mean(v) for k, v in metrics.items()}
    print(f"Averages for {directory}:", averages)
    return averages

def plot_comparison(llm_d_metrics, vllm_metrics, title_prefix, output_path):
    """Create a plot with two subplots comparing TTFT and ITL metrics."""
    print(f"\nPlotting comparison for {title_prefix}")
    print("llm-d metrics:", llm_d_metrics)
    print("vllm metrics:", vllm_metrics)
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 6))
    
    # Data for plotting
    metrics = ["Mean", "P95"]
    x = np.arange(len(metrics))
    bar_width = 0.35
    
    # TTFT data reshaping
    llm_d_ttft = [llm_d_metrics['mean_ttft_ms'], llm_d_metrics['p95_ttft_ms']]
    vllm_ttft = [vllm_metrics['mean_ttft_ms'], vllm_metrics['p95_ttft_ms']]
    
    # ITL data reshaping
    llm_d_itl = [llm_d_metrics['mean_itl_ms'], llm_d_metrics['p95_itl_ms']]
    vllm_itl = [vllm_metrics['mean_itl_ms'], vllm_metrics['p95_itl_ms']]
    
    # TTFT subplot
    ax1.bar(x - bar_width/2, llm_d_ttft, bar_width, label='llm-d', color='skyblue', alpha=0.8)
    ax1.bar(x + bar_width/2, vllm_ttft, bar_width, label='vLLM v1', color='lightcoral', alpha=0.8)
    
    # Add value labels
    for i, v in enumerate(llm_d_ttft):
        ax1.text(i - bar_width/2, v, f'{v:.1f}', ha='center', va='bottom')
    for i, v in enumerate(vllm_ttft):
        ax1.text(i + bar_width/2, v, f'{v:.1f}', ha='center', va='bottom')
    
    ax1.set_xlabel('Metric')
    ax1.set_ylabel('Time (ms)')
    ax1.set_title(f'{title_prefix} - TTFT')
    ax1.set_xticks(x)
    ax1.set_xticklabels(metrics)
    ax1.legend()
    ax1.grid(True, axis='y', linestyle='--', alpha=0.7)
    
    # ITL subplot
    ax2.bar(x - bar_width/2, llm_d_itl, bar_width, label='llm-d', color='skyblue', alpha=0.8)
    ax2.bar(x + bar_width/2, vllm_itl, bar_width, label='vLLM v1', color='lightcoral', alpha=0.8)
    
    # Add value labels
    for i, v in enumerate(llm_d_itl):
        ax2.text(i - bar_width/2, v, f'{v:.1f}', ha='center', va='bottom')
    for i, v in enumerate(vllm_itl):
        ax2.text(i + bar_width/2, v, f'{v:.1f}', ha='center', va='bottom')
    
    ax2.set_xlabel('Metric')
    ax2.set_ylabel('Time (ms)')
    ax2.set_title(f'{title_prefix} - ITL')
    ax2.set_xticks(x)
    ax2.set_xticklabels(metrics)
    ax2.legend()
    ax2.grid(True, axis='y', linestyle='--', alpha=0.7)
    
    plt.tight_layout()
    print(f"Saving plot to {output_path}")
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    plt.close()

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.join(script_dir, "../../collected/data/openshift/exp-7/H100")
    output_dir = os.path.join(script_dir, "../../")  # or wherever you want the plots
    os.makedirs(output_dir, exist_ok=True)
    
    # Load metrics for all setups
    llm_d_1p1d = load_and_average_metrics(os.path.join(base_dir, "llm-d-1p1d"))
    vllm_2replicas = load_and_average_metrics(os.path.join(base_dir, "vllm-2replicas"))
    llm_d_2p1d = load_and_average_metrics(os.path.join(base_dir, "llm-d-2p1d"))
    vllm_3replicas = load_and_average_metrics(os.path.join(base_dir, "vllm-3replicas"))
    
    # Plot 1P1D vs 2 Replicas comparison
    plot_comparison(
        llm_d_1p1d,
        vllm_2replicas,
        "1P1D vs 2 Replicas",
        os.path.join(output_dir, 'comparison_1p1d_vs_2replicas.png')
    )
    
    # Plot 2P1D vs 3 Replicas comparison
    plot_comparison(
        llm_d_2p1d,
        vllm_3replicas,
        "2P1D vs 3 Replicas",
        os.path.join(output_dir, 'comparison_2p1d_vs_3replicas.png')
    )
    
    print(f"Plots have been saved to {output_dir}")

if __name__ == "__main__":
    main() 