"""Strict options accepted by the HAR capture plugin."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HarCaptureOptions(BaseModel):
    """Configuration for deterministic HAR normalization."""

    model_config = ConfigDict(extra="forbid", strict=True)

    filter_static_resources: bool = True
    infer_response_variables: bool = True
    normalize_browser_headers: bool = True
    exclude_entry_indices: list[int] = Field(default_factory=list)
    required_response_body_entry_indices: list[int] = Field(default_factory=list)
    setup_entry_indices: list[int] = Field(default_factory=list)
    state_probe_entry_indices: list[int] = Field(default_factory=list)
    strip_credentials: bool = False

    @model_validator(mode="after")
    def validate_entry_indices(self) -> HarCaptureOptions:
        for option_name, indices in (
            ("exclude_entry_indices", self.exclude_entry_indices),
            (
                "required_response_body_entry_indices",
                self.required_response_body_entry_indices,
            ),
            ("setup_entry_indices", self.setup_entry_indices),
            ("state_probe_entry_indices", self.state_probe_entry_indices),
        ):
            if any(index < 0 for index in indices):
                raise ValueError(f"{option_name} must contain only non-negative indices")
            if len(indices) != len(set(indices)):
                raise ValueError(f"{option_name} must not contain duplicates")

        conflicts = sorted(
            set(self.setup_entry_indices).intersection(self.state_probe_entry_indices)
        )
        if conflicts:
            raise ValueError(
                "role index conflict: setup_entry_indices and state_probe_entry_indices "
                f"overlap at original entry indices {conflicts}"
            )

        exclude_setup_conflicts = sorted(
            set(self.exclude_entry_indices).intersection(self.setup_entry_indices)
        )
        if exclude_setup_conflicts:
            raise ValueError(
                "entry index conflict: exclude_entry_indices and setup_entry_indices "
                "overlap at original entry indices "
                f"{exclude_setup_conflicts}"
            )

        exclude_probe_conflicts = sorted(
            set(self.exclude_entry_indices).intersection(self.state_probe_entry_indices)
        )
        if exclude_probe_conflicts:
            raise ValueError(
                "entry index conflict: exclude_entry_indices and "
                "state_probe_entry_indices overlap at original entry indices "
                f"{exclude_probe_conflicts}"
            )

        exclude_required_conflicts = sorted(
            set(self.exclude_entry_indices).intersection(
                self.required_response_body_entry_indices
            )
        )
        if exclude_required_conflicts:
            raise ValueError(
                "entry index conflict: exclude_entry_indices and "
                "required_response_body_entry_indices overlap at original entry indices "
                f"{exclude_required_conflicts}"
            )
        return self
