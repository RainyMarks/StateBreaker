"""Finding reports: executable PoC, JSON evidence bundle, HTML summary (§15).

The PoC is rendered only from an already-verified AttackPlan plus a real
attack trial — the reporter never invents requests (spec §15.2).
"""

from statebreaker.reporting.html import render_html_report
from statebreaker.reporting.json_report import build_json_report
from statebreaker.reporting.poc import render_poc_script
from statebreaker.reporting.writer import write_finding_reports

__all__ = [
    "build_json_report",
    "render_html_report",
    "render_poc_script",
    "write_finding_reports",
]
