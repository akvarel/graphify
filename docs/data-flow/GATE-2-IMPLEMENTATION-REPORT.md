# Gate 2 Implementation Report: Java Local Data Flow

## Decision

Gate 2 implements the first conservative static data-flow slice in Graphify. Java is the first language because BugZero's Avion-scale validation target is Java/Micronaut and Gate 0 identified it as the highest-value product path.

This gate adds extraction only. It does not add traversal, framework-specific endpoint or protocol modeling, runtime overlays, deployment correlation, persistence changes, or cross-service joins.

## Frozen v1 representation

All facts are source-derived and use `metadata.provenance = STATIC_AST`, `confidence = EXTRACTED`, and `confidence_score = 1.0`.

### Value nodes

Graphify emits `type = data_value` nodes for:

| `metadata.kind` | Meaning |
|---|---|
| `PARAMETER` | Declared Java method or constructor parameter |
| `RETURN_VALUE` | Synthetic value representing a declared non-void method return |
| `LOCAL` | Declared local variable or directly constructed local object |
| `FIELD` | Declared field |

Each value records its portable Java owner symbol, Java name, source location, and declared Java type when available. Value IDs derive from the file stem and owner signature rather than the checkout's absolute path.

### Static edges

| Relation | Direction and meaning |
|---|---|
| `FLOWS_TO` | Source value initializes or is assigned to a non-field target value |
| `PASSED_AS_ARGUMENT` | Argument value is passed to the matching parameter of an exactly resolved direct call or constructor; `argumentIndex` records its zero-based position |
| `RETURNED_AS` | Source value is returned as the current method's return value |
| `READ_FROM` | A field value is read into a local, return, or resolved call parameter |
| `WRITTEN_TO` | Source value is assigned to a field |
| `TRANSFORMED_BY` | An argument supports the return of an exactly resolved non-void method, with `transformationSymbol` identifying the method |

Every emitted edge has existing source and target nodes. Runtime or deployment evidence cannot create these edges. Consumers that need distinct data-flow relations between the same endpoints must call `build_from_json(..., directed=True, multigraph=True)`; the legacy Graph/DiGraph modes intentionally collapse parallel edges.

## Supported Java facts

- parameter, local, field, and non-void return identities
- local initialization and assignment
- field reads and writes
- return statements
- direct same-file method calls with a resolved receiver and exact arity
- unqualified calls, `this` calls, same-file static class receivers, and receivers whose declared local, parameter, or field type resolves to a same-file class
- direct same-file constructor argument mapping
- declaration-order independence for methods and fields
- conservative overload handling by class, name, and exact arity

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

## Acceptance evidence

Golden extraction tests exercise Graphify's public `extract()` interface and graph construction boundary. They verify:

- scoped value nodes and frozen v1 metadata
- local assignment, field read/write, argument, return, and transformation edges
- direct constructor mapping
- method and field declaration-order independence
- explicit `this` receiver handling
- exact-arity overload resolution
- portable value IDs and distinct overload return positions
- public graph-build preservation of argument relations
- receiver-aware field reads and writes
- void methods do not create return or transformation facts
- symbolic expressions, lambda captures, reflective calls, and ambiguous calls produce no guessed edges
- every v1 edge references existing nodes
- extracted output survives `build_from_json()`

Observed validation on the final tree:

| Check | Result |
|---|---|
| Ruff on changed Python files | PASS |
| Pyright on `java_data_flow.py` | PASS, 0 errors |
| Focused Java data-flow/member/type and graph-build tests | PASS, 114 tests |
| Broad extraction and Java regression set | PASS, 574 tests |
| Complete Graphify suite with ambient `DEEPSEEK_API_KEY` removed for backend-isolation tests | PASS, 4,428 passed and 3 skipped |
| `graphify update .` | PASS; graph regenerated |
| `git diff --check` | PASS |

The first unisolated full-suite attempt exposed two pre-existing backend-detection test failures because the host exports `DEEPSEEK_API_KEY`. Removing that ambient credential, as those isolation tests require, produced the complete passing result above. An earlier concurrent-edit run also briefly loaded old implementation code with new assertions; it was superseded by the definitive no-edit full-suite pass. Independent re-review compiled three adversarial fixtures and confirmed parallel relation preservation, field-argument reads, nested field access, and lambda-capture omission through the public interfaces.

## Known baseline limitation

Basic interprocedural extraction is currently bounded to exactly resolved declarations within one Java source file. Cross-file symbol resolution remains a later extraction extension and must not be approximated by name-only matching.

# STOP FOR REVIEW

Gate 3 traversal and framework implementation remain blocked until Gate 2 receives explicit approval.
