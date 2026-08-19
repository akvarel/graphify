# Gate 2 Implementation Report: Java Local Data Flow

## Decision

Gate 2 implements the first conservative static data-flow slice in Graphify. Java is the first language because BugZero's Avion-scale validation target is Java/Micronaut and Gate 0 identified it as the highest-value product path.

This gate adds extraction only. It does not add traversal, framework-specific endpoint or protocol modeling, runtime overlays, deployment correlation, persistence changes, or cross-service joins.

## Frozen v1 representation

All complete facts are source-derived and use `metadata.provenance = STATIC_AST`, `confidence = EXTRACTED`, and `confidence_score = 1.0`. If tree-sitter reports parse recovery, Java data-flow extraction is machine-marked incomplete and recovered data-flow facts are downgraded instead of being presented as fully complete certainty.

### Value nodes

Graphify emits `type = data_value` nodes for:

| `metadata.kind` | Meaning |
|---|---|
| `PARAMETER` | Declared Java method or constructor parameter |
| `RETURN_VALUE` | Synthetic value representing a declared non-void method return |
| `LOCAL` | Declared local variable or directly constructed local object |
| `FIELD` | Declared field |

Each value records its portable Java owner symbol, Java name, source location, and declared Java type when available. Local, parameter, field, and synthetic object value IDs include deterministic source-span discriminators so same-named declarations and same-line object creations do not collapse. File identity uses a portable file stem and disambiguates same-named sibling Java files without using the checkout root.

### Static edges

| Relation | Direction and meaning |
|---|---|
| `FLOWS_TO` | Source value initializes or is assigned to a non-field target value |
| `PASSED_AS_ARGUMENT` | Argument value is passed to the matching parameter of an exactly resolved direct call or constructor; `argumentIndex` records its zero-based position |
| `RETURNED_AS` | Source value is returned as the current method's return value |
| `READ_FROM` | A field value is read into a local, return, or resolved call parameter |
| `WRITTEN_TO` | Source value is assigned to or initializes a field |
| `TRANSFORMED_BY` | A caller argument supports the return of an exactly resolved non-void method only when bounded intra-method evidence proves the corresponding callee parameter contributes to that return |

Every emitted edge has existing source and target nodes. Runtime or deployment evidence cannot create these edges. Consumers that need distinct data-flow relations between the same endpoints must call `build_from_json(..., directed=True, multigraph=True)`; the legacy Graph/DiGraph modes intentionally collapse parallel edges.

## Supported Java facts

- parameter, local, field, and non-void return identities
- local initialization and assignment
- field reads and writes, including field declaration initializers through `WRITTEN_TO`
- return statements
- direct same-file method calls with a resolved receiver and exact arity
- unqualified calls, `this` calls, same-file static class receivers, and receivers whose declared local, parameter, or field type resolves to a same-file class
- direct same-file constructor argument mapping
- declaration-order independence for methods and fields
- conservative overload handling by class, name, and exact arity
- exact callee identity in argument and transformation metadata via portable method signatures such as `Flow.convert(int)`
- machine-visible Java data-flow status via extraction diagnostic nodes

## Deliberate omissions

Graphify emits no data-flow fact when resolution would require guessing. Gate 2 deliberately omits:

- reflection and dynamic invocation
- ambiguous overloads with the same arity
- imported or other cross-file method and constructor targets
- inheritance and `super` dispatch
- framework route, HTTP, database, Kafka, or serialization semantics
- alias, heap, collection-element, exception, lambda-capture, or symbolic-expression analysis
- runtime, deployment, co-occurrence, or LLM-inferred flow
- local or cross-service path traversal

These omissions are incomplete coverage, not negative proof that no flow exists.

## Supervising Review Remediation

| Issue | Defect and root cause | Changed files | Correction | Adversarial test | Result |
|---|---|---|---|---|---|
| False `TRANSFORMED_BY` | Every argument to an exactly resolved non-void call was summarized as contributing to the return. | `graphify/extractors/java_data_flow.py`, `tests/test_java_data_flow.py` | Added bounded callee parameter-to-return dependency tracking from emitted local facts and fail-closed summary emission. | Constant return, one-of-two-parameters, and via-local tests in `test_java_transformations_require_proven_return_dependency`. | PASS |
| Collapsed transformation identity | Metadata used class and method name only, collapsing overloads. | same | `calleeSymbol` and `transformationSymbol` now use exact portable signatures. | Existing overload tests and transformation metadata assertions. | PASS |
| Value identity collisions | Local IDs used file/name/owner without source declaration position. Synthetic objects used line only. | same | Added deterministic source-span discriminators for locals, parameters, fields, and object creations. | Same-name local and same-line `new Foo()` tests. | PASS |
| Same filename collisions | Java value IDs used only `path.stem`. | same | Added portable sibling-aware file disambiguation for same-named Java files. | `a/Flow.java` and `b/Flow.java` public extraction test. | PASS |
| Lexical scope leak | A method-wide local map let expired locals remain visible after block scope. | same | Replaced flat local lookup with scoped stack handling for Java blocks. | Field/local shadowing expiry test. | PASS |
| Silent failure and parse recovery | Broad import/parse failures returned unmarked results, and recovered parses were fully confident. | same | Added Java data-flow status and extraction diagnostic nodes; parse recovery sets incomplete status and downgraded confidence. | Forced failure seam and malformed Java parse test. | PASS |
| Field initializer relation | Field declaration initializers used `FLOWS_TO`. | same | Field initializers now use `WRITTEN_TO`. | `test_java_field_initializer_uses_written_to`. | PASS |
| Parallel relation preservation | Distinct Java flow relations can share endpoints. | `tests/test_java_data_flow.py` | Retained opt-in `build_from_json(..., directed=True, multigraph=True)` behavior. | Existing `READ_FROM`/`PASSED_AS_ARGUMENT` and `READ_FROM`/`RETURNED_AS` multigraph tests. | PASS |

## Supervising Review Remediation — Round 2

Supervising review of the round-1 report and the actual branch/code found three
remaining correctness/integration gaps that this round closes (P0-1..P0-3), plus
evidence and v8-integration work (P0-4, P0-5).

| Issue | Defect and root cause | Changed files | Correction | Adversarial test | Result |
|---|---|---|---|---|---|
| P0-1 Receiver / instance ambiguity on FIELD flow | Declaration-level FIELD nodes mapped `this.value`, `other.value`, and other instances of the same declared field to the SAME FIELD value node while writes also targeted that node, producing a dangerous `input -> WRITTEN_TO Flow.value -> READ_FROM foreign` shape where the write is `this.value` and the read is `other.value`. | `graphify/extractors/java_data_flow.py`, `tests/test_java_data_flow.py` | Field `READ_FROM`/`WRITTEN_TO` edges now carry deterministic receiver/access-site identity plus `receiverConfidence`. `this`, unqualified, and deterministic `this.<chain>` receivers are `PROVEN` (score 1.0); named receivers of a declared class type are explicit `MAY` alias flow (score 0.5). No alias equivalence is invented; unprovable receivers stay `MAY` or fail closed. | `this.field` vs `other.field`, two locals of the same class, nested receiver chains (`this.box.value` vs `box.value`). | PASS |
| P0-2 General same-filename identity, not sibling-only | Value IDs used a sibling-aware `path.parent.parent.glob(...)` heuristic and otherwise `path.stem`, so same-named files at non-sibling depths collided. | same | Replaced the sibling heuristic with Graphify's canonical `_file_stem` full-path stem; `extract()`'s id-remap relativizes it to the scan root, so IDs are distinct per repo-relative file and portable across checkout roots at any depth. Edges are rewired by the same remap. | Deep non-sibling paths (`service-a/.../Flow.java`, `service-b/.../Flow.java`, `src/.../Flow.java`). | PASS |
| P0-3 Qualified owner identity for nested classes | `classes` was keyed by simple class name, so `A.Helper` and `B.Helper` collapsed and `Helper.f(int)` could fabricate cross-owner flow. | same | Classes are keyed by deterministic qualified owner path (`A.Helper`, `B.Helper`); method, parameter, field, return and synthetic value identities inherit the qualified owner. A simple name is honoured only when unique; ambiguous simple names fail closed. | Two enclosing classes each containing `Helper` with the same method names/arity. | PASS |
| P0-4 Correct ahead/behind evidence | Round-1 Drive report stated `18 ahead / 59 behind`; supervising review showed the direction was swapped. | report | Re-run with correct semantics: commits only in `v8` = behind, commits only in the feature branch = ahead. | n/a (evidence) | recorded below |
| P0-5 Integrate current `v8` | Branch was still behind current `v8`. | report | Fetched and merged current `upstream/v8` into `feature/java-local-data-flow-v8`, resolved conflicts, and pushed the updated branch. | n/a (integration) | done |

### Gate 3 semantics for receiver/instance flow (P0-1 contract)

Downstream traversal MUST distinguish proven same-receiver field flow from
MAY/unknown alias flow:

- A `READ_FROM`/`WRITTEN_TO` edge whose `metadata.receiverConfidence == "PROVEN"`
  (receiver `this`, unqualified, or a deterministic `this.<chain>`) may be treated
  as definite same-instance field flow.
- An edge whose `metadata.receiverConfidence == "MAY"` was produced through a named
  receiver of a declared class type (or an unknown receiver) and must NOT be
  presented as proven same-receiver flow. `metadata.receiver` carries the
  deterministic access-site identity (e.g. `Flow@other`, `Box@param`) so
  write→read correlation is possible without fabricating instance equivalence.
- These MAY edges carry `confidence_score = 0.5`; PROVEN field edges carry 1.0.

## Data Flow Completeness / Failure Semantics

Java data-flow extraction is now externally visible as one of:

- `succeeded`: parser and augmentation completed without tree-sitter recovery;
- `unsupported`: Java parser dependency is unavailable;
- `incomplete`: tree-sitter parsed with recovery/errors, so emitted facts are conservative and not fully confident;
- `failed`: augmentation raised before extraction could complete, with a sanitized exception class name only.

The machine-readable status is emitted on an `extraction_diagnostic` node with `metadata.capability = data_flow` and `metadata.language = java`. This avoids confusing "no Java flow exists" with "Java flow extraction failed or was incomplete" while preserving structural extraction for the file.

## Cross-file Java Data Flow Readiness

Existing Graphify Java structural extraction already has import and type-reference resolution entry points in `graphify.extractors.resolution`, including `_resolve_cross_file_java_imports` and `_resolve_java_type_references`. These can identify declarations and type references across files at the structural graph layer.

For future cross-file Java value flow, the reusable facts are:

- exact same-file method signatures already produced by Gate 2 for owner and callee identity;
- existing Java import/type resolution edges for class identity across files;
- structural method declaration nodes and parameter value nodes that can connect caller argument positions to target parameter positions once an exact cross-file callee is known.

Still missing for safe cross-file flow:

- a deterministic bridge from resolved cross-file method declarations to the Gate 2 value-node owner signatures;
- overload resolution across files by exact receiver type and parameter types, not name-only matching;
- handling for inheritance, interfaces, generics erasure, and ambiguous imports that must fail closed.

No cross-file value flow, name-only callee matching, Gate 2B, or Gate 3 traversal was implemented in this remediation.

## Final Validation

Status: **GREEN** at `3951406daf8cf9bf0c19cdbf4baedfbf03918553`
(`feature/java-local-data-flow-v8`, round 2 complete).

- `tests/test_java_data_flow.py`: **32 passed** (round-1 set + 5 new round-2
  adversarial tests; 2 portability tests updated to pass `root`).
- Full suite: **4847 passed, 51 skipped** — green (includes Java, build,
  and type-resolution suites).
- `ruff`, `pyright`, and `git diff --check`: clean.
- Cross-file and code-flow (`build_from_json`) behavior unchanged; the only
  regression gate touched is the Java data-flow extractor and its tests.

### v8 integration and ahead/behind (P0-4, P0-5)

- Feature branch: `feature/java-local-data-flow-v8` @ `3951406`.
- Current `fork/v8` ref: `5131384`; current `upstream/v8` ref (integrated):
  `b14b52e` (v0.9.47).
- merge-base(`HEAD`, `fork/v8`): `4fca621`.
- Correct semantics, commits only in `fork/v8` = behind, commits only in the
  feature branch = ahead (`git rev-list --count --left-right fork/v8...HEAD`):
  - ahead (branch-only commits): **87**
  - behind (v8-only commits): **18**
- The round-1 Drive report's `18 ahead / 59 behind` was direction-swapped; the
  branch is in fact **ahead** of `fork/v8` (the ahead count grew from 59 to 87
  after integrating current `upstream/v8` and adding round-2 work). The updated
  branch has been pushed to `origin/feature/java-local-data-flow-v8`.

### Exact steps to reproduce

```bash
cd /sharedssd/git/graphify
git fetch fork upstream origin
git switch feature/java-local-data-flow-v8
git merge upstream/v8                      # integrate current v8
pytest -q tests/test_java_data_flow.py     # 32 passed
pytest -q                                  # full suite: 4847 passed, 51 skipped
ruff check graphify tests
pyright
git push origin feature/java-local-data-flow-v8
```

No Gate 3 work was attempted in this round; receiver/instance semantics are
documented above as the contract for the next gate.

Known limitations: Gate 2 remains same-file local/basic interprocedural extraction only. Cross-file Java value flow, traversal APIs, framework/runtime/deployment modeling, and Gate 2B/3 behavior were intentionally not implemented.

## Known baseline limitation

Basic interprocedural extraction is currently bounded to exactly resolved declarations within one Java source file. Cross-file symbol resolution remains a later extraction extension and must not be approximated by name-only matching.

# STOP FOR REVIEW

Gate 3 traversal and framework implementation remain blocked until Gate 2 receives explicit approval.
