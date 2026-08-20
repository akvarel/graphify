# Gate 4A Java Persistence-Boundary Evidence Contract

## Purpose

Gate 4A adds deterministic, source-derived Java persistence evidence for a
minimal JPA/Spring Data subset. It answers whether a value reaches a proven
persistence write boundary, whether a supported read boundary produces a value,
and which exact repository/entity and explicit physical mappings are involved.
It does not claim full ORM, SQL, column-level lineage, transaction, or runtime
semantics.

## Supported framework identities

JPA annotation namespaces:

- `jakarta.persistence.*`
- `javax.persistence.*`

Spring Data contracts:

- `org.springframework.data.repository.CrudRepository`
- `org.springframework.data.jpa.repository.JpaRepository`

An identity is accepted only through inline-qualified syntax, an explicit import,
a uniquely supported framework wildcard import, or exact same-package source
identity where applicable. A simple name or method name is never sufficient.

## Supported entity mapping subset

Supported:

- class-level exact `@Entity`;
- explicit literal `@Table(name="...")`;
- field-access entities proven by field `@Id` and no contradicting `@Access`;
- field exact `@Id`;
- explicit literal field `@Column(name="...")`.

Logical identity and physical mapping are distinct:

- `persistenceEntity` / `entityFqn` and `persistenceAttribute` /
  `attributeName` are logical source identities;
- `tableName` / `columnName` are emitted only for explicit, deterministically
  decoded string-literal annotation values;
- absent table/column annotations are `UNSPECIFIED`, not inferred from Java names;
- non-literal or unresolved annotation values are `UNKNOWN`, with no fabricated
  physical name and a machine-visible diagnostic.

Existing class nodes and field `data_value` nodes are enriched in place. No
parallel semantic entity/attribute graph is created.

## Access strategy

Field mapping is emitted only when field access is defensible:

- at least one exact field `@Id`;
- no exact class `@Access(PROPERTY)`;
- no method `@Id` (property access);
- no field `@Access(PROPERTY)` contradiction.

Property, mixed, conflicting or unresolved access produces
`kind=persistence_resolution` diagnostics, no field mapping, and PARTIAL
persistence-boundary completeness when an exact repository call still proves the
logical persistence operation.

## Repository identity

A repository is supported only when an interface directly extends one exact
supported Spring Data contract with an entity generic argument. The entity type
must resolve to exactly one source-derived `@Entity` by qualified name, explicit
import, or unique same-package identity. Ambiguous/unresolved entity identity
produces no positive persistence boundary.

Direct contract inheritance is the Gate 4A subset. Generic substitution through
arbitrary intermediate repository hierarchies is a known false negative, not a
name-based inference opportunity.

## Call-site boundary representation

Node type: `persistence_boundary`.

Each node is tied to one source call site and includes:

- `boundaryKind=PERSISTENCE_WRITE|PERSISTENCE_READ`;
- `framework=SPRING_DATA`;
- `operation=save|findById`;
- exact `repositoryFqn`, supported contract FQN and exact `entityFqn`;
- access/mapping completeness and explicit physical mapping when available;
- `provenance=FRAMEWORK_CONTRACT` and `sourceProvenance=STATIC_AST`;
- `receiverConfidence`, `analysisCompleteness`;
- canonical source file/location.

Call sites at different locations have distinct boundary IDs and evidence.
Repeated extraction of the same call site is stable and checkout-root independent.

## Direct value-flow evidence

- exact `save(entity)`: `entity value --FLOWS_TO--> PERSISTENCE_WRITE boundary`;
- exact `findById(id)` with an existing captured sink:
  `PERSISTENCE_READ boundary --FLOWS_TO--> receiving local/field/return value`.

These are direct call-site facts justified by a supported framework contract.
No transitive reachability is persisted. No field -> physical-column value-flow
edge is created merely because a field belongs to an entity.

`SUPPORTED_DATA_FLOW_RELATIONS` is unchanged. Structural relations remain
non-value-flow and caller configuration cannot inject them.

## Provenance and epistemic state

- annotation/type/call syntax: `STATIC_AST`;
- the meaning of supported Spring Data operations: `FRAMEWORK_CONTRACT`;
- parse-incomplete call/entity/repository sources: `analysisCompleteness=PARTIAL`;
- named receiver instance identity follows existing Java rules (normally `MAY`),
  never upgraded by the persistence layer;
- property/mixed mapping support is PARTIAL even when the logical save/read
  boundary is exact;
- no GVR PASS/FAIL/UNKNOWN or VERIFIED verdict is emitted.

## Diagnostics and fail-closed behavior

Diagnostic node type: `extraction_diagnostic`,
`kind=persistence_resolution`, capability `persistence_boundary`.

States: `AMBIGUOUS`, `UNRESOLVED`, `UNSUPPORTED`, `PARTIAL` as applicable.
Metadata includes canonical caller file/location, reason, receiver/repository/
entity identities, operation/arity and bounded candidate counts/identities.

Positive boundaries are not emitted for ambiguous/unresolved repository or entity
identity. Random classes with `save()` / `findById()`, repository-like names and
unsupported imported types produce no persistence semantics. Gate 3 normalizes
blocking persistence diagnostics into the existing portable boundary-event
contract without changing completeness rules.

## Portability

All boundary/diagnostic IDs use existing `_file_stem` + source-location identity
and the normal public graph canonical remap. After source-file relativization,
Gate 4A canonicalizes persistence diagnostic `callerFile` to the public
repo-relative source path. Absolute checkout/cache roots do not participate in
public IDs, mapping evidence, diagnostics, Gate 3 evidence keys or normalized
results.

## Supported / unsupported matrix

| Construct | Gate 4A |
| --- | --- |
| jakarta/javax `@Entity`, field `@Id` | Supported |
| literal `@Table(name=...)`, `@Column(name=...)` | Supported explicit mapping |
| absent table/column | Logical only, physical unspecified |
| non-literal table/column | UNKNOWN + diagnostic, no physical name |
| field access | Supported when proven |
| property/mixed access | Diagnostic / PARTIAL; no field mapping |
| direct `JpaRepository<T,ID>` / `CrudRepository<T,ID>` | Supported |
| `save(entity)` / `findById(id)` | Supported call-site boundary |
| arbitrary repository hierarchy/generic substitution | Not implemented |
| EntityManager | Not implemented in this slice |
| saveAll, merge, delete, derived queries, @Query | Not implemented |
| JPQL/HQL, Criteria, JDBC/native SQL | Not implemented |
| relationships, embedded/composite, cascades | Not implemented |
| dirty checking, transactions, naming strategies | Not implemented |
| runtime/database state | Not implemented |

## Gate 3 composition

Gate 4A evidence composes with the existing bounded query-time traversal:
forward traversal reaches write boundaries; backward traversal identifies direct
contributors; read boundaries reach captured results; PARTIAL evidence remains
PARTIAL; blocking persistence diagnostics prevent unsafe completeness claims in
the relevant reached caller file. Gate 3 relation allowlist, evidence-key,
termination/accounting, and `complete_supported_search` invariants remain
unchanged.
