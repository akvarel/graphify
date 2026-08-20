# Gate 3 — Bounded Data-Flow Traversal Implementation Report

## Status

**Gate 3** (bounded interprocedural data-flow traversal + GVR-consumable derived
evidence). Ready for supervising review. **STOP FOR SUPERVISING REVIEW.**

## Repository / branch / SHAs

- Repository: `akvarel/graphify`
- Source branch (approved Gate 2B): `feature/java-cross-file-data-flow-v8`
- Approved Gate 2B review point: `c6b56a761039f813fe12fbbcddcc7925dc9ee214`
- Gate 2B implementation/remediation: `53afba492040bbe61b45632eb3617e98bd4b2c7e`
- Target branch: `feature/java-bounded-data-flow-traversal-v8`
- Starting SHA (branch base): `c6b56a761039f813fe12fbbcddcc7925dc9ee214`
- Final SHA: `13d503052f3e5e33de55d8e20dcd82b5c7f83a6c`
- `upstream/v8` HEAD: `b14b52e94ec3d9840413d81777f4c134eac0a40d` (unchanged)
- Merge-base with `upstream/v8`: `b14b52e94ec3d9840413d81777f4c134eac0a40d`
- ahead / behind vs `upstream/v8`: 37 ahead / 0 behind
- Working tree was clean before edits; upstream did not change after the
  approved Gate 2B point, so no architectural merge/rebase conflict arose.
- Branch pushed to `fork` (authorized non-main feature branch). Not pushed to
  `v8`, `main`, or `master`.

## Changed / new files

- `graphify/data_flow_query.py` — new bounded traversal layer + public contracts.
- `tests/test_data_flow_traversal.py` — graph-level independent fixtures (A–T,
  GVR-oriented, adversarial).
- `tests/test_data_flow_traversal_java_integration.py` — Java integration
  traversal tests over real Gate 2B extraction output.
- `docs/data-flow/GATE-3-ARCHITECTURE-INVENTORY.md` — new.
- `docs/data-flow/GATE-3-BOUNDED-TRAVERSAL-IMPLEMENTATION-REPORT.md` — this file.
- `docs/data-flow/GVR-DERIVED-EVIDENCE-CONTRACT.md` — new.
- `docs/data-flow/GATE-2B-REFERENCE-GAP-ANALYSIS.md` — appended a Gate 3 delta
  (GitNexus re-inspection; no material architecture change; no code copied).

## Reused query/traversal components

No second graph-query subsystem or second Java resolver was created. Gate 3
consumes the public graph dict (`nodes`/`edges`) produced by Gate 2/2B and the
`extraction_diagnostic` nodes. See `GATE-3-ARCHITECTURE-INVENTORY.md` for the
full inventory and rejected alternatives (`affected.py`, networkx, closure
table, persistent index).

## Public contracts

`graphify/data_flow_query.py`:

- `DataFlowQuery(start, target=None, direction=FORWARD|BACKWARD, max_depth=4,
  max_paths=50, max_expansions=2000, allowed_relations=DEFAULT, stop_nodes=())`.
  Bounds are mandatory; no public query defaults to an unbounded walk.
- `DataFlowEvidenceRef(key, relation, source, target, source_file,
  source_location, provenance, confidence_score, argument_index,
  receiver_confidence, analysis_completeness)`.
- `DataFlowPathStep(source, target, relation, evidence)`.
- `DataFlowPath(steps, supporting_evidence, path_identity, path_exactness,
  path_receiver_confidence, path_coverage)`.
- `DataFlowTraversalResult(paths, start, target, direction, visited_count,
  expanded_count, truncated, termination_reason, query_bounds, boundary_events,
  search_coverage, complete_supported_search, start_node_found,
  target_node_found)`.
- Entry points: `run_data_flow_query(nodes, edges, query)` and
  `run_query(graph, query)`.

## Traversal relation allowlist

`FLOWS_TO`, `PASSED_AS_ARGUMENT`, `RETURNED_AS`, `READ_FROM`, `WRITTEN_TO`,
`TRANSFORMED_BY`. `CALLS`, `references`, `imports`, `contains`, `method`,
`inherits`, etc. are **not** value flow and are excluded. Rationale documented
in the inventory and enforced by `DEFAULT_DATA_FLOW_RELATIONS`.

## Forward algorithm

Iterative DFS over paths with an explicit stack. Each frame is
`(path of edges, per-path visited set, depth)`. Expansion uses a deterministic
adjacency index built once per query, sorted by
`(source, target, relation, source_location, evidence_key)`. Only allowed
relations expand. Per-path visited sets prevent revisiting a node within one
path (cycle-safe). Global `max_expansions` budget caps total edge pushes.
`max_paths` caps emitted paths. `max_depth` caps hops per path.

## Backward algorithm

Same walker over the reverse adjacency index (edges whose `target == node`),
moving toward each edge's `source`. No reverse facts are persisted; reverse
traversal is a query operation over direct edge orientation. Paths are presented
in **flow direction** (the walker reverses the edge list for BACKWARD), so a
backward result is the same flow chain a forward walk would find for the same
direct facts (mutual consistency is tested).

## Point-to-point algorithm

When `target` is set, only paths whose final node equals `target` are emitted,
and expansion stops at the target (a path is complete at the target). `start ==
target` yields a single identity path (zero steps). Zero returned paths are
distinguishable from "no path": `complete_supported_search` is False whenever
the search was truncated or hit a blocking boundary, so zero target-paths under
an incomplete search never imply whole-program impossibility.

## Evidence-key algorithm

`_evidence_key(edge)` = `df:<sha256(canonical JSON of sorted fields)>` where the
fields are: relation, canonical source node id, canonical target node id,
repo-relative `source_file`, `source_location`, `provenance`, and (when present)
`argumentIndex`. Volatile fields (absolute checkout paths, wall-clock
timestamps, in-memory object ids, arbitrary dict ordering) are excluded.

Important: the Gate 2B cross-file edge metadata carries a `callee` value that
embeds the absolute checkout-path slug (from the `cache_root` fallback) and is
therefore **not** checkout-root independent. It is intentionally excluded from
the evidence key; the callee identity is already captured canonically by the
edge's own `target` node id. This is proven by the checkout-root portability
tests (identical keys across `rootA`/`rootB`).

## Path identity / dedup semantics

`path_identity` is the ordered tuple of evidence keys (one per hop). Two paths
are distinct whenever they use different direct evidence, even if their node
sequences are identical (e.g. `A --READ_FROM--> B` vs `A --FLOWS_TO--> B`).
Emitted paths are deduplicated by `path_identity` and finally sorted
deterministically.

## Epistemic propagation

- `path_receiver_confidence` = `PROVEN` only if every step's
  `receiver_confidence == "PROVEN"`, else `MAY` (any MAY step prevents
  all-PROVEN).
- `path_coverage` = `COMPLETE_FOR_SUPPORTED_CONSTRUCT` only if every step is
  complete, else `PARTIAL`.
- `path_exactness` is always `EXACT_FOR_RETURNED_PATH` — it never implies global
  completeness.
- `search_coverage` (result-level) = `PARTIAL` if any emitted path is PARTIAL or
  a blocking boundary is surfaced.
- `complete_supported_search` (result-level) = True only when not truncated, no
  blocking boundary, and coverage complete. MAY/EXACT/`confidence_score` are
  never numerically averaged into a truth float; no confidence laundering.

## Ambiguity / unresolved / unsupported boundary semantics

Gate 2B `extraction_diagnostic` nodes are **not** flow steps. A blocking
diagnostic (`AMBIGUOUS`/`UNRESOLVED`/`UNSUPPORTED`) is surfaced as a
`boundary_event` (with `resolution`, `reason`, `callerFile`, `callerLocation`,
`receiver`, `method`, `arity`, `importContext`, `candidateCount`) when the
traversal reached a data value in the diagnostic's caller file (matched by
repo-relative suffix). No synthetic target edge is fabricated; the search simply
does not continue through the boundary. This distinguishes:

- `NO PATH` (complete supported search, no boundary)
- `PATH SEARCH STOPPED AT AMBIGUOUS BOUNDARY`
- `PATH SEARCH STOPPED AT UNSUPPORTED BOUNDARY`
- `PATH SEARCH TRUNCATED BY QUERY LIMIT`

all machine-visible via `boundary_events`, `truncated`, and
`complete_supported_search`.

## Truncation semantics

`truncated` + `termination_reason` ∈ `COMPLETE` / `MAX_DEPTH` / `MAX_PATHS` /
`MAX_EXPANSIONS`. A depth cut is flagged only when edges actually exist beyond
the bound. Results are never silently partial-as-complete.

## Cycle semantics

Per-path visited sets ensure no node is revisited within one path (no infinite
recursion, no repeated state). A cycle can be represented without endless
repetition; parallel/divergent paths sharing a node but differing before/after a
cycle boundary are preserved. Self-loop edges (a node flowing into itself) make
no progress and are skipped at the seed. Complexity: O(bounded expanded edges ×
path bookkeeping), memory O(bounded stack). Documented in the module and
covered by `test_l`/`test_adv_self_loop`/`test_adv_cycle`.

## Direct-vs-derived evidence boundary

Direct facts live in the source graph. Derived paths exist only inside a
`DataFlowTraversalResult`. No `CAN_FLOW_TO`/`REACHES`/`TRANSITIVE_FLOWS_TO`/
`DERIVED_FLOW`/`IMPACTS` edge is persisted; `test_c`/`test_s`/integration
removed-edge test assert the source graph is never mutated.

## GVR integration boundary

Gate 3 produces source-evidence (direct facts, bounded derived paths, evidence
references, deterministic path summaries, epistemic metadata, truncation and
boundary events). It produces **no** GVR `VERIFIED` verdict, Claim Ledger, global
Claim Dependency Graph persistence, risk policy, or production-action
authorization. `Derived path exists != GVR VERIFIED`; `all direct edges EXACT !=
whole-program completeness`. The future GVR layer (whose reference draft is
inspected, not merged — see `docs/gvr/GOAL-VERIFICATION-CORE.md` and
`TEXT-SEARCH-VERIFIER.md` on `feature/gvr-goal-verification-core-clean-v8`)
decides whether a path is sufficiently supported for a use.

## Tests

### Graph-level independent fixtures (`test_data_flow_traversal.py`, 44 tests)
Hand-authored graphs independent of the Java extractor, covering:

- **A** same-file linear chain (fwd/back); **B** cross-file chain with
  evidence-dependency list; **C** multi-hop across three files, no persisted
  transitive edge; **D** branching; **E** merge (backward); **F** constant-return
  negative; **G** receiver-MAY never PROVEN; **H** parse-incomplete PARTIAL;
  **I/J/K** ambiguous/unsupported boundary events; **L** cycle; **M/N/O**
  max_depth/max_paths/max_expansions truncation; **P** parallel relations distinct
  identity; **Q** file-order independence; **R** checkout-root portability;
  **S** removed direct edge; **T** no-path complete vs incomplete.

### GVR-oriented dependency tests (10)
Every step points to direct evidence; keys deterministic; no GVR verdict; removing
a supporting edge removes the path; frontier ambiguity preserved; truncation
preserved; MAY never upgraded; PARTIAL never upgraded; exact path != global
completeness; result sufficient for a future claim-dependency adapter without
reparsing.

### Java integration fixtures (`test_data_flow_traversal_java_integration.py`, 10)
Over real Gate 2B extraction output: cross-file chain to sink, backward, constant-
return negative, receiver-MAY, overload/wildcard ambiguous boundaries, file-order
independence, checkout-root portability, removed direct edge, point-to-point
truncation no-completeness-claim.

### Independent falsification
Adversarial graph-level tests: diamond DAG, repeated node via different evidence,
self-loop, zero-depth, start==target, nonexistent start/target, unknown relation
(CALLS not traversed), exact path + unrelated ambiguous boundary (not surfaced),
duplicate direct edges (dedup), huge branching + tiny max_paths, max_expansions
hit before max_depth, deterministic order under shuffled inputs, backward
consistency, removed supporting edge, checkout-root relocation. No false
positives found; documented conservative semantics are covered.

## Validation executed (exact)

```bash
.venv/bin/python -m pytest tests/test_data_flow_traversal.py -q          # 44 passed
.venv/bin/python -m pytest tests/test_data_flow_traversal_java_integration.py -q  # 10 passed
.venv/bin/python -m pytest tests/test_java_cross_file_data_flow.py \
                      tests/test_java_cross_file_gvr_boundary.py \
                      tests/test_java_cross_file_fixture_suite.py -q     # 36 passed (Gate 2B regression)
.venv/bin/python -m pytest -q                                            # full suite
.venv/bin/ruff check graphify tests
.venv/bin/pyright graphify/data_flow_query.py                             # 0 errors
git diff --check
```

### pyright (truthful baseline)
- Base SHA `c6b56a7` (approved Gate 2B), measured on a clean worktree: full
  repository `pyright` = **600 errors / 3 warnings** (pre-existing baseline;
  includes an environmental `watchdog` import error in a test).
- Final HEAD: **600 errors / 3 warnings** — identical to base, i.e. **zero new
  type errors** across the repository.
- New module `graphify/data_flow_query.py`: **0 errors, 0 warnings**.
- The global `pyright` command is not claimed clean; it is reported truthfully
  with the base/final comparison.

## Performance (before/after + stress)

Baseline: Gate 2B fixture extraction = 0.077 s, 146 nodes, 95 edges,
34 direct value-flow edges.

Traversal (runtime controlled by query bounds, not whole-project closure):

| Shape | Query | paths | visited | expanded | truncated | time |
| --- | --- | --- | --- | --- | --- | --- |
| long linear chain (100) | fwd d=100 p=1000 e=5000 | 99 | 100 | 99 | COMPLETE | ~49 ms |
| wide branching (60, fan 59) | fwd d=3 p=200 e=300 | 59 | 60 | 59 | COMPLETE | ~1.5 ms |
| cyclic (50) | fwd d=8 p=500 e=2000 | 8 | 9 | 8 | MAX_DEPTH | ~0.7 ms |
| dense small (25, ~15%) | fwd d=4 p=300 e=1500 | 219 | 25 | 219 | MAX_DEPTH | ~8.9 ms |
| fixture A graph | fwd d=6 p=200 e=2000 | 7 | 5 | 7 | COMPLETE | ~0.6 ms |

Runtime is bounded by `max_depth`/`max_paths`/`max_expansions`; no unbounded
benchmark. Peak RSS was not separated above noise on this corpus (not claimed).

## False positives discovered

None. All adversarial cases either produced correct bounded results or matched
the documented conservative semantics (no fabricated paths, no confidence
laundering, no silent truncation).

## False negatives / known limitations

- A path is only as long as the direct evidence graph allows. If the extractor
  did not emit a direct hop (e.g. a parameter consumed in an arithmetic
  expression without an emitted `FLOWS_TO`), the derived path stops there (this
  is faithful to the evidence, not an analyzer bug). See the integration note
  in the test module; case `A_same_package` is used because it emits the
  continuous chain.
- A blocking diagnostic is surfaced when traversal reaches *any* data value in
  the diagnostic's caller file (file-level association), which can over-surface
  an unrelated boundary in the same file as a coverage caveat. This is
  conservative (never a fabricated path) and documented.
- Boundary association uses a `/`-delimited repo-relative suffix match because
  the diagnostic `callerFile` is an absolute extractor path; a pathological
  same-basename path in an unrelated directory is a conservative over-surface.
- Backward paths are reported in flow direction (not reverse-walk order), which
  is the natural orientation for a forward GVR claim.
- No query cache, no historical revision storage (per task: prefer none).

## Recommendation for Gate 4

Gate 4 can decide whether persistence/revision scoping is still needed. The
query-time traversal here already satisfies "bounded, reproducible, evidence-backed,
no transitive persistence"; a future Gate 4 should only add persistence if
measurements show a clear need, and any cache identity must include enough
graph/revision identity to prevent stale derived evidence from appearing current.

## Out-of-scope confirmation

Not implemented: Gate 4 persistence redesign, persisted transitive closure,
whole-project all-pairs reachability, Process/STEP_IN_PROCESS, Leiden, full
CFG/CDG/SSA/PDG/taint engine, Micronaut/Spring HTTP boundary mapping,
Kafka/message boundary mapping, JPA/Hibernate/SQL lineage, OpenAPI/client-server
mapping, runtime trace overlay, ICE integration, Deployment Intelligence,
pod/image/Git revision narrowing, incident/time-window filtering,
`changed_on_path`, test/config enrichment, root-cause ranking, GVR Claim Ledger,
GVR verdict persistence, GVR production policy, LLM-derived flow edges, semantic
similarity as proof, a second source graph, a second Java resolver. No GVR
branch was merged or cherry-picked.


## Supervising Review Remediation (task 05-gate3-supervising-review-remediation)

Gate status: **CHANGES REQUIRED** -> **remediated**. Each issue below lists
defect, root cause, changed files, correction, regression tests, adversarial
falsification, and result. All issues resolved on the existing Gate 3 feature
branch; nothing pushed to `v8`/`main`/`master`; no Gate 4 work begun.

Final remediation HEAD (recorded at completion): `TBD_RECORD_AFTER_COMMIT`

### P0-1 — Same-file `receiverConfidence=MAY` laundered to `PROVEN`

- **Defect:** `_receiver_confidence(edge)` returned `PROVEN` for every
  non-cross-file edge, upgrading a direct same-file MAY fact to PROVEN.
- **Root cause:** receiver confidence was gated on `cross_file == true` instead
  of being derived from the evidence.
- **Changed files:** `graphify/data_flow_query.py`.
- **Correction:** an explicit `metadata.receiverConfidence` is preserved for both
  same-file and cross-file edges; when absent, receiver-oriented relations
  (`READ_FROM`/`WRITTEN_TO`/`PASSED_AS_ARGUMENT`) default to `MAY` (fail closed)
  and non-receiver relations default to `PROVEN`; any MAY step forces path MAY;
  `confidence_score` is never reinterpreted into PROVEN.
- **Regression tests:** `test_remed_same_file_receiver_may_not_laundered`,
  `test_remed_same_file_receiver_may_written_to`,
  `test_remed_same_file_explicit_proven_receiver_stays_proven`,
  `test_remed_receiver_oriented_no_metadata_defaults_may`,
  `test_remed_any_may_step_forces_path_may` (graph) and
  `test_integration_same_file_receiver_may_preserved` (Java fixture
  `O_same_file_receiver_may`).
- **Result:** PASS. Same-file MAY never becomes PROVEN; explicit PROVEN and
  cross-file MAY behavior preserved.

### P0-2 — Custom `allowed_relations` turned `CALLS` into value flow

- **Defect:** `allowed_relations` was trusted directly, so a caller could request
  `{"CALLS"}` and traverse structural relations as value flow.
- **Root cause:** no intersection with the supported value-flow vocabulary.
- **Changed files:** `graphify/data_flow_query.py`.
- **Correction:** `effective_allowed = requested & SUPPORTED_DATA_FLOW_RELATIONS`;
  rejected/unsupported requested relations are surfaced in
  `query_bounds["rejected_relations"]` and `rejected_relations`.
- **Regression tests:** `test_remed_calls_allowlist_injection_no_path`,
  `test_remed_mixed_valid_invalid_relations_only_valid_traversed`,
  `test_remed_structural_relations_never_value_flow` (imports/references/
  contains/method/inherits/uses).
- **Result:** PASS. Structural relations can never become value-flow steps.

### P0-3 — Missing start reported as a complete search

- **Defect:** a missing start could yield `start_node_found=False, paths=[],
  truncated=False, search_coverage=COMPLETE, complete_supported_search=True`.
- **Root cause:** no explicit input-resolution path in the result model.
- **Changed files:** `graphify/data_flow_query.py`.
- **Correction:** when start is missing, the traversal does not run and the
  result is `paths=()`, `complete_supported_search=False`, `search_coverage=UNKNOWN`,
  `termination_reason=START_NODE_NOT_FOUND`, `input_resolution=START_NODE_NOT_FOUND`.
  A missing target (point-to-point) with a present start is likewise treated as
  unresolved/incomplete (`TARGET_NODE_NOT_FOUND`), never as proof of no path.
- **Regression tests:** `test_remed_missing_start_not_complete`,
  `test_remed_present_start_missing_target_not_complete`,
  `test_remed_both_missing_not_complete`.
- **Result:** PASS. Missing start/target can never be a complete search.

### P0-4 — `max_depth=0` reported as a complete search

- **Defect:** the walker only entered for `max_depth > 0`, so `max_depth=0` with
  an existing eligible outgoing edge still reported `COMPLETE`.
- **Root cause:** zero-depth was treated as "no work" rather than "cut off".
- **Changed files:** `graphify/data_flow_query.py`; corrected
  `test_adv_zero_depth_query` (it previously encoded the wrong semantics).
- **Correction:** at `max_depth=0`, if an eligible edge exists in the selected
  direction, the result is truncated/incomplete with `MAX_DEPTH`; if none, a
  zero-expansion search may be complete; the start==target identity path is
  explicitly defined. Symmetric for backward.
- **Regression tests:** `test_remed_zero_depth_with_edge_max_depth`,
  `test_remed_zero_depth_no_eligible_edge_complete`,
  `test_remed_zero_depth_backward_with_edge_max_depth`,
  `test_remed_zero_depth_identity_path_defined`.
- **Result:** PASS. Zero-depth cutoff is machine-visible when eligible edges exist.

### P0-5 — Same-file parse-incomplete evidence upgraded to COMPLETE

- **Defect:** `_analysis_completeness` returned `COMPLETE` for every
  non-cross-file edge regardless of parse incompleteness.
- **Root cause:** assumed `same-file == complete`.
- **Changed files:** `graphify/data_flow_query.py`,
  `graphify/extractors/java_data_flow.py` (minimal Gate 2 enrichment).
- **Correction:** Gate 2 same-file edges now emit an explicit
  `analysisCompleteness` (parse-incomplete -> `PARTIAL`), mirroring cross-file.
  Gate 3 preserves explicit `analysisCompleteness` for both; when absent it uses a
  conservative fallback (`confidence_score < 1.0 -> PARTIAL`). Completeness is
  kept distinct from confidence.
- **Regression tests:** `test_remed_same_file_partial_not_complete`,
  `test_remed_same_file_degraded_score_no_completeness_metadata_partial`,
  `test_remed_same_file_exact_remains_complete`,
  `test_remed_cross_file_partial_stays_partial`,
  `test_remed_mixed_complete_partial_hop_stays_partial`.
- **Result:** PASS. Same-file degraded evidence is never upgraded to complete.

### P0-6 — Explored PARTIAL dead-end branches disappeared

- **Defect:** `has_partial_coverage` was computed only from returned paths, so a
  PARTIAL branch that did not reach the target vanished from search coverage.
- **Root cause:** epistemic state was tracked over emitted paths, not the
  explored region.
- **Changed files:** `graphify/data_flow_query.py`.
- **Correction:** `_record_edge_state` accumulates `encountered_partial_evidence`,
  `encountered_unknown_evidence`, `encountered_may_evidence` over every edge
  consumed/reached during traversal. An explored PARTIAL/UNKNOWN dead-end degrades
  `search_coverage`; an unrelated unreachable PARTIAL edge never does. An exact
  returned path may coexist with `search_coverage == PARTIAL`.
- **Regression tests:** `test_remed_explored_partial_dead_end_degrades_search`,
  `test_remed_unrelated_partial_component_does_not_degrade`,
  `test_remed_exact_path_plus_explored_partial_branch_coexists`,
  `test_remed_explored_partial_dead_end_backward_degrades_search`,
  `test_remed_explored_may_surfaced`.
- **Result:** PASS. Explored PARTIAL degrades search; unrelated PARTIAL does not.

### P0-7 — Boundary events not portable GVR evidence dependencies

- **Defect:** `boundary_events` emitted the absolute extractor `callerFile`
  and had no stable evidence reference to the diagnostic.
- **Root cause:** boundary events were metadata blobs, not evidence refs.
- **Changed files:** `graphify/data_flow_query.py`.
- **Correction:** each boundary event now exposes `boundary_evidence_key`
  (deterministic, checkout-root independent), `diagnostic_node_id` /
  `diagnostic_evidence_key` (stable diagnostic reference),
  `canonical_caller_file` (repo-relative node `source_file`), caller location,
  resolution, reason, receiver, receiver_fqn, receiver_confidence, method,
  arity, import_context, candidate_count. No absolute checkout root leaks into
  the public GVR-facing identity.
- **Regression tests:** `test_remed_boundary_event_has_stable_evidence_dependency`,
  `test_remed_boundary_event_checkout_root_portable` (graph),
  `test_integration_boundary_event_portability_across_roots` (Java I_overload).
- **Result:** PASS. Boundary events are portable first-class evidence refs.

### P1-1 — `MAX_PATHS` means an actual cutoff

- **Defect:** `MAX_PATHS` was set when `len(paths) == cap` even if no more
  candidate paths remained.
- **Root cause:** equality with the cap was treated as truncation.
- **Changed files:** `graphify/data_flow_query.py`.
- **Correction:** `MAX_PATHS` is reported only when the cap prevented emitting an
  additional candidate path (an unexplored frontier remained); when completion is
  deterministically known (empty frontier), `COMPLETE` is reported even at
  `len(paths) == cap`.
- **Regression tests:** `test_remed_exactly_at_max_paths_complete`,
  `test_remed_one_over_max_paths_truncated`,
  `test_remed_max_paths_deterministic_under_input_order`.
- **Result:** PASS. Cap equality with an empty frontier is COMPLETE; a real
  over-cap cutoff is MAX_PATHS.

### P1-2 — Validate `direction` and bounds at runtime

- **Defect:** `Literal` did not enforce `direction`; invalid bounds were silently
  normalized.
- **Root cause:** no runtime validation of public query inputs.
- **Changed files:** `graphify/data_flow_query.py`.
- **Correction:** `_validate_query` rejects (ValueError) invalid `direction`,
  negative `max_depth`, `max_paths < 1`, and `max_expansions < 1`.
  `allowed_relations` is sanitized per P0-2.
- **Regression tests:** `test_remed_invalid_direction_rejected`,
  `test_remed_invalid_negative_depth_rejected`,
  `test_remed_invalid_zero_max_paths_rejected`,
  `test_remed_invalid_zero_max_expansions_rejected`.
- **Result:** PASS. Deterministic, documented validation policy.

### Formal `complete_supported_search` invariant

The formal criterion (documented in `GVR-DERIVED-EVIDENCE-CONTRACT.md` and
enforced in `run_data_flow_query`) requires, simultaneously:
`input_resolution == RESOLVED`, `query_validity`, `not truncated`, no blocking
boundary in the explored region, no PARTIAL evidence, no UNKNOWN evidence, and
`search_coverage == COMPLETE_FOR_SUPPORTED_CONSTRUCT`. An exact returned path
does not imply a complete search; a complete search does not imply whole-program
completeness. These five dimensions are kept separate:
`path_exactness`, `path_epistemic_state`, `search_coverage`, `search_termination`,
`query_validity`.

### Adversarial falsification suite

The independent falsification suite in `tests/test_data_flow_traversal.py`
covers all section-12 mandatory cases: same-file MAY receiver; same-file
degraded/partial evidence; custom `CALLS` injection; mixed valid+invalid
relations; missing start; missing target; zero depth with/without outgoing edge;
explored PARTIAL dead-end; exact path + separate PARTIAL branch; unrelated
PARTIAL component not reached; boundary-event checkout-root portability; boundary
event stable evidence dependency; exactly-at-max_paths complete; one-over-max_paths
truncation; invalid direction; invalid bounds; backward equivalents; shuffled
order; parallel evidence-distinct edges.

### Validation (remediation)

```text
tests/test_data_flow_traversal.py                           80 passed
tests/test_data_flow_traversal_java_integration.py          12 passed
Gate 2/2B regression (data_flow + cross_file + gvr_boundary + fixture_suite)  68 passed
full suite                                                   4975 passed, 51 skipped, 0 failed
ruff                                                         pass
pyright graphify/data_flow_query.py                          0 errors / 0 warnings
git diff --check                                             clean
```

Full pyright baseline comparison (base `c6b56a7` vs remediation HEAD) is
reported truthfully in the final report; the existing repository baseline is
non-zero and is not claimed clean.
