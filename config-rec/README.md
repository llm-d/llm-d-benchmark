# Config Recommendation Tool

Finding the most cost-effective, optimal configuration for serving models on llm-d based on hardware specification, workload characteristics, and SLO requirements.

## Install

* Requires python 3.11+

```
python -m venv .venv
source .venv/bin/activate
pip install -r config-rec/requirements.txt
```

## Run

```
streamlit run config-rec/Home.py
```