import unicodedata

from graphify.gvr import (
    Proposal,
    TextSearchAssertion,
    TextSearchVerifier,
    VerificationContext,
    VerificationVerdict,
    VerifierRegistry,
    evaluate_text_search,
    verify,
)


LATVIAN_WEEKDAYS = (
    "pirmdiena",
    "otrdiena",
    "trešdiena",
    "ceturtdiena",
    "piektdiena",
    "sestdiena",
    "svētdiena",
)


def _verify(assertion: TextSearchAssertion):
    return verify(
        VerificationContext(),
        Proposal(actions=()),
        registry=VerifierRegistry((TextSearchVerifier((assertion,)),)),
    )


def test_plain_e_search_finds_all_seven_latvian_weekdays():
    assertion = TextSearchAssertion(
        id="latvian-weekdays-plain-e",
        items=LATVIAN_WEEKDAYS,
        needle="e",
        requested_needle="e",
        require_grounding=True,
        claimed_matches=LATVIAN_WEEKDAYS,
    )

    result = evaluate_text_search(assertion)
    report = _verify(assertion)

    assert result.actual_matches == LATVIAN_WEEKDAYS
    assert report.verdict == VerificationVerdict.PASS


def test_regression_old_wrong_plain_e_subset_is_rejected():
    # Historical failure shape: the answer omitted pirmdiena, otrdiena and
    # svētdiena even though every weekday contains the plain code point `e` in
    # the shared `-diena` portion.
    old_wrong_subset = (
        "trešdiena",
        "ceturtdiena",
        "piektdiena",
        "sestdiena",
    )
    assertion = TextSearchAssertion(
        id="latvian-weekdays-old-wrong-subset",
        items=LATVIAN_WEEKDAYS,
        needle="e",
        requested_needle="e",
        require_grounding=True,
        claimed_matches=old_wrong_subset,
    )

    report = _verify(assertion)

    assert report.verdict == VerificationVerdict.FAIL
    issue = report.failures[0]
    assert issue.code == "TEXT_SEARCH_MISMATCH"
    assert "pirmdiena" in issue.message
    assert "otrdiena" in issue.message
    assert "svētdiena" in issue.message


def test_wrongly_converting_requested_plain_e_to_macron_e_is_rejected():
    # This catches an earlier-layer interpretation failure: a candidate that
    # silently changes explicit plain `e` into Latvian long `ē` is not allowed
    # to earn PASS merely because its search result is internally correct for ē.
    assertion = TextSearchAssertion(
        id="latvian-weekdays-wrong-needle",
        items=LATVIAN_WEEKDAYS,
        requested_needle="e",
        require_grounding=True,
        needle="ē",
        claimed_matches=("svētdiena",),
    )

    report = _verify(assertion)

    assert report.verdict == VerificationVerdict.FAIL
    assert any(issue.code == "TEXT_SEARCH_SPEC_MISMATCH" for issue in report.failures)


def test_required_but_missing_search_grounding_is_unknown():
    assertion = TextSearchAssertion(
        id="latvian-weekdays-ungrounded",
        items=LATVIAN_WEEKDAYS,
        needle="e",
        requested_needle=None,
        require_grounding=True,
        claimed_matches=LATVIAN_WEEKDAYS,
    )

    report = _verify(assertion)

    assert report.verdict == VerificationVerdict.UNKNOWN
    assert any(issue.code == "TEXT_SEARCH_SPEC_UNGROUNDED" for issue in report.unknowns)


def test_plain_e_and_long_e_macron_are_not_conflated():
    plain = evaluate_text_search(
        TextSearchAssertion(
            id="plain-e",
            items=LATVIAN_WEEKDAYS,
            needle="e",
            claimed_matches=(),
        )
    )
    macron = evaluate_text_search(
        TextSearchAssertion(
            id="long-e",
            items=LATVIAN_WEEKDAYS,
            needle="ē",
            claimed_matches=(),
        )
    )

    assert plain.actual_matches == LATVIAN_WEEKDAYS
    assert macron.actual_matches == ("svētdiena",)


def test_search_for_long_e_macron_accepts_only_svetdiena_when_requested():
    assertion = TextSearchAssertion(
        id="latvian-weekdays-long-e",
        items=LATVIAN_WEEKDAYS,
        requested_needle="ē",
        require_grounding=True,
        needle="ē",
        claimed_matches=("svētdiena",),
    )

    assert _verify(assertion).verdict == VerificationVerdict.PASS


def test_reversing_each_weekday_preserves_plain_e_membership():
    assertion = TextSearchAssertion(
        id="latvian-weekdays-reversed-plain-e",
        items=LATVIAN_WEEKDAYS,
        requested_needle="e",
        require_grounding=True,
        needle="e",
        claimed_matches=LATVIAN_WEEKDAYS,
        reverse=True,
    )

    result = evaluate_text_search(assertion)
    report = _verify(assertion)

    assert result.actual_matches == LATVIAN_WEEKDAYS
    assert report.verdict == VerificationVerdict.PASS


def test_reversed_old_wrong_subset_is_still_rejected():
    assertion = TextSearchAssertion(
        id="latvian-weekdays-reversed-old-wrong-subset",
        items=LATVIAN_WEEKDAYS,
        requested_needle="e",
        require_grounding=True,
        needle="e",
        claimed_matches=(
            "trešdiena",
            "ceturtdiena",
            "piektdiena",
            "sestdiena",
        ),
        reverse=True,
    )

    assert _verify(assertion).verdict == VerificationVerdict.FAIL


def test_search_is_literal_unicode_by_default():
    # NFC normalization preserves the semantic distinction between U+0065 `e`
    # and U+0113 `ē`; it does not strip diacritics.
    assertion = TextSearchAssertion(
        id="literal-unicode",
        items=("ē",),
        needle="e",
        claimed_matches=(),
    )

    result = evaluate_text_search(assertion)

    assert result.actual_matches == ()


def test_reverse_normalizes_decomposed_macron_before_reversal():
    decomposed = unicodedata.normalize("NFD", "ē")
    assertion = TextSearchAssertion(
        id="reverse-decomposed-macron",
        items=(decomposed,),
        needle="ē",
        claimed_matches=(decomposed,),
        reverse=True,
    )

    result = evaluate_text_search(assertion)

    assert result.actual_matches == (decomposed,)
