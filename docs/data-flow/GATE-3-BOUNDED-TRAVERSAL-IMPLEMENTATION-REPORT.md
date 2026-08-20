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
- Final SHA: `[FINAL_SHA]` (set at commit time)
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
