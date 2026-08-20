# Gate 4B Deterministic EntityManager and Delete Evidence — Implementation Report

Task: `08-gate4b-entitymanager-delete-boundary-evidence`.
Branch: `feature/java-persistence-boundary-completion-v8`.
Approved Gate 4A base: `c9c16a832d4f2bf8476d8b6025052172b45efd82`.
Gate 4A implementation commit: `926246c0f9a4b4df6aaf1d729c6f6c1c062a2c7d`.
Upstream/v8 at start and final fetch: `b2cd36267456c166788c95be6e68574064a92a42`.
Final merge-base: `b2cd36267456c166788c95be6e68574064a92a42`.
Ahead/behind before Gate 4B commits: upstream ahead **0**, branch ahead **45**.
Final implementation SHA: `ea3d898596ff8e84c219e12f6fafef149810817c`.
Final branch HEAD: recorded in the Drive handoff after the documentation commit.

## Request interpretation recheck

The initial user messages named only a new Drive task. No implementation was retained
before the exact task document, approved base, branch and Gate 4B-only boundary were
read. After the request was fully settled, the implementation and tests were checked
again against all 32 mandatory scenarios and every non-negotiable invariant.

One conservative interpretation was necessary: “exact” `deleteById(ID)` method
contract means a represented argument must resolve to the repository's exact ID
generic before positive evidence is emitted. Unrepresented/literal keys therefore
fail closed. Wildcard `EntityManager` identity was likewise treated as ambiguous,
not inferred from a simple name. These choices prioritize the task's explicit
precision-over-recall and no-fabricated-semantics requirements.

## Implementation summary

Gate 4B extends the existing Gate 4A call-record and persistence resolver paths.
It does not introduce a parser, resolver, graph, storage layer, transitive closure
or GVR verdict subsystem.

Implemented exact contracts:

- jakarta/javax `EntityManager.persist(entity)`;
- jakarta/javax `EntityManager.merge(entity)` with optional captured output;
- jakarta/javax `EntityManager.find(Entity.class,id)` with optional key/output
  evidence;
- jakarta/javax `EntityManager.remove(entity)`;
- direct exact Spring Data `delete(entity)`;
- direct exact Spring Data `deleteById(id)`.

All positive evidence reuses `persistence_boundary` and direct `FLOWS_TO` edges.
Entity arguments and find class literals resolve to exact Gate 4A JPA entity
records. Method names alone remain negative.

## Changed files

- `graphify/extractors/java_data_flow.py`;
- `graphify/extractors/resolution.py`;
- `graphify/data_flow_query.py`;
- `tests/test_java_persistence_boundary_completion.py`;
- `docs/data-flow/GATE-4A-PERSISTENCE-EVIDENCE-CONTRACT.md`;
- `docs/data-flow/GATE-4B-ARCHITECTURE-INVENTORY.md`;
- `docs/data-flow/GATE-4B-PERSISTENCE-EVIDENCE-CONTRACT.md`;
- this report.

## Mandatory adversarial-case mapping

| # | Scenario | Test |
| --- | --- | --- |
| 1-2,5 | jakarta/javax exact persist entity | `test_4b_01_02_05_exact_entity_manager_persist` |
| 3 | custom same-name EntityManager | `test_4b_03_custom_same_name_entity_manager_fails_closed` |
| 4 | ambiguous receiver | `test_4b_04_ambiguous_entity_manager_receiver` |
| 6 | persist non-entity | `test_4b_06_persist_non_entity_fails_closed` |
| 7-8 | merge input and captured output | `test_4b_07_08_merge_input_and_captured_return` |
| 9 | ignored merge output | `test_4b_09_ignored_merge_has_input_only` |
| 10 | exact find class literal/key/result | `test_4b_10_exact_find_class_literal_and_result` |
| 11 | dynamic find class | `test_4b_11_dynamic_find_class_fails_closed` |
| 12 | ambiguous class literal | `test_4b_12_ambiguous_find_entity_class_literal` |
| 13-14 | remove entity/non-entity | `test_4b_13_14_remove_entity_contract` |
| 15 | exact repository delete | `test_4b_15_exact_repository_delete` |
| 16 | repository delete mismatch | `test_4b_16_repository_delete_type_mismatch` |
| 17 | deleteById key evidence | `test_4b_17_exact_delete_by_id_key_evidence` |
| 18-19 | delete method/repository lookalikes | `test_4b_18_19_delete_name_lookalikes_are_negative` |
| 20 | duplicate call-site identity | `test_4b_20_duplicate_call_sites_distinct` |
| 21 | parse incomplete remains PARTIAL | `test_4b_21_parse_incomplete_remains_partial` |
| 22 | positive root portability | `test_4b_22_entity_manager_checkout_root_portability` |
| 23 | diagnostic/event root portability | `test_4b_23_diagnostic_checkout_root_portability` |
| 24 | repeated extraction | `test_4b_24_repeated_extraction_stable` |
| 25 | shuffled file order | `test_4b_25_shuffled_file_order_deterministic` |
| 26-27 | forward/backward Gate 3 traversal | `test_4b_26_27_forward_backward_traversal` |
| 28 | structural injection rejected | `test_4b_28_structural_relation_injection_rejected` |
| 29 | reached unresolved boundary incomplete | `test_4b_29_reached_unresolved_boundary_is_incomplete` |
| 30 | unrelated diagnostic region isolation | `test_4b_30_unrelated_diagnostic_does_not_degrade_search` |
| 31 | call-site scoped generic suppression | `test_4b_31_exact_call_suppresses_only_its_generic_diagnostic` |
| 32 | no forbidden persisted relation/verdict | `test_4b_32_no_forbidden_persisted_relations_or_verdicts` |

Additional falsification covers custom non-JPA `@Entity` arguments, a
same-file `EntityManager` shadowing a conflicting framework import, and a
`deleteById` argument whose type does not match the repository ID generic.

## Validation

- Gate 4B focused: **32 passed**.
- Gate 4A focused: **26 passed**.
- Gate 3 traversal + Java integration: **125 passed**.
- Gate 2/2B Java regressions: **68 passed**.
- Full pytest: **5164 passed, 51 skipped, 3 warnings** in 103.23 s.
- Ruff on `graphify` and `tests`: PASS.
- Pyright changed production modules, exact approved base vs Gate 4B:
  **38 errors / 0 warnings vs 38 errors / 0 warnings**, net new **0**.
- Pyright repository-wide, exact approved base vs Gate 4B:
  **609 errors / 3 warnings vs 608 errors / 3 warnings**, net new **0**.
- `git diff --check`: PASS.
- Added-line unfinished-marker scan: PASS.
- Gate 4B file secret-assignment scan: PASS.
- `graphify update .`: PASS; **60240 nodes, 73683 edges, 2634 communities**.
  Expected optional parser warnings remained for SQL/DM fixtures, and the known
  partial `tests/fixtures/sample.luau` syntax fixture was reported.

Pyright baseline commands ran in a clean detached worktree at the exact approved
Gate 4A SHA, not against the modified task branch.

## Performance sanity sample

- non-persistence: 60 files, 360 nodes, 180 edges, 0 persistence boundaries,
  0 direct persistence edges, 149.1 ms;
- persistence: 60 files, 502 nodes, 460 edges, 120 direct boundaries,
  160 direct persistence edges, 262.3 ms.

The sample is bounded direct extraction. No closure or traversal result is stored.

## Known limitations and false negatives

- EntityManager wildcard imports remain fail-closed because a source type from the
  current package can shadow an on-demand import without a complete Java compiler
  symbol table. Explicit imports and qualified receiver types are supported.
- Only direct supported repository inheritance is modeled.
- Dynamic class expressions for `find` are unsupported.
- Literal/non-represented `deleteById` key arguments fail closed because this
  slice cannot prove the ID-generic method contract without a represented value/type.
- Property/mixed access has no attribute mapping.
- No field-to-column flow, SQL/JPQL/JDBC, relationships, cascades, dirty checking,
  flush, transaction, runtime row or physical database claims.

## Local adversarial review findings

The final local review found and fixed three precision risks:

1. A same-file custom `EntityManager` could be laundered through a conflicting
   explicit framework import. Source-declared same-file identity now wins.
2. `deleteById` initially used only repository identity, method name and arity.
   The argument must now resolve to the exact repository ID generic.
3. Repository ID contract metadata is exposed on repository/boundary/diagnostic
   evidence and asserted by focused tests.

The review also rechecked exact call-site suppression, portable diagnostic keys,
MAY/PARTIAL propagation, direct-only edges and forbidden relation absence.

## Subagent execution note

The user required Spark only through OpenAI OAuth. Four read-only agents were
launched using `OpenAI/gpt-5.3-codex-spark` for architecture, tests, epistemics
and framework-contract review. All four failed before execution with
`usage_limit_reached`. No fallback model was substituted. Local adversarial
review and its findings are reported explicitly.

## Explicit scope confirmation

No Gate 5 messaging/events, Gate 6 HTTP/service work, runtime overlay, Deployment
Intelligence, ICE, GVR implementation, `akvarel/gvr` change, BugZero Verify logic,
second graph/parser/resolver or persisted transitive reachability was started.
