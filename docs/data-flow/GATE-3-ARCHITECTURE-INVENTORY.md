# Gate 3 — Architecture Inventory

**Branch:** `feature/java-bounded-data-flow-traversal-v8`
**Gate:** 3 (bounded interprocedural data-flow traversal + GVR-consumable derived evidence)
**Inspected:** at implementation time on the build machine.

This document records what traversal/query machinery already existed in
Graphify, what Gate 3 reuses, what is missing, what must not be duplicated, the
selected insertion point, and rejected alternatives.

## 1. Existing reusable traversal/query components

### `graphify/affected.py`
A **blast-radius** query tool: from a seed node it walks the **reverse**
structural graph (`calls`, `references`, `imports`, `inherits`, `extends`,
`implements`, `uses`, …) up to a fixed `depth` and returns the set of affected
nodes (`AffectedHit`). It:
- builds no adjacency index (walks `graph.in_edges` directly);
- reports **affected nodes**, not value-flow paths;
- has no per-query `max_paths` / `max_expansions` budget, no evidence keys, no
  epistemic propagation, no boundary events;
- uses networkx `nx.Graph`.

### `graphify/global_graph.py`
Loads/saves a merged `~/.graphify/global-graph.json` (networkx node-link) and
merges per-repo graphs with repo-tag prefixed ids. It is a **storage/merge**
utility, not a traversal engine.

### `graphify/extractors/java_data_flow.py` + `graphify/extractors/resolution.py`
The Gate 2 / 2B **evidence producers**. They emit `data_value` nodes and
direct relations (`FLOWS_TO`, `PASSED_AS_ARGUMENT`, `RETURNED_AS`, `READ_FROM`,
`WRITTEN_TO`, `TRANSFORMED_BY`) plus `extraction_diagnostic` nodes. Gate 3
**consumes** their output; it does not parse Java and does not re-resolve.

### `graphify/extract.py`
Runs extractors and the repository resolution pass, then id-remaps to canonical
repo-relative ids. It produces the public graph dict (`nodes` / `edges`) that
Gate 3 traverses.

## 2. What Gate 3 can reuse

- The **public graph shape** (`nodes`/`edges` dicts) and its canonical,
  checkout-root-independent node ids.
- The **relation vocabulary** already emitted (the value-flow relations below).
- The **`extraction_diagnostic`** nodes already emitted by Gate 2B for
  ambiguous/unresolved/unsupported boundaries.
- The **conservative epistemic conventions** already used by Gate 2/2B
  (`receiverConfidence` PROVEN/MAY, `analysisCompleteness`, `resolution`).
- `affected.py`'s deterministic ordering and repo-relative path conventions as a
  style reference (not its algorithm).

## 3. What is missing (why a new module is justified)

- A **bounded, query-time data-flow path** API with `max_depth`, `max_paths`,
  `max_expansions` (no public API may default to an unbounded whole-graph walk).
- **Path identity** that distinguishes parallel/evidence-distinct paths, not just
  node sequences.
- **Deterministic direct-evidence keys** (checkout-root independent) so a later
  GVR adapter can reference the exact direct edges backing a derived path.
- **Epistemic propagation** (MAY never → PROVEN; PARTIAL never → complete;
  truncation and boundary events machine-visible).
- **Forward, backward, and point-to-point** traversal over the value-flow
  allowlist.
- **Boundary-event surfacing** for ambiguous/unresolved/unsupported boundaries.

`affected.py` is a blast-radius *reachability* tool over structural relations;
it is not a value-flow path engine and cannot be extended into one without
changing its contract (it would violate "do not traverse every relation" and
would not produce evidence-backed paths). This is a focused data-flow contract,
not a second generic graph-query subsystem.

## 4. What must not be duplicated

- No new adjacency/graph database or index layer (build once per query from the
  edges list).
- No second Java resolver (Gate 3 never parses source).
- No second graph storage representation (traverse the public graph dict).
- No persistence of transitive facts (query-time only).

## 5. Selected implementation insertion point

`graphify/data_flow_query.py` — a top-level, language-agnostic module beside
`affected.py`, operating over the public graph dict. Public contracts:
`DataFlowQuery`, `DataFlowPath`, `DataFlowPathStep`, `DataFlowEvidenceRef`,
`DataFlowTraversalResult`, entry points `run_data_flow_query(nodes, edges,
query)` and `run_query(graph, query)`.

## 6. Traversal relation allowlist

Only value-flow relations are traversed (Gate 2/2B relations):

`FLOWS_TO`, `PASSED_AS_ARGUMENT`, `RETURNED_AS`, `READ_FROM`, `WRITTEN_TO`,
`TRANSFORMED_BY`.

`CALLS` is **not** value flow and is excluded. `references`, `imports`,
`contains`, `method`, `inherits`, `uses`, `calls`, `implements` etc. connect
nodes but do not move a value and are **not** traversed. Rationale: a derived
path step must always be backed by direct value-flow evidence; traversing
structural relations would fabricate value-flow semantics.

## 7. Rejected alternatives and why

| Alternative | Rejected because |
| --- | --- |
| Reuse/extend `affected.py` for Gate 3 | It is a structural blast-radius reachability tool with a different contract (affected-node set, no per-query path budgets, no evidence keys, no epistemic state, reverse-walk only over structural relations). Changing it would break its contract and would not yield evidence-backed value-flow paths. |
| Build on networkx directly for path enumeration | Gate 3 already consumes the public graph dict; importing networkx for the walk adds a dependency and re-introduces order-sensitivity the deterministic edge-sort already handles. `affected.py` already uses networkx; the data-flow walker does not need it. |
| Persist a transitive closure table / precompute reachability | Explicitly prohibited (query-time only; no persisted `CAN_FLOW_TO`). |
| Store adjacency index persistently | One-time per-query index build is O(edges) and cheap; persistence is premature and prohibited by the task. |
| Reuse the global graph merge layer | Global graph is storage/merge, not a query engine; out of scope. |
