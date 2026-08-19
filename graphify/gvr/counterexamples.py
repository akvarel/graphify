"""Counterexample witnesses for deterministic GVR verification results."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .core import VerificationReport, VerificationVerdict


@dataclass(frozen=True)
class Counterexample:
    """A minimal witness showing why a proposal did not verify.

    This is intentionally not an LLM-generated narrative. It records the failed
    or unknown proposition and the concrete post-state value when one exists.
    """

    verifier: str
    code: str
    subject: str | None
    attribute: str | None
    expected_operator: str | None
    expected_value: Any
    actual_value: Any
    verdict: VerificationVerdict
    action: str | None = None


def counterexamples(report: VerificationReport) -> tuple[Counterexample, ...]:
    """Return bounded minimal witnesses for FAIL/UNKNOWN issues."""
    result: list[Counterexample] = []
    for issue in report.issues:
        if issue.verdict == VerificationVerdict.PASS:
            continue
        predicate = issue.predicate
        if predicate is None:
            result.append(Counterexample(
                verifier=issue.verifier,
                code=issue.code,
                subject=None,
                attribute=None,
                expected_operator=None,
                expected_value=None,
                actual_value=None,
                verdict=issue.verdict,
                action=issue.action,
            ))
            continue
        key = predicate.key()
        result.append(Counterexample(
            verifier=issue.verifier,
            code=issue.code,
            subject=predicate.subject,
            attribute=predicate.attribute,
            expected_operator=predicate.operator,
            expected_value=predicate.value,
            actual_value=report.final_state.get(key),
            verdict=issue.verdict,
            action=issue.action,
        ))
    return tuple(result)
