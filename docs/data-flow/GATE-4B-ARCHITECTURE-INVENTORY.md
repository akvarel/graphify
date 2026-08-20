# Gate 4B Java Persistence Architecture Inventory and Delta

Task: `08-gate4b-entitymanager-delete-boundary-evidence`.

Approved base: `c9c16a832d4f2bf8476d8b6025052172b45efd82`.
Target branch: `feature/java-persistence-boundary-completion-v8`.
Upstream/v8 at start: `b2cd36267456c166788c95be6e68574064a92a42`.

## Existing architecture reused

Gate 4B remains inside the Gate 2/2B/3/4A architecture:

1. `graphify/extractors/java_data_flow.py` owns the single tree-sitter Java parse
   used for local value nodes/edges, call-site facts, imports, declared receiver
   types, arguments, and return sinks.
2. `graphify/extractors/resolution.py::_resolve_java_persistence_records` runs
   inside the existing repository-wide Java resolution pass. It owns exact
   framework/entity matching, diagnostics, direct boundary nodes and direct
   `FLOWS_TO` edges.
3. `graphify/data_flow_query.py` consumes public graph nodes/edges at query time.
   It does not persist paths or transitive reachability.
4. `graphify/extract.py` performs the existing canonical repository-relative ID
   and source-file remap, including persistence diagnostic `callerFile`.
5. Gate 4A entity, attribute and direct Spring Data repository records remain the
   source of exact entity/repository identity and mapping completeness.

The Graphify report at the approved base identified
`augment_java_data_flow`, `_resolve_java_persistence_records`,
`_resolve_cross_file_java_data_flow`, `_collect_boundary_events`, and
`run_data_flow_query` as the relevant community hubs. No additional parser,
resolver, graph or storage layer is needed.

## Gate 4B extraction delta

The per-file persistence call record allowlist is extended from
`save`/`findById` to:

- Spring Data: `save`, `findById`, `delete`, `deleteById`;
- JPA EntityManager: `persist`, `merge`, `find`, `remove`.

A record is only a candidate. Method names do not create persistence semantics.
The record carries existing source-derived facts:

- declared receiver type/FQN resolution and receiver confidence;
- imports, package and deterministic call-site identity;
- argument value-node IDs when represented;
- Java class-literal raw type for `find(Entity.class, id)`;
- captured local/field/return sink when represented;
- parse completeness.

Class-literal recording is syntax evidence only. Entity identity remains a global
resolver decision.

## Gate 4B resolver delta

The existing persistence resolver adds an exact contract branch for:

- `jakarta.persistence.EntityManager`;
- `javax.persistence.EntityManager`.

It also extends exact direct `JpaRepository<T,ID>` / `CrudRepository<T,ID>`
operations with `delete(T)` and `deleteById(ID)`.

Positive evidence requires:

- exact framework receiver FQN;
- supported operation arity;
- exact source-derived JPA entity identity where the operation carries an entity;
- exact class literal for `EntityManager.find`;
- exact repository ID-generic match for `deleteById`;
- existing argument/output data-value nodes for each emitted direct edge.

A same-file source class shadows a conflicting imported framework simple name.
This prevents invalid/conflicting source from laundering a custom
`EntityManager` into a JPA contract.

## Direct graph delta

Gate 4B reuses node type `persistence_boundary` and relation `FLOWS_TO`.

| Contract | Direct evidence |
| --- | --- |
| `EntityManager.persist(entity)` | entity value -> WRITE boundary |
| `EntityManager.merge(entity)` | entity value -> WRITE boundary; boundary -> captured result when present |
| `EntityManager.find(Entity.class,id)` | represented key value -> READ boundary; boundary -> captured result when present |
| `EntityManager.remove(entity)` | entity value -> DELETE boundary |
| `Repository.delete(entity)` | entity value -> DELETE boundary |
| `Repository.deleteById(id)` | exact ID-generic represented key value -> DELETE boundary |

No class-literal node, physical table/column write, related-row delete, cascade,
transaction, dirty-checking or runtime database state is asserted.

## Diagnostics and suppression

Gate 4B reuses `extraction_diagnostic` with
`kind=persistence_resolution`. Material unresolved/ambiguous/unsupported
receiver, entity argument, class literal and signature cases fail closed.

Generic `cross_file_resolution` unresolved-callee diagnostics are suppressed
only after an exact supported framework call is fully recognized. Suppression is
keyed by caller file, exact call location and method. Another unresolved call
with the same method name remains visible.

## Gate 3 composition

All new positive edges use the unchanged Gate 3 value-flow allowlist. Boundary
evidence keys now also include diagnostic framework identity. Blocking
persistence diagnostics remain portable boundary events scoped to reached caller
files. MAY receiver confidence and PARTIAL/UNKNOWN completeness are propagated,
never upgraded.

## Explicit non-delta

Gate 4B does not add:

- persisted transitive facts or whole-project reachability;
- structural relations as value flow;
- field/attribute-to-column value lineage;
- SQL, JPQL, JDBC, native queries or schema migration semantics;
- flush, transaction, dirty-checking, cascade or relationship effects;
- derived repository query methods, `deleteAll`, bulk or batch operations;
- runtime overlay, HTTP, messaging/events, Deployment Intelligence or ICE;
- GVR verdicts, BugZero policy logic or changes to `akvarel/gvr`.
