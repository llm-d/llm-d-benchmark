#!/usr/bin/env python3

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import glob
import os
import argparse
import shutil
import numpy as np
import tarfile
import tempfile

def load_and_combine_csvs(directory, system_name):
    """Load all CSV files from the directory and combine them."""
    all_data = []

    print(f"Looking for CSV files in: {directory}")

    if not os.path.exists(directory):
        print(f"Directory not found: {directory}")
        return None

    # Look for LMBench CSV files directly in the directory
    csv_files = glob.glob(os.path.join(directory, "LMBench_long_input_output_*.csv"))

    # Also check for CSV files in any subdirectories (model directories)
    subdirs = [d for d in os.listdir(directory) if os.path.isdir(os.path.join(directory, d)) and not d.startswith('.')]
    for subdir in subdirs:
        subdir_path = os.path.join(directory, subdir)
        subdir_csv_files = glob.glob(os.path.join(subdir_path, "LMBench_long_input_output_*.csv"))
        csv_files.extend(subdir_csv_files)

        # Check for nested subdirectories (in case of stack_name/model_name structure)
        nested_subdirs = [d for d in os.listdir(subdir_path) if os.path.isdir(os.path.join(subdir_path, d)) and not d.startswith('.')]
        for nested_subdir in nested_subdirs:
            nested_path = os.path.join(subdir_path, nested_subdir)
            nested_csv_files = glob.glob(os.path.join(nested_path, "LMBench_long_input_output_*.csv"))
            csv_files.extend(nested_csv_files)

    if not csv_files:
        print(f"No LMBench CSV files found in {directory} or its subdirectories")
        print(f"Directory contents: {os.listdir(directory) if os.path.exists(directory) else 'Directory does not exist'}")
        return None

    print(f"Found {len(csv_files)} CSV files for {system_name}")

    for csv_file in csv_files:
        print(f"Processing CSV file: {csv_file}")
        try:
            # Extract QPS from filename
            qps = float(os.path.basename(csv_file).split('_')[-1].replace('.csv', ''))
            df = pd.read_csv(csv_file)
            df['qps'] = qps
            df['system'] = system_name  # Add system identifier

            # Extract model name from directory structure if possible
            relative_path = os.path.relpath(csv_file, directory)
            path_parts = relative_path.split(os.sep)
            if len(path_parts) > 1:
                df['model'] = path_parts[0]  # Use first subdirectory as model name
            else:
                df['model'] = 'default'  # Fallback if no subdirectory

            all_data.append(df)
        except Exception as e:
            print(f"Error processing {csv_file}: {e}")
            continue

    if not all_data:
        print(f"No valid LMBench CSV files processed for {system_name}")
        return None

    return pd.concat(all_data, ignore_index=True)

def create_plots_readme(plots_dir):
    """Create a README.md file describing the comparison plots."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(script_dir, 'readme-analyze-compare-template.md')
    readme_path = os.path.join(plots_dir, 'README.md')
    if os.path.exists(template_path):
        shutil.copyfile(template_path, readme_path)
        print(f"Copied comparison template to: {readme_path}")
    else:
        print(f"Warning: Comparison template file not found at {template_path}, using default content")
        readme_content = """# Benchmark Comparison Analysis

This directory contains visualization files comparing the performance between two LLM deployments.

## Latency Comparison

![Latency Comparison](latency_comparison.png)

This plot shows four key latency metrics compared between the two systems:

1. **Time to First Token (TTFT) Comparison**
   - Shows how quickly each system starts generating tokens
   - Lower values indicate faster initial response

2. **Generation Time Comparison**
   - Shows the time taken to generate the complete response
   - Helps identify performance differences in generation speed

3. **Total Time Comparison**
   - Shows the complete end-to-end latency
   - Combines initial response time and generation time

4. **Token Generation Rate Comparison**
   - Shows how many tokens are generated per second
   - Higher values indicate better throughput

## Throughput Comparison

![Throughput Comparison](throughput_comparison.png)

This plot compares throughput-related metrics between the systems:

1. **Throughput (Tokens/Second) Comparison**
   - Shows the overall token processing rate for each system
   - Combines both prompt and generation tokens
   - Higher values indicate better performance

2. **Relative Performance Improvement**
   - Shows the percentage improvement of one system over the other
   - Helps quantify the efficiency gains

## QPS Comparison

![QPS Comparison](qps_comparison.png)

This plot shows how each system performs at different QPS (Queries Per Second) levels:

1. **Latency vs QPS Comparison**
   - Shows how response time increases with higher query loads
   - Helps identify at which point each system begins to degrade

2. **Token Rate vs QPS Comparison**
   - Shows how token generation speed changes with increasing load
   - Helps identify maximum effective throughput for each system
"""
        with open(readme_path, 'w') as f:
            f.write(readme_content)
        print(f"Created README.md at: {readme_path}")

# --- Chart Prettification Settings ---
def set_pretty_plot_style():
    sns.set_theme(style="whitegrid", palette="Set2", font_scale=1.2)
    plt.rcParams['axes.titlesize'] = 16
    plt.rcParams['axes.titleweight'] = 'bold'
    plt.rcParams['axes.labelsize'] = 14
    plt.rcParams['axes.labelweight'] = 'normal'
    plt.rcParams['legend.fontsize'] = 12
    plt.rcParams['xtick.labelsize'] = 12
    plt.rcParams['ytick.labelsize'] = 12
    plt.rcParams['figure.figsize'] = [15, 10]
    plt.rcParams['savefig.dpi'] = 150
    plt.rcParams['savefig.transparent'] = True

def analyze_latency_comparison(standalone_df, llmd_df, plots_dir):
    set_pretty_plot_style()
    # Combine dataframes for comparison
    standalone_df['system'] = 'Standalone'
    llmd_df['system'] = 'LLM-D'
    combined_df = pd.concat([standalone_df, llmd_df], ignore_index=True)

    # Create total_time and tokens_per_second columns
    combined_df['total_time'] = combined_df['ttft'] + combined_df['generation_time']
    combined_df['tokens_per_second'] = combined_df['generation_tokens'] / combined_df['generation_time']

    fig, axes = plt.subplots(2, 2, figsize=(18, 12))

    # Plot 1: Time to First Token (TTFT) Comparison
    sns.boxplot(x='system', y='ttft', data=combined_df, ax=axes[0, 0])
    axes[0, 0].set_title('Time to First Token Comparison')
    axes[0, 0].set_xlabel('System')
    axes[0, 0].set_ylabel('TTFT (seconds)')

    # Plot 2: Generation Time Comparison
    sns.boxplot(x='system', y='generation_time', data=combined_df, ax=axes[0, 1])
    axes[0, 1].set_title('Generation Time Comparison')
    axes[0, 1].set_xlabel('System')
    axes[0, 1].set_ylabel('Generation Time (seconds)')

    # Plot 3: Total Time Comparison
    sns.boxplot(x='system', y='total_time', data=combined_df, ax=axes[1, 0])
    axes[1, 0].set_title('Total Time Comparison')
    axes[1, 0].set_xlabel('System')
    axes[1, 0].set_ylabel('Total Time (seconds)')

    # Plot 4: Token Generation Rate Comparison
    sns.boxplot(x='system', y='tokens_per_second', data=combined_df, ax=axes[1, 1])
    axes[1, 1].set_title('Token Generation Rate Comparison')
    axes[1, 1].set_xlabel('System')
    axes[1, 1].set_ylabel('Tokens per Second')

    for ax in axes.flat:
        sns.despine(ax=ax)

    plt.tight_layout(pad=2)
    plt.savefig(os.path.join(plots_dir, 'latency_comparison.png'))
    plt.close()

def analyze_throughput_comparison(standalone_df, llmd_df, plots_dir):
    set_pretty_plot_style()

    # Calculate throughput metrics for both systems
    standalone_throughput = standalone_df.groupby('qps').agg({
        'prompt_tokens': 'mean',
        'generation_tokens': 'mean',
        'generation_time': 'mean'
    }).reset_index()
    standalone_throughput['tokens_per_second'] = (
        standalone_throughput['prompt_tokens'] + standalone_throughput['generation_tokens']
    ) / standalone_throughput['generation_time']
    standalone_throughput['system'] = 'Standalone'

    llmd_throughput = llmd_df.groupby('qps').agg({
        'prompt_tokens': 'mean',
        'generation_tokens': 'mean',
        'generation_time': 'mean'
    }).reset_index()
    llmd_throughput['tokens_per_second'] = (
        llmd_throughput['prompt_tokens'] + llmd_throughput['generation_tokens']
    ) / llmd_throughput['generation_time']
    llmd_throughput['system'] = 'LLM-D'

    # Combine for plotting
    combined_throughput = pd.concat([standalone_throughput, llmd_throughput], ignore_index=True)

    fig, axes = plt.subplots(1, 2, figsize=(18, 7))

    # Plot 1: Throughput Comparison
    sns.barplot(x='qps', y='tokens_per_second', hue='system', data=combined_throughput, ax=axes[0])
    axes[0].set_title('Throughput Comparison (Tokens/Second)')
    axes[0].set_xlabel('Queries per Second')
    axes[0].set_ylabel('Tokens per Second')
    axes[0].legend(title='System')

    # Plot 2: Relative Performance Improvement
    # Calculate the percentage improvement of LLM-D over Standalone
    qps_values = sorted(combined_throughput['qps'].unique())
    improvement_data = []

    for qps in qps_values:
        standalone_tps = standalone_throughput[standalone_throughput['qps'] == qps]['tokens_per_second'].values[0]
        llmd_tps = llmd_throughput[llmd_throughput['qps'] == qps]['tokens_per_second'].values[0]
        improvement = ((llmd_tps / standalone_tps) - 1) * 100  # percentage improvement
        improvement_data.append({'qps': qps, 'improvement': improvement})

    improvement_df = pd.DataFrame(improvement_data)

    sns.barplot(x='qps', y='improvement', data=improvement_df, ax=axes[1], color='green')
    axes[1].set_title('Relative Performance Improvement (LLM-D vs Standalone)')
    axes[1].set_xlabel('Queries per Second')
    axes[1].set_ylabel('Improvement (%)')
    axes[1].axhline(y=0, color='r', linestyle='-', alpha=0.3)

    # Add percentage labels on bars
    for i, p in enumerate(axes[1].patches):
        height = p.get_height()
        axes[1].annotate(f'{height:.1f}%',
                        (p.get_x() + p.get_width() / 2., height),
                        ha='center', va='bottom',
                        fontsize=10)

    for ax in axes.flat:
        sns.despine(ax=ax)

    plt.tight_layout(pad=2)
    plt.savefig(os.path.join(plots_dir, 'throughput_comparison.png'))
    plt.close()

def analyze_qps_comparison(standalone_df, llmd_df, plots_dir):
    set_pretty_plot_style()

    # Combine dataframes for comparison
    standalone_df['system'] = 'Standalone'
    llmd_df['system'] = 'LLM-D'
    combined_df = pd.concat([standalone_df, llmd_df], ignore_index=True)

    # Create total_time and tokens_per_second columns
    combined_df['total_time'] = combined_df['ttft'] + combined_df['generation_time']
    combined_df['tokens_per_second'] = combined_df['generation_tokens'] / combined_df['generation_time']

    fig, axes = plt.subplots(1, 2, figsize=(18, 7))

    # Plot 1: Latency vs QPS Comparison
    sns.lineplot(x='qps', y='total_time', hue='system', data=combined_df,
                 estimator='median', ci=95, marker='o', ax=axes[0])
    axes[0].set_title('Latency vs QPS Comparison')
    axes[0].set_xlabel('Queries per Second')
    axes[0].set_ylabel('Total Response Time (seconds)')
    axes[0].legend(title='System')

    # Plot 2: Token Generation Rate vs QPS Comparison
    sns.lineplot(x='qps', y='tokens_per_second', hue='system', data=combined_df,
                 estimator='median', ci=95, marker='o', ax=axes[1])
    axes[1].set_title('Token Generation Rate vs QPS Comparison')
    axes[1].set_xlabel('Queries per Second')
    axes[1].set_ylabel('Tokens per Second')
    axes[1].legend(title='System')

    for ax in axes.flat:
        sns.despine(ax=ax)

    plt.tight_layout(pad=2)
    plt.savefig(os.path.join(plots_dir, 'qps_comparison.png'))
    plt.close()

def print_comparison_statistics(standalone_df, llmd_df):
    """Print comparative statistics between the two systems."""
    print("\nComparison Statistics:")
    print("=" * 60)

    # Per QPS statistics for both systems
    print("\nLatency Statistics by QPS:")

    qps_values = sorted(set(standalone_df['qps'].unique()) | set(llmd_df['qps'].unique()))

    # Create a comparison table
    comparison_data = []

    for qps in qps_values:
        standalone_qps_data = standalone_df[standalone_df['qps'] == qps]
        llmd_qps_data = llmd_df[llmd_df['qps'] == qps]

        if len(standalone_qps_data) > 0 and len(llmd_qps_data) > 0:
            standalone_ttft = standalone_qps_data['ttft'].median()
            llmd_ttft = llmd_qps_data['ttft'].median()
            ttft_improvement = ((standalone_ttft - llmd_ttft) / standalone_ttft) * 100 if standalone_ttft > 0 else 0

            standalone_gen_time = standalone_qps_data['generation_time'].median()
            llmd_gen_time = llmd_qps_data['generation_time'].median()
            gen_time_improvement = ((standalone_gen_time - llmd_gen_time) / standalone_gen_time) * 100 if standalone_gen_time > 0 else 0

            standalone_total = standalone_ttft + standalone_gen_time
            llmd_total = llmd_ttft + llmd_gen_time
            total_improvement = ((standalone_total - llmd_total) / standalone_total) * 100 if standalone_total > 0 else 0

            standalone_tokens_per_sec = standalone_qps_data['generation_tokens'].median() / standalone_gen_time if standalone_gen_time > 0 else 0
            llmd_tokens_per_sec = llmd_qps_data['generation_tokens'].median() / llmd_gen_time if llmd_gen_time > 0 else 0
            tokens_improvement = ((llmd_tokens_per_sec - standalone_tokens_per_sec) / standalone_tokens_per_sec) * 100 if standalone_tokens_per_sec > 0 else 0

            comparison_data.append({
                'QPS': qps,
                'Standalone TTFT': standalone_ttft,
                'LLM-D TTFT': llmd_ttft,
                'TTFT Improvement': f"{ttft_improvement:.1f}%",
                'Standalone Gen Time': standalone_gen_time,
                'LLM-D Gen Time': llmd_gen_time,
                'Gen Time Improvement': f"{gen_time_improvement:.1f}%",
                'Standalone Total': standalone_total,
                'LLM-D Total': llmd_total,
                'Total Improvement': f"{total_improvement:.1f}%",
                'Standalone Tokens/s': standalone_tokens_per_sec,
                'LLM-D Tokens/s': llmd_tokens_per_sec,
                'Tokens/s Improvement': f"{tokens_improvement:.1f}%"
            })

    comparison_df = pd.DataFrame(comparison_data)
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 150)
    print(comparison_df.round(3))

    print("\nOverall System Comparison:")
    print(f"Standalone total requests: {len(standalone_df)}")
    print(f"LLM-D total requests: {len(llmd_df)}")

    standalone_total_time = (standalone_df['ttft'] + standalone_df['generation_time']).median()
    llmd_total_time = (llmd_df['ttft'] + llmd_df['generation_time']).median()
    total_time_improvement = ((standalone_total_time - llmd_total_time) / standalone_total_time) * 100 if standalone_total_time > 0 else 0

    standalone_tokens_per_sec = standalone_df['generation_tokens'].sum() / standalone_df['generation_time'].sum()
    llmd_tokens_per_sec = llmd_df['generation_tokens'].sum() / llmd_df['generation_time'].sum()
    tokens_per_sec_improvement = ((llmd_tokens_per_sec - standalone_tokens_per_sec) / standalone_tokens_per_sec) * 100 if standalone_tokens_per_sec > 0 else 0

    print(f"\nOverall median response time:")
    print(f"  Standalone: {standalone_total_time:.3f} seconds")
    print(f"  LLM-D: {llmd_total_time:.3f} seconds")
    print(f"  Improvement: {total_time_improvement:.1f}%")

    print(f"\nOverall throughput (tokens/second):")
    print(f"  Standalone: {standalone_tokens_per_sec:.3f} tokens/second")
    print(f"  LLM-D: {llmd_tokens_per_sec:.3f} tokens/second")
    print(f"  Improvement: {tokens_per_sec_improvement:.1f}%")

def main():
    # Get environment variables from ConfigMaps
    standalone_stack_name = os.environ.get("STANDALONE_LLMDBENCH_FMPERF_STACK_NAME")
    llmd_stack_name = os.environ.get("LLMD_LLMDBENCH_FMPERF_STACK_NAME")
    results_base_dir = os.environ.get("LLMDBENCH_FMPERF_RESULTS_DIR", "/requests")

    # Parse command line arguments (keeping for backward compatibility)
    parser = argparse.ArgumentParser(description='Compare benchmark results between two systems.')
    parser.add_argument('--standalone-dir',
                      help='Directory containing the standalone benchmark results (optional if env vars are set)')
    parser.add_argument('--llmd-dir',
                      help='Directory containing the LLM-D benchmark results (optional if env vars are set)')
    parser.add_argument('--output-dir', default="./comparison-results",
                      help='Directory to save comparison results (default: ./comparison-results)')
    args = parser.parse_args()

    # Determine directories from environment variables or command line arguments
    if standalone_stack_name and llmd_stack_name:
        standalone_dir = os.path.join(results_base_dir, "standalone", standalone_stack_name)
        llmd_dir = os.path.join(results_base_dir, "llm-d", llmd_stack_name)
        print(f"Using environment variables to determine paths:")
        print(f"  Standalone stack name: {standalone_stack_name}")
        print(f"  LLM-D stack name: {llmd_stack_name}")
        print(f"  Results base directory: {results_base_dir}")
    elif args.standalone_dir and args.llmd_dir:
        standalone_dir = args.standalone_dir
        llmd_dir = args.llmd_dir
        print(f"Using command line arguments for directories:")
    else:
        print("Error: Either set environment variables (STANDALONE_LLMDBENCH_FMPERF_STACK_NAME, LLMD_LLMDBENCH_FMPERF_STACK_NAME)")
        print("       or provide --standalone-dir and --llmd-dir arguments")
        return

    print(f"  Standalone directory: {standalone_dir}")
    print(f"  LLM-D directory: {llmd_dir}")

    os.makedirs(args.output_dir, exist_ok=True)
    plots_dir = os.path.join(args.output_dir, 'plots')
    os.makedirs(plots_dir, exist_ok=True)

    print(f"Loading standalone data from: {standalone_dir}")
    standalone_df = load_and_combine_csvs(standalone_dir, 'Standalone')

    print(f"Loading LLM-D data from: {llmd_dir}")
    llmd_df = load_and_combine_csvs(llmd_dir, 'LLM-D')

    if standalone_df is None or llmd_df is None:
        print("Error: Could not load data from one or both directories.")
        print("Make sure the directories contain the expected subdirectory structure:")
        print("  standalone/ -> model-dir/ -> LMBench_*.csv files")
        print("  llm-d/ -> model-dir/ -> LMBench_*.csv files")
        return

    create_plots_readme(plots_dir)

    analyze_latency_comparison(standalone_df, llmd_df, plots_dir)
    analyze_throughput_comparison(standalone_df, llmd_df, plots_dir)
    analyze_qps_comparison(standalone_df, llmd_df, plots_dir)

    print_comparison_statistics(standalone_df, llmd_df)

    print(f"\nComparison analysis complete! Check {plots_dir} for visualization files:")
    print(f"- {os.path.join(plots_dir, 'latency_comparison.png')}")
    print(f"- {os.path.join(plots_dir, 'throughput_comparison.png')}")
    print(f"- {os.path.join(plots_dir, 'qps_comparison.png')}")
    print(f"- {os.path.join(plots_dir, 'README.md')}")

if __name__ == "__main__":
    main()
