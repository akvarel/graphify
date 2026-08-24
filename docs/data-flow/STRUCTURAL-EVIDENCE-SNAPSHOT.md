# Structural Evidence Snapshot Contract v2

`graphify.structural_evidence.v2` is Graphify's deterministic public envelope for exporting source-derived structural evidence to downstream consumers such as the Global Verification Runtime (GVR).

Version 2 is an intentional, non-backward-compatible schema revision. Version 1 scoped evidence to a Git revision but did not prove that the traversal result came from that revision. A v1 document is rejected rather than silently reinterpreted as bound evidence.

## Authoritative binding chain

The only authoritative construction path is:

```text
derive_git_source_authority(clean repository)
-> build_bound_structural_index(authority, source paths)
-> run_bound_data_flow_query(index, bounded query)
-> build_structural_evidence_snapshot(bound analysis)
```

The public types are:

- `GitSourceAuthority`: captured clean `git.commit` authority.
- `BoundStructuralIndex`: exact extracted nodes and edges plus a deterministic, checkout-root-independent `index_fingerprint`.
- `BoundStructuralAnalysis`: traversal result plus its source authority, index fingerprint, traversal fingerprint, and combined binding fingerprint.
- `StructuralEvidenceSnapshot`: trusted serialized v2 evidence envelope carrying `analysis_binding`.

`build_structural_evidence_snapshot()` rejects an unbound `DataFlowTraversalResult`, even when an independently valid `GitSourceAuthority` is supplied. `source_authority=` and `source_revision=` are confirmation-only inputs for a bound analysis. They cannot relabel it.

Low-level `extract()` and `run_data_flow_query()` remain diagnostic/general-purpose APIs. Their outputs are not authoritative GVR structural evidence and cannot directly produce a trusted snapshot. Unit tests that isolate snapshot normalization use an explicitly private test-only constructor path. Production and fixture generation use only the public bound pipeline.

## Existing Graphify identity and the chosen design

Graphify had no repository revision identity propagated through extraction or traversal. `semantic_search.graph_fingerprint()` fingerprints only semantically indexed node text and omits traversal-relevant edges, so it is insufficient as structural authority.

The v2 index fingerprint therefore covers the exact extracted node and edge state consumed by traversal. Canonicalization removes the checkout-root locator and root-derived ID prefix while retaining semantic extracted content. The traversal fingerprint covers the exact normalized `DataFlowTraversalResult`. The combined binding fingerprint covers:

- serialized source revision scope and its fingerprint;
- exact extracted/indexed state fingerprint;
- exact traversal result fingerprint.

Absolute checkout paths, cache locations, timestamps, request IDs, run IDs, and correlation IDs do not participate in public semantic identity.

## TOCTOU semantics

`build_bound_structural_index()` validates the captured authority immediately before extraction and again immediately after extraction.

- Dirty or untracked source fails closed.
- A checkout or commit movement after authority capture but before or during extraction fails closed and requires a new authority.
- After a bound index and bound analysis are produced, later checkout movement does not mutate their captured identity.
- Mutation of the bound index before traversal or the traversal result before snapshot construction is detected by fingerprint recomputation.

The extraction cache should be outside the source repository or ignored by Git. Otherwise its writes correctly make the authoritative worktree dirty and fail closed.

## Identity and revisions

A snapshot records independent source and analyzer axes:

- `source_revision_scope` identifies the analyzed source as `git.commit` plus its commit SHA. The local checkout path is omitted.
- `analysis_binding` proves which extracted/indexed graph and traversal were sealed to that source scope.
- `analyzer_revision` identifies the Graphify analyzer release independently of the source revision.

The snapshot fingerprint covers these axes and all public evidence content. The same commit and extracted state at different checkout roots yields the same identity. A source revision, index state, traversal result, or analyzer revision change advances identity.

Observation-only `request_id`, `query_id`, `run_id`, `correlation_id`, and `observation_id` values are excluded from semantic query identity.

## Evidence and integrity model

- `facts` are direct typed source facts with content-addressed `df:<sha256>` keys.
- `paths` are ordered references to direct facts and do not invent evidence.
- `coverage` records bounds, termination, truncation, input resolution, and epistemic limitations.
- `blockers` represent unresolved, ambiguous, or unsupported boundaries with `bnd:<sha256>` and `diag:` keys.

Identical duplicates collapse deterministically. Conflicting payloads under one immutable fact, path, or blocker identity fail closed. Every path identity must exactly equal its ordered supporting evidence keys, every referenced `df:` key must exist, and every fact-to-path reference must resolve.

No snapshot contains a verification verdict. Absence remains `UNKNOWN` unless the separate bounded-search coverage contract proves complete supported search.

## Compatibility

The `df:<sha256>` and `bnd:<sha256>` canonicalization remains unchanged and GVR-compatible. The schema/format and outer snapshot fingerprint namespace advanced to v2 because `analysis_binding` is mandatory trusted state.

`load_snapshot()` and `validate_snapshot()` verify schema, namespaces, typed fields, content-addressed keys, cross-references, the internal binding fingerprint, and the outer snapshot fingerprint. Recomputing only the outer fingerprint after tampering with binding components does not make a document valid.

## Usage

```python
from pathlib import Path

from graphify.data_flow_query import DataFlowQuery
from graphify.structural_evidence import (
    build_bound_structural_index,
    build_structural_evidence_snapshot,
    derive_git_source_authority,
    run_bound_data_flow_query,
    serialize_snapshot,
)

repo = Path("/path/to/clean/repository")
authority = derive_git_source_authority(repo)
index = build_bound_structural_index(
    authority,
    sorted(repo.rglob("*.java")),
    cache_root=repo.parent / ".graphify-authoritative-cache",
)
analysis = run_bound_data_flow_query(index, DataFlowQuery(start="node-id", max_depth=6))
snapshot = build_structural_evidence_snapshot(analysis)
payload = serialize_snapshot(snapshot)
```

The committed acceptance fixture is `tests/fixtures/structural_evidence/structural_evidence_snapshot.json`. Its generator creates a deterministic Git repository, derives authority, extracts through `build_bound_structural_index()`, traverses through `run_bound_data_flow_query()`, and exports through the bound snapshot builder.
