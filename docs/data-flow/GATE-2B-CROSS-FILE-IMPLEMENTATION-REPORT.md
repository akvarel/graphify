# Gate 2B — Deterministic Cross-File Java Data Flow

## Implementation Report

- **Repository:** `akvarel/graphify` (OSS source-derived evidence layer)
- **Branch:** `feature/java-cross-file-data-flow-v8`
- **Starting SHA:** `d8b663f04092ec1d43eba8e027604bd833c1d957`
- **Final SHA:** see git log (this branch's HEAD)
- **Current upstream/v8 SHA:** `b14b52e94ec3d9840413d81777f4c134eac0a40d`
- **Merge-base (HEAD…upstream/v8):** `b14b52e94ec3d9840413d81777f4c134eac0a40d`
- **ahead/behind vs upstream/v8:** `32 ahead / 0 behind` (Gate 2 commits already integrated on this branch)

---

## 1. What was built

Gate 2 established deterministic **same-file** Java data-flow facts (PARAMETER,
LOCAL, FIELD, RETURN_VALUE and FLOWS_TO, PASSED_AS_ARGUMENT, RETURNED_AS,
READ_FROM, WRITTEN_TO, TRANSFORMED_BY). Interprocedural flow stopped at the file
boundary. Gate 2B closes the file boundary for the minimum useful path:

```
caller argument
→ cross-file resolved callee parameter
→ callee local/static flow
→ callee return
→ caller receiving value
```

Implemented deterministically and **without a second Java resolver**:

1. **Per-file extractor records cross-file call intents** (`java_data_flow.py`).
   When `resolve_call` cannot find an exact same-file candidate and the receiver
   resolves to a deterministic cross-file class (explicit import, same-package,
   or fully-qualified), the **existing** per-file analyzer emits a
   `cross_file_calls` record carrying:
   - resolved receiver FQN (`_receiver_fqn`, reusing the file's own package +
     import facts),
   - method name, argument count,
   - caller-side argument value nodes (with their positional index),
   - the caller-side return sink (captured at `local_variable_declaration`,
     `assignment_expression`, and `return_statement` sites),
   - `receiverConfidence` (PROVEN for static/`this`/constructor receivers, MAY
     for instance receivers),
   - source `file` + `location`.

2. **A new repository-wide pass** (`_resolve_cross_file_java_data_flow` in
   `resolution.py`, wired into `extract.py` **before** the id-remap passes)
   links those records to exact callees:
   - builds a global **method/constructor index** from emitted `data_value`
     nodes grouped by `(package, owner symbol)`. The extractor stamps `package`
     on every data-value node, `param_index` on parameters, and
     `param_return_deps` + param value-node ids on the return node. The index is
     keyed by `(package, owner simple name, method name, arity)`.
   - matches each record by **exact** receiver FQN + method name + arity;
   - emits `PASSED_AS_ARGUMENT` (caller arg → callee param), `TRANSFORMED_BY`
     (only when the callee's own Gate 2 facts already prove
     param[i] → return), and `FLOWS_TO` (callee return → caller receiving
     value);
   - **fails closed** on ambiguity or an unresolved receiver.

No transitive closure, no path enumeration, and **no GVR verdicts** are emitted
by the source layer.

---

## 2. Architecture changes

| File | Change |
| --- | --- |
| `graphify/extractors/java_data_flow.py` | Parse `package` + simple-name→FQN imports + wildcard-import flag; stamp `package` on every data-value node and `param_index` on parameters; record cross-file call/constructor intents; capture caller-side return sinks; stamp `param_return_deps` / `param_value_ids` / `package` on the return node and method node; enrich (rather than duplicate) the generic method node so symbol/param metadata survives node dedup. |
| `graphify/extractors/resolution.py` | New `_resolve_cross_file_java_data_flow(per_file, paths, all_nodes, all_edges)` pass: global method/constructor index from `data_value` nodes; exact FQN+name+arity matching; emit `PASSED_AS_ARGUMENT` / `TRANSFORMED_BY` / `FLOWS_TO` with `provenance=CROSS_FILE`; propagate `receiverConfidence`, `analysisCompleteness`, and parse-incompleteness. |
| `graphify/extract.py` | Import + invoke the cross-file pass before the id-remap passes (so pre-remap value-node ids match, and the remap rewrites the added edges' endpoints together with the nodes). |

### Reused existing resolver components

- Same-file method/constructor resolution, value-node identity, `add_value` /
  `add_edge` (existing Gate 2 analyzer).
- The file's own `package_declaration` + `import_declaration` facts — the same
  import/package facts the resolution layer (`_resolve_cross_file_java_imports`,
  `_resolve_java_type_references`) uses. **No parallel resolver, second symbol
  graph, or private name matcher.**
- Gate 2's `param_return_deps` (bounded local dependency proof) is reused to
  decide `TRANSFORMED_BY`; it is not recomputed by a second analyzer.
- `sanitize_metadata`, `_make_id`, `_file_stem` existing utilities.

---

## 3. Cross-file resolution algorithm

1. **Emit phase (per file, existing analyzer):**
   - `_receiver_fqn(target_cls)`: FQN if the class name contains `.`; explicit
     import FQN if present; same-package FQN when the file has **no** wildcard
     imports; `AMBIGUOUS`/`UNRESOLVED` otherwise (fail closed).
   - `receiverConfidence`: PROVEN for `this`/unqualified/static-class/constructor
     receivers, MAY for instance receivers.
   - Caller-side return sink captured at the assignment/local-declaration/return
     sites.

2. **Index phase (repository pass):**
   - Group all emitted `data_value` nodes by `(package, owner symbol)`.
   - Parameters ordered by `param_index`; return node + `param_return_deps`
     from the return node's stamped metadata; `incomplete` set when any node
     `confidence_score < 1.0` (parse-incomplete target).
   - Key: `(package, owner simple name, method name, arity)`.

3. **Match phase:**
   - For each recorded cross-file call, look up candidates by exact
     `(package, owner, method, arity)`.
   - Exactly one candidate → emit edges; zero or multiple → fail closed.

4. **Emission phase:**
   - `arg[i] --PASSED_AS_ARGUMENT--> param[i]`
   - `arg[i] --TRANSFORMED_BY--> callee.return` iff `i ∈ callee.param_return_deps`
   - `callee.return --FLOWS_TO--> caller receiving value`
   - Every edge carries `metadata.provenance = "CROSS_FILE"`,
     `receiver`, `receiverConfidence`, `callee`, `calleeSymbol`,
     `analysisCompleteness`, `argumentIndex` (where relevant), `source_file`,
     `source_location`.

---

## 4. Supported cases (deterministic)

1. Same-package class resolution without an explicit import (A).
2. Explicit imports (B).
3. Fully-qualified references.
4. Cross-file constructor argument mapping by exact target and position (F).
5. Cross-file return propagation using existing callee local facts (C).
6. Static class receivers → PROVEN (N, static-import-like when the static class
   is named).
7. `TRANSFORMED_BY` only when the callee's own bounded facts prove
   param → return (D, E).

## 5. Unsupported / ambiguous cases (fail closed)

- Wildcard imports with multiple candidates (J): a bare simple-name receiver
  when the file has a wildcard import is `AMBIGUOUS` → no edge.
- Same-arity overloads without sufficient type evidence (I): no edge.
- Nested duplicate simple class names (H): nested receiver FQNs are not
  package-qualified → fail closed (no fabricated cross-package edge; documented
  false negative).
- Default-package cross-file calls: a bare receiver in the default package is
  `UNRESOLVED` → no edge.
- Inheritance/interface dispatch with multiple implementations: not implemented
  in this gate (future).
- Generic raw-type ambiguity, reflection/dynamic invocation: not resolved.
- `analysisCompleteness` = `PARTIAL` propagates from a parse-incomplete target
  and caps edge `confidence_score` at 0.8 (M).

---

## 6. GVR boundary

The source layer emits **source-derived facts + evidence + epistemic metadata**
only. It does **not** issue GVR verification verdicts. `provenance = CROSS_FILE`
(or `STATIC_AST`) + `receiverConfidence` + `analysisCompleteness` +
`confidence_score` describe the strength and coverage of the *static evidence*;
they are **not** `GVR VERIFIED`. See
`docs/data-flow/GVR-SOURCE-EVIDENCE-CONTRACT.md`.

`receiverConfidence = MAY` is preserved across linkage; it is never upgraded to
definite same-instance flow merely because edges connect.

---

## 7. Tests

New public-boundary test files (run through `extract(..., cache_root=...)`):

- `tests/test_java_cross_file_data_flow.py` — 14 tests covering fixture cases
  A (same-package), B (explicit import), C (return flow), D (constant-return
  negative), E (multi-parameter dependency), F (constructor mapping), G (same
  simple name in different packages), H (nested duplicate names), I
  (same-arity overload fails closed), J (wildcard ambiguity fails closed),
  K (file-order independence), L (checkout-root portability), M (parse-incomplete
  target → PARTIAL), N (receiver MAY preserved), O (removed target → no stale
  edge).
- `tests/test_java_cross_file_gvr_boundary.py` — 4 tests proving the source
  layer preserves provenance/receiver confidence, exposes exact source+target
  identity, never emits a GVR verdict state, and never claims global
  completeness from an exact local fact.

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_java_type_resolution.py -q
.venv/bin/python -m pytest tests/test_java_cross_file_data_flow.py \
                      tests/test_java_cross_file_gvr_boundary.py -q
.venv/bin/python -m pytest -q          # full suite
.venv/bin/ruff check graphify tests
.venv/bin/pyright
git diff --check
```

### Independent falsification (Section 17/18)

An adversarial review was performed over cases beyond the primary fixtures:
duplicate package/class names, nested classes, wildcard imports, same-arity
overloads, unrelated same-named methods, parse-incomplete targets, and removed
targets. See the adversarial fixtures in `tests/test_java_cross_file_data_flow.py`
(J, H, I, M, O). In addition, all nine fixture cases were compiled with
`javac 25.0.3` (`A`, `B`, `C`, `D`, `E`, `F`, `I`, `J`, `N` all `OK`), proving
the fixtures are valid Java and that the resolver's fail-closed decisions
(overload ambiguity in `I`, wildcard ambiguity in `J`) reflect genuine Java
semantics rather than malformed input. The verification also relies on
manually-defined expected exact targets at the public extraction boundary,
plus explicit negative assertions.

---

## 8. Performance baseline

Measured on the dedicated fixture corpus (19 Java files) on the build machine.
Gate 2 (before) vs Gate 2B (after), both via `extract(..., cache_root=...)`:

| Metric | Gate 2 (before) | Gate 2B (after) |
| --- | --- | --- |
| Full extraction time | ~0.061 s | ~0.053 s (noise) |
| Graph node count | 136 | 136 |
| Graph edge count | 76 | 95 |
| `data_value` count | 56 | 56 |
| Cross-file edge count | 0 | 19 |
| Exact cross-file resolutions | 0 | 8 |
| Ambiguous cross-file candidates | 0 | 1 (I_overload) |
| Unresolved cross-file candidates | 0 | 0 |

The cross-file pass adds ~19 edges with negligible measurable cost
(`O(records × index-lookup)`); node and `data_value` counts are unchanged. The
index is built once from the already-emitted `data_value` nodes, so no new
parse pass or persistent store was introduced. No optimisation was performed
before measuring.

## 9. Known limitations

- Value `data_value` node ids embed the checkout-root path slug (pre-existing
  Gate 2 behavior); cross-file edges reference the exact same node ids, so the
  graph is internally coherent, and `L` (portability) is validated at the
  relationship level.
- Default-package cross-file linkage and nested-class cross-file linkage are
  false negatives (fail closed) by design.
- Static imports of individual members are supported only when the target class
  is explicitly named (Java requires the class name for static method calls).
- Constructor returns are `void` and carry no `FLOWS_TO` (only parameter
  mapping).
- No transitive closure, no Gate 3 traversal.

## 10. Recommendation for Gate 3

Gate 3 can traverse the deterministic cross-file edges emitted here (exact
identity, receiver confidence, and analysis completeness already attached).
Ambiguity and incompleteness are machine-visible on the edges, so a future
traversal can stop or degrade at ambiguous/incomplete boundaries rather than
over-claim.
