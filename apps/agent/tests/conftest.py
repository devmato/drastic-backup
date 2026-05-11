import os
from pathlib import Path

os.environ.setdefault(
    "DRASTIC_AGENT_DATA_DIR", str(Path(__file__).resolve().parent / ".agent-data")
)
