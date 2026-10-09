from llmdbenchmark._report_source import require_published_benchmark_report

__package_name__ = "llmdbenchmark"
__version__ = "0.8.0"
__package_home__ = "https://github.com/llm-d/llm-d-benchmark"

# Fail on import, before any harness code writes a report.
require_published_benchmark_report()
