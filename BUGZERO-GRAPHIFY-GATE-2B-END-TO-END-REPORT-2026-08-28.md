# BugZero / Graphify Gate 2B End-to-End Report

Date: 2026-08-28
Repository: `/sharedssd/git/graphify`
Branch: `feature/java-cross-file-data-flow-v8`
Scope: Gate 2 integration and Gate 2B authority-semantic finalization only. Gate 3 was not started.

## 1. Authorized inputs and immutable Git evidence

- Approved Gate 2 branch HEAD:
  `7f4bba86a0991cd15c54e8da2d6494c909c1a3c7`
- Validated v8 revision:
  `43d54acbfa9e731f7a592bb582c1f4b9d48ed73e`
- Gate 2B pre-task branch HEAD:
  `c6b56a761039f813fe12fbbcddcc7925dc9ee214`
- Normal non-force merge commit integrating approved Gate 2:
  `2ceee6b86edb64a5a302685110f58a3df54956c1`
  - parent 1: `c6b56a761039f813fe12fbbcddcc7925dc9ee214`
  - parent 2: `7f4bba86a0991cd15c54e8da2d6494c909c1a3c7`
  - one content conflict occurred in
    `graphify/extractors/java_data_flow.py` return handling;
  - resolution retained both Gate 2B return-sink capture and approved Gate 2
    field-authority propagation on `READ_FROM` and `RETURNED_AS`;
  - inherited trailing whitespace in the approved Gate 2 report header was
    removed so `git diff --check` could pass.
- Gate 2B semantic RED commit:
  `70bf35d9f5dd9ae78cbccc8f15d36a383ed3335c`
- Gate 2B semantic GREEN production commit:
  `9b21e758509af9712c4b3487b9243737b871f4d9`

Every task-authored commit contains `AI-assisted: Jcode`. No history rewrite,
force-push, protected-branch write, production action, or Gate 3 work occurred.

## 2. Implemented Gate 2B contract

Cross-file Java evidence no longer uses the obsolete aggregate
`receiverConfidence` field. Cross-file positive edges and resolution diagnostics
now expose separate dimensions:

| Field | Contract |
| --- | --- |
| `declarationResolution` | Target/declaration identity only. Positive edges are `EXACT`; diagnostics may be `EXACT`, `AMBIGUOUS`, `UNRESOLVED`, or `UNSUPPORTED`. |
| `receiverKind` | Source receiver form, including `NAMED_FIELD`, `NAMED_LOCAL`, `NAMED_PARAMETER`, `STATIC_CLASS`, `CONSTRUCTOR`, `THIS`, and `UNQUALIFIED`. |
| `receiverPath` | Deterministic source access-site identity, not a runtime heap-object identity. |
| `fieldScope` | `INSTANCE`, `STATIC`, or `NOT_APPLICABLE`. |
| `instanceAuthority` | `UNKNOWN` for instance receivers; `NOT_APPLICABLE` for static-class and constructor boundaries. |
| `aliasAuthority` | `MAY` for instance receivers; `NOT_APPLICABLE` for static-class and constructor boundaries. |

The cross-file confidence score is derived from authority, not declaration
resolution:

- exact instance receiver: `declarationResolution = EXACT`,
  `instanceAuthority = UNKNOWN`, `aliasAuthority = MAY`, score `0.5`;
- static class or constructor: instance and alias authority are
  `NOT_APPLICABLE`, score `1.0` when analysis is complete;
- parse-incomplete target still caps the score as previously documented.

Therefore, exact FQN + method + arity resolution cannot launder an unknown
runtime instance or possible alias into stronger authority.

## 3. Changed task files

Production and tests:

- `graphify/extractors/java_data_flow.py`
- `graphify/extractors/resolution.py`
- `tests/test_java_cross_file_data_flow.py`
- `tests/test_java_cross_file_fixture_suite.py`
- `tests/test_java_cross_file_gvr_boundary.py`

Contracts and evidence:

- `docs/data-flow/GATE-2B-CROSS-FILE-IMPLEMENTATION-REPORT.md`
- `docs/data-flow/GATE-2B-REFERENCE-GAP-ANALYSIS.md`
- `docs/data-flow/GVR-SOURCE-EVIDENCE-CONTRACT.md`
- this report

The normal Gate 2 merge also integrated the approved Gate 2/v8 file set. Those
merged files were not reauthored as unrelated Gate 2B changes.

## 4. TDD evidence

### RED

Command:

```bash
.venv/bin/python -m pytest \
  tests/test_java_cross_file_data_flow.py::test_cross_file_instance_call_links_arg_param_return \
  tests/test_java_cross_file_data_flow.py::test_cross_file_static_call_has_no_instance_or_alias_question \
  tests/test_java_cross_file_data_flow.py::test_cross_file_constructor_argument_link \
  tests/test_java_cross_file_gvr_boundary.py::test_exact_resolution_never_upgrades_instance_or_alias_authority \
  -q
```

Result before production changes: **4 failed**. Every failure showed that
cross-file evidence still emitted `receiverConfidence` and did not expose the
new separated authority fields.

### GREEN

The same command after the production change: **4 passed**, 1 existing pytest
configuration warning.

Broader Gate 2B command:

```bash
.venv/bin/python -m pytest \
  tests/test_java_cross_file_data_flow.py \
  tests/test_java_cross_file_gvr_boundary.py \
  tests/test_java_cross_file_fixture_suite.py -q
```

Result: **36 passed**, 1 existing warning.

The fixture suite additionally asserts on every positive cross-file edge that:

- `receiverConfidence` is absent;
- `declarationResolution == EXACT`;
- all five receiver/field/instance/alias dimensions are present;
- `UNKNOWN` instance authority implies `MAY` alias authority and score `0.5`;
- non-instance boundaries use `NOT_APPLICABLE` consistently.

## 5. Required validation evidence

### Gate 2 A1-A7 regression suite

```bash
uv run --frozen pytest -q tests/test_java_data_flow.py
```

Result: **43 passed**, 1 existing warning. This includes approved A1-A7 field
authority regressions and parenthesized-field metadata preservation.

### Gate 2B portability, diagnostic, and adversarial suites

```bash
uv run --frozen pytest -q \
  tests/test_java_cross_file_data_flow.py \
  tests/test_java_cross_file_gvr_boundary.py \
  tests/test_java_cross_file_fixture_suite.py
```

Result: **36 passed**, 1 existing warning.

Covered behavior includes checkout-root portability, repo-relative collision
resistance, exact/ambiguous/unresolved/unsupported diagnostics, overload and
wildcard fail-closed behavior, parse-incomplete propagation, stale-target
removal, file-order independence, constant-return negative evidence, exact
instance authority non-upgrade, static-class handling, and constructor handling.

### Valid-fixture Java compilation

```bash
javac -version
# javac -d <scratch-case-output> <all files for each manifest case>
```

Compiler: `javac 25.0.3`.

All manifest cases compiled successfully:

- `A_same_package`: OK
- `B_explicit_import`: OK
- `C_cross_file_return`: OK
- `D_constant_return`: OK
- `E_multi_param`: OK
- `F_constructor`: OK
- `I_overload`: OK
- `J_wildcard`: OK
- `N_receiver_may`: OK

### Full pytest

```bash
uv run --frozen pytest -q
```

Result: **5174 passed, 72 skipped, 4 warnings** in 96.75 seconds.
The warnings were pre-existing pytest configuration, one Python escape-sequence
warning, and two expected semantic-cache scope warnings.

### Ruff

```bash
uv run --frozen ruff check graphify tests
```

Result: **PASS**, `All checks passed!`.

### Truthful Pyright comparison

Full-project Pyright is not clean and is not reported as clean. The comparison
used the same Pyright installation and Python interpreter for the integrated
pre-migration base and the GREEN implementation:

```bash
uv run --frozen pyright \
  --pythonpath /sharedssd/git/graphify/.venv/bin/python
```

- integrated base `2ceee6b86edb64a5a302685110f58a3df54956c1`:
  **569 errors, 5 warnings**;
- GREEN `9b21e758509af9712c4b3487b9243737b871f4d9`:
  **569 errors, 5 warnings**;
- net change: **0 errors, 0 warnings**.

Targeted comparison:

```bash
uv run --frozen pyright \
  --pythonpath /sharedssd/git/graphify/.venv/bin/python \
  graphify/extractors/java_data_flow.py \
  graphify/extractors/resolution.py \
  tests/test_java_cross_file_data_flow.py \
  tests/test_java_cross_file_gvr_boundary.py \
  tests/test_java_cross_file_fixture_suite.py
```

- integrated base: **24 errors, 0 warnings**;
- GREEN: **24 errors, 0 warnings**;
- all 24 diagnostics are pre-existing in earlier portions of
  `resolution.py`;
- changed Gate 2B ranges, `java_data_flow.py`, and the changed Gate 2B tests:
  **0 diagnostics**.

### Validated v8 comparison

At production SHA `9b21e758509af9712c4b3487b9243737b871f4d9`:

```bash
git merge-base HEAD 43d54acbfa9e731f7a592bb582c1f4b9d48ed73e
git rev-list --left-right --count \
  43d54acbfa9e731f7a592bb582c1f4b9d48ed73e...HEAD
```

- merge base:
  `43d54acbfa9e731f7a592bb582c1f4b9d48ed73e`;
- v8-only / branch-only commits: **0 / 46**;
- the feature branch is behind validated v8 by **0** commits.

### Diff check

```bash
git diff --check
```

Result: **PASS**.

### Graphify update

```bash
uv run --frozen graphify update .
```

Result: **PASS**.

- rebuilt graph: **60,470 nodes, 73,519 edges, 2,609 communities**;
- updated `graphify-out/graph.json`, `graph.html`, and `GRAPH_REPORT.md`;
- known optional-parser warnings: 7 SQL files without `tree_sitter_sql`, 1 DM
  file without `tree-sitter-dm`, and one recovered Luau fixture;
- community labels were mechanically hub-renamed because the community set
  changed; no LLM labeling was requested or required.

## 6. Architectural boundary and non-goals

Preserved:

- exact FQN + owner + method + arity matching;
- fail-closed overload and wildcard ambiguity;
- no second Java resolver;
- no additional Java parse pass;
- no positive edge from unresolved/ambiguous/unsupported attempts;
- public diagnostic nodes for attempted boundaries;
- no GVR verdict emission;
- no transitive closure;
- no runtime heap or alias proof.

Not started:

- Gate 3 traversal;
- same-instance bridging;
- interface/inheritance dispatch;
- framework, runtime, deployment, persistence, or cross-service modeling.

## 7. Delivery status

The validated code commits were pushed without force to the non-protected
branch:

```bash
git push fork feature/java-cross-file-data-flow-v8
```

Result:

```text
c6b56a7..9b21e75  feature/java-cross-file-data-flow-v8 -> feature/java-cross-file-data-flow-v8
```

The documentation/report commit is pushed to the same branch after this report
is committed. No pull request or protected-branch write was requested or
performed.
