# Gate 4B Java EntityManager and Delete Evidence Contract

## Purpose

Gate 4B completes the narrow deterministic Java persistence-boundary slice with
exact EntityManager operations and exact Spring Data delete operations. Evidence
is source-derived syntax plus documented framework contracts. It is not ORM
execution simulation or database lineage.

## Exact framework identities

Supported EntityManager receiver FQNs:

- `jakarta.persistence.EntityManager`;
- `javax.persistence.EntityManager`.

Supported repository contracts remain:

- `org.springframework.data.repository.CrudRepository`;
- `org.springframework.data.jpa.repository.JpaRepository`.

A method name or simple receiver name is insufficient. Qualified/explicitly
imported types and the existing exact Java mechanisms may prove identity.
Wildcard, unresolved, conflicting, or non-JPA same-name receiver identity fails
closed. A source-declared same-file class wins over a conflicting import.

## Operation vocabulary

The stable source-operation metadata is:

- `operation=persist`, `operationKind=PERSIST`;
- `operation=merge`, `operationKind=MERGE`;
- `operation=find`, `operationKind=FIND`;
- `operation=remove`, `operationKind=REMOVE`;
- `operation=delete`, `operationKind=DELETE`;
- `operation=deleteById`, `operationKind=DELETE_BY_ID`.

Gate 4A operations retain `save` and `findById`, now also exposing normalized
`operationKind` and `persistenceDirection` metadata.

Boundary direction is explicit:

- `WRITE`: persist, merge, save;
- `READ`: find, findById;
- `DELETE`: remove, delete, deleteById.

`boundaryKind` remains `PERSISTENCE_WRITE`, `PERSISTENCE_READ`, or
`PERSISTENCE_DELETE`.

## Positive direct evidence

### EntityManager.persist

Requirements: exact EntityManager receiver, arity one, represented argument
value, and argument type resolving to exactly one supported source JPA entity.

Evidence: `entity value --FLOWS_TO--> WRITE boundary`.

### EntityManager.merge

The persist requirements apply. The input entity and returned managed value are
not treated as object-identical.

Evidence:

- `entity value --FLOWS_TO--> WRITE boundary`;
- `WRITE boundary --FLOWS_TO--> captured result`, only when an existing receiving
  local/field/return data value exists.

Ignored return values create no output edge or invented value.

### EntityManager.find

Requirements: exact EntityManager receiver, arity two, first argument is a
statically resolvable Java class literal for exactly one supported JPA entity.
Dynamic `Class` expressions are unsupported.

Evidence:

- represented id/key value `--FLOWS_TO--> READ boundary`, when available;
- `READ boundary --FLOWS_TO--> captured result`, when available.

The class literal establishes entity identity but is not invented as a value-flow
node.

### EntityManager.remove

Requirements: exact EntityManager receiver, arity one, represented argument value
whose type resolves to exactly one supported JPA entity.

Evidence: `entity value --FLOWS_TO--> DELETE boundary`.

No cascade, related entity, row-count or physical row claim is made.

### Spring Data delete

Requirements: one exact direct supported repository contract and exact repository
entity generic; represented argument value whose exact entity type matches that
generic.

Evidence: `entity value --FLOWS_TO--> DELETE boundary`.

### Spring Data deleteById

Requirements: exact supported repository identity, arity one, and a represented
argument whose exact Java type matches the repository ID generic.

Evidence: key value `--FLOWS_TO--> DELETE boundary`. Literal or otherwise
unrepresented key arguments fail closed because this slice cannot prove the
inherited ID-typed method contract without a value/type fact.

## Boundary metadata

Every positive boundary exposes stable machine-readable metadata:

- `kind=persistence_boundary`, `capability=persistence_boundary`;
- source operation and normalized operation kind;
- boundary kind and READ/WRITE/DELETE direction;
- `framework=JPA_ENTITY_MANAGER|SPRING_DATA`;
- exact framework contract and receiver FQN;
- repository FQN/contract and exact ID type FQN where applicable;
- exact entity FQN;
- Gate 4A access strategy, mapping completeness, and only explicit physical names;
- receiver identity and receiver confidence;
- analysis completeness;
- `provenance=FRAMEWORK_CONTRACT` and `sourceProvenance=STATIC_AST`;
- canonical repository-relative source file/location.

Call location participates in deterministic boundary identity, so duplicate call
sites are distinct while repeated extraction is stable. Absolute checkout paths
do not participate.

## Epistemic rules

- Candidate call records do not prove a framework contract.
- Named receiver instance identity remains MAY where the existing Java layer
  cannot prove alias identity.
- MAY is never upgraded to PROVEN.
- Parse-incomplete call/entity/repository evidence is PARTIAL.
- Unsupported property/mixed mapping remains PARTIAL even when the logical call
  contract is exact.
- PARTIAL or UNKNOWN never becomes complete.
- No GVR verdict or VERIFIED persistence claim is emitted.

## Diagnostics

Blocking cases use `kind=persistence_resolution` with bounded reason and identity
metadata. Reasons cover:

- EntityManager receiver unresolved/ambiguous or non-JPA same-name;
- unsupported operation signature;
- persist/merge/remove argument value/type unresolved or not an exact JPA entity;
- dynamic, unresolved or ambiguous find class literal;
- repository/entity ambiguity;
- delete entity or deleteById ID-generic argument mismatch;
- parse incompleteness via analysis completeness.

No positive edge is emitted for a blocking unresolved aspect. Generic
unresolved-callee suppression occurs only for the same exact fully recognized
framework call site.

## Gate 3 contract

All direct edges use `FLOWS_TO`, already in the Gate 3 allowlist. Forward and
backward traversal remains deterministic. Blocking persistence diagnostics become
portable boundary events only when their caller file is in the explored region.
Framework identity participates in boundary evidence keys. File/edge order does
not affect path or evidence identity. Derived paths are never persisted.

## Unsupported cases

Unsupported by design:

- arbitrary objects exposing persistence-looking method names;
- custom same-name EntityManager or custom non-JPA `@Entity`;
- dynamic find class expressions;
- arbitrary intermediate repository generic substitution;
- `saveAll`, `deleteAll`, batch/bulk operations and derived query parsing;
- field/column value lineage, relationships, cascades and orphan removal;
- embedded/composite identity expansion;
- SQL/JPQL/JDBC/native query/schema migration analysis;
- dirty checking, flush, commit and transaction behavior;
- runtime database state or traces;
- HTTP, messaging/events, Gate 5+, GVR or BugZero policy logic.
