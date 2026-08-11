# IMPLEMENTATION-REPORT — Runtime-Observability Anchors (slice 1)

Branch: `feature/runtime-observability-anchors` (created from `upstream/v8`)
Scope: TypeScript/JavaScript **static log callsites only** (plain `.js/.jsx/.mjs/.cjs/.ts/.tsx/.mts/.cts`).

## Summary

Graphify now extracts dedicated **`observability_anchor`** nodes for recognized
logging callsites in plain JS/TS files, connected to their enclosing symbol.
The extraction reuses the existing single graph (no second graph), rides the
existing per-file AST pipeline, cache, id-remap, build/merge, and export
machinery unchanged, and stays schema-compatible (`file_type` values, edge
validation, AST-tier provenance).

## Node shape

Emitted per recognized logging **callsite** (never per template):

```json
{
  "id": "<stem>_observability_log_template_<sha12>_<line>",
  "label": "log_template <canonical template>",
  "file_type": "code",
  "type": "observability_anchor",
  "anchor_kind": "LOG_TEMPLATE",
  "canonicalization_version": "runtime-code-canonicalization/v1",
  "source_file": "<repo-relative path>",
  "source_location": "L<line>",
  "canonical_template": "<canonical template>",
  "sha256": "<hex SHA-256 of canonical_template>",
  "metadata": {
    "language": "typescript" | "javascript",
    "framework": "console" | "logger" | "bugzero_loki",
    "method": "debug" | "info" | "warn" | "error" | "log" | "push",
    "enclosing_symbol": "<canonical nid of enclosing function/method/file>",
    "enclosing_symbol_label": "<label of the enclosing symbol>"
  }
}
```

Dynamic callsites omit `canonical_template`/`sha256` and use
`anchor_kind = "DYNAMIC_LOG_CALLSITE"` with label `dynamic_log_callsite`.

## Edges

- `LOG_TEMPLATE` anchors: enclosing symbol `--emits_log_template-->` anchor
  (`confidence: EXTRACTED`, `source_file`/`source_location` stamped).
- `DYNAMIC_LOG_CALLSITE` anchors: enclosing symbol
  `--has_dynamic_log_callsite-->` anchor.

Enclosing symbol resolution: the call-walk inside `_extract_generic` already
tracks the exact `caller_nid` for every walked body (functions, methods,
const-assigned arrows, CJS/prototype functions, `this.X = fn` methods,
class-field arrows, #1630 inline closures, class-field initializers), so anchors
inside function bodies are attributed precisely. Module-scope logging calls are
attributed to the file node via a boundary-respecting scan (same
`_JS_SCOPE_BOUNDARY` rule the existing module-dispatch scan uses, so bodies are
never double-attributed).

## Frameworks and message recovery (never guessed)

- **console**: `console.debug/info/warn/error(...)` — first argument.
- **logger**: member calls `<recv>.debug/info/warn/error(...)` whose receiver's
  final identifier segment is `logger` or `log` (case-insensitive), e.g.
  pino/winston `logger.info(...)`, NestJS `this.logger.warn(...)`.
- **bugzero_loki** (BugZero `LokiClient`): `<loki>.log({ message: ... })` and
  `<loki>.push([{ message: ... }, ...])` where the receiver's final identifier
  segment starts with `loki` (`loki`, `lokiClient`, `LokiClient`,
  `loki_client`). Recovered from the object literal's `message` value; the
  array form joins each element's static message with `" | "` and degrades to
  dynamic when any element is not statically recoverable.

Message canonicalization:
- Plain string literal → raw `string_content` text.
- Untagged template literal → literal fragments verbatim, every `${...}`
  substitution becomes `<arg>` (`` `user ${id} ready` `` → `user <arg> ready`).
- Tagged template, `+` concatenation, call/identifier/member expressions and
  any other computed message → `DYNAMIC_LOG_CALLSITE` (no template is invented).
- Calls with no message-bearing argument are not anchored.

Out of scope for this slice (documented in code): `console.log`,
`logger.log`, container formats (Svelte/Astro/Vue — their call-walk differs),
and every non-JS/TS language.

## Node IDs (deterministic, no callsite collapsing)

`make_id(<file stem>, "observability", ("log_template" | "dynamic_log_callsite"),
[<first 12 hex of sha256(canonical_template)>], <line>)`, with a per-line
counter suffix appended only to disambiguate same-line collisions. Within a
revision IDs are fully deterministic; separate callsites (including duplicate
templates at different lines, and two calls on the same line) always get
distinct IDs. Editing a call on another line never renumbers an anchor.

`extract()`'s id-remap passes rewrite the anchor id and its edge endpoints to
the canonical repo-relative form exactly like every other AST node, and a final
reconciliation step re-derives each anchor's `metadata.enclosing_symbol` (and
label) from its own canonical edge source, so the metadata can never hold a
stale pre-remap id.

## Files changed

- `graphify/extractors/observability.py` (new) — pure classification /
  canonicalization helpers: `classify_log_callsite`, `extract_log_message`,
  `canonicalize_log_message`, `sha256_hex`, `shorten_anchor_label`, plus the
  canonicalization-version constant and anchor-kind constants.
- `graphify/extractors/engine.py` — `_extract_generic(..., emit_observability_anchors=False)`;
  the `_emit_obs_anchor` closure; a hook at the top of `walk_calls`'s
  `call_types` branch; the module-scope scan next to `_scan_js_module_dispatch`;
  `nid_to_label` for caller labels.
- `graphify/extract.py` — `extract_js` enables the flag; the final
  anchor-metadata reconciliation loop in `extract()`.
- `tests/test_observability_anchors.py` (new) — 33 focused tests (see below).

## Tests

`tests/test_observability_anchors.py` (all pass, 33 tests):

- classification: console level methods, console.log out of scope, logger
  receiver variants, arbitrary receivers rejected, Loki receiver variants,
  plain calls rejected;
- message recovery: static strings, template-literal `<arg>` substitution,
  concatenation → dynamic, identifier → dynamic, tagged template → dynamic,
  no-args → not anchored, Loki object literal, Loki push-array join, Loki
  computed → dynamic, Loki missing key → dynamic, Loki interpolation;
- extraction end-to-end: node shape (type/anchor_kind/canonicalization_version/
  canonical_template/sha256/source_file/source_location/metadata), static
  console templates, interpolation, dynamic concatenation never guessed,
  logger + Loki callsites, duplicate templates as distinct callsites, multiple
  callsites on one line stay distinct, enclosing-symbol edges (method,
  function, module-scope file node), dynamic calls in methods, out-of-scope
  patterns not anchored, deterministic IDs across runs, container formats not
  anchored, batch-extract metadata reconciliation;
- build/export round-trip preserves anchor attributes and the
  `emits_log_template` edge;
- reindex deletion: re-extracting a changed file drops its stale anchor
  (node + edge) while an unchanged file's anchor survives;
- reindex edit: a changed template yields a new anchor id and the old one is
  replaced, no accumulation.

Broader validation:

- Focused extraction/build/export/CLI/language suites: 938 passed.
- Full test suite (this repo, `tests/`): **4336 passed, 1 skipped**; the only
  failure is `tests/test_ollama.py` backend-detection, which also fails on the
  clean baseline in this environment (machine has DeepSeek/other API keys set;
  verified by stashing the change) — unrelated to this work.
- `ruff check` (repo lint policy: E9/F63/F7/F82) passes on all changed files.
- `graphify update .` (per AGENTS.md, AST-only, no API cost) rebuilt the
  corpus graph: the fixture `tests/fixtures/dynamic_import.ts` anchor
  (`logger.info('no dynamic imports here')`, L29) is present in
  `graphify-out/graph.json` with a canonical repo-relative id, a real
  existing `enclosing_symbol` node, and one `emits_log_template` edge.

## Limitations

- Only static first-message recovery; computed messages are anchored as
  `DYNAMIC_LOG_CALLSITE` without content.
- `console.log` / `logger.log` are deliberately not anchored yet (method sets
  per slice scope); `console.trace`/`table` and similar are not covered.
- Svelte/Astro/Vue script blocks and non-JS/TS languages are out of scope.
- BugZero `LokiClient` calls whose payload is built by a helper
  (`loki.log(entry("info", "job started"))`) are correctly dynamic — the
  message is only statically recoverable when the object/array literal is
  written inline.
- Module-scope logging inside inline (untracked) closures is not anchored,
  mirroring the existing call-graph coverage.

## Commit

Committed on `feature/runtime-observability-anchors` with marker
`AI-assisted: Jcode`. Not pushed.
