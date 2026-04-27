FROM python:3.11-slim

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install discovery tool's runtime deps (pydantic in here covers the
# schema modules imported from llmdbenchmark.analysis.benchmark_report).
COPY llm_d_stack_discovery/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the discovery package itself.
COPY llm_d_stack_discovery/ /app/llm_d_stack_discovery/

# Copy just the slice of llmdbenchmark we actually import
# (llmdbenchmark.analysis.benchmark_report.schema_v0_2[_components]).
# Intermediate __init__.py files are included so the dotted import
# resolves on PYTHONPATH=/app below -- no separate pip install needed.
COPY llmdbenchmark/__init__.py /app/llmdbenchmark/__init__.py
COPY llmdbenchmark/analysis/__init__.py /app/llmdbenchmark/analysis/__init__.py
COPY llmdbenchmark/analysis/benchmark_report/ /app/llmdbenchmark/analysis/benchmark_report/

RUN useradd -m -u 1000 discovery && chown -R discovery:discovery /app
USER discovery

ENV PYTHONPATH=/app
ENTRYPOINT ["python", "-m", "llm_d_stack_discovery.cli"]
