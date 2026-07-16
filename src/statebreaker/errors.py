"""Typed exceptions shared by the CLI, runtime, and plugins."""


class StateBreakerError(Exception):
    """Base error for expected StateBreaker failures."""


class DocumentError(StateBreakerError):
    """A YAML or JSON document could not be loaded or validated."""


class TemplateError(StateBreakerError):
    """A request template references a missing or unusable variable."""


class ExtractionError(StateBreakerError):
    """A response extractor could not obtain its required value."""


class RuntimeRequestError(StateBreakerError):
    """A shared-runtime HTTP operation failed."""


class PluginError(StateBreakerError):
    """A plugin could not be discovered, validated, or invoked."""
