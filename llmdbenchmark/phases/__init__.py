"""Phase implementations for the llmdbenchmark CLI.

Each lifecycle phase (standup, smoketest, run, teardown) lives in its own
module. ``cli.py`` dispatches to the ``execute_*`` public functions defined
here; the ``do_*`` functions are the re-entrant cores used by
``phases.experiment`` to compose a DoE matrix.

``phases.common`` holds ``PhaseError`` and helpers shared across phases.
Phase modules must NEVER import from ``llmdbenchmark.cli`` to avoid
circular imports.
"""
