def pytest_configure(config):
    # CI runs these against the in-repo package and everything else against
    # the published release llmdbenchmark pins.
    config.addinivalue_line(
        "markers",
        "local_benchmark_report: opts in to the in-repo Benchmark Report package",
    )
