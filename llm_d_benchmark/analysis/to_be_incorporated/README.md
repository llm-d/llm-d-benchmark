# Plotting Scripts

This directory contains scripts for plotting benchmark results.

## Setup with uv

1. Install uv if you haven't already:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

2. Create a virtual environment and install dependencies:
```bash
uv venv
source .venv/bin/activate  # On Unix/macOS
# or
.venv\Scripts\activate  # On Windows

uv pip install -r requirements.txt
```

## Running the Scripts

To run the ITL vs QPS plotting script:
```bash
python plot_itl_vs_qps.py
```

The script will generate a plot showing the relationship between QPS and average Inter-token Latency, saved as 'itl_vs_qps.png'. 