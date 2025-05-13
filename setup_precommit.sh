#!/usr/bin/env bash
# Copyright 2024-2025 IBM Corporation | IBM Confidential
#
python3 -m venv venv
source venv/bin/activate
pip3 install -r .pre-commit_requirements.txt
pre-commit install
