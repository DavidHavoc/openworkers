"""widgetctl entrypoint.

README claims `--port 9000` is the dashboard launch flag — but this
implementation actually uses `--bind` and defaults to port 8000.
This drift is intentional for the audit-test fixture.
"""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(prog="widgetctl")
    parser.add_argument("--bind", default="127.0.0.1:8000")
    args = parser.parse_args()
    print(f"Starting widgetctl on {args.bind}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
