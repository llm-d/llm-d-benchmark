FROM python:3.11-slim

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install discovery tool's runtime deps.
COPY llm_d_stack_discovery/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install the Benchmark Report library (schema modules imported as
# llmd_benchmark_report) from the in-repo package.
COPY benchmark-report/ /tmp/benchmark-report/
RUN pip install --no-cache-dir /tmp/benchmark-report && rm -rf /tmp/benchmark-report

# Copy the discovery package itself.
COPY llm_d_stack_discovery/ /app/llm_d_stack_discovery/

RUN useradd -m -u 1000 discovery && chown -R discovery:discovery /app
USER discovery

ENV PYTHONPATH=/app
ENTRYPOINT ["python", "-m", "llm_d_stack_discovery.cli"]
