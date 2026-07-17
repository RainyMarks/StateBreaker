"""Minimal reporter: render a single PDF summary from a RunBundle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fpdf import FPDF
from statebreaker.errors import PluginError
from statebreaker.models import PluginManifest, ReportArtifacts, RunBundle


class PdfReporterPlugin:
    """Write a simple one-file PDF report under the output directory."""

    manifest = PluginManifest(
        plugin_id="team.pdf-reporter",
        name="Minimal PDF reporter",
        version="0.1.0",
        api_version="0.1",
        group="statebreaker.reporter",
        capabilities=["pdf", "run-bundle-summary"],
        description="Renders a single PDF summary for one RunBundle.",
    )

    async def render(self, bundle: RunBundle, output_dir: Path) -> ReportArtifacts:
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = output_dir / "statebreaker-report.pdf"
            _write_pdf(bundle, pdf_path)
            # Side JSON is handy for debugging; PDF is the primary artifact.
            summary_path = output_dir / "report-summary.json"
            summary_path.write_text(
                json.dumps(_summary_dict(bundle), ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise PluginError(f"failed to write report artifacts: {exc}") from exc

        return ReportArtifacts(
            files=[
                str(pdf_path.resolve()),
                str(summary_path.resolve()),
            ],
            metadata={
                "reporter": self.manifest.plugin_id,
                "format": "pdf",
                "primary": str(pdf_path.resolve()),
                "findings_count": len(bundle.findings),
                "attack_plan_id": bundle.attack_plan.id,
                "run_id": bundle.result.run_id,
            },
        )


def _summary_dict(bundle: RunBundle) -> dict[str, Any]:
    return {
        "workflow": bundle.workflow.name,
        "attack_plan_id": bundle.attack_plan.id,
        "attack_type": bundle.attack_plan.attack_type,
        "run_id": bundle.result.run_id,
        "started_at": bundle.result.started_at.isoformat(),
        "finished_at": bundle.result.finished_at.isoformat(),
        "before_state": bundle.result.before_state,
        "after_state": bundle.result.after_state,
        "response_status_codes": [item.status_code for item in bundle.result.responses],
        "findings": [
            {
                "id": finding.id,
                "verdict": finding.verdict,
                "title": finding.title,
                "invariant_id": finding.invariant_id,
                "details": finding.details,
            }
            for finding in bundle.findings
        ],
        "plugin_data": bundle.result.plugin_data,
    }


def _write_pdf(bundle: RunBundle, path: Path) -> None:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "StateBreaker Attack Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    pdf.ln(2)

    lines = [
        f"Workflow: {bundle.workflow.name}",
        f"Base URL: {bundle.workflow.base_url}",
        f"Attack plan: {bundle.attack_plan.id}",
        f"Attack type: {bundle.attack_plan.attack_type}",
        f"Target steps: {', '.join(bundle.attack_plan.target_steps)}",
        f"Run ID: {bundle.result.run_id}",
        f"Started: {bundle.result.started_at.isoformat()}",
        f"Finished: {bundle.result.finished_at.isoformat()}",
        "",
        "=== State evidence ===",
        f"Before: {_short_json(bundle.result.before_state)}",
        f"After: {_short_json(bundle.result.after_state)}",
        "",
        "=== HTTP responses ===",
    ]
    if bundle.result.responses:
        for record in bundle.result.responses[:20]:
            lines.append(
                f"- [{record.request_ordinal}] {record.step_id} "
                f"status={record.status_code} elapsed_ms={record.elapsed_ms:.1f} "
                f"corr={record.correlation_id}"
            )
    else:
        lines.append("- (no responses recorded)")

    lines.append("")
    lines.append("=== Findings ===")
    if bundle.findings:
        for finding in bundle.findings:
            lines.append(f"- [{finding.verdict}] {finding.id}: {finding.title}")
            if finding.invariant_id:
                lines.append(f"  invariant: {finding.invariant_id}")
            observed = finding.details.get("observed_delta", finding.details.get("observed_count"))
            if observed is not None:
                lines.append(f"  observed: {observed}")
    else:
        lines.append("- (no findings)")

    plugin_flag = bundle.result.plugin_data.get("vulnerability_observed")
    if plugin_flag is not None:
        lines.append("")
        lines.append(f"Executor heuristic vulnerability_observed: {plugin_flag}")

    lines.append("")
    lines.append("Note: minimal auto-generated report for authorized lab use only.")

    width = pdf.epw
    for line in lines:
        safe = _pdf_safe(line)
        if safe == "":
            pdf.ln(4)
            continue
        pdf.set_x(pdf.l_margin)
        # soft-wrap long unbroken JSON by inserting spaces every 80 chars
        wrapped = _force_wrap(safe, 80)
        pdf.multi_cell(width, 6, wrapped)

    pdf.output(str(path))


def _short_json(value: Any, limit: int = 220) -> str:
    text = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _force_wrap(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    parts = [text[index : index + width] for index in range(0, len(text), width)]
    return "\n".join(parts)


def _pdf_safe(text: str) -> str:
    """Core PDF fonts are Latin-1; keep the report readable without extra fonts."""

    return text.encode("latin-1", errors="replace").decode("latin-1")
