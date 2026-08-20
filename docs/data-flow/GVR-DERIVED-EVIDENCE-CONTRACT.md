# GVR Derived-Evidence Contract (Gate 3)

This document defines the contract between the Gate 3 bounded data-flow
traversal (source-evidence layer) and a future GVR adapter. It describes what
Gate 3 emits, how derived evidence is backed by direct evidence, how epistemic
state propagates, and what Gate 3 explicitly does not decide.

It is written to be consumed by the GVR concepts in
`docs/gvr/GOAL-VERIFICATION-CORE.md` (PASS/FAIL/UNKNOWN, `Evidence(kind,
reference, metadata)`, verifier registry, counterexamples/witnesses,
deterministic evidence vs model proposals) — which are inspected on the draft
branch `feature/gvr-goal-verification-core-clean-v8` and **not** merged here.

---

## 1. Direct evidence

Direct evidence is a persisted source fact from Gate 2 / 2B. It is always one
direct edge in the source graph:

- relation ∈ `{FLOWS_TO, PASSED_AS_ARGUMENT, RETURNED_AS, READ_FROM,
  WRITTEN_TO, TRANSFORMED_BY}`;
- canonical source node id; canonical target node id;
- repo-relative `source_file` and `source_location`;
- `provenance` (`STATIC_AST` / `CROSS_FILE`);
- optional cross-file `receiverConfidence` (PROVEN/MAY) and
  `analysisCompleteness`.

## 2. Direct evidence key

Every direct edge can be referenced by a deterministic, checkout-root
independent evidence key:

```text
df:<sha256(canonical JSON of [relation, source id, target id,
     repo-relative source_file, source_location, provenance,
     argumentIndex?])>
```

The same direct source fact at the same canonical source revision always maps to
the same key; different parallel relations or different source locations map to
distinct keys. The Gate 2B metadata `callee` (which embeds an absolute checkout
slug) is deliberately excluded; callee identity is captured canonically by the
edge's own target node id.

## 3. Derived path evidence

A derived path is a **query-time** artifact, never persisted as a source fact. A
`DataFlowPath` is an ordered sequence of `DataFlowPathStep`s, each backed by
exactly one direct evidence ref:

```text
Path P1:
  A --PASSED_AS_ARGUMENT--> B     step1
  B --FLOWS_TO--> C               step2
  C --RETURNED_AS--> D            step3
supporting_evidence = [E1, E2, E3]
```

## 4. Supporting dependencies

Each path exposes `supporting_evidence` (the ordered direct evidence refs) and
`path_identity` (the ordered evidence keys). A later GVR adapter can express,
without reparsing source:

```text
Claim: A CAN_FLOW_TO D
supports: P1
P1 depends_on: [E1, E2, E3]
```

The adapter never has to reconstruct the path from prose.

## 5. Epistemic propagation

- `path_receiver_confidence` = `PROVEN` only if every step is PROVEN, else `MAY`.
- `path_coverage` = `COMPLETE_FOR_SUPPORTED_CONSTRUCT` only if every step is
  complete, else `PARTIAL`.
- `path_exactness` = `EXACT_FOR_RETURNED_PATH` (exact returned path).
- `search_coverage` (result) and `complete_supported_search` (result).

Required semantics:

1. any `MAY` step prevents an all-PROVEN presentation;
2. any `PARTIAL` step/boundary prevents a complete-coverage claim;
3. a traversal cutoff prevents completeness claims beyond the cutoff;
4. encountered AMBIGUOUS/UNRESOLVED/UNSUPPORTED boundaries are retained;
5. an exact path can coexist with partial search coverage.

Valid example:

```text
pathExactness        = EXACT_FOR_RETURNED_PATH
pathReceiverConfidence = MAY
searchCoverage       = PARTIAL
truncated            = false
```

This is never collapsed to a single float. `confidence_score` is carried per
evidence ref for inspection but is never averaged into a derived truth value.

## 6. Truncation

`DataFlowTraversalResult.truncated` + `termination_reason` ∈
`COMPLETE / MAX_DEPTH / MAX_PATHS / MAX_EXPANSIONS`. A truncated result must
never be presented as complete.

## 7. Ambiguity / unresolved / unsupported boundary events

Blocking `extraction_diagnostic` boundaries (`AMBIGUOUS` / `UNRESOLVED` /
`UNSUPPORTED`) are surfaced as `boundary_event` entries (resolution, reason,
callerFile, callerLocation, receiver, method, arity, importContext,
candidateCount) when the traversal reached a data value in the boundary's caller
file. They are **not** flow steps and never fabricate a path through the
boundary. This keeps machine-visible:

```text
NO PATH
PATH SEARCH STOPPED AT AMBIGUOUS BOUNDARY
PATH SEARCH STOPPED AT UNSUPPORTED BOUNDARY
PATH SEARCH TRUNCATED BY QUERY LIMIT
```

## 8. What GVR may derive later

GVR may form claims (`A CAN_FLOW_TO D`) whose `supports` reference a derived
path, whose dependencies reference direct evidence keys, and whose epistemic
state (MAY/PARTIAL/truncated/boundary events) gates whether a verifier can
accept the claim as `PASS`/`FAIL`/`UNKNOWN`. GVR's fail-closed ordering
(`FAIL > UNKNOWN > PASS`) applies at the GVR layer, not here.

## 9. What Gate 3 explicitly does not decide

- Whether a path/claim is sufficiently supported for a specific use.
- Any GVR verdict (`PASS`/`FAIL`/`UNKNOWN`, `VERIFIED`).
- A GVR Claim Ledger, global Claim Dependency Graph persistence, risk policy,
  production-action authorization, runtime/deployment/incident verdicts, or
  autonomous remediation policy.
- `Derived path exists` is **not** `GVR VERIFIED`; `all direct edges EXACT` is
  **not** whole-program completeness.

## 10. No confidence laundering

Traversal never increases certainty from multiple edges or multiple paths:

```text
MAY + EXACT => PROVEN            forbidden
0.5 + 1.0 => 0.75 => VERIFIED    forbidden
three independent-looking paths => definite truth   forbidden
```

Multiple paths are multiple evidence alternatives; whether they strengthen a
claim is a later verifier/policy decision, not a traversal decision.

## 11. Supervising-review remediation contract (task 05)

The following semantics are part of the public contract and are enforced by
`graphify/data_flow_query.py`.

### Query validity (P1-2)

Invalid direction (`direction not in {FORWARD, BACKWARD}`), negative
`max_depth`, `max_paths < 1`, or `max_expansions < 1` are rejected
deterministically with `ValueError` before any traversal. There is no silent
normalization and no contradictory behavior.

### Effective relation allowlist (P0-2)

The caller's `allowed_relations` is intersected with the supported value-flow
vocabulary `SUPPORTED_DATA_FLOW_RELATIONS` = `DEFAULT_DATA_FLOW_RELATIONS`. The
effective allowlist drives adjacency. Unsupported requested relations (e.g.
`CALLS`, `imports`, `references`, `contains`, `method`, `inherits`, `uses`)
are exposed in `query_bounds["rejected_relations"]` and `rejected_relations` and
can never become value-flow steps, regardless of what a caller requests.

### Same-file and cross-file receiver confidence (P0-1)

Receiver confidence is derived from the direct evidence itself, never from
`cross_file == false`. An explicit `metadata.receiverConfidence` is preserved for
both same-file and cross-file edges. When it is absent, receiver-oriented
relations (`READ_FROM`, `WRITTEN_TO`, `PASSED_AS_ARGUMENT`) default to `MAY`
(fail closed: identity cannot be proven from the evidence); non-receiver
relations (`FLOWS_TO`, `RETURNED_AS`, `TRANSFORMED_BY`) default to `PROVEN` (no
receiver to prove). Any `MAY` step forces `path_receiver_confidence == MAY`.
`confidence_score` is never numerically reinterpreted into `PROVEN`.

### Same-file incomplete evidence (P0-5)

Completeness is distinct from confidence. Gate 2 emits an explicit
`analysisCompleteness` for same-file edges (parse-incomplete -> `PARTIAL`,
otherwise `COMPLETE_FOR_SUPPORTED_CONSTRUCT`), mirroring the cross-file
representation. Gate 3 preserves an explicit `analysisCompleteness` for both
same-file and cross-file edges. When completeness metadata is absent (e.g.
hand-authored fixtures), a conservative fallback applies: a direct fact with
`confidence_score < 1.0` is treated as `PARTIAL` (never upgraded to complete);
a full-trust edge is treated as `COMPLETE_FOR_SUPPORTED_CONSTRUCT`. Any
`PARTIAL` step makes `path_coverage == PARTIAL` and degrades search coverage.

### Explored-region vs returned-path search coverage (P0-6)

Epistemic state is tracked over the **explored search region**, not only over
returned target paths. `encountered_partial_evidence`,
`encountered_unknown_evidence`, and `encountered_may_evidence` are surfaced on
the result. An explored `PARTIAL`/`UNKNOWN` dead-end branch degrades
`search_coverage` to `PARTIAL` even when it produces no returned path. A
`PARTIAL` edge in an unrelated, unreachable component never degrades search
(because it is never consumed). An exact returned path may coexist with
`search_coverage == PARTIAL`.

### Missing start / target semantics (P0-3)

When the start node is absent, the traversal does not run and the result is:
`paths == ()`, `complete_supported_search == False`, `search_coverage == UNKNOWN`,
`termination_reason == START_NODE_NOT_FOUND`, `input_resolution == START_NODE_NOT_FOUND`.
For a point-to-point query with a present start but a missing target, the target
search is treated as unresolved/incomplete (never as proof of no path):
`termination_reason == TARGET_NODE_NOT_FOUND`, `input_resolution == TARGET_NODE_NOT_FOUND`,
`complete_supported_search == False`. Neither case is ordinary truncation and
neither can be reported as a complete search.

### Zero-depth semantics (P0-4)

`max_depth == 0` never traverses. If at least one eligible edge exists in the
selected direction from the start, the result is truncated/incomplete with
`termination_reason == MAX_DEPTH` (machine-visible cutoff before examining
reachable flow). If no eligible edge exists, a zero-expansion search may be
complete. The start == target identity path is emitted and explicitly defined
at any depth, including zero. The rule is symmetric for backward traversal.

### Boundary evidence identity and portability (P0-7)

Boundary events are first-class evidence references, not prose metadata blobs.
Each exposes: `boundary_evidence_key` (deterministic, checkout-root independent,
derived only from canonical public evidence fields), `diagnostic_node_id` and
`diagnostic_evidence_key` (stable reference to the underlying diagnostic node),
`canonical_caller_file` (repo-relative node `source_file`, never the absolute
extractor path), `caller_location`, `resolution`, `reason`, `receiver`,
`receiver_fqn`, `receiver_confidence`, `method`, `arity`, `import_context`,
`candidate_count`. No absolute checkout root participates in the public
GVR-facing boundary identity. The boundary evidence key deliberately excludes
the diagnostic node id (its stability across a Graphify remap is not guaranteed);
the node id is surfaced separately as a reference.

### Formal criterion for `complete_supported_search`

`complete_supported_search == True` is allowed **only if all** of the following
hold (and is otherwise `False`):

```text
input_resolution == RESOLVED
query_validity == True
not truncated
no blocking boundary in the explored/relevant search region
no PARTIAL evidence encountered in the explored region
no UNKNOWN evidence encountered in the explored region
search_coverage == COMPLETE_FOR_SUPPORTED_CONSTRUCT
```

An exact returned path does not imply a complete search. A complete search does
not imply whole-program completeness. `MAX_PATHS` is reported only when the cap
prevented emitting an additional candidate path (unexplored frontier remained),
never merely when `len(paths) == cap`.
