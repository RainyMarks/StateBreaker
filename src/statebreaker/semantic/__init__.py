"""Optional LLM assistance layer (spec §16).

The advisor may label and rank, never decide: verdicts come only from state
evidence. The system must work end-to-end with the advisor disabled (Noop).
"""

from statebreaker.semantic.advisor import SemanticAdvisor, SemanticContext, SemanticLabel
from statebreaker.semantic.noop import NoopSemanticAdvisor

__all__ = [
    "NoopSemanticAdvisor",
    "SemanticAdvisor",
    "SemanticContext",
    "SemanticLabel",
]
