FROM python:3.11-slim

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install benchmark_report first (dependency)
COPY benchmark_report/ /tmp/benchmark_report/
RUN pip install --no-cache-dir /tmp/benchmark_report || echo "benchmark_report not available"

# Copy and install discovery tool
COPY llm_d_stack_discovery/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY llm_d_stack_discovery/ /app/llm_d_stack_discovery/

RUN useradd -m -u 1000 discovery && chown -R discovery:discovery /app
USER discovery

ENV PYTHONPATH=/app
ENTRYPOINT ["python", "-m", "llm_d_stack_discovery.cli"]
