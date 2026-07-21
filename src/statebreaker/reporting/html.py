"""Minimal static HTML report for one finding (spec §15.1).

Presentation boundary: identity headers and credentials are redacted here.
"""

from __future__ import annotations

import html

from statebreaker.artifacts.redaction import redact_mapping
from statebreaker.i18n import bi, current_language
from statebreaker.models.discovery import AttackPlan
from statebreaker.models.findings import Finding


def render_html_report(finding: Finding, plan: AttackPlan) -> str:
    """A self-contained, no-JavaScript summary of the verdict and evidence."""
    html_lang = "zh-CN" if current_language() == "zh-CN" else "en"
    report_title = html.escape(bi("StateBreaker 报告", "StateBreaker report"))
    heading = html.escape(bi("StateBreaker finding 报告", "StateBreaker finding report"))
    intro = html.escape(
        bi(
            "本报告只展示已保存证据，不会编造请求或修改 JSON 证据结构。",
            "This report only presents stored evidence; it does not invent requests "
            "or change the JSON evidence schema.",
        )
    )
    labels = {
        "finding": html.escape(bi("Finding 编号", "Finding")),
        "verdict": html.escape(bi("判定", "Verdict")),
        "candidate": html.escape(bi("候选", "Candidate")),
        "scheduler": html.escape(bi("调度器", "Scheduler")),
        "minimum_concurrency": html.escape(bi("最小并发数", "Minimum concurrency")),
        "best_scheduler": html.escape(bi("最佳调度器", "Best scheduler")),
        "explanation": html.escape(bi("解释", "Explanation")),
        "instances": html.escape(bi("并发实例", "Attack instances")),
        "trials": html.escape(bi("证据 trial", "Evidence trials")),
        "candidate_detail": html.escape(bi("候选详情", "Candidate detail")),
    }
    stats = finding.statistics
    stats_rows = ""
    if stats is not None:
        success_label = html.escape(bi("成功率", "Success rate"))
        skew_label = html.escape(bi("中位释放偏移", "Median release skew"))
        stats_rows = (
            f"<tr><th>{success_label}</th><td>{stats.successes}/{stats.rounds}"
            f" ({stats.success_rate:.0%})</td></tr>"
            f"<tr><th>{skew_label}</th>"
            f"<td>{stats.median_release_skew_ms:.3f} ms</td></tr>"
        )
    explanations = "".join(
        f"<li>{html.escape(line)}</li>" for line in finding.explanation
    )
    instances = "".join(
        f"<li>{html.escape(instance.instance_id)} — action "
        f"{html.escape(instance.action_id)}, session "
        f"{html.escape(instance.session_id)}</li>"
        for instance in plan.action_instances
    )
    redacted_candidate = redact_mapping(finding.candidate.model_dump(mode="json"))
    evidence = "".join(f"<li>{html.escape(ref)}</li>" for ref in finding.evidence_refs)
    best_scheduler = html.escape(finding.best_scheduler or plan.scheduler)
    return f"""<!doctype html>
<html lang="{html_lang}">
<head>
<meta charset="utf-8">
<title>{report_title} - {html.escape(finding.finding_id)}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; max-width: 60rem; }}
table {{ border-collapse: collapse; }}
th, td {{ border: 1px solid #ccc; padding: 0.3rem 0.7rem; text-align: left; }}
th {{ background: #f3f3f3; }}
.verdict-confirmed {{ color: #0a7d2c; font-weight: bold; }}
</style>
</head>
<body>
<h1>{heading}</h1>
<p>{intro}</p>
<table>
<tr><th>{labels["finding"]}</th><td>{html.escape(finding.finding_id)}</td></tr>
<tr><th>{labels["verdict"]}</th><td class="verdict-{html.escape(finding.verdict)}">
{html.escape(finding.verdict.upper())} ({finding.confidence:.2f})</td></tr>
<tr><th>{labels["candidate"]}</th><td>{html.escape(finding.candidate.candidate_id)}</td></tr>
<tr><th>{labels["scheduler"]}</th><td>{html.escape(plan.scheduler)}</td></tr>
<tr><th>{labels["minimum_concurrency"]}</th><td>{finding.minimum_concurrency or "n/a"}</td></tr>
<tr><th>{labels["best_scheduler"]}</th><td>{best_scheduler}</td></tr>
{stats_rows}
</table>
<h2>{labels["explanation"]}</h2>
<ul>{explanations}</ul>
<h2>{labels["instances"]}</h2>
<ul>{instances}</ul>
<h2>{labels["trials"]}</h2>
<ul>{evidence}</ul>
<h2>{labels["candidate_detail"]}</h2>
<pre>{html.escape(str(redacted_candidate))}</pre>
</body>
</html>
"""
