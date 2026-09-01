"""Deterministic goal/action verification for GVR.

The core consumes structured state and proposals. Semantic extraction from
natural language is intentionally outside this module: an LLM may propose
Goals/Actions/Predicates, but only deterministic verifiers decide whether the
structured proposal satisfies supplied constraints.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Protocol


class VerificationVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class IndeterminateValue(Enum):
    """Marker for a post-state slot that cannot be derived deterministically."""

    UNKNOWN = "INDETERMINATE"


_MISSING = object()


@dataclass(frozen=True)
class Predicate:
    """A deterministic proposition over one world-state slot."""

    subject: str
    attribute: str
    operator: str
    value: Any = None

    def key(self) -> tuple[str, str]:
        return self.subject, self.attribute


@dataclass(frozen=True)
class Goal:
    id: str
    conditions: tuple[Predicate, ...]
    description: str = ""


@dataclass(frozen=True)
class Action:
    name: str
    preconditions: tuple[Predicate, ...] = ()
    effects: tuple[Predicate, ...] = ()
    consumes: tuple[Predicate, ...] = ()


@dataclass(frozen=True)
class Proposal:
    actions: tuple[Action, ...]
    claims: tuple[Predicate, ...] = ()


@dataclass(frozen=True)
class Evidence:
    kind: str
    reference: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationContext:
    state: dict[tuple[str, str], Any] = field(default_factory=dict)
    goals: tuple[Goal, ...] = ()
    invariants: tuple[Predicate, ...] = ()
    resources: dict[tuple[str, str], float] = field(default_factory=dict)
    evidence: tuple[Evidence, ...] = ()

    @classmethod
    def from_nested_state(
        cls,
        state: dict[str, dict[str, Any]],
        **kwargs: Any,
    ) -> "VerificationContext":
        flattened = {
            (subject, attribute): value
            for subject, attributes in state.items()
            for attribute, value in attributes.items()
        }
        return cls(state=flattened, **kwargs)


@dataclass(frozen=True)
class VerificationIssue:
    verifier: str
    code: str
    verdict: VerificationVerdict
    message: str
    predicate: Predicate | None = None
    action: str | None = None


@dataclass
class VerificationReport:
    verdict: VerificationVerdict
    issues: list[VerificationIssue] = field(default_factory=list)
    initial_state: dict[tuple[str, str], Any] = field(default_factory=dict)
    final_state: dict[tuple[str, str], Any] = field(default_factory=dict)
    evidence: tuple[Evidence, ...] = ()

    @property
    def failures(self) -> list[VerificationIssue]:
        return [issue for issue in self.issues if issue.verdict == VerificationVerdict.FAIL]

    @property
    def unknowns(self) -> list[VerificationIssue]:
        return [issue for issue in self.issues if issue.verdict == VerificationVerdict.UNKNOWN]


class Verifier(Protocol):
    name: str

    def verify(
        self,
        context: VerificationContext,
        proposal: Proposal,
        final_state: dict[tuple[str, str], Any],
    ) -> list[VerificationIssue]: ...


def _combine(verdicts: Iterable[VerificationVerdict]) -> VerificationVerdict:
    values = set(verdicts)
    if VerificationVerdict.FAIL in values:
        return VerificationVerdict.FAIL
    if VerificationVerdict.UNKNOWN in values:
        return VerificationVerdict.UNKNOWN
    return VerificationVerdict.PASS


def _compare(actual: Any, predicate: Predicate) -> VerificationVerdict:
    op = predicate.operator.upper()
    expected = predicate.value
    if actual is IndeterminateValue.UNKNOWN:
        return VerificationVerdict.UNKNOWN
    if actual is _MISSING:
        if op == "NOT_EXISTS":
            return VerificationVerdict.PASS
        return VerificationVerdict.UNKNOWN
    if op == "EXISTS":
        return VerificationVerdict.PASS
    if op == "NOT_EXISTS":
        return VerificationVerdict.FAIL
    try:
        if op in {"EQ", "=="}:
            return VerificationVerdict.PASS if actual == expected else VerificationVerdict.FAIL
        if op in {"NE", "!="}:
            return VerificationVerdict.PASS if actual != expected else VerificationVerdict.FAIL
        if op in {"GT", ">"}:
            return VerificationVerdict.PASS if actual > expected else VerificationVerdict.FAIL
        if op in {"GE", ">="}:
            return VerificationVerdict.PASS if actual >= expected else VerificationVerdict.FAIL
        if op in {"LT", "<"}:
            return VerificationVerdict.PASS if actual < expected else VerificationVerdict.FAIL
        if op in {"LE", "<="}:
            return VerificationVerdict.PASS if actual <= expected else VerificationVerdict.FAIL
        if op == "IN":
            return VerificationVerdict.PASS if actual in expected else VerificationVerdict.FAIL
        if op == "NOT_IN":
            return VerificationVerdict.PASS if actual not in expected else VerificationVerdict.FAIL
    except (TypeError, ValueError):
        return VerificationVerdict.UNKNOWN
    return VerificationVerdict.UNKNOWN


def evaluate(predicate: Predicate, state: dict[tuple[str, str], Any]) -> VerificationVerdict:
    return _compare(state.get(predicate.key(), _MISSING), predicate)


def _requirement_amount(requirement: Predicate) -> float | None:
    try:
        return float(requirement.value)
    except (TypeError, ValueError):
        return None


def _resource_verdict(
    requirement: Predicate,
    resources: dict[tuple[str, str], float],
) -> VerificationVerdict:
    amount = _requirement_amount(requirement)
    if amount is None:
        return VerificationVerdict.UNKNOWN
    available = resources.get(requirement.key())
    if available is None:
        return VerificationVerdict.UNKNOWN
    return VerificationVerdict.PASS if available >= amount else VerificationVerdict.FAIL


def _apply_effect(state: dict[tuple[str, str], Any], effect: Predicate) -> bool:
    """Apply a deterministic effect; otherwise taint its post-state as unknown."""
    op = effect.operator.upper()
    key = effect.key()
    if op in {"SET", "EQ", "=="}:
        state[key] = effect.value
        return True
    if op == "DELETE":
        state.pop(key, None)
        return True
    if op in {"ADD", "INCREMENT"}:
        current = state.get(key, _MISSING)
        if current is _MISSING or current is IndeterminateValue.UNKNOWN:
            state[key] = IndeterminateValue.UNKNOWN
            return False
        try:
            state[key] = current + effect.value
            return True
        except (TypeError, ValueError):
            state[key] = IndeterminateValue.UNKNOWN
            return False
    if op in {"SUBTRACT", "DECREMENT"}:
        current = state.get(key, _MISSING)
        if current is _MISSING or current is IndeterminateValue.UNKNOWN:
            state[key] = IndeterminateValue.UNKNOWN
            return False
        try:
            state[key] = current - effect.value
            return True
        except (TypeError, ValueError):
            state[key] = IndeterminateValue.UNKNOWN
            return False
    state[key] = IndeterminateValue.UNKNOWN
    return False


def _transition_action(
    state: dict[tuple[str, str], Any],
    resources: dict[tuple[str, str], float],
    action: Action,
) -> VerificationVerdict:
    """Advance one action without fabricating postconditions.

    - definite failed prerequisite/resource => action cannot execute; no effect;
    - unknown prerequisite/resource => action may or may not execute; affected
      post-state becomes indeterminate;
    - all gates pass => consume resources and apply deterministic effects;
      unsupported effects taint only their affected slots.
    """
    precondition_status = _combine(evaluate(item, state) for item in action.preconditions)
    resource_status = _combine(_resource_verdict(item, resources) for item in action.consumes)
    gate_status = _combine((precondition_status, resource_status))

    if gate_status == VerificationVerdict.FAIL:
        return gate_status
    if gate_status == VerificationVerdict.UNKNOWN:
        for effect in action.effects:
            state[effect.key()] = IndeterminateValue.UNKNOWN
        return gate_status

    for requirement in action.consumes:
        amount = _requirement_amount(requirement)
        if amount is not None:
            resources[requirement.key()] -= amount

    effects_status = VerificationVerdict.PASS
    for effect in action.effects:
        if not _apply_effect(state, effect):
            effects_status = VerificationVerdict.UNKNOWN
    return effects_status


class PreconditionVerifier:
    name = "preconditions"

    def verify(
        self,
        context: VerificationContext,
        proposal: Proposal,
        final_state: dict[tuple[str, str], Any],
    ) -> list[VerificationIssue]:
        state = dict(context.state)
        resources = dict(context.resources)
        issues: list[VerificationIssue] = []
        for action in proposal.actions:
            for predicate in action.preconditions:
                verdict = evaluate(predicate, state)
                if verdict != VerificationVerdict.PASS:
                    issues.append(VerificationIssue(
                        verifier=self.name,
                        code=(
                            "PRECONDITION_FAILED"
                            if verdict == VerificationVerdict.FAIL
                            else "PRECONDITION_UNKNOWN"
                        ),
                        verdict=verdict,
                        message=f"Action {action.name!r} precondition is not established",
                        predicate=predicate,
                        action=action.name,
                    ))
            _transition_action(state, resources, action)
        return issues


class GoalSatisfactionVerifier:
    name = "goal_satisfaction"

    def verify(
        self,
        context: VerificationContext,
        proposal: Proposal,
        final_state: dict[tuple[str, str], Any],
    ) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        for goal in context.goals:
            for predicate in goal.conditions:
                verdict = evaluate(predicate, final_state)
                if verdict != VerificationVerdict.PASS:
                    issues.append(VerificationIssue(
                        verifier=self.name,
                        code=(
                            "GOAL_UNSATISFIED"
                            if verdict == VerificationVerdict.FAIL
                            else "GOAL_UNKNOWN"
                        ),
                        verdict=verdict,
                        message=f"Goal {goal.id!r} is not established after proposal",
                        predicate=predicate,
                    ))
        return issues


class InvariantVerifier:
    name = "invariants"

    def verify(
        self,
        context: VerificationContext,
        proposal: Proposal,
        final_state: dict[tuple[str, str], Any],
    ) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        for predicate in context.invariants:
            verdict = evaluate(predicate, final_state)
            if verdict != VerificationVerdict.PASS:
                issues.append(VerificationIssue(
                    verifier=self.name,
                    code=(
                        "INVARIANT_VIOLATED"
                        if verdict == VerificationVerdict.FAIL
                        else "INVARIANT_UNKNOWN"
                    ),
                    verdict=verdict,
                    message="Required invariant is not established after proposal",
                    predicate=predicate,
                ))
        return issues


class ResourceVerifier:
    name = "resources"

    def verify(
        self,
        context: VerificationContext,
        proposal: Proposal,
        final_state: dict[tuple[str, str], Any],
    ) -> list[VerificationIssue]:
        state = dict(context.state)
        remaining = dict(context.resources)
        issues: list[VerificationIssue] = []
        for action in proposal.actions:
            for requirement in action.consumes:
                verdict = _resource_verdict(requirement, remaining)
                if verdict == VerificationVerdict.PASS:
                    continue
                if _requirement_amount(requirement) is None:
                    code = "RESOURCE_REQUIREMENT_UNKNOWN"
                    message = f"Resource requirement for action {action.name!r} is not numeric"
                elif requirement.key() not in remaining:
                    code = "RESOURCE_UNKNOWN"
                    message = f"Resource required by action {action.name!r} is unknown"
                else:
                    code = "RESOURCE_INSUFFICIENT"
                    message = f"Action {action.name!r} does not have enough resource"
                issues.append(VerificationIssue(
                    verifier=self.name,
                    code=code,
                    verdict=verdict,
                    message=message,
                    predicate=requirement,
                    action=action.name,
                ))
            _transition_action(state, remaining, action)
        return issues


class ClaimConsistencyVerifier:
    name = "claim_consistency"

    def verify(
        self,
        context: VerificationContext,
        proposal: Proposal,
        final_state: dict[tuple[str, str], Any],
    ) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        seen: dict[tuple[str, str], Any] = {}
        for claim in proposal.claims:
            if claim.operator.upper() not in {"EQ", "=="}:
                continue
            key = claim.key()
            if key in seen and seen[key] != claim.value:
                issues.append(VerificationIssue(
                    verifier=self.name,
                    code="CLAIM_CONTRADICTION",
                    verdict=VerificationVerdict.FAIL,
                    message="Proposal contains contradictory claims about the same state slot",
                    predicate=claim,
                ))
            seen[key] = claim.value
        return issues


class EffectCoverageVerifier:
    """Expose effects the generic runtime cannot deterministically simulate."""

    name = "effect_coverage"

    def verify(
        self,
        context: VerificationContext,
        proposal: Proposal,
        final_state: dict[tuple[str, str], Any],
    ) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        state = dict(context.state)
        resources = dict(context.resources)
        for action in proposal.actions:
            gate = _combine(
                [evaluate(item, state) for item in action.preconditions]
                + [_resource_verdict(item, resources) for item in action.consumes]
            )
            if gate == VerificationVerdict.PASS:
                for effect in action.effects:
                    if not _apply_effect(state, effect):
                        issues.append(VerificationIssue(
                            verifier=self.name,
                            code="EFFECT_UNSUPPORTED",
                            verdict=VerificationVerdict.UNKNOWN,
                            message=(
                                f"Generic runtime cannot deterministically simulate "
                                f"effect for action {action.name!r}"
                            ),
                            predicate=effect,
                            action=action.name,
                        ))
                for requirement in action.consumes:
                    amount = _requirement_amount(requirement)
                    if amount is not None:
                        resources[requirement.key()] -= amount
            else:
                _transition_action(state, resources, action)
        return issues


class VerifierRegistry:
    def __init__(self, verifiers: Iterable[Verifier] = ()) -> None:
        self._verifiers: dict[str, Verifier] = {}
        for verifier in verifiers:
            self.register(verifier)

    def register(self, verifier: Verifier) -> None:
        if not verifier.name:
            raise ValueError("verifier name must be non-empty")
        if verifier.name in self._verifiers:
            raise ValueError(f"verifier already registered: {verifier.name}")
        self._verifiers[verifier.name] = verifier

    def values(self) -> tuple[Verifier, ...]:
        return tuple(self._verifiers.values())


def default_registry() -> VerifierRegistry:
    return VerifierRegistry((
        PreconditionVerifier(),
        ResourceVerifier(),
        EffectCoverageVerifier(),
        ClaimConsistencyVerifier(),
        GoalSatisfactionVerifier(),
        InvariantVerifier(),
    ))


def simulate(context: VerificationContext, proposal: Proposal) -> dict[tuple[str, str], Any]:
    state = dict(context.state)
    resources = dict(context.resources)
    for action in proposal.actions:
        _transition_action(state, resources, action)
    return state


def _aggregate(issues: Iterable[VerificationIssue]) -> VerificationVerdict:
    return _combine(issue.verdict for issue in issues)


def verify(
    context: VerificationContext,
    proposal: Proposal,
    registry: VerifierRegistry | None = None,
) -> VerificationReport:
    registry = registry or default_registry()
    final_state = simulate(context, proposal)
    issues: list[VerificationIssue] = []
    for verifier in registry.values():
        issues.extend(verifier.verify(context, proposal, final_state))
    return VerificationReport(
        verdict=_aggregate(issues),
        issues=issues,
        initial_state=dict(context.state),
        final_state=final_state,
        evidence=context.evidence,
    )
