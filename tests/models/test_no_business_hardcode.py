"""The core must stay business-agnostic (spec §27.4): no target-specific words."""

from __future__ import annotations

import re
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[2] / "src" / "statebreaker"

FORBIDDEN = re.compile(
    r"coupon|redeem|discount|bug50|withdraw|invite|milk\s*tea|奶茶|voucher", re.IGNORECASE
)


def _core_python_files() -> list[Path]:
    return sorted(CORE_ROOT.rglob("*.py"))


def test_core_contains_no_business_specific_terms() -> None:
    offenders: list[str] = []
    for path in _core_python_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if FORBIDDEN.search(line):
                offenders.append(f"{path.relative_to(CORE_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "business-specific terms in core:\n" + "\n".join(offenders)


def test_core_tree_exists() -> None:
    assert _core_python_files(), "expected core sources under src/statebreaker"
