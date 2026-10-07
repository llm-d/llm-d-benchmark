def pytest_configure(config):
    # CI runs these against the in-repo package and everything else against
    # the published release llmdbenchmark pins.
    config.addinivalue_line(
        "markers",
        "benchmark_report: exercises the Benchmark Report package source",
    )
