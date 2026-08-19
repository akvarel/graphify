# GVR Source-Evidence Contract

This is an integration note describing what the OSS source-derived evidence
layer (Graphify-derived) emits and what the future Global Verification Runtime
(GVR) must decide. It is **not** a new global schema implementation, and it is
**not** an implementation of the GVR Claim Ledger.

## 1. The architectural boundary

```
SOURCE CODE
  ↓
OSS Source Evidence Layer (Graphify-derived)
  - AST
  - symbol resolution
  - value positions
  - static data-flow facts
  - cross-file static evidence
  ↓ facts + evidence
GVR — Global Verification Runtime
  - claims, evidence, verifiers, invalidation, verdicts
  ↓ verified state
BugZero private
```

The source layer produces **source-derived facts and evidence**. It does **not**
issue GVR verification verdicts. `confidence` ≠ `verification`.

## 2. What the source layer emits

Per-file and cross-file Java data-flow facts:

| Fact | Relations | Example |
| --- | --- | --- |
| Value positions | PARAMETER, LOCAL, FIELD, RETURN_VALUE nodes | `price` (PARAMETER of `PricingService.calculate(double)`) |
| Local flow | FLOWS_TO | `base -> finalPrice` |
| Argument passing | PASSED_AS_ARGUMENT | `b -> price` (argument index 0) |
| Return | RETURNED_AS | `converted -> return` |
| Read / write | READ_FROM, WRITTEN_TO | field reads/writes |
| Transformation | TRANSFORMED_BY | only when the callee's own bounded facts prove param→return |
| **Cross-file (Gate 2B)** | PASSED_AS_ARGUMENT, TRANSFORMED_BY, FLOWS_TO with `provenance = "CROSS_FILE"` | caller arg → callee param; callee return → caller receiving value |

## 3. Evidence that accompanies each fact

A cross-file edge (Gate 2B) carries, via `metadata`:

- `provenance` — `STATIC_AST` (same-file) or `CROSS_FILE`.
- `receiver` — the resolved receiver FQN (e.g. `acme.PricingService`).
- `receiverConfidence` — `PROVEN` (static/`this`/constructor) or `MAY`
  (instance). **Preserved across linkage; never upgraded.**
- `callee` / `calleeSymbol` — exact target node id + symbol
  (e.g. `PricingService.calculate(double)`).
- `argumentIndex` — positional argument index (cross-file parameter mapping).
- `analysisCompleteness` — `COMPLETE_FOR_SUPPORTED_CONSTRUCT` or `PARTIAL`
  (parse-incomplete target).
- `source_file` / `source_location` — caller AST location.
- `cross_file` — boolean marker.

Top-level edge fields: `source`, `target`, `relation`, `confidence`
(`EXTRACTED`/`INFERRED`), `confidence_score` (1.0 PROVEN, 0.5 MAY, capped at
0.8 for PARTIAL), `source_file`, `source_location`, `weight`.

## 4. Epistemic status

The resolution of a cross-file identity is expressed as one of:

- `EXACT` — deterministic identity (explicit import / same-package /
  fully-qualified / static receiver).
- `AMBIGUOUS` — fail closed, no edge (e.g. wildcard import with multiple
  candidates, same-arity overload).
- `UNRESOLVED` — no defensible target (e.g. default package, unknown type).
- `UNSUPPORTED` — construct not handled in this gate (e.g. interface dispatch).

Coverage:

- `COMPLETE_FOR_SUPPORTED_CONSTRUCT` — the specific supported construct was
  fully analyzed.
- `PARTIAL` — the target extraction was parse-incomplete; the fact is degraded
  and machine-visible as such.
- `UNKNOWN` — coverage not determined.

Valid and important state: **relationship existence = EXACT, analysis
coverage = PARTIAL.** An exact local fact must not be read as a claim of
whole-program completeness.

### 4.1 Attempted boundaries are machine-visible evidence (remediation P0-2)

An AMBIGUOUS / UNRESOLVED / UNSUPPORTED resolution is **not** modeled as
"no flow exists": the source layer emits one bounded
`extraction_diagnostic` node (`metadata.kind == "cross_file_resolution"`) per
attempted cross-file boundary. GVR must never have to infer uncertainty from
the absence of an edge. Each diagnostic is a **node, never a positive
relation** (no `FLOWS_TO`/`CALLS`), and creates no transitive graph fact. It
carries:

- `resolution` — `EXACT` / `AMBIGUOUS` / `UNRESOLVED` / `UNSUPPORTED`.
- `coverage` — `COMPLETE_FOR_SUPPORTED_CONSTRUCT` / `PARTIAL` / `UNKNOWN`.
- `callerFile`, `callerLocation`, `receiver` (sanitized type text),
  `receiverFqn` (when resolved), `receiverConfidence` (`PROVEN`/`MAY`).
- `importContext` — `same_package` / `explicit_import` / `qualified` /
  `wildcard` / `default_package` / `unknown`.
- `method`, `constructor`, `arity`.
- `reason` — machine-readable code (e.g. `wildcard_import_ambiguous`,
  `overload_ambiguity`, `receiver_unresolved`, `receiver_unsupported_type`).
- `candidateCount`, and `candidates` (identities) only when deterministically
  known and safe — never raw source values or secrets.
- `extractor` — `graphify`.

Exact boundaries emit `EXACT` diagnostics **and** the positive edges
(PASSED_AS_ARGUMENT / TRANSFORMED_BY / FLOWS_TO). Absence of a diagnostic
means the analyzer never attempted the boundary (e.g. no call on a class
receiver), which is distinguishable from an attempted-but-unresolved boundary.
These diagnostics are the machine-visible source from which the resolution
statistics (`cross_file_resolution_stats`) are derived.

## 5. What `confidence` means

`confidence_score` describes the strength of the **static evidence**, not a
verification verdict:

- `1.0` — deterministic static evidence, PROVEN receiver, complete construct.
- `0.5` — evidence exists but the receiver is `MAY` (same-instance not proven).
- `≤ 0.8` — evidence exists but the target was parse-incomplete.

> `STATIC_AST + EXACT + confidence_score = 1.0` is **not** synonymous with
> `GVR VERIFIED`.

## 6. What the source layer explicitly does NOT decide

- It does **not** issue GVR verdicts (`VERIFIED`, `GVR_VERIFIED`,
  `RUNTIME_VERIFIED`, `DEPLOYMENT_VERIFIED`, `ROOT_CAUSE_VERIFIED`).
- It does **not** compute transitive closure: source fact `A→B` and `B→C` does
  **not** cause the extractor to persist `A CAN_FLOW_TO C`.
- It does **not** use semantic similarity as proof of a static relation.
- It does **not** claim global completeness from an exact local fact.

## 7. What GVR must decide later

Given a source fact and its evidence, GVR decides whether and how that fact may
become verified state — e.g. whether a source relation survives invalidation,
whether a `MAY` receiver can ever be upgraded by runtime evidence, and how
ambiguity/incompleteness is represented in claims.

## 8. Candidate mapping (not implemented)

| Source fact | Possible future GVR claim |
| --- | --- |
| `A PASSED_AS_ARGUMENT B.parameter[0]` | `A CAN_FLOW_TO B` |
| `B RETURNED_AS B.return` | `B CAN_FLOW_TO caller value` |
| `A TRANSFORMED_BY B` | `A contributes-to B` (only with bounded param→return proof) |

These are documented candidate mappings; they are **not** implemented and do not
persist derived transitive facts.

## 9. Source-layer states that are explicitly forbidden

`VERIFIED`, `GVR_VERIFIED`, `RUNTIME_VERIFIED`, `DEPLOYMENT_VERIFIED`,
`ROOT_CAUSE_VERIFIED`. These belong to GVR only.
