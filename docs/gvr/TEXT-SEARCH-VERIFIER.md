# GVR Text Search Verifier

## Purpose

`TextSearchVerifier` is a small domain plugin demonstrating how GVR can verify
an objectively checkable answer without asking another LLM whether the answer
"looks right".

It independently checks two layers:

1. **spec grounding** — did the candidate execute the literal search the task
   actually requested?
2. **result verification** — does the claimed match set equal a fresh literal
   scan of the supplied source items?

This separation is important. A result may be internally correct for the wrong
question.

## Verdicts

- grounded spec + correct result => `PASS`;
- grounded spec differs from executed spec => `FAIL / TEXT_SEARCH_SPEC_MISMATCH`;
- correct spec but wrong result => `FAIL / TEXT_SEARCH_MISMATCH`;
- grounding is required but unavailable => `UNKNOWN / TEXT_SEARCH_SPEC_UNGROUNDED`.

No semantic similarity or LLM judgment participates in the literal scan.

## Latvian weekday regression

Source corpus:

```text
pirmdiena
otrdiena
trešdiena
ceturtdiena
piektdiena
sestdiena
svētdiena
```

For a grounded request to search for plain Unicode `e` (U+0065), the verifier
computes all seven words as matches. The common suffix `-diena` contains plain
`e` in every item.

The historical wrong subset:

```text
trešdiena
ceturtdiena
piektdiena
sestdiena
```

is rejected with the missing items:

```text
pirmdiena
otrdiena
svētdiena
```

A candidate that silently executes `ē` instead of the grounded plain `e` is
rejected at the spec-grounding layer even though `svētdiena` is an internally
correct result for the different `ē` search.

A genuine grounded `ē` request correctly yields only:

```text
svētdiena
```

## Reverse invariant

The plugin can apply per-item reversal before scanning while retaining original
item identity. Searching the reversed Latvian weekday strings for plain `e`
still yields all seven source items, demonstrating the invariant that reversing
a string does not remove its character membership.

The implementation normalizes Unicode before reversal so a decomposed sequence
such as `e` + COMBINING MACRON can compose before code-point reversal.

## Limitation

GVR cannot manufacture trustworthy user-intent grounding from nothing.

`requested_needle` must come from an independently grounded task fact. For an
explicit literal such as `e`, a deterministic request parser can provide that
fact. For genuinely ambiguous natural language, the correct state is UNKNOWN
until clarification or stronger evidence resolves the requested operation.

This is deliberate: a verifier that lets the same model both reinterpret the
question and certify its reinterpretation is self-verification, not independent
verification.
