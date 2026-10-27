"""Keep test-owned temporary data isolated from the user's system temp ACLs."""

from pathlib import Path
from uuid import uuid4


def pytest_configure(config):
    if config.option.basetemp is None:
        parent = Path(__file__).resolve().parents[1] / "data" / "pytest"
        parent.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(parent / uuid4().hex)
