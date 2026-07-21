"""Typed exceptions shared across StateBreaker modules."""


class StateBreakerError(Exception):
    """Base error for expected StateBreaker failures."""


class DocumentError(StateBreakerError):
    """A YAML or JSON document could not be loaded or validated."""


class ConfigError(StateBreakerError):
    """The project configuration is invalid or inconsistent."""


class TemplateError(StateBreakerError):
    """A request template references a missing or unusable variable."""


class ExtractionError(StateBreakerError):
    """A value could not be extracted from a response."""


class CaptureError(StateBreakerError):
    """A capture source could not be parsed or executed."""


class ExecutionError(StateBreakerError):
    """An experiment or attack execution failed."""


class BudgetExhaustedError(StateBreakerError):
    """The scan budget was exhausted before the stage completed."""


class ScopeViolationError(StateBreakerError):
    """A request would leave the configured target scope."""


class ArtifactError(StateBreakerError):
    """An artifact could not be stored, indexed, or loaded."""
