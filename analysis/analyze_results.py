#!/usr/bin/env python3

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import glob
import os
import argparse
import shutil
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def load_and_combine_csvs(directory):
    """Load all CSV files from the directory and combine them."""
    all_data = []

    # Look for LMBench CSV files in the directory and its subdirectories
    csv_files = []
    for root, _, files in os.walk(directory):
        csv_files.extend(glob.glob(os.path.join(root, "LMBench*.csv")))

    if not csv_files:
        logger.error(f"No LMBench CSV files found in {directory} or its subdirectories")
        logger.error("Expected files matching pattern: LMBench*.csv")
        logger.error("Please ensure the results directory contains the benchmark output files.")
        return None

    for csv_file in csv_files:
        try:
            # Extract QPS from filename
            qps = float(os.path.basename(csv_file).split('_')[-1].replace('.csv', '').replace('qps',''))
            df = pd.read_csv(csv_file)
            df['qps'] = qps
            # Add model name from parent directory
            model_name = os.path.basename(os.path.dirname(csv_file))
            df['model'] = model_name
            all_data.append(df)
            logger.info(f"Loaded data from: {csv_file}")
        except Exception as e:
            logger.error(f"Error loading {csv_file}: {str(e)}")
            continue

    if not all_data:
        logger.error("No data could be loaded from any CSV files.")
        return None

    return pd.concat(all_data, ignore_index=True)

def create_plots_readme(plots_dir):
    """Create a README.md file describing the plots."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(script_dir, 'readme-analyze-template.md')
    readme_path = os.path.join(plots_dir, 'README.md')

    if os.path.exists(template_path):
        shutil.copyfile(template_path, readme_path)
        logger.info(f"Created README.md at: {readme_path}")
    else:
        logger.info(f"Warning: Template file not found at {template_path}, using default content")
        readme_content = """# Benchmark Analysis Plots

This directory contains visualization files generated from the benchmark results.

## Latency Analysis

![Latency Analysis](latency_analysis.png)

This plot shows four key latency metrics across different QPS (Queries Per Second) levels:

1. **Time to First Token (TTFT) vs QPS**
   - Shows how quickly the model starts generating tokens
   - Lower values indicate faster initial response

2. **Generation Time vs QPS**
   - Shows the time taken to generate the complete response
   - Helps identify performance bottlenecks at different load levels

3. **Total Time (TTFT + Generation) vs QPS**
   - Shows the complete end-to-end latency
   - Combines initial response time and generation time

4. **Token Generation Rate vs QPS**
   - Shows how many tokens are generated per second
   - Higher values indicate better throughput

## Throughput Analysis

![Throughput Analysis](throughput_analysis.png)

This plot shows two throughput-related metrics:

1. **Throughput (Tokens/Second) vs QPS**
   - Shows the overall token processing rate
   - Combines both prompt and generation tokens
   - Higher values indicate better performance

2. **Token Counts vs QPS**
   - Shows the average number of prompt and generation tokens
   - Helps understand the input/output token ratio
   - Useful for capacity planning
"""
        with open(readme_path, 'w') as f:
            f.write(readme_content)
        logger.info(f"Created README.md at: {readme_path}")

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

def analyze_latency(df, plots_dir):
    set_pretty_plot_style()
    fig, axes = plt.subplots(2, 2, figsize=(18, 12))
    # Plot 1: Time to First Token (TTFT) vs QPS
    sns.boxplot(x='qps', y='ttft', data=df, ax=axes[0, 0])
    axes[0, 0].set_title('Time to First Token vs QPS')
    axes[0, 0].set_xlabel('Queries per Second')
    axes[0, 0].set_ylabel('TTFT (seconds)')
    # Plot 2: Generation Time vs QPS
    sns.boxplot(x='qps', y='generation_time', data=df, ax=axes[0, 1])
    axes[0, 1].set_title('Generation Time vs QPS')
    axes[0, 1].set_xlabel('Queries per Second')
    axes[0, 1].set_ylabel('Generation Time (seconds)')
    # Plot 3: Total Time (TTFT + Generation) vs QPS
    df['total_time'] = df['ttft'] + df['generation_time']
    sns.boxplot(x='qps', y='total_time', data=df, ax=axes[1, 0])
    axes[1, 0].set_title('Total Time vs QPS')
    axes[1, 0].set_xlabel('Queries per Second')
    axes[1, 0].set_ylabel('Total Time (seconds)')
    # Plot 4: Token Generation Rate vs QPS
    df['tokens_per_second'] = df['generation_tokens'] / df['generation_time']
    sns.boxplot(x='qps', y='tokens_per_second', data=df, ax=axes[1, 1])
    axes[1, 1].set_title('Token Generation Rate vs QPS')
    axes[1, 1].set_xlabel('Queries per Second')
    axes[1, 1].set_ylabel('Tokens per Second')
    for ax in axes.flat:
        sns.despine(ax=ax)
    plt.tight_layout(pad=2)
    plt.savefig(os.path.join(plots_dir, 'latency_analysis.png'))
    plt.close()

def analyze_throughput(df, plots_dir):
    set_pretty_plot_style()
    fig, axes = plt.subplots(1, 2, figsize=(18, 5))
    # Calculate throughput metrics
    throughput_data = df.groupby('qps').agg({
        'prompt_tokens': 'mean',
        'generation_tokens': 'mean',
        'generation_time': 'mean'
    }).reset_index()
    throughput_data['tokens_per_second'] = (
        throughput_data['prompt_tokens'] + throughput_data['generation_tokens']
    ) / throughput_data['generation_time']
    # Plot throughput
    sns.barplot(x='qps', y='tokens_per_second', data=throughput_data, ax=axes[0])
    axes[0].set_title('Throughput (Tokens/Second) vs QPS')
    axes[0].set_xlabel('Queries per Second')
    axes[0].set_ylabel('Tokens per Second')
    # Plot token counts
    throughput_data_melted = pd.melt(
        throughput_data,
        id_vars=['qps'],
        value_vars=['prompt_tokens', 'generation_tokens'],
        var_name='Token Type',
        value_name='Count'
    )
    sns.barplot(x='qps', y='Count', hue='Token Type', data=throughput_data_melted, ax=axes[1])
    axes[1].set_title('Token Counts vs QPS')
    axes[1].set_xlabel('Queries per Second')
    axes[1].set_ylabel('Number of Tokens')
    axes[1].legend(title='Token Type', loc='upper right')
    for ax in axes.flat:
        sns.despine(ax=ax)
    plt.tight_layout(pad=2)
    plt.savefig(os.path.join(plots_dir, 'throughput_analysis.png'))
    plt.close()

def print_statistics(df, data_dir):
    """Print key statistics about the benchmark results."""
    sep="=" * 50

    qps_stats = df.groupby('qps').agg({
        'ttft': ['mean', 'std', 'min', 'max'],
        'generation_time': ['mean', 'std', 'min', 'max'],
        'prompt_tokens': 'mean',
        'generation_tokens': 'mean'
    }).round(4)

    token_stats = df.agg({
        'prompt_tokens': ['mean', 'std', 'min', 'max'],
        'generation_tokens': ['mean', 'std', 'min', 'max']
    }).round(4)

    _msg=f"\nBenchmark Statistics:\
        \n{sep}\
        \nnOverall Statistics:\
        \nTotal number of requests: {len(df)}\
        \nNumber of unique users: {df['user_id'].nunique()}\
        \nNumber of QPS levels tested: {df['qps'].nunique()}\
        \nPer QPS Statistics:\
        \n{qps_stats} \
        \nToken Statistics:\
        \n{token_stats}"

    print(_msg)

    with open(f"{data_dir}/stats.txt", 'w') as fp :
        fp.write(_msg)

def main():
    # Parse command line arguments
    env_vars = os.environ

    if 'LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY' in env_vars and 'LLMDBENCH_RUN_EXPERIMENT_LAUNCHER' in env_vars :
        if env_vars['LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY'] == "1" and env_vars['LLMDBENCH_RUN_EXPERIMENT_LAUNCHER'] == "1" :
            logger.info(f"\nEnviroment variable \"LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY\" is set to \"1\", and this is a pod. Will skip execution")
            exit(0)

    default_dir = "/tmp/"

    if 'LLMDBENCH_CONTROL_WORK_DIR' in env_vars:
        default_dir = f"{env_vars['LLMDBENCH_CONTROL_WORK_DIR']}"

    if os.path.exists(f"{default_dir}/results") :
        default_dir = f"{default_dir}/results"

    parser = argparse.ArgumentParser(description='Analyze benchmark results from CSV files.')
    parser.add_argument('--results-dir',
                      default=default_dir,
                      help=f'Directory containing the CSV files (default: {default_dir}')
    args = parser.parse_args()

    # Set style
    sns.set_style("whitegrid")
    plt.rcParams['figure.figsize'] = [12, 8]

    # Create plots directory
    plots_dir = f"{args.results_dir.replace('/results','')}/analysis/plots"
    data_dir = f"{args.results_dir.replace('/results','')}/analysis/data"
    os.makedirs(plots_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)

    # Create README for plots
    create_plots_readme(plots_dir)

    # Load data
    logger.info(f"Loading data from: {args.results_dir}")
    df = load_and_combine_csvs(args.results_dir)

    if df is None:
        logger.info("Error: Could not load any data. Exiting.")
        return

    # Generate visualizations
    analyze_latency(df, plots_dir)
    analyze_throughput(df, plots_dir)

    # Print statistics
    print_statistics(df, data_dir)

    logger.info(f"\nAnalysis complete! Checl {data_dir} for stats.txt and check {plots_dir} for visualization files:")
    logger.info(f"- {os.path.join(plots_dir, 'latency_analysis.png')}")
    logger.info(f"- {os.path.join(plots_dir, 'throughput_analysis.png')}")
    logger.info(f"- {os.path.join(plots_dir, 'README.md')}")

if __name__ == "__main__":
    main()
