"""Pytest configuration.

Integration tests hit the live Databricks workspace and consume warehouse time, so they
are opt-in. Unit tests run everywhere with no credentials.
"""

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="run tests that query the live Databricks workspace",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-integration"):
        return
    skip = pytest.mark.skip(reason="needs --run-integration (queries the live workspace)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
