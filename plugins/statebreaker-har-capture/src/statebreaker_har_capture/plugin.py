"""StateBreaker capture plugin entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError
from statebreaker.models import PluginManifest, Workflow

from statebreaker_har_capture.errors import HarCaptureError
from statebreaker_har_capture.har_parser import parse_har
from statebreaker_har_capture.normalizer import normalize_har
from statebreaker_har_capture.options import HarCaptureOptions


class HarCapturePlugin:
    """Import minimal HAR 1.2 recordings without sending network traffic."""

    manifest = PluginManifest(
        plugin_id="har.capture",
        name="StateBreaker HAR Capture",
        version="0.1.0",
        api_version="0.1",
        group="statebreaker.capture",
        capabilities=[
            "har-1.2",
            "deterministic-workflow",
            "offline-import",
            "json-body",
            "form-body",
            "replayable-credentials",
            "static-resource-filtering",
            "explicit-entry-exclusion",
            "browser-header-normalization",
            "required-response-body-validation",
            "json-response-extractors",
            "explicit-step-roles",
        ],
        description=(
            "Offline HAR 1.2 import and normalization plugin that produces deterministic "
            "StateBreaker workflows without network access."
        ),
    )

    async def capture(self, source: Path, options: dict[str, Any]) -> Workflow:
        """Parse *source* and return a validated Workflow instance."""

        try:
            validated_options = HarCaptureOptions.model_validate(options)
        except ValidationError as exc:
            raise HarCaptureError(
                f"HAR options error at {source}: invalid capture options ({exc.errors()[0]['msg']})"
            ) from exc

        document = parse_har(source)
        candidate = normalize_har(document, validated_options)
        try:
            return Workflow.model_validate(candidate)
        except ValidationError as exc:
            raise HarCaptureError(
                f"HAR workflow error at {source}: normalized workflow is invalid "
                f"({exc.errors()[0]['msg']})"
            ) from exc
