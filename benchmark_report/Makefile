# This is a minimal Makefile for code formatting and linting.
# Once llm-d-benchmark is fully converted to Python, a single Makefile
# covering the repository will replace this.

format:
	ruff format .
	black .
	ruff check --fix .

lint:
	ruff check .
	pylint .
