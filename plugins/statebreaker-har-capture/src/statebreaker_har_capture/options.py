"""Strict options accepted by the HAR capture plugin."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HarCaptureOptions(BaseModel):
    """Configuration for deterministic HAR normalization."""

    model_config = ConfigDict(extra="forbid", strict=True)

    state_probe_entry_indices: list[int] = Field(default_factory=list)
    strip_credentials: bool = False

    @model_validator(mode="after")
    def validate_probe_indices(self) -> HarCaptureOptions:
        if any(index < 0 for index in self.state_probe_entry_indices):
            raise ValueError("state_probe_entry_indices must contain only non-negative indices")
        if len(self.state_probe_entry_indices) != len(set(self.state_probe_entry_indices)):
            raise ValueError("state_probe_entry_indices must not contain duplicates")
        return self
