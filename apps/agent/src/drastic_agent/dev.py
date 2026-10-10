"""Restart the development agent when agent or shared Python sources change."""

import os
from pathlib import Path

from watchfiles import PythonFilter
from watchfiles.run import run_process

from drastic_agent.config import DefaultConfig


def _parse_debounce_ms() -> int:
    configured = os.environ.get(
        "DRASTIC_AGENT_HOT_RELOAD_DEBOUNCE_MS",
        DefaultConfig.AGENT_HOT_RELOAD_DEBOUNCE_MS,
    )
    try:
        return max(int(str(configured).strip()), 0)
    except ValueError:
        return DefaultConfig.AGENT_HOT_RELOAD_DEBOUNCE_MS


def main() -> int:
    watch_paths = (Path("src/drastic_agent"), Path("../../libs/python/common/src/drastic_common"))
    debounce_ms = _parse_debounce_ms()

    print(f"Starting agent hot reload for {', '.join(map(str, watch_paths))} with debounce {debounce_ms}ms")

    return run_process(
        *watch_paths,
        target="uv run drastic-agent",
        target_type="command",
        watch_filter=PythonFilter(),
        debounce=debounce_ms,
    )


if __name__ == "__main__":
    raise SystemExit(main())
