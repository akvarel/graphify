"""Domain-agnostic verification primitives for GVR.

This package intentionally contains no BugZero-specific policy and no LLM calls.
It evaluates structured goals, preconditions, effects, invariants, and evidence
using deterministic PASS / FAIL / UNKNOWN semantics.
"""

from .core import (
    Action,
    Evidence,
    Goal,
    Predicate,
    Proposal,
    VerificationContext,
    VerificationIssue,
    VerificationReport,
    VerificationVerdict,
    VerifierRegistry,
    default_registry,
    verify,
)
from .counterexamples import Counterexample, counterexamples

__all__ = [
    "Action",
    "Counterexample",
    "Evidence",
    "Goal",
    "Predicate",
    "Proposal",
    "VerificationContext",
    "VerificationIssue",
    "VerificationReport",
    "VerificationVerdict",
    "VerifierRegistry",
    "counterexamples",
    "default_registry",
    "verify",
]
