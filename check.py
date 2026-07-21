#!/usr/bin/env python
"""Run the full local quality gate: ruff, mypy --strict, pytest."""

from __future__ import annotations

import subprocess
import sys

COMMANDS = [
    ["ruff", "check", "src", "tests"],
    [sys.executable, "-m", "mypy"],
    [sys.executable, "-m", "pytest", "tests", "-q"],
]


def main() -> int:
    for command in COMMANDS:
        print(f"$ {' '.join(command)}", flush=True)
        result = subprocess.run(command)
        if result.returncode != 0:
            print(f"FAILED: {' '.join(command)}")
            return result.returncode
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
