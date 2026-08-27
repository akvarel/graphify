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

The Round-2 P0-1 statement that `this`, unqualified access, or a deterministic
`this.<chain>` proves runtime same-instance identity is superseded by Round 3.
A deterministic source receiver path is an access-site identity only.

## Supervising Review Remediation — Round 3

Round 3 separates exact field declaration resolution from runtime instance and
alias authority. It also integrates the exact authorized v8 revision without
rewriting history.

| Issue | Defect and root cause | Changed files | Correction | Adversarial test | Result |
|---|---|---|---|---|---|
| R3-A Declaration certainty was treated as runtime instance proof | Round 2 marked `this`, unqualified access, and deterministic `this.<chain>` paths `PROVEN`, although the FIELD node represents a declaration and the extractor performs no heap/instance analysis. | `graphify/extractors/java_data_flow.py`, `tests/test_java_data_flow.py` | Field edges now emit `declarationResolution = EXACT` separately from `instanceAuthority = UNKNOWN` and `aliasAuthority = MAY`. Every ordinary instance access has `receiverConfidence = MAY`, including `this` and unqualified access. | A1 explicit `this.value`; A2 unqualified `value`; A3 named `other.value`. | PASS |
| R3-B Deterministic nested paths could be laundered into same-instance authority | `this.box.value` had a stable `Flow.box` path and was therefore marked `PROVEN`; left/right receiver paths were not explicitly distinguished from alias evidence. | same | `receiverKind` and `receiverPath` preserve deterministic source access identity, but nested instance paths remain `UNKNOWN`/`MAY`. Distinct `left`/`right` paths prove neither aliasing nor non-aliasing. | A4 nested `this.box.value`; A5 `left.value` versus `right.value`. | PASS |
| R3-C Static class scope was conflated with instance receiver confidence | Explicit class access used the same `PROVEN` vocabulary as instance accesses and field declarations did not retain the AST `static` modifier. | same | Static modifiers are collected from the Java AST. Explicit `Counter.value` emits `receiverKind = STATIC_CLASS`, `fieldScope = STATIC`, and instance/alias authority `NOT_APPLICABLE`. Explicit class access to a non-static field fails closed. | A6 explicit static class field plus independent invalid `Type.instanceField` omission probe. | PASS |
| R3-D Field authority could be lost on parallel field-derived relations | Receiver authority was present only on selected `READ_FROM`/`WRITTEN_TO` edges, while field-derived `PASSED_AS_ARGUMENT`, `RETURNED_AS`, and `TRANSFORMED_BY` could retain unqualified confidence. Assignment reads could also use `FLOWS_TO`. | `graphify/extractors/java_data_flow.py` | The same field authority metadata is carried on field-derived call/return/transformation relations. Field-to-local assignments use `READ_FROM`, preserving the frozen field-read semantics and MAY authority. | Existing parallel-relation/build tests plus A1-A6 and the independent adversarial probe. | PASS |
| R3-E Integrate exact current v8 | The branch started 62 commits behind exact v8 `43d54ac`. | merge commit | Merged `43d54acbfa9e731f7a592bb582c1f4b9d48ed73e` normally with the `ort` strategy and `--no-ff`. There were no conflicts and no manual conflict resolutions. | Focused and full validation after integration. | PASS |

### Gate 3 semantics for receiver/instance flow (Round-3 contract)

Downstream traversal MUST keep field declaration certainty separate from runtime
instance and alias authority:

- `metadata.declarationResolution == "EXACT"` means only that the Java source
  expression resolved to one exact FIELD declaration.
- For every ordinary instance field access, including `this.field`, unqualified
  `field`, named receivers, and nested paths such as `this.box.value`,
  `metadata.instanceAuthority == "UNKNOWN"`, `metadata.aliasAuthority == "MAY"`,
  and `metadata.receiverConfidence == "MAY"`.
- `metadata.receiverKind` and `metadata.receiverPath` are deterministic source
  access-site descriptors. Equal declaration IDs, equal receiver paths, `this`,
  or unqualified syntax MUST NOT by themselves authorize definite same-instance
  write-to-read bridging. Distinct paths such as `Flow@left` and `Flow@right`
  likewise MUST NOT be presented as proof of non-aliasing.
- Static field access is not an instance claim. An exactly resolved explicit
  class receiver has `fieldScope == "STATIC"`, `receiverKind == "STATIC_CLASS"`,
  and instance/alias authority `NOT_APPLICABLE`.
- MAY instance field edges carry `confidence_score = 0.5`. This is conservative
  source-level evidence, not a runtime heap fact.

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

## Final Validation — Round 2 (historical)

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
  branch has been pushed to `fork/feature/java-local-data-flow-v8`.

### Exact steps to reproduce

```bash
cd /sharedssd/git/graphify
git fetch fork upstream
git switch feature/java-local-data-flow-v8
git merge upstream/v8                      # integrate current v8
pytest -q tests/test_java_data_flow.py     # 32 passed
pytest -q                                  # full suite: 4847 passed, 51 skipped
ruff check graphify tests
pyright
git push fork feature/java-local-data-flow-v8
```

No Gate 3 work was attempted in this round; receiver/instance semantics are
documented above as the contract for the next gate.

Known limitations: Gate 2 remains same-file local/basic interprocedural extraction only. Cross-file Java value flow, traversal APIs, framework/runtime/deployment modeling, and Gate 2B/3 behavior were intentionally not implemented.

## Known baseline limitation

Basic interprocedural extraction is currently bounded to exactly resolved declarations within one Java source file. Cross-file symbol resolution remains a later extraction extension and must not be approximated by name-only matching.

## Final Validation — Round 3

Validated production commit:
`3755c8ad87ed9f8edeb42cebf2871344affc3b60` on
`feature/java-local-data-flow-v8`.

Git evidence:

- starting branch HEAD: `d8b663f04092ec1d43eba8e027604bd833c1d957`;
- RED test commit: `a6734ea18b4580ceb98b2f2defab6883a9541194`
  (parent `d8b663f04092ec1d43eba8e027604bd833c1d957`);
- exact integrated `fork/v8` and `upstream/v8`:
  `43d54acbfa9e731f7a592bb582c1f4b9d48ed73e`;
- normal merge commit: `a5293ec6f7cb1b628b10f43da220a373ccb4dd64`
  (parents `a6734ea18b4580ceb98b2f2defab6883a9541194` and
  `43d54acbfa9e731f7a592bb582c1f4b9d48ed73e`); no conflicts;
- merge-base(`3755c8a`, `upstream/v8`):
  `43d54acbfa9e731f7a592bb582c1f4b9d48ed73e`;
- at the validated production commit, v8-only / branch-only commits:
  **0 / 35**. The branch was therefore behind v8 by **0** commits.

RED/GREEN evidence:

- clean pre-RED Java data-flow baseline: **32 passed**;
- authentic RED command:
  `uv run --frozen pytest -q tests/test_java_data_flow.py`:
  **8 failed, 30 passed**;
- GREEN Java data-flow suite: **38 passed**;
- Java resolution/member/call group: **106 passed**;
- build/multigraph group: **118 passed**;
- public extraction group: **248 passed, 4 skipped**;
- full suite: **5133 passed, 72 skipped**, 4 warnings;
- `uv run --frozen ruff check graphify tests`: PASS;
- `uv run --frozen pyright graphify/extractors/java_data_flow.py tests/test_java_data_flow.py`:
  **0 errors, 0 warnings**;
- full-project `uv run --frozen pyright` was executed and remains blocked by
  **568 errors, 5 warnings** in unrelated integrated-branch files, including
  missing optional `watchdog`; neither changed file reports an error;
- `git diff --check`: PASS;
- `uv run --frozen graphify update .`: PASS. It reported existing optional-parser
  warnings for 7 SQL files and 1 DM file, plus one recovered Luau fixture.

Independent adversarial extraction, using a fixture distinct from A1-A6, proved:

- nested instance paths `Flow.box`, `Flow@left.box`, and `Flow@right.box` remain
  distinct and MAY;
- no field edge retains `receiverConfidence = PROVEN`;
- explicit static class-scope edges are emitted;
- invalid explicit class access to a non-static field is omitted.

Round-3 limitations and boundaries:

- no cross-file value flow;
- no Gate 2B work;
- no Gate 3 traversal or same-instance bridging implementation;
- no framework, runtime, deployment, persistence, cross-service, or LLM-derived
  flow;
- no name-only resolver;
- no production system or deployment modification, Google Drive upload, or push was performed.

# STOP FOR REVIEW

Gate 3 traversal and framework implementation remain blocked until Gate 2 receives explicit approval.
