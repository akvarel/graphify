"""Deterministic text-membership verifier plugin for GVR.

This verifier is deliberately language-agnostic. It checks a claimed search
result against literal source strings using configurable case-sensitivity,
Unicode normalization, and optional per-item reversal.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import unicodedata

from .core import (
    Predicate,
    Proposal,
    VerificationContext,
    VerificationIssue,
    VerificationVerdict,
)


@dataclass(frozen=True)
class TextSearchAssertion:
    """A claim about which source items contain a literal needle.

    `claimed_matches` contains the original source-item values, not transformed
    text. This keeps identity stable even when `reverse=True` is used to test a
    transformation invariant.
    """

    id: str
    items: tuple[str, ...]
    needle: str
    claimed_matches: tuple[str, ...]
    case_sensitive: bool = True
    unicode_normalization: str | None = "NFC"
    reverse: bool = False
    order_sensitive: bool = False


@dataclass(frozen=True)
class TextSearchResult:
    assertion_id: str
    actual_matches: tuple[str, ...]
    claimed_matches: tuple[str, ...]


def _prepare(value: str, assertion: TextSearchAssertion) -> str:
    text = value[::-1] if assertion.reverse else value
    if assertion.unicode_normalization:
        text = unicodedata.normalize(assertion.unicode_normalization, text)
    if not assertion.case_sensitive:
        text = text.casefold()
    return text


def _actual_matches(assertion: TextSearchAssertion) -> tuple[str, ...]:
    needle = assertion.needle
    if assertion.unicode_normalization:
        needle = unicodedata.normalize(assertion.unicode_normalization, needle)
    if not assertion.case_sensitive:
        needle = needle.casefold()
    return tuple(item for item in assertion.items if needle in _prepare(item, assertion))


def evaluate_text_search(assertion: TextSearchAssertion) -> TextSearchResult:
    return TextSearchResult(
        assertion_id=assertion.id,
        actual_matches=_actual_matches(assertion),
        claimed_matches=assertion.claimed_matches,
    )


def _same_collection(assertion: TextSearchAssertion, result: TextSearchResult) -> bool:
    if assertion.order_sensitive:
        return result.actual_matches == result.claimed_matches
    return Counter(result.actual_matches) == Counter(result.claimed_matches)


class TextSearchVerifier:
    """Verifier for one or more explicit literal-text search assertions."""

    name = "text_search"

    def __init__(self, assertions: tuple[TextSearchAssertion, ...]) -> None:
        self.assertions = assertions

    def verify(
        self,
        context: VerificationContext,
        proposal: Proposal,
        final_state: dict[tuple[str, str], object],
    ) -> list[VerificationIssue]:
        del context, proposal, final_state
        issues: list[VerificationIssue] = []
        for assertion in self.assertions:
            result = evaluate_text_search(assertion)
            if _same_collection(assertion, result):
                continue
            missing = tuple(item for item in result.actual_matches if item not in result.claimed_matches)
            unexpected = tuple(item for item in result.claimed_matches if item not in result.actual_matches)
            issues.append(
                VerificationIssue(
                    verifier=self.name,
                    code="TEXT_SEARCH_MISMATCH",
                    verdict=VerificationVerdict.FAIL,
                    message=(
                        f"Text search {assertion.id!r} does not match literal source data; "
                        f"missing={missing!r}, unexpected={unexpected!r}"
                    ),
                    predicate=Predicate(
                        subject=f"text_search:{assertion.id}",
                        attribute="matches",
                        operator="EQ",
                        value=result.actual_matches,
                    ),
                )
            )
        return issues
