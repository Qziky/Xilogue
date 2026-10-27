"""Read-only desktop-state evidence while a human performs lock/unlock and UAC.

uv run --locked python tests/manual/desktop_probe.py --seconds 180
Does not capture images, automate security UI, or change any Windows settings.
"""

import argparse
import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from xilogue.collector.windows import WindowsMonitor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=180)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("seconds must be positive and finite")
    output = Path("data/desktop-probe") / (uuid4().hex + ".jsonl")
    output.parent.mkdir(parents=True, exist_ok=True)
    monitor = WindowsMonitor()
    previous = None
    started = time.perf_counter()
    print(json.dumps({"output": str(output), "seconds": args.seconds}), flush=True)
    with output.open("x", encoding="utf-8") as file:
        while time.perf_counter() - started < args.seconds:
            available = monitor.desktop_available()
            if available != previous:
                item = {
                    "at_utc": datetime.now(UTC).isoformat(),
                    "elapsed_seconds": time.perf_counter() - started,
                    "desktop_available": available,
                }
                line = json.dumps(item)
                file.write(line + "\n")
                file.flush()
                print(line, flush=True)
                previous = available
            time.sleep(0.25)


if __name__ == "__main__":
    main()
