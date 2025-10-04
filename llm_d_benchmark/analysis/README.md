# llm-d-benchmark Analysis

## Jupyter Analysis Notebook

Data analysis can be performed using the [Jupyter](https://docs.jupyter.org/en/latest/) notebook [analysis_pd.ipynb](analysis_pd.ipynb) using Jupyter Lab, an interactive development environment. This notebook is written in Python, and will import all benchmark report files found within a provided list of directories and populate a [Pandas](https://pandas.pydata.org/) DataFrame. You may then execute pre-built plotting functions, modify these functions, or add your own custom analysis.

### Creating a Python virtual environment

To get started, you must first have a Python ≥3.12 environment installed. If you are running Mac or Linux you likely already satisfy this requirement. For Windows you may download a Python distribution like [Anaconda](https://www.anaconda.com/download).

Next you will need to create a virtual environment, where we will install the requisite Python packages for analysis.

- [Create a new Python 3 virtual environment.](https://docs.python.org/3/library/venv.html) \
  Linux/Mac:
  ```bash
  python -m venv /path/to/new/virtual/environment
  ```
  Windows:
  ```powershell
  PS> python -m venv C:\path\to\new\virtual\environment
  ```
- Activate the virtual environment \
  Linux/Mac:
  ```bash
  source <venv_path>/bin/activate
  ```
  Windows:
  ```
  C:\> <venv_path>\Scripts\activate.bat
  ```
- Install packages from [../build/requirements-analysis.txt](../build/requirements-analysis.txt)
  ```bash
  pip install -r build/requirements-analysis.txt
  ```
- Install Jupyter Lab
  ```bash
  pip install jupyterlab
  ```

### Running `analysis.ipynb`

After activating the virtual environment, launch Jupyter Lab, optionally adding the path to `analysis.ipynb` as an argument to open it immediately.
```bash
jupyter lab analysis.ipynb
```

This should open Jupyter Lab in your web browser. With the analysis notebook open, click to select the first cell, then press `Shift + Enter` to execute the cell. Any printouts or error messages will be shown immediately after the cell.

