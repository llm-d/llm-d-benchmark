FROM registry.access.redhat.com/ubi9/python-39:latest

WORKDIR /app

COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

#RUN pip install git+https://github.com/fmperf-project/fmperf.git@main
# TODO Replace this with fmperf-project/main when PR https://github.com/fmperf-project/fmperf/pull/41 merges
RUN pip install git+https://github.com/sallyom/fmperf.git@job-k8s

COPY run_benchmark.py /app/run_benchmark.py

ENV PYTHONPATH=/app

ENTRYPOINT ["python", "/app/run_benchmark.py"]
