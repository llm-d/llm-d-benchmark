"""
Mocks DB storing info about common accelerators used for LLM serving and inference
"""
import json

gpu_specs = {}

with open("config_explorer/db.json") as f:
    gpu_specs = json.load(f)
