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

Branch: `feature/java-local-data-flow-v8`

Final delivery SHA: supplied externally after commit creation. This report does not embed a self-referential final commit hash.

Remote comparison evidence before remediation:

- `upstream/v8`: `4fca621532a23f84f69c31e397b75f8105cb5390`
- Full-suite baseline evidence before these fixes: 4568 passed, 47 skipped.

Post-7cbbdf7 remediation added regression coverage for:

- assignment kill/order so stale local initializer dependencies do not create false `TRANSFORMED_BY` edges;
- for-loop lexical scope so initializer locals expire after the loop and do not shadow fields;
- exact same-file callee identity for overloads, with portable signature metadata for different arities and fail-closed same-arity ambiguity;
- deferred field initializer processing so forward field references emit `WRITTEN_TO` after all fields are indexed;
- documented unsupported status for unavailable `tree_sitter_java` rather than reporting a failed extractor.

Validation commands executed in the project virtual environment after remediation:

| Check | Result |
|---|---|
| `.venv/bin/python -m pytest tests/test_java_data_flow.py -q` | PASS, 25 passed, 1 warning |
| `.venv/bin/python -m pytest tests/test_java_data_flow.py tests/test_java_type_resolution.py tests/test_java_member_calls.py tests/test_observability_anchors_java.py tests/test_build.py -q` | PASS, 165 passed, 1 warning |
| `.venv/bin/python -m ruff check graphify/extractors/java_data_flow.py tests/test_java_data_flow.py` | PASS |
| `.venv/bin/pyright graphify/extractors/java_data_flow.py` | PASS, 0 errors |
| `.venv/bin/graphify update .` | PASS; graph regenerated with existing optional SQL/DM parser warnings and a known fixture syntax warning |
| `git diff --check` | PASS |

Do not treat the pre-remediation full-suite baseline as evidence for these fixes.

Known limitations: Gate 2 remains same-file local/basic interprocedural extraction only. Cross-file Java value flow, traversal APIs, framework/runtime/deployment modeling, and Gate 2B/3 behavior were intentionally not implemented.

## Known baseline limitation

Basic interprocedural extraction is currently bounded to exactly resolved declarations within one Java source file. Cross-file symbol resolution remains a later extraction extension and must not be approximated by name-only matching.

# STOP FOR REVIEW

Gate 3 traversal and framework implementation remain blocked until Gate 2 receives explicit approval.
