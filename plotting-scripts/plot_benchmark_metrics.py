import pandas as pd
import matplotlib.pyplot as plt
import glob
import os
import re
import argparse

# Define method types and their display names
METHOD_TYPES = {
    'vllm': 'vLLM v1',
    'llm-d': 'LLM-d',
    'vllm-prod': 'vLLM + LMCache',
    'lmcache': 'vLLM Production Stack + LMCache',
    'lmcache-0310': 'vLLM Production Stack + LMCache (03-10-2025)',
    'vllm-70b': 'vLLM v1',
    'baseline-llm-d-70b': 'llm-d w/o KVCache offloading',
    'lmcache-llm-d-70b': 'llm-d w KVCache offloading',
    'lmcache-indexing-llm-d-70b': 'llm-d w KVCache offloading + KVCache indexing',
    'lmcache-vllm-70b': 'Production Stack(vLLM v1) + LMCache',
    'vllm-70b-2replicas': 'vLLM v1 (2 replicas) + Round Robin',
    'llm-d-70b-2replicas': 'llm-d (2 replicas)' + '\n' + 'KVCache (score=2) & Load (score=1) aware routing',
    'vllm-standalone-llama-3-70b-2replicas-H100': 'vLLM v1 (2 replicas) + Round Robin (H100)',
    'llm-d-70b-2replicas-H100': 'llm-d (2 replicas)' + '\n' + 'Prefix (score=2) & Load (score=1) aware routing (H100)',
    'llm-d-70b-2replicas-H100-no-router': 'llm-d (2 replicas)' + '\n' + 'Round Robin (H100)',
    'vllm-llama4-tp4': 'vLLM v1 (TP=4)',
    'llm-d-llama4-tp4': 'llm-d (TP=4)',
    'lmcache-llm-d-llama4-tp4': 'llm-d w KVCache offloading (TP=4)',
}

# Define benchmark types and their titles
BENCHMARK_TYPES = {
    'sharegpt': 'ShareGPT',
    'long_input': 'Long Input Short Output',
    'short_input': 'Short Input Short Output'
}

# Define QPS ranges for each benchmark type
BENCHMARK_QPS_RANGES = {
    # 'sharegpt': (0, 1.4),
    'sharegpt': (0, 100.0),
    'long_input': (0, 1.2),
    'short_input': (0, 10.0)
}

# Define y-axis ranges for each metric
BENCHMARK_Y_RANGES = {
    'itl': (0, 0.1),      # Inter-token Latency in seconds
    'ttft': (0, 1.0),     # Time to First Token in seconds
    'throughput': (5000, 30000)  # Throughput in tokens per second
}

def extract_qps(filename):
    # Try to extract QPS value from filename
    # Pattern 1: LMBench_sharegpt_output_0.5.csv -> 0.5
    # Pattern 2: LMBench_short_input_qps0.5.csv -> 0.5
    match = re.search(r'(?:output_|qps)(\d+\.?\d*)\.csv', filename)
    if match:
        return float(match.group(1))
    return None

def calculate_itl(df):
    # Calculate ITL (Inter-token Latency) as generation_time / generation_tokens
    return df['generation_time'] / df['generation_tokens']

def calculate_throughput(df):
    # Calculate total tokens (input + output)
    total_tokens = df['prompt_tokens'].sum() + df['generation_tokens'].sum()
    
    # Calculate total time (latest finish time - earliest launch time)
    total_time = df['finish_time'].max() - df['launch_time'].min()
    
    # Calculate throughput (tokens per second)
    return total_tokens / total_time

def process_csv_files(benchmark_type, method, benchmark_dir):
    # Get all CSV files matching the pattern
    data_dir = os.path.join(benchmark_dir, method)
    pattern = f'LMBench_{benchmark_type}_*.csv'
    csv_files = glob.glob(os.path.join(data_dir, pattern))
    
    if not csv_files:
        print(f"No CSV files found for {benchmark_type} in {data_dir}")
        return None
    
    # Store results
    results = {
        'qps': [],
        'itl': [],
        'ttft': [],
        'throughput': []
    }
    
    # Process each file
    for file in sorted(csv_files):
        qps = extract_qps(file)
        if qps is None:
            print(f"Could not extract QPS from filename: {file}")
            continue
            
        try:
            # Read CSV file
            df = pd.read_csv(file)
            
            # Calculate metrics
            itl = calculate_itl(df).mean()
            ttft = df['ttft'].mean()
            throughput = calculate_throughput(df)
            
            results['qps'].append(qps)
            results['itl'].append(itl)
            results['ttft'].append(ttft)
            results['throughput'].append(throughput)
            
            print(f"Processed {file}:")
            print(f"  QPS={qps}")
            print(f"  Avg ITL={itl:.4f}s")
            print(f"  Avg TTFT={ttft:.4f}s")
            print(f"  Throughput={throughput:.2f} tokens/s")
        except Exception as e:
            print(f"Error processing {file}: {str(e)}")
            continue
    
    if not results['qps']:
        print(f"No valid data found for {benchmark_type}")
        return None
    
    # Sort all metrics by QPS
    sorted_indices = sorted(range(len(results['qps'])), key=lambda i: results['qps'][i])
    for key in results:
        results[key] = [results[key][i] for i in sorted_indices]
    
    return results

def plot_metrics(results_dict, benchmark_type, title, benchmark_dir, model_name):
    if not results_dict:
        return
    
    # Create figure with three subplots
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(18, 6))
    
    # Add main title with model name
    fig.suptitle(f"{title} - {model_name}", fontsize=20, y=1.02)
    
    # Define colors for different methods
    colors = ['bo-', 'ro-', 'go-', 'mo-', 'co-', 'yo-']
    
    # Get QPS range for this benchmark type
    qps_min, qps_max = BENCHMARK_QPS_RANGES[benchmark_type]
    
    # Plot ITL
    for i, (method, results) in enumerate(results_dict.items()):
        if results:
            ax1.plot(results['qps'], results['itl'], colors[i % len(colors)], 
                    linewidth=2, markersize=8, label=METHOD_TYPES[method])
    ax1.set_xlabel('QPS')
    ax1.set_ylabel('Average Inter-token Latency (s)')
    ax1.set_title('Average Inter-token Latency vs QPS')
    ax1.set_xlim(qps_min, qps_max)
    ax1.set_ylim(BENCHMARK_Y_RANGES['itl'])
    ax1.grid(True)
    ax1.legend()
    
    # Plot TTFT
    for i, (method, results) in enumerate(results_dict.items()):
        if results:
            ax2.plot(results['qps'], results['ttft'], colors[i % len(colors)], 
                    linewidth=2, markersize=8, label=METHOD_TYPES[method])
    ax2.set_xlabel('QPS')
    ax2.set_ylabel('Average Time to First Token (s)')
    ax2.set_title('Average Time to First Token vs QPS')
    ax2.set_xlim(qps_min, qps_max)
    ax2.set_ylim(BENCHMARK_Y_RANGES['ttft'])
    ax2.grid(True)
    ax2.legend()
    
    # Plot Throughput
    for i, (method, results) in enumerate(results_dict.items()):
        if results:
            ax3.plot(results['qps'], results['throughput'], colors[i % len(colors)], 
                    linewidth=2, markersize=8, label=METHOD_TYPES[method])
    ax3.set_xlabel('QPS')
    ax3.set_ylabel('Throughput (tokens/s)')
    ax3.set_title('Throughput vs QPS')
    ax3.set_xlim(qps_min, qps_max)
    ax3.set_ylim(BENCHMARK_Y_RANGES['throughput'])
    ax3.grid(True)
    ax3.legend()
    
    # Adjust layout and save
    plt.tight_layout()
    output_file = os.path.join(os.path.dirname(__file__), f'benchmark_metrics_{benchmark_type}.png')
    plt.savefig(output_file, bbox_inches='tight')
    plt.close()
    print(f"Plot for {title} saved to {output_file}")

def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description='Plot benchmark metrics from CSV files')
    parser.add_argument('--benchmark-dir', 
                      default=os.path.join(os.path.dirname(__file__), '..', 'data', 'k8s', 'lmbenchmark'),
                      help='Path to the benchmark directory containing the method subdirectories')
    parser.add_argument('--model-name',
                      default='Llama-3.1-8B-Instruct',
                      help='Name of the model being benchmarked (default: Llama-3.1-8B-Instruct)')
    args = parser.parse_args()
    
    # Process and plot each benchmark type
    for benchmark_type, title in BENCHMARK_TYPES.items():
        print(f"\nProcessing {title} benchmark for {args.model_name}...")
        results_dict = {}
        for method in METHOD_TYPES.keys():
            results = process_csv_files(benchmark_type, method, args.benchmark_dir)
            if results:
                results_dict[method] = results
        plot_metrics(results_dict, benchmark_type, title, args.benchmark_dir, args.model_name)

if __name__ == "__main__":
    main() 