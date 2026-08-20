# Gate 4A Deterministic Java Persistence-Boundary Evidence — Implementation Report

Task: `07-gate4a-java-persistence-boundary-evidence`.
Branch: `feature/java-persistence-boundary-v8`.
Approved Gate 3 base: `3286d6dd6c3ecae3fa8fb52ef0af28fbba663b67`.
Upstream/v8 at start: `b2cd36267456c166788c95be6e68574064a92a42`.
Final implementation SHA: `926246c0f9a4b4df6aaf1d729c6f6c1c062a2c7d`.
Final documentation/handoff commit SHA: recorded in the Google Drive supervising-review
handoff because a commit cannot contain its own SHA.

## Implementation summary

Gate 4A extends the existing Java parse/data-flow/resolution pipeline with a
minimal deterministic JPA/Spring Data evidence slice. It does not create a second
Java parser/resolver, a second graph, persisted transitive closure, GVR verdicts,
or global entity-field -> DB-column flow.

### Entity and attribute evidence

The existing Java data-flow parse reuses `engine.py` annotation helpers and
inspects tree-sitter annotation argument nodes. Exact `jakarta.persistence` /
`javax.persistence` `@Entity`, field `@Id`, literal `@Table(name=...)` and
literal field `@Column(name=...)` enrich existing class/field data-value nodes.
Logical identity is represented without inventing physical table/column names.
Non-literal mapping values become UNKNOWN and diagnostic evidence.

### Access strategy

Field mapping requires field `@Id` and no conflicting `@Access` / method `@Id`.
Property, mixed or unresolved access produces a persistence diagnostic, no field
mapping and PARTIAL call-boundary evidence when an exact framework call still
proves logical persistence.

### Repository identity

Only direct, exact `JpaRepository<Entity,Id>` or
`CrudRepository<Entity,Id>` contracts are supported. Entity generic identity is
resolved by qualified name, explicit import or unique same-package source
entity. Same names without exact context fail closed. Repository-like names and
arbitrary `save()` methods produce no persistence semantics.

### Call-site boundaries

Each exact `save(entity)` / `findById(id)` invocation creates one deterministic
`persistence_boundary` node. Direct edges:

- entity value `FLOWS_TO` write boundary;
- read boundary `FLOWS_TO` captured receiving value.

Metadata distinguishes `STATIC_AST` source evidence from
`FRAMEWORK_CONTRACT` semantics and carries repository/entity FQNs, mapping
state, receiver confidence, analysis completeness and source identity.

### Diagnostics / Gate 3

`persistence_resolution` diagnostics are emitted for ambiguous/unresolved/
unsupported mapping or identity. Gate 3 boundary-event normalization consumes
this diagnostic kind without widening relations or changing completeness. Exact
Spring Data boundaries supersede the generic Java cross-file `callee_unresolved`
diagnostic at the same call site, avoiding a false incompleteness claim.

## Changed files

- `graphify/extractors/java_data_flow.py`
- `graphify/extractors/resolution.py`
- `graphify/extract.py`
- `graphify/data_flow_query.py`
- `tests/test_java_persistence_boundary.py`
- `docs/data-flow/GATE-4A-ARCHITECTURE-INVENTORY.md`
- `docs/data-flow/GATE-4A-PERSISTENCE-EVIDENCE-CONTRACT.md`
- this implementation report

## Mandatory adversarial-case mapping

| # | Scenario | Test |
| --- | --- | --- |
| 1 | explicit entity/table/column | `test_4a_01_explicit_entity_table_column_mapping` |
| 2 | entity without table | `test_4a_02_03_logical_mapping_does_not_invent_physical_names` |
| 3 | field without column | `test_4a_02_03_logical_mapping_does_not_invent_physical_names` |
| 4 | non-literal mapping | `test_4a_04_non_literal_mapping_fails_closed` |
| 5 | exact JpaRepository | `test_4a_05_exact_jpa_repository` |
| 6 | exact CrudRepository | `test_4a_06_exact_crud_repository` |
| 7 | same simple entity + explicit import | `test_4a_07_same_simple_entity_explicit_import` |
| 8 | ambiguous entity | `test_4a_08_ambiguous_entity_no_boundary` |
| 9 | repository-like unsupported interface | `test_4a_09_10_names_alone_are_not_persistence` |
| 10 | unrelated save method | `test_4a_09_10_names_alone_are_not_persistence` |
| 11 | exact save write boundary | `test_4a_11_exact_save_write_boundary` |
| 12 | exact findById read boundary | `test_4a_12_exact_find_by_id_read_boundary` |
| 13 | ambiguous save receiver | `test_4a_13_ambiguous_repository_receiver_fails_closed` |
| 14 | property access | `test_4a_14_property_access_is_partial_and_no_field_mapping` |
| 15 | mixed access | `test_4a_15_mixed_access_diagnostic` |
| 16 | parse incomplete | `test_4a_16_parse_incomplete_propagates_partial` |
| 17 | checkout-root portability | `test_4a_17_checkout_root_portability`, `test_4a_persistence_diagnostic_checkout_root_portability` |
| 18 | shuffled file order | `test_4a_18_shuffled_file_order_deterministic` |
| 19 | Gate 3 forward write traversal | `test_4a_19_20_gate3_forward_backward_write_boundary` |
| 20 | Gate 3 backward write traversal | `test_4a_19_20_gate3_forward_backward_write_boundary` |
| 21 | structural relation injection | `test_4a_21_structural_relation_not_injectable` |
| 22 | duplicate call sites distinct | `test_4a_22_duplicate_call_sites_evidence_distinct` |
| 23 | repeated extraction stable | `test_4a_23_repeated_extraction_stable` |
| 24 | relationship annotations no flow | `test_4a_24_relationship_annotation_no_value_flow` |
| 25 | unresolved imported save type | `test_4a_25_unresolved_imported_save_not_spring_data` |

Additional negatives:

- `test_4a_custom_entity_annotation_not_jpa`;
- `test_4a_save_argument_must_match_repository_entity`.

## Validation

- Gate 4A focused tests: **26 passed**.
- Gate 3 traversal + Java integration: **125 passed**.
- Gate 2/2B Java regressions: **68 passed**.
- Full suite: **5132 passed, 51 skipped, 3 warnings** in 105.84 s.
- Ruff on `graphify` and `tests`: PASS.
- Pyright, changed production modules: approved base **148 errors / 0
  warnings**, Gate 4A HEAD **148 errors / 0 warnings**. Net new errors: **0**.
- Pyright, repository-wide: approved base **606 errors / 3 warnings**, Gate
  4A HEAD **605 errors / 3 warnings**. Net new errors: **0**.
- `git diff --check`: PASS.
- `graphify update .`: PASS, producing 15,112 nodes and 28,479 edges.
- Performance sample, 60-file non-persistence corpus: 260 nodes, 120 edges,
  zero persistence boundaries/edges, 154.2 ms.
- Performance sample, 60-file persistence corpus: 421 nodes, 340 edges, 40
  direct boundaries, 40 direct persistence edges, 201.9 ms.

The performance sample confirms bounded extraction and direct evidence only. No
transitive closure or traversal result is persisted.

## Known false negatives / limitations

- EntityManager is intentionally not implemented after completing the Spring Data
  slice; adding it would widen receiver/API contracts beyond this narrow gate.
- Only direct supported repository contract inheritance is implemented; arbitrary
  intermediate generic repository substitution is not.
- Property/mixed access has no attribute mapping.
- Non-literal annotation constants are not evaluated.
- No relationship/embedded/composite/cascade/dirty-checking/transaction semantics.
- No naming-strategy inference, SQL/JPQL/JDBC/native-query parsing, physical DB
  runtime state, schema migration, or exact physical column lineage.
- `findById` produces a value-flow edge only when the existing Java data-flow
  representation captures a receiving local/field/return value.

## Explicit out-of-scope confirmation

No Gate 4B, Gate 5 messaging/events, Gate 6 HTTP/service boundary, runtime
overlay, Deployment Intelligence, ICE, GVR implementation, `akvarel/gvr`
changes, BugZero Verify product logic, LLM-derived persistence evidence, second
Java resolver, second graph or persisted transitive reachability was added.

## Scope interpretation confirmed before finalization

The task permits a minimum coherent Spring Data or EntityManager slice. This
implementation deliberately chose the Spring Data `JpaRepository` /
`CrudRepository` slice and treated EntityManager support as out of scope. The
other potentially ambiguous choice was access strategy: only field access is
mapped positively; property, mixed and unresolved access fail closed rather
than guessing. These interpretations were rechecked against the final code and
tests before handoff.

## Subagent execution note

The requested OpenAI OAuth Spark route was identified as
`gpt-5.3-codex-spark`. Four read-only subagents were launched for architecture,
extractor design, adversarial tests and epistemic review, followed by one final
review retry. OpenAI returned `usage_limit_reached` for all five launches before
execution. No alternate model was silently substituted. Local implementation
and an independent local adversarial review therefore remain explicitly
documented. That review found and fixed two issues before handoff: a `save`
argument whose type did not match the repository entity could create a false
positive boundary, and persistence diagnostic `callerFile` metadata could leak
the checkout root into derived evidence identity.
