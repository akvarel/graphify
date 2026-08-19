from graphify.gvr import (
    Action,
    Goal,
    Predicate,
    Proposal,
    VerificationContext,
    VerificationVerdict,
    VerifierRegistry,
    default_registry,
    verify,
)


def test_car_wash_rejects_walking_because_car_never_reaches_wash():
    context = VerificationContext.from_nested_state(
        {
            "person": {"location": "home"},
            "car": {"location": "home", "washed": False},
        },
        goals=(
            Goal(
                id="wash-car",
                conditions=(
                    Predicate("car", "location", "EQ", "car_wash"),
                    Predicate("car", "washed", "EQ", True),
                ),
            ),
        ),
    )
    proposal = Proposal(
        actions=(
            Action(
                "walk_to_car_wash",
                effects=(Predicate("person", "location", "SET", "car_wash"),),
            ),
        ),
    )

    report = verify(context, proposal)

    assert report.verdict == VerificationVerdict.FAIL
    assert {(issue.predicate.subject, issue.predicate.attribute) for issue in report.failures if issue.predicate} == {
        ("car", "location"),
        ("car", "washed"),
    }


def test_car_wash_passes_when_the_car_is_moved_and_washed():
    context = VerificationContext.from_nested_state(
        {"person": {"location": "home"}, "car": {"location": "home", "washed": False}},
        goals=(Goal("wash-car", (Predicate("car", "washed", "EQ", True),)),),
    )
    proposal = Proposal(
        actions=(
            Action(
                "drive_to_car_wash",
                preconditions=(Predicate("car", "location", "EQ", "home"),),
                effects=(
                    Predicate("person", "location", "SET", "car_wash"),
                    Predicate("car", "location", "SET", "car_wash"),
                ),
            ),
            Action(
                "wash_car",
                preconditions=(Predicate("car", "location", "EQ", "car_wash"),),
                effects=(Predicate("car", "washed", "SET", True),),
            ),
        ),
    )

    assert verify(context, proposal).verdict == VerificationVerdict.PASS


def test_missing_email_precondition_is_unknown_not_fabricated():
    context = VerificationContext.from_nested_state({"ivan": {"name": "Ivan"}})
    proposal = Proposal(
        actions=(
            Action(
                "send_email",
                preconditions=(Predicate("ivan", "email", "EXISTS"),),
            ),
        ),
    )

    report = verify(context, proposal)

    assert report.verdict == VerificationVerdict.UNKNOWN
    assert any(issue.code == "PRECONDITION_UNKNOWN" for issue in report.unknowns)


def test_insufficient_fuel_fails_deterministically():
    context = VerificationContext(resources={("car", "fuel_liters"): 5.0})
    proposal = Proposal(
        actions=(
            Action(
                "drive_100km",
                consumes=(Predicate("car", "fuel_liters", "GE", 8.0),),
            ),
        ),
    )

    report = verify(context, proposal)

    assert report.verdict == VerificationVerdict.FAIL
    assert any(issue.code == "RESOURCE_INSUFFICIENT" for issue in report.failures)


def test_resources_are_consumed_across_multiple_actions():
    context = VerificationContext(resources={("battery", "percent"): 10.0})
    proposal = Proposal(
        actions=(
            Action("step-1", consumes=(Predicate("battery", "percent", "GE", 6.0),)),
            Action("step-2", consumes=(Predicate("battery", "percent", "GE", 6.0),)),
        ),
    )

    report = verify(context, proposal)

    assert report.verdict == VerificationVerdict.FAIL
    assert any(issue.action == "step-2" and issue.code == "RESOURCE_INSUFFICIENT" for issue in report.failures)


def test_unsupported_effect_makes_result_unknown():
    context = VerificationContext.from_nested_state(
        {"document": {"approved": False}},
        goals=(Goal("approved", (Predicate("document", "approved", "EQ", True),)),),
    )
    proposal = Proposal(
        actions=(Action("magic", effects=(Predicate("document", "approved", "CALL_TOOL", "approve"),)),),
    )

    report = verify(context, proposal)

    assert report.verdict == VerificationVerdict.UNKNOWN
    assert any(issue.code == "EFFECT_UNSUPPORTED" for issue in report.unknowns)


def test_invariant_violation_fails_even_if_goal_passes():
    context = VerificationContext.from_nested_state(
        {"account": {"balance": 10}, "order": {"paid": False}},
        goals=(Goal("paid", (Predicate("order", "paid", "EQ", True),)),),
        invariants=(Predicate("account", "balance", "GE", 0),),
    )
    proposal = Proposal(
        actions=(
            Action(
                "pay",
                effects=(
                    Predicate("account", "balance", "SET", -5),
                    Predicate("order", "paid", "SET", True),
                ),
            ),
        ),
    )

    report = verify(context, proposal)

    assert report.verdict == VerificationVerdict.FAIL
    assert any(issue.code == "INVARIANT_VIOLATED" for issue in report.failures)


def test_contradictory_proposal_claims_fail():
    proposal = Proposal(
        actions=(),
        claims=(
            Predicate("service", "healthy", "EQ", True),
            Predicate("service", "healthy", "EQ", False),
        ),
    )

    report = verify(VerificationContext(), proposal)

    assert report.verdict == VerificationVerdict.FAIL
    assert any(issue.code == "CLAIM_CONTRADICTION" for issue in report.failures)


def test_unknown_goal_state_is_unknown():
    context = VerificationContext(
        goals=(Goal("weather", (Predicate("outside", "dry", "EQ", True),)),),
    )

    assert verify(context, Proposal(actions=())).verdict == VerificationVerdict.UNKNOWN


def test_duplicate_verifier_registration_is_rejected():
    registry = default_registry()
    duplicate = default_registry().values()[0]

    try:
        registry.register(duplicate)
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("duplicate verifier registration must fail")


def test_empty_registry_passes_without_claiming_unchecked_goals():
    # A deliberately empty registry is a caller choice. It proves only that no
    # selected verifier failed; production policy must decide which verifiers are mandatory.
    registry = VerifierRegistry()
    context = VerificationContext(goals=(Goal("g", (Predicate("x", "y", "EQ", 1),)),))

    assert verify(context, Proposal(actions=()), registry=registry).verdict == VerificationVerdict.PASS
