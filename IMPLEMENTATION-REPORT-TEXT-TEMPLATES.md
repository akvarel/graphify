# IMPLEMENTATION-REPORT — Text Template Extraction

## Summary

Graphify now emits first-class `type=text_template` nodes for useful source string templates in JavaScript/TypeScript, Python, Java, and PHP. The slice is integrated into the existing generic tree-sitter extractor and runs after symbol discovery/call attribution so template nodes can be attached to the exact enclosing owner.

## Node shape

Each text template node includes:

- `type: "text_template"`
- `template_kind: "CONSTANT" | "TEMPLATE"`
- `canonical_template` with interpolation replaced by `<arg>`
- `canonicalization_version` using the same version string as runtime observability anchors
- versioned SHA-256 fingerprint over `canonicalization_version + "\n" + canonical_template`
- deterministic occurrence ID with file stem, digest prefix, source line, and per-line occurrence counter
- `source_file` and `source_location`
- metadata: `language`, `enclosing_symbol`, `enclosing_symbol_label`, and optional `bound_name`

Duplicate source occurrences are intentionally preserved as separate nodes even when their canonical template and fingerprint match.

## Edges

- `owner -> template` via `contains_text_template` for every emitted template.
- `owner -> template` via `defines_text` when the template is directly bound to a variable, field, or constant name.

## Filtering

The extractor excludes import/module specifiers, Python docstrings, strings inside recognized observability anchor log calls, empty/whitespace templates, very short punctuation-only strings, and likely secrets or credential-like values. Secret-looking values are skipped entirely and are not stored in raw form.

## Language coverage

- JS/TS: string literals, template literals, direct const/let bindings, interpolation as `<arg>`.
- Python: string constants, f-strings, direct assignments, docstring suppression.
- Java: string fields/local variables, concatenated templates with dynamic segments as `<arg>`, SLF4J log de-duplication.
- PHP: constants, variable-bound strings, interpolated strings, PSR/Laravel/error_log de-duplication.

## Validation

RED was committed first with failing tests in `tests/test_text_templates.py`. GREEN implementation then passed:

```bash
.venv/bin/pytest -q tests/test_text_templates.py
# 4 passed

.venv/bin/pytest -q tests/test_text_templates.py tests/test_observability_anchors.py
# 44 passed
```
