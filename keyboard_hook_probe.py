"""Utility script to record raw keyboard events using the `keyboard` package.

This helper can be used on the affected Windows machine to verify whether
low-level keyboard hooks keep receiving events when the main application stops
reacting to Ctrl+C.  The script prints every event with a timestamp to stdout
and optionally mirrors it to a log file so the recording can be inspected after
longer runs.

Usage example::

    python keyboard_hook_probe.py --log keyboard_events.log

Press ``Ctrl+Shift+Q`` or ``Ctrl+C`` to stop the capture.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
import time
from pathlib import Path
from typing import Optional

import keyboard


def _format_event(event: "keyboard.KeyboardEvent") -> str:
    timestamp = _dt.datetime.now().isoformat(timespec="milliseconds")
    return (
        f"{timestamp} event_type={event.event_type:5s} "
        f"name={event.name!r:<10} scan_code={event.scan_code:<4d} "
        f"is_keypad={event.is_keypad} device={event.device!s}"
    )


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Record raw keyboard events via keyboard.hook(print) to determine "
            "whether the global hook stops receiving Ctrl+C."
        )
    )
    parser.add_argument(
        "--log",
        type=Path,
        help="Optional file path to append the captured events.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Optional maximum duration in seconds before the script exits.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    log_file = None
    if args.log is not None:
        try:
            log_file = args.log.open("a", encoding="utf-8")
        except OSError as exc:  # pragma: no cover - depends on filesystem
            print(f"Failed to open log file {args.log}: {exc}", file=sys.stderr)
            return 1

    stop_message = (
        "Recording raw keyboard events. Press Ctrl+Shift+Q or Ctrl+C to stop."
    )
    print(stop_message)

    def _handler(event: "keyboard.KeyboardEvent") -> None:
        line = _format_event(event)
        print(line, flush=True)
        if log_file is not None:
            log_file.write(line + "\n")
            log_file.flush()

    keyboard.hook(_handler)

    try:
        if args.duration is not None:
            deadline = time.time() + max(args.duration, 0)
            while time.time() < deadline:
                time.sleep(0.1)
        else:
            keyboard.wait("ctrl+shift+q")
    except KeyboardInterrupt:
        pass
    finally:
        keyboard.unhook_all()
        if log_file is not None:
            log_file.close()

    return 0


if __name__ == "__main__":  # pragma: no cover - manual utility script
    raise SystemExit(main())
