"""Run a bundled StateBreaker lab without Docker."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

LABS = {
    "coupon-race": ("coupon-race", "app.main:app", 8080),
    "payment-step-skip": ("payment-step-skip", "payment_lab.main:app", 8090),
    "bank-double-withdraw": ("bank-double-withdraw", "bank_lab.main:app", 8091),
    "payment-callback-idempotency": (
        "payment-callback-idempotency",
        "callback_lab.main:app",
        8092,
    ),
    "refund-vs-fulfill-race": ("refund-vs-fulfill-race", "refund_lab.main:app", 8093),
    "payment-binding-mismatch": (
        "payment-binding-mismatch",
        "binding_lab.main:app",
        8094,
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a StateBreaker lab locally.")
    parser.add_argument("lab", choices=sorted(LABS))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    lab_dir, app_import, default_port = LABS[args.lab]
    root = Path(__file__).resolve().parent / lab_dir
    sys.path.insert(0, str(root))
    uvicorn.run(app_import, host=args.host, port=args.port or default_port, access_log=False)


if __name__ == "__main__":
    main()