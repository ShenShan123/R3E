import os
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "orfs: requires RUN_ORFS=1 and live ORFS artifacts (nangate45/csr)",
    )


def pytest_collection_modifyitems(config, items):
    if not os.environ.get("RUN_ORFS"):
        skip = pytest.mark.skip(reason="set RUN_ORFS=1 to run")
        for item in items:
            if item.get_closest_marker("orfs"):
                item.add_marker(skip)
