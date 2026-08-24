# Structural Evidence Snapshot Contract v1

`graphify.structural_evidence.v1` is Graphify's deterministic public envelope for exporting source-derived structural evidence to downstream consumers such as the Global Verification Runtime (GVR).

## Identity and revisions

A snapshot records two independent revision axes:

- `source_revision_scope` identifies the analyzed source as `git.commit` plus its commit SHA. The local checkout path is intentionally excluded from public serialization and fingerprints.
- `analyzer_revision` identifies the Graphify analyzer release that produced the evidence. It is namespaced as `graphifyy/<revision>` and changes independently of the source revision.

The snapshot fingerprint covers both axes and all public evidence content. It therefore changes when either the source or analyzer semantics change, while remaining byte-stable across checkout roots and mapping order.

Observation-only `request_id`, `query_id`, `run_id`, `correlation_id`, and `observation_id` values are excluded from semantic query identity. They cannot alter immutable fact keys or the snapshot content fingerprint.

## Evidence model

- `facts` are direct, typed source facts. Each carries a content-addressed `df:<sha256>` key, relation, endpoints, source location, provenance, analysis completeness, receiver confidence, and optional argument index.
- `paths` are derived ordered references to direct facts. They do not create new evidence.
- `coverage` records search bounds, termination, truncation, input resolution, encountered partial or unknown constructs, and whether `MAY` evidence was encountered.
- `blockers` are first-class unresolved, ambiguous, or unsupported boundaries. They carry deterministic `bnd:<sha256>` keys and `diag:` references instead of fabricating a traversable edge.

Identical duplicate facts are collapsed deterministically. Conflicting payloads under one immutable fact, path, or blocker identity fail closed.

No snapshot contains a verification verdict. `confidence`, `exactness`, provenance, and coverage describe evidence quality only.

Downstream GVR consumers must treat absent evidence as `UNKNOWN` unless the separate bounded traversal/search contract certifies complete supported search. Silence in a structural snapshot is not authoritative `NO_PATH` evidence.

## Exact and partial evidence

Exact paths are emitted only when every supporting hop is exact and the receiver is proven. A path is partial when a supported construct is only partially analyzed, when receiver identity is `MAY`, or when an unresolved boundary prevents complete supported search. The snapshot preserves these distinctions in `path_exactness`, `receiver_confidence`, per-fact `analysis_completeness`, search coverage, and blockers.

Supported provenance values are:

- `STATIC_AST`
- `CROSS_FILE`
- `FRAMEWORK_CONTRACT`

Supported construct coverage values are `COMPLETE_FOR_SUPPORTED_CONSTRUCT`, `PARTIAL`, and `UNKNOWN`. Receiver confidence is `PROVEN` or `MAY`.

The v1 traversal-backed export publishes currently proven direct data-flow/reference facts plus persistence/framework blockers already represented by the branch. General `CALL`, general symbol `REFERENCE`, and `IMPORT` export are not promoted to exact structural relations by this contract when the current analyzer cannot prove the target identity. Synthetic architecture edges are out of scope.

## Data-flow key compatibility

`graphify.structural_evidence.df_key_from_edge` is the single implementation used by the public contract and `graphify.data_flow_query._evidence_key`. Its canonical field pairs and SHA-256 encoding remain byte-compatible with GVR's Graphify `df:` validator. Absolute paths and volatile metadata never participate.

## Serialization and validation

Use:

```python
from graphify.structural_evidence import (
    build_structural_evidence_snapshot,
    load_snapshot,
    serialize_snapshot,
    validate_snapshot,
)
```

`serialize_snapshot` emits deterministic compact JSON with sorted keys, UTF-8 content, and non-finite numbers rejected. `load_snapshot` and `validate_snapshot` verify schema, namespaces, typed fields, content-addressed keys, and the snapshot fingerprint.

The generated acceptance fixture is `tests/fixtures/structural_evidence/structural_evidence_snapshot.json`. `test_committed_fixture_is_current` regenerates it from a real deterministic git repository, Java extraction, and bounded data-flow traversal, then requires exact structural equality.
