"""Deterministic text-membership verifier plugin for GVR.

This verifier is deliberately language-agnostic. It checks both that an
executed search specification matches an explicitly grounded literal request
(where available) and that the claimed result matches literal source strings.
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

    `needle` is the needle the candidate/executor actually used.
    `requested_needle` is an independently grounded literal from the user/task
    specification when one is available. When `require_grounding` is true and
    the grounded literal is absent, verification returns UNKNOWN instead of
    silently trusting the candidate specification.

    `claimed_matches` contains original source-item values, not transformed
    text. This keeps identity stable when `reverse=True` is used.
    """

    id: str
    items: tuple[str, ...]
    needle: str
    claimed_matches: tuple[str, ...]
    requested_needle: str | None = None
    require_grounding: bool = False
    case_sensitive: bool = True
    unicode_normalization: str | None = "NFC"
    reverse: bool = False
    order_sensitive: bool = False


@dataclass(frozen=True)
class TextSearchResult:
    assertion_id: str
    actual_matches: tuple[str, ...]
    claimed_matches: tuple[str, ...]


def _normalize_literal(value: str, assertion: TextSearchAssertion) -> str:
    text = value
    if assertion.unicode_normalization:
        text = unicodedata.normalize(assertion.unicode_normalization, text)
    if not assertion.case_sensitive:
        text = text.casefold()
    return text


def _prepare(value: str, assertion: TextSearchAssertion) -> str:
    # Normalize before reversal. This prevents a decomposed diacritic sequence
    # such as e + COMBINING MACRON from being reversed into an invalid ordering
    # before NFC has a chance to compose it.
    text = _normalize_literal(value, assertion)
    return text[::-1] if assertion.reverse else text


def _actual_matches(assertion: TextSearchAssertion) -> tuple[str, ...]:
    needle = _normalize_literal(assertion.needle, assertion)
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


def _grounding_issue(assertion: TextSearchAssertion) -> VerificationIssue | None:
    if assertion.requested_needle is None:
        if assertion.require_grounding:
            return VerificationIssue(
                verifier=TextSearchVerifier.name,
                code="TEXT_SEARCH_SPEC_UNGROUNDED",
                verdict=VerificationVerdict.UNKNOWN,
                message=(
                    f"Text search {assertion.id!r} requires an independently grounded "
                    "requested needle"
                ),
                predicate=Predicate(
                    subject=f"text_search:{assertion.id}",
                    attribute="needle",
                    operator="EQ",
                    value=assertion.needle,
                ),
            )
        return None

    requested = _normalize_literal(assertion.requested_needle, assertion)
    executed = _normalize_literal(assertion.needle, assertion)
    if requested == executed:
        return None
    return VerificationIssue(
        verifier=TextSearchVerifier.name,
        code="TEXT_SEARCH_SPEC_MISMATCH",
        verdict=VerificationVerdict.FAIL,
        message=(
            f"Text search {assertion.id!r} executed needle {assertion.needle!r}, "
            f"but grounded request requires {assertion.requested_needle!r}"
        ),
        predicate=Predicate(
            subject=f"text_search:{assertion.id}",
            attribute="needle",
            operator="EQ",
            value=assertion.requested_needle,
        ),
    )


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
            grounding = _grounding_issue(assertion)
            if grounding is not None:
                issues.append(grounding)
                # A known spec mismatch invalidates result verification against
                # that candidate spec. An ungrounded spec can still be checked
                # for internal result consistency, but it cannot earn PASS.
                if grounding.verdict == VerificationVerdict.FAIL:
                    continue

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
