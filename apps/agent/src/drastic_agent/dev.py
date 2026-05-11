import os
from pathlib import Path

from watchfiles import PythonFilter
from watchfiles.run import run_process


def _parse_debounce_ms() -> int:
    configured = str(os.getenv("DRASTIC_AGENT_HOT_RELOAD_DEBOUNCE_MS") or "2500").strip()
    try:
        debounce_ms = int(configured)
    except ValueError:
        debounce_ms = 2500

    return max(debounce_ms, 0)


def main() -> int:
    watch_path = Path("src/drastic_agent")
    debounce_ms = _parse_debounce_ms()

    print(f"Starting agent hot reload for {watch_path} with debounce {debounce_ms}ms")

    return run_process(
        watch_path,
        target="uv run drastic-agent",
        target_type="command",
        watch_filter=PythonFilter(),
        debounce=debounce_ms,
    )


if __name__ == "__main__":
    raise SystemExit(main())
