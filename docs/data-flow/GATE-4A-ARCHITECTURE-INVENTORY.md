# Gate 4A Architecture Inventory

Status: pre-implementation inventory for task `07-gate4a-java-persistence-boundary-evidence`.
Base: approved Gate 3 integrated HEAD `3286d6dd6c3ecae3fa8fb52ef0af28fbba663b67`.
Branch: `feature/java-persistence-boundary-v8`.
Upstream/v8 at start: `b2cd36267456c166788c95be6e68574064a92a42`, already contained by the approved base.

## Existing Java extraction pipeline

1. `graphify.extract.extract_java()` calls the generic Java AST extractor and
   then the single Java data-flow augmenter `augment_java_data_flow()`.
2. `graphify/extractors/engine.py` already parses Java class/interface inheritance,
   declared field types, annotation names (including inline-qualified names), and
   class literals. Gate 4A must reuse the same tree-sitter parse and annotation
   nodes, not introduce a regex/LLM annotation parser.
3. `graphify/extractors/java_data_flow.py` reparses the file once for the existing
   Java data-flow augmentation, records package/import facts, declared receiver
   types, value nodes, local/same-file flows and cross-file call intents.
4. `graphify/extractors/resolution.py::_resolve_cross_file_java_data_flow()` is the
   repository-wide Java resolution pass. It runs before canonical ID remapping,
   resolves exact receiver FQNs/callees and emits fail-closed diagnostics.
5. `extract()` then canonicalizes IDs/source paths and rewrites edge endpoints.

Gate 4A will extend items 3 and 4. It will not create a second parser or a second
independent Java resolver.

## Existing graph/value-flow contracts

Direct value evidence uses these relations:

- `FLOWS_TO`: source value/event output flows into a receiving value/event.
- `PASSED_AS_ARGUMENT`: caller argument value flows to an exact callee parameter.
- `RETURNED_AS`: a value is returned by the current method.
- `READ_FROM`: a read depends on a field/source value.
- `WRITTEN_TO`: an assigned value is written to a field value.
- `TRANSFORMED_BY`: exact supported transformation dependency.

`CALLS`, `references`, `imports`, `contains`, `inherits`, and `implements` are
structural, not value flow. Gate 4A will not widen
`SUPPORTED_DATA_FLOW_RELATIONS`.

Persistence call boundaries will use call-site-specific nodes. Exact `save(x)`
will emit direct `x --FLOWS_TO--> write boundary`; exact `findById(id)` will emit
`read boundary --FLOWS_TO--> result sink` only when the existing data-flow
augmenter captured the receiving local/field/return value. No global entity
field -> physical column value-flow edge will be emitted.

## Existing Java identity/resolution evidence

- Package: parsed from `package_declaration`.
- Explicit imports: `simple name -> exact FQN` in `java_imports`.
- Wildcard imports: currently identified as ambiguous for generic cross-file
  receiver resolution; Gate 4A will retain exact wildcard package names only for
  resolving the two specifically supported JPA annotation namespaces.
- Receiver type: derived from local/parameter/field declared types.
- Receiver FQN: qualified name, explicit import, or same-package evidence;
  wildcard/name-only ambiguity fails closed.
- Cross-file call intent: exact call location, receiver resolution/confidence,
  method/arity, argument value IDs and return sink.
- Canonical IDs: pre-remap IDs use `_file_stem(path)` and source location; the
  normal extract remap makes public IDs/source paths checkout-root independent.

## Existing diagnostics and Gate 3 integration

Gate 2B diagnostics are `extraction_diagnostic` nodes with canonicalizable
source identity and metadata including `kind`, `resolution`, `coverage`, caller
file/location, receiver, method, arity and reason. Gate 4A will emit analogous
`kind=persistence_resolution` diagnostics and extend Gate 3 boundary-event
normalization to consume this kind without changing traversal completeness rules.

Gate 3 traverses only the fixed direct relation allowlist. It preserves
`receiverConfidence`, `analysisCompleteness`, provenance, evidence keys,
boundary events, truncation, and explored-region epistemic state. Persistence
facts must fit this contract. Gate 3 itself will not infer persistence semantics.

## Annotation and entity mapping reuse

`engine.py` already exposes `_java_annotation_nodes()` and
`_java_annotation_names()`. Gate 4A will reuse these functions over the existing
Java data-flow parse tree and inspect tree-sitter annotation argument nodes for
literal `name` values. It will not evaluate arbitrary Java expressions/constants.

Existing class nodes are sufficient as logical entity identities and existing
`data_value(kind=FIELD)` nodes are sufficient as logical attributes. Gate 4A will
enrich those nodes with persistence mapping metadata instead of introducing a
second semantic entity/attribute graph.

## Minimal supported Gate 4A representation

### Entity/class node enrichment

- `persistenceEntity=true`
- `entityFqn`
- `entityNamespace=jakarta.persistence|javax.persistence`
- `accessStrategy=FIELD|UNKNOWN|UNSUPPORTED`
- `tableMapping=EXPLICIT|UNSPECIFIED|UNKNOWN`
- `tableName` only for a proven literal `@Table(name="...")`
- `analysisCompleteness`
- `persistenceProvenance=STATIC_AST`

### Field data-value node enrichment

- `persistenceAttribute=true`
- `entityFqn`, `attributeName`
- `persistenceId=true` when exact field `@Id`
- `columnMapping=EXPLICIT|UNSPECIFIED|UNKNOWN`
- `columnName` only for a proven literal `@Column(name="...")`
- no physical column name inferred from the Java field name

### Call-site boundary node

Type: `persistence_boundary`.

Metadata includes `boundaryKind=PERSISTENCE_WRITE|PERSISTENCE_READ`,
`framework=SPRING_DATA`, `operation=save|findById`, `repositoryFqn`, `entityFqn`,
logical/physical mapping state, `provenance=FRAMEWORK_CONTRACT`,
`sourceProvenance=STATIC_AST`, `receiverConfidence`, `analysisCompleteness`,
source file/location and a deterministic call-site identity.

Duplicate call sites remain evidence-distinct by source location. Repeated
extraction of the same call site remains stable.

## Supported identity rules

- JPA annotations are accepted only when resolved to `jakarta.persistence.*` or
  `javax.persistence.*` by inline-qualified syntax, explicit import, or one exact
  supported wildcard package.
- Spring Data repositories are accepted only for a directly and deterministically
  resolved `JpaRepository<Entity, Id>` or `CrudRepository<Entity, Id>` contract.
- Repository entity generic type resolves by qualified name, explicit import, or
  a unique same-package entity. Same simple names without exact context fail
  closed.
- A type or method merely named `Repository`, `save`, `find`, or `persist` is not
  persistence evidence.

## Access strategy / mapping fail-closed rules

Field access is supported only when `@Id` is on a field and there is no
contradicting class/field `@Access(PROPERTY)` or method `@Id`. Property/mixed/
conflicting access emits `persistence_resolution` diagnostics and no fabricated
field/physical mapping. Entity logical identity may remain source-derived.

Non-literal `@Table(name=...)` / `@Column(name=...)` is not evaluated. Mapping is
`UNKNOWN`; no physical name is emitted; a machine-visible diagnostic records the
unsupported non-literal value.

Parse-incomplete entity/repository/call sources propagate `PARTIAL` through
`analysisCompleteness`; they never become complete.

## Explicitly out of scope

EntityManager (unless the Spring Data slice is complete and evidence quality is
unchanged), saveAll, merge/delete, derived queries, @Query, JPQL/HQL, Criteria,
JDBC/native SQL, Hibernate Session, relationships/joins, embedded/composite
mapping, cascades, dirty checking, transactions, naming strategies, generated
IDs/version semantics, runtime/database state, Gate 4B/5/6, GVR, BugZero Verify.

## Primary implementation points

- `graphify/extractors/java_data_flow.py`: collect entity/repository/call candidate
  records and enrich local entity/field nodes from the existing parse.
- `graphify/extractors/resolution.py`: extend the existing repository-wide Java
  resolver with deterministic persistence resolution/emission.
- `graphify/data_flow_query.py`: normalize `persistence_resolution` diagnostics
  into the existing boundary-event contract only; do not change value-flow
  allowlists or completeness semantics.
- New focused fixture/test files and Gate 3 integration tests.
