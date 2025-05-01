import pandas as pd
import matplotlib.pyplot as plt
import glob
import os
import re

def extract_qps(filename):
    # Extract QPS value from filename (e.g., LMBench_sharegpt_output_0.5.csv -> 0.5)
    match = re.search(r'output_(\d+\.?\d*)\.csv', filename)
    if match:
        return float(match.group(1))
    return None

def main():
    # Get all CSV files matching the pattern
    data_dir = os.path.join(os.path.dirname(__file__), '..', 'data', 'k8s', 'lmbenchmark')
    csv_files = glob.glob(os.path.join(data_dir, 'LMBench_sharegpt_output_*.csv'))
    
    if not csv_files:
        print(f"No CSV files found in {data_dir}")
        return
    
    # Store results
    qps_values = []
    avg_ttft_values = []
    
    # Process each file
    for file in sorted(csv_files):
        qps = extract_qps(file)
        if qps is None:
            print(f"Could not extract QPS from filename: {file}")
            continue
            
        try:
            # Read CSV file
            df = pd.read_csv(file)
            
            # Calculate average TTFT
            avg_ttft = df['ttft'].mean()
            
            qps_values.append(qps)
            avg_ttft_values.append(avg_ttft)
            print(f"Processed {file}: QPS={qps}, Avg TTFT={avg_ttft:.4f}s")
        except Exception as e:
            print(f"Error processing {file}: {str(e)}")
            continue
    
    if not qps_values:
        print("No valid data found in any CSV files")
        return
    
    # Sort QPS and TTFT values
    sorted_pairs = sorted(zip(qps_values, avg_ttft_values))
    qps_values, avg_ttft_values = zip(*sorted_pairs)
    
    # Create the plot
    plt.figure(figsize=(10, 6))
    plt.plot(qps_values, avg_ttft_values, 'ro-', linewidth=2, markersize=8)
    plt.xlabel('QPS')
    plt.ylabel('Average Time to First Token (s)')
    plt.title('Average Time to First Token vs QPS')
    plt.grid(True)
    
    # Save the plot
    output_file = os.path.join(os.path.dirname(__file__), 'ttft_vs_qps.png')
    plt.savefig(output_file)
    plt.close()
    print(f"Plot saved to {output_file}")

if __name__ == "__main__":
    main() 