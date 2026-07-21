"""StateBreaker CLI: capture -> discover -> scan -> findings."""

from __future__ import annotations

import typer

from statebreaker.cli import candidates, capture, discover, findings, project, report, scan, wizard
from statebreaker.i18n import bi

app = typer.Typer(
    name="statebreaker",
    help=bi("基于流量的黑盒竞态条件发现。", "Trace-driven black-box race condition discovery."),
    add_completion=False,
    no_args_is_help=True,
)
app.add_typer(project.app, name="project")
app.add_typer(capture.app, name="capture")
app.add_typer(candidates.app, name="candidates")
app.add_typer(findings.app, name="findings")
app.command(
    "run",
    help=bi(
        "一键完成项目、流量、发现、扫描和报告；默认不逐步确认。",
        "Run project setup, capture, discovery, scan and reports; skips confirmations by default.",
    ),
)(wizard.run)
app.command(
    "wizard",
    help=bi(
        "用交互向导逐步确认项目、流量、扫描和报告。",
        "Run the guided setup and scan workflow with confirmations.",
    ),
)(wizard.wizard)
app.command(
    "discover",
    help=bi("只分析正常流量，不执行并发攻击实验。", "Analyze a capture without attacking."),
)(discover.discover)
app.command(
    "scan",
    help=bi(
        "运行自动竞态扫描；报告请使用 report 或 run/wizard 生成。",
        "Run the automatic race scan; use report or run/wizard to generate reports.",
    ),
)(scan.scan)
app.command(
    "report",
    help=bi(
        "为已确认 finding 生成 PoC、JSON 和 HTML 报告。",
        "Generate PoC and reports for a finding.",
    ),
)(report.report)
app.command(
    "reproduce",
    help=bi("打印或写出某个 finding 的可执行 PoC。", "Print the executable PoC for a finding."),
)(report.reproduce)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
