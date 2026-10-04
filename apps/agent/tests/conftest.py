import os
import time
from pathlib import Path

import pytest

os.environ.setdefault(
    "DRASTIC_AGENT_DATA_DIR", str(Path(__file__).resolve().parent / ".agent-data")
)


@pytest.fixture
def berlin_timezone(monkeypatch):
    try:
        with monkeypatch.context() as patch:
            patch.setenv("TZ", "Europe/Berlin")
            time.tzset()
            yield
    finally:
        time.tzset()
