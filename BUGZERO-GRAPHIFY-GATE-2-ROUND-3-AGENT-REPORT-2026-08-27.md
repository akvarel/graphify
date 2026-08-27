# BugZero / Graphify Gate 2 Round 3 Agent Report

Date: 2026-08-27  
Repository: `/sharedssd/git/graphify` (`akvarel/graphify` fork)  
Branch: `feature/java-local-data-flow-v8`  
Tracking branch: `fork/feature/java-local-data-flow-v8`

## Authorization and scope

Executed only Gate 2 Round 3 Java local/basic-interprocedural static data-flow remediation.

Not implemented:

- cross-file Java value flow;
- Gate 2B;
- Gate 3 traversal or same-instance bridging;
- framework, runtime, deployment, persistence, cross-service, or LLM-derived flow;
- a name-only resolver;
- production systems or deployments.

No push and no Google Drive upload were performed. The local branch is paused for independent supervising review.

## Exact Git evidence

Preflight supplied by the coordinator and independently verified:

- starting HEAD: `d8b663f04092ec1d43eba8e027604bd833c1d957`;
- `fork/v8`: `43d54acbfa9e731f7a592bb582c1f4b9d48ed73e`;
- `upstream/v8`: `43d54acbfa9e731f7a592bb582c1f4b9d48ed73e`;
- starting merge-base: `b14b52e94ec3d9840413d81777f4c134eac0a40d`;
- starting divergence relative to v8: v8-only **62**, branch-only **32**;
- worktree: clean;
- current branch/tracking: `feature/java-local-data-flow-v8` tracking `fork/feature/java-local-data-flow-v8`.

Round 3 commits:

1. RED tests: `a6734ea18b4580ceb98b2f2defab6883a9541194`
   - parent: `d8b663f04092ec1d43eba8e027604bd833c1d957`;
   - tests only;
   - commit contains `AI-assisted: Jcode`.
2. Exact v8 integration: `a5293ec6f7cb1b628b10f43da220a373ccb4dd64`
   - parents: `a6734ea18b4580ceb98b2f2defab6883a9541194` and `43d54acbfa9e731f7a592bb582c1f4b9d48ed73e`;
   - normal `--no-ff` `ort` merge;
   - no conflicts and no manual conflict resolutions;
   - commit contains `AI-assisted: Jcode`.
3. Production GREEN: `3755c8ad87ed9f8edeb42cebf2871344affc3b60`
   - commit contains `AI-assisted: Jcode`.
4. Initial evidence docs: `54ec03dd9ac94e51ae8671844c144524ddc3c0fa`
   - docs only;
   - commit contains `AI-assisted: Jcode`.
5. A7 RED tests: `73a686df747bec23e70d158687527e8f5c6ffd4c`
   - parent: `54ec03dd9ac94e51ae8671844c144524ddc3c0fa`;
   - tests only;
   - commit contains `AI-assisted: Jcode`.
6. A7 production GREEN: `25d24717da07193aa591880664e55a34004bfe55`
   - parent: `73a686df747bec23e70d158687527e8f5c6ffd4c`;
   - production only;
   - commit contains `AI-assisted: Jcode`.

At the final validated production HEAD
`25d24717da07193aa591880664e55a34004bfe55`:

- `fork/v8` = `upstream/v8` = `43d54acbfa9e731f7a592bb582c1f4b9d48ed73e`;
- merge-base with v8: `43d54acbfa9e731f7a592bb582c1f4b9d48ed73e`;
- v8-only / branch-only commits: **0 / 38**;
- branch behind v8: **0**.

## Changed files authored in Round 3

- `tests/test_java_data_flow.py`
- `graphify/extractors/java_data_flow.py`
- `docs/data-flow/GATE-2-IMPLEMENTATION-REPORT.md`
- `BUGZERO-GRAPHIFY-GATE-2-ROUND-3-AGENT-REPORT-2026-08-27.md`

The v8 merge also integrated the upstream files changed between the prior merge-base and exact v8. Those upstream changes were not rewritten.

## RED-first evidence

Clean baseline command:

```bash
uv run --frozen pytest -q tests/test_java_data_flow.py
```

Result before Round 3 tests: **32 passed**, 1 warning.

The separate RED commit added mandatory public-boundary adversarial cases:

- **A1**: explicit `this.value` resolves the declaration exactly but does not prove runtime instance identity;
- **A2**: unqualified `value` resolves exactly but remains instance `UNKNOWN` / alias `MAY`;
- **A3**: named `other.value` preserves a deterministic named receiver path and remains MAY;
- **A4**: nested `this.box.value` preserves `Flow.box` but is not definite instance evidence;
- **A5**: `left.value` and `right.value` preserve distinct access paths without proving aliasing or non-aliasing;
- **A6**: explicit static `Counter.value` has class scope and no instance/alias question.
- **A7**: parenthesized `(this.value)` preserves the same field authority metadata
  on exactly five affected edges: assignment `READ_FROM`; return `READ_FROM` and
  `RETURNED_AS`; call `PASSED_AS_ARGUMENT` and `TRANSFORMED_BY`.

Two prior Round-2 expectations were also corrected from `PROVEN` to `MAY` for `this` and nested-this access.

Authentic RED result with production code unchanged:

- **8 failed, 30 passed**, 1 warning.

After the test commit, and before any production change, the checkpoint was sent to the coordinator.

The independent A7 RED commit left production unchanged and ran only the new
parameterized regression. Authentic result:

- command:
  `uv run --frozen pytest -q tests/test_java_data_flow.py::test_java_field_authority_a7_parenthesized_explicit_this_preserves_metadata`;
- **5 failed**, one per specified edge;
- every failure showed missing `declarationResolution`, `receiverKind`,
  `receiverPath`, `instanceAuthority`, and `aliasAuthority`.

## Production design and implementation

### Declaration resolution versus runtime authority

Exactly resolved field accesses now expose separate metadata axes:

- `declarationResolution = EXACT`;
- `declarationOwner` identifies the qualified source owner;
- `receiverKind` records the syntactic/access category;
- `receiverPath` records a deterministic source access-site path;
- legacy `receiver` is retained as the same deterministic path for compatibility.

For every ordinary instance field access, including explicit `this`, unqualified access, named receivers, and nested access:

- `fieldScope = INSTANCE`;
- `instanceAuthority = UNKNOWN`;
- `aliasAuthority = MAY`;
- `receiverConfidence = MAY`;
- edge `confidence_score = 0.5`.

A stable path is not a heap identity. Same declaration, same receiver path, `this`, or unqualified syntax cannot authorize definite same-instance bridging. Distinct paths cannot authorize a non-alias conclusion.

### Static class scope

Java field declarations retain the AST `static` modifier.

Explicit class access to an exactly resolved static field emits:

- `fieldScope = STATIC`;
- `receiverKind = STATIC_CLASS`;
- deterministic class `receiverPath`;
- `instanceAuthority = NOT_APPLICABLE`;
- `aliasAuthority = NOT_APPLICABLE`.

Explicit class access to a non-static field fails closed and emits no field flow.

### Field-derived relation authority

Field authority metadata is propagated across field-derived:

- `READ_FROM`;
- `WRITTEN_TO`;
- `PASSED_AS_ARGUMENT`;
- `RETURNED_AS`;
- `TRANSFORMED_BY`.

Field-to-local assignment reads use `READ_FROM`, not `FLOWS_TO`, preserving the frozen field-read relation and preventing authority laundering through a non-field assignment edge.

### Shared parenthesized-expression normalization

`expr_value` and `field_edge_md` now call the same
`unwrap_parenthesized_expression` helper before resolving the underlying node.
The helper preserves the prior single-named-child parenthesis behavior, including
nested parentheses, and does not add support for any other expression category.
Consequently, `(this.value)` resolves to the same FIELD declaration and receiver
authority metadata on assignment, return, and call-derived edges.

A proposed declaration node-ID metadata field was deliberately removed after a public-boundary probe showed that it would retain a pre-canonical ID after extraction remapping. The final metadata uses the portable qualified declaration owner while the FIELD endpoint remains the declaration identity.

## Gate 3 contract

Gate 3 remains unimplemented and blocked pending review.

Any future traversal must obey all of the following:

1. `declarationResolution = EXACT` proves only exact source declaration resolution.
2. Instance `this`, unqualified, named, and nested field paths remain `UNKNOWN` / `MAY`.
3. No definite same-instance write-to-read bridge may be created solely from a shared FIELD declaration, `this`, unqualified syntax, or equal `receiverPath`.
4. Distinct paths such as `Flow@left` and `Flow@right` are not proof of non-aliasing.
5. Explicit static class scope is not an instance claim.

## Validation evidence

All commands used the project uv environment.

### Focused pytest

```bash
uv run --frozen pytest -q tests/test_java_data_flow.py
```

- **43 passed**, 1 warning.

```bash
uv run --frozen pytest -q \
  tests/test_java_data_flow.py::test_java_field_authority_a7_parenthesized_explicit_this_preserves_metadata
```

- **5 passed**, 1 warning.

```bash
uv run --frozen pytest -q \
  tests/test_java_type_resolution.py \
  tests/test_java_member_calls.py \
  tests/test_symbol_resolution.py \
  tests/test_cross_language_call_resolution.py \
  tests/test_observability_anchors_java.py
```

- **106 passed**, 1 warning.

```bash
uv run --frozen pytest -q \
  tests/test_build.py \
  tests/test_multigraph_compat.py \
  tests/test_multigraph_diagnostics.py
```

- **118 passed**, 1 warning.

```bash
uv run --frozen pytest -q \
  tests/test_extract.py \
  tests/test_extract_cli.py \
  tests/test_extract_code_only_cli.py
```

- **248 passed, 4 skipped**, 1 warning.

### Full pytest

```bash
uv run --frozen pytest -q
```

- **5138 passed, 72 skipped**, 3 warnings;
- elapsed: 87.74 seconds;
- result: PASS.

### Static and diff checks

```bash
uv run --frozen ruff check graphify tests
```

- PASS.

```bash
uv run --frozen pyright graphify/extractors/java_data_flow.py tests/test_java_data_flow.py
```

- **0 errors, 0 warnings**.

```bash
uv run --frozen pyright
```

- executed;
- **568 errors, 5 warnings** in unrelated integrated-branch files;
- includes missing optional `watchdog` imports;
- neither changed Python file reports an error;
- this is recorded as an existing full-project type-check limitation, not reported as a false PASS.

```bash
git diff --check
```

- PASS.

### Graph refresh

```bash
uv run --frozen graphify update .
```

- PASS;
- graph rebuilt successfully;
- existing environment warnings: 7 SQL files lacked optional `tree_sitter_sql`, 1 DM file lacked optional `tree-sitter-dm`, and one Luau fixture used parser recovery;
- no tracked graph output remained modified.

### Independent adversarial falsification

A separate public extraction fixture, distinct from A1-A7, combined nested `this`, nested left/right receiver paths, explicit static access, and invalid class access to a non-static field.

Observed assertions:

- instance edges: **3**;
- static edges: **3**;
- instance paths: `Flow.box`, `Flow@left.box`, `Flow@right.box`;
- all instance edges remained `UNKNOWN` / `MAY` with score 0.5;
- `receiverConfidence = PROVEN` edge count: **0**;
- invalid explicit class access to a non-static field: omitted.

## Limitations and blockers

Within Gate 2 Round 3, no implementation blocker remains.

Known limitations retained deliberately:

- local/basic interprocedural extraction remains same-file only;
- no alias, heap, or runtime object identity analysis exists;
- receiver paths are deterministic access-site descriptors only;
- cross-file exact callee/value linkage remains analysis-only future work;
- Gate 3 traversal remains unauthorized;
- full-project Pyright has 568 unrelated integrated-branch errors, while changed files are clean.

## Handoff status

- requested behavior implemented: YES;
- exact v8 integrated normally: YES;
- branch behind v8: 0;
- mandatory A1-A7 public tests: PASS;
- focused and full pytest: PASS;
- Ruff: PASS;
- changed-file Pyright: PASS;
- full-project Pyright: executed with exact limitation recorded;
- graph refreshed: YES;
- implementation report updated: YES;
- production systems/deployments modified: NO;
- cross-file flow implemented: NO;
- Gate 2B started: NO;
- Gate 3 started: NO;
- pushed: NO;
- Google Drive upload: NO.

STOP FOR SUPERVISING REVIEW
