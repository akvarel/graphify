"""Runtime-observability anchor extraction for JS/TS static log callsites.

Covers the first observability-anchor slice: dedicated ``observability_anchor``
nodes (``anchor_kind`` LOG_TEMPLATE / DYNAMIC_LOG_CALLSITE) with canonical
templates and SHA-256 fingerprints for statically recoverable messages,
connected to their enclosing symbol via ``emits_log_template`` /
``has_dynamic_log_callsite`` edges. Also verifies graph build/export round-trip
and incremental reindex deletion.
"""
from __future__ import annotations

import hashlib
import json

import networkx as nx

from graphify.build import build, build_merge
from graphify.extract import extract_js
from graphify.extractors.observability import (
    ANCHOR_KIND_DYNAMIC_CALLSITE,
    ANCHOR_KIND_LOG_TEMPLATE,
    CANONICALIZATION_VERSION,
    classify_log_callsite,
    extract_log_message,
)
from graphify.validate import validate_extraction


def _anchors(result: dict) -> list[dict]:
    return [n for n in result.get("nodes", []) if n.get("type") == "observability_anchor"]


def _anchor_edges(result: dict, relation: str | None = None) -> list[dict]:
    rels = {"emits_log_template", "has_dynamic_log_callsite"}
    return [e for e in result.get("edges", []) if e["relation"] in rels and (relation is None or e["relation"] == relation)]


def _write_js(tmp_path, name: str, src: str) -> None:
    p = tmp_path / name
    p.write_text(src, encoding="utf-8")
    return p


# ── classification helpers (pure) ────────────────────────────────────────────

def _first_call(src: str, lang: str = "javascript"):
    """Parse a JS/TS snippet and return the first call_expression node."""
    import importlib
    from tree_sitter import Language, Parser

    if lang == "typescript":
        mod = importlib.import_module("tree_sitter_typescript")
        language = Language(mod.language_typescript())
    else:
        mod = importlib.import_module("tree_sitter_javascript")
        language = Language(mod.language())
    source = src.encode("utf-8")
    root = Parser(language).parse(source).root_node

    def find(n):
        if n.type == "call_expression":
            return n
        for c in n.children:
            found = find(c)
            if found is not None:
                return found
        return None

    return find(root), source


class TestClassifyLogCallsite:
    def test_console_level_methods_recognized(self):
        for method in ("debug", "info", "warn", "error"):
            node, source = _first_call(f"console.{method}('x');")
            assert classify_log_callsite(node, source) == ("console", method)

    def test_console_log_out_of_scope(self):
        node, source = _first_call("console.log('x');")
        assert classify_log_callsite(node, source) is None

    def test_logger_receivers(self):
        for recv in ("logger", "log", "Logger", "this.logger", "ctx.logger"):
            node, source = _first_call(f"{recv}.info('x');")
            assert classify_log_callsite(node, source) == ("logger", "info")

    def test_arbitrary_receiver_not_a_logger(self):
        node, source = _first_call("client.info('x');")
        assert classify_log_callsite(node, source) is None

    def test_loki_receivers(self):
        for recv in ("loki", "lokiClient", "LokiClient", "loki_client", "LOKI"):
            node, source = _first_call(f"{recv}.log({{ message: 'x' }});")
            assert classify_log_callsite(node, source) == ("bugzero_loki", "log")
            node, source = _first_call(f"{recv}.push([{{ message: 'x' }}]);")
            assert classify_log_callsite(node, source) == ("bugzero_loki", "push")

    def test_plain_function_call_not_logging(self):
        node, source = _first_call("doSomething('x');")
        assert classify_log_callsite(node, source) is None


class TestMessageRecovery:
    def test_static_string(self):
        node, source = _first_call("console.info('job started');")
        assert extract_log_message(node, source, "console") == ("static", "job started")

    def test_template_literal_substitutions_become_arg(self):
        node, source = _first_call("console.warn(`user ${id} ready`);")
        assert extract_log_message(node, source, "console") == ("static", "user <arg> ready")

    def test_concatenation_is_dynamic(self):
        node, source = _first_call('console.error("oops " + err.message);')
        assert extract_log_message(node, source, "console") == ("dynamic", None)

    def test_identifier_is_dynamic(self):
        node, source = _first_call("console.info(msg);")
        assert extract_log_message(node, source, "console") == ("dynamic", None)

    def test_tagged_template_is_dynamic(self):
        node, source = _first_call("console.info(chalk`bold text`);")
        assert extract_log_message(node, source, "console") == ("dynamic", None)

    def test_no_arguments_not_anchored(self):
        node, source = _first_call("console.info();")
        assert extract_log_message(node, source, "console") is None

    def test_loki_object_message_literal(self):
        node, source = _first_call(
            "loki.log({ level: 'info', message: 'pushed', labels: {} });"
        )
        assert extract_log_message(node, source, "bugzero_loki") == ("static", "pushed")

    def test_loki_push_array_joins_static_messages(self):
        node, source = _first_call(
            "loki.push([{ level: 'info', message: 'a' }, { level: 'warn', message: 'b' }]);"
        )
        assert extract_log_message(node, source, "bugzero_loki") == ("static", "a | b")

    def test_loki_computed_message_is_dynamic(self):
        node, source = _first_call("loki.log(entry('info', msg));")
        assert extract_log_message(node, source, "bugzero_loki") == ("dynamic", None)

    def test_loki_missing_message_key_is_dynamic(self):
        node, source = _first_call("loki.log({ level: 'info' });")
        assert extract_log_message(node, source, "bugzero_loki") == ("dynamic", None)

    def test_loki_message_interpolation(self):
        node, source = _first_call("loki.log({ message: `run ${id} done` });")
        assert extract_log_message(node, source, "bugzero_loki") == ("static", "run <arg> done")


# ── extraction end to end ────────────────────────────────────────────────────

def test_extract_static_console_template_node_shape(tmp_path):
    f = _write_js(tmp_path, "app.js", 'function go() {\n  console.info("job started");\n}\n')
    result = extract_js(f)
    anchors = _anchors(result)
    assert len(anchors) == 1
    a = anchors[0]
    assert a["type"] == "observability_anchor"
    assert a["anchor_kind"] == ANCHOR_KIND_LOG_TEMPLATE
    assert a["canonicalization_version"] == CANONICALIZATION_VERSION
    assert a["canonical_template"] == "job started"
    assert a["sha256"] == hashlib.sha256(b"job started").hexdigest()
    assert a["source_file"] == str(f)
    assert a["source_location"] == "L2"
    md = a["metadata"]
    assert md["language"] == "javascript"
    assert md["framework"] == "console"
    assert md["method"] == "info"
    go_nid = [n["id"] for n in result["nodes"] if n["label"] == "go()"][0]
    assert md["enclosing_symbol"] == go_nid
    # validation: the extraction is schema-valid (no imports -> no dangling refs)
    assert validate_extraction(result) == []


def test_extract_template_literal_interpolation(tmp_path):
    f = _write_js(tmp_path, "app.ts", "export function go(id: string) {\n  console.warn(`user ${id} ready`);\n}\n")
    result = extract_js(f)
    anchors = _anchors(result)
    assert len(anchors) == 1
    a = anchors[0]
    assert a["anchor_kind"] == ANCHOR_KIND_LOG_TEMPLATE
    assert a["canonical_template"] == "user <arg> ready"
    assert a["metadata"]["language"] == "typescript"
    assert a["sha256"] == hashlib.sha256(b"user <arg> ready").hexdigest()


def test_extract_dynamic_concatenation_never_guessed(tmp_path):
    f = _write_js(tmp_path, "app.js", 'function go(err) {\n  console.error("oops " + err.message);\n}\n')
    result = extract_js(f)
    anchors = _anchors(result)
    assert len(anchors) == 1
    a = anchors[0]
    assert a["anchor_kind"] == ANCHOR_KIND_DYNAMIC_CALLSITE
    assert "canonical_template" not in a
    assert "sha256" not in a
    assert a["label"] == "dynamic_log_callsite"
    edges = _anchor_edges(result)
    assert edges and edges[0]["relation"] == "has_dynamic_log_callsite"


def test_extract_logger_and_loki_callsites(tmp_path):
    src = (
        "const loki = new LokiClient({});\n"
        "export function run() {\n"
        '  logger.debug("booted");\n'
        '  this.logger.info("nested");\n'
        '  loki.log({ level: "info", message: "pushed", labels: {} });\n'
        '  loki.push([{ level: "info", message: "a" }, { level: "warn", message: "b" }]);\n'
        "}\n"
    )
    f = _write_js(tmp_path, "run.ts", src)
    result = extract_js(f)
    anchors = _anchors(result)
    by_fw = {}
    for a in anchors:
        by_fw.setdefault(a["metadata"]["framework"], []).append(a)
    assert sorted(by_fw) == ["bugzero_loki", "logger"]
    assert [a["canonical_template"] for a in by_fw["logger"]] == ["booted", "nested"]
    loki_templates = sorted(a["canonical_template"] for a in by_fw["bugzero_loki"])
    assert loki_templates == ["a | b", "pushed"]


def test_extract_duplicate_templates_are_distinct_callsites(tmp_path):
    f = _write_js(
        tmp_path, "dup.js",
        "function a() {\n"
        '  console.info("dup");\n'
        '  console.info("dup");\n'
        "}\n"
        'console.info("dup");\n',
    )
    result = extract_js(f)
    anchors = _anchors(result)
    # three separate callsites, three nodes — never collapsed by template
    assert len(anchors) == 3
    ids = {a["id"] for a in anchors}
    assert len(ids) == 3
    assert {a["source_location"] for a in anchors} == {"L2", "L3", "L5"}
    # every static anchor keeps its full fingerprint
    assert all(a["canonical_template"] == "dup" for a in anchors)


def test_extract_multiple_callsites_same_line_distinct(tmp_path):
    f = _write_js(
        tmp_path, "oneline.js",
        'function a() {\n'
        '  console.info("one"); console.warn("two");\n'
        "}\n",
    )
    result = extract_js(f)
    anchors = _anchors(result)
    assert len(anchors) == 2
    ids = [a["id"] for a in anchors]
    assert len(set(ids)) == 2, "same-line callsites must not collapse"
    assert {a["canonical_template"] for a in anchors} == {"one", "two"}
    assert {a["source_location"] for a in anchors} == {"L2"}


def test_extract_enclosing_symbol_edges(tmp_path):
    src = (
        "class Service {\n"
        "  run() {\n"
        '    console.debug("in method");\n'
        "  }\n"
        "}\n"
        "function top() {\n"
        '  console.info("in function");\n'
        "}\n"
        'console.warn("module scope");\n'
    )
    f = _write_js(tmp_path, "svc.ts", src)
    result = extract_js(f)
    by_label = {n["label"]: n["id"] for n in result["nodes"]}
    file_nid = by_label["svc.ts"]
    run_nid = by_label[".run()"]
    top_nid = by_label["top()"]
    edges = {(e["source"], e["target"], e["relation"]) for e in _anchor_edges(result)}
    assert len(edges) == 3
    anchors_by_line = {a["source_location"]: a for a in _anchors(result)}
    assert (run_nid, anchors_by_line["L3"]["id"], "emits_log_template") in edges
    assert (top_nid, anchors_by_line["L7"]["id"], "emits_log_template") in edges
    assert (file_nid, anchors_by_line["L9"]["id"], "emits_log_template") in edges
    # module-scope anchor metadata names the file node as its enclosing symbol
    assert anchors_by_line["L9"]["metadata"]["enclosing_symbol"] == file_nid


def test_extract_dynamic_calls_in_method_connect_to_method(tmp_path):
    f = _write_js(
        tmp_path, "dyn.ts",
        "class S {\n"
        "  fail(err: Error) {\n"
        '    console.error("boom " + err.message);\n'
        "  }\n"
        "}\n",
    )
    result = extract_js(f)
    anchors = _anchors(result)
    assert len(anchors) == 1
    a = anchors[0]
    assert a["anchor_kind"] == ANCHOR_KIND_DYNAMIC_CALLSITE
    assert a["metadata"]["enclosing_symbol_label"] == ".fail()"


def test_extract_out_of_scope_patterns_not_anchored(tmp_path):
    f = _write_js(
        tmp_path, "scope.js",
        'function go() {\n'
        '  console.log("plain log");\n'
        '  logger.log("plain logger log");\n'
        '  console.info();\n'
        "}\n"
        'client.info("not a logger");\n',
    )
    result = extract_js(f)
    assert _anchors(result) == []


def test_extract_ids_deterministic_across_runs(tmp_path):
    src = (
        "function a() {\n"
        '  console.info("dup");\n'
        '  console.warn(`x ${n}`);\n'
        "}\n"
    )
    f = _write_js(tmp_path, "det.js", src)
    r1 = extract_js(f)
    r2 = extract_js(f)
    ids1 = {a["id"] for a in _anchors(r1)}
    ids2 = {a["id"] for a in _anchors(r2)}
    assert ids1 == ids2
    assert len(ids1) == 2


def test_extract_container_formats_not_anchored(tmp_path):
    from graphify.extract import extract_astro, extract_svelte, extract_vue

    svelte = _write_js(tmp_path, "c.svelte", '<script>function go() { console.info("x"); }</script>')
    vue = _write_js(tmp_path, "c.vue", '<script setup lang="ts">function go() { console.info("x"); }</script>')
    astro = _write_js(tmp_path, "c.astro", "---\nfunction go() { console.info('x'); }\n---")
    assert _anchors(extract_svelte(svelte)) == []
    assert _anchors(extract_vue(vue)) == []
    assert _anchors(extract_astro(astro)) == []


def test_batch_extract_reconciles_enclosing_symbol_metadata(tmp_path):
    """extract() remaps absolute-path-derived ids to repo-relative form; the
    anchor's enclosing_symbol metadata must be reconciled to the canonical id
    (its own edge's source), never a stale pre-remap value."""
    from graphify.extract import extract

    root = tmp_path / "corpus"
    root.mkdir()
    f = root / "svc.ts"
    f.write_text('export function go() {\n  console.info("hi");\n}\n', encoding="utf-8")
    result = extract([f], root=root)
    anchor = _anchors(result)[0]
    edge = [e for e in _anchor_edges(result, "emits_log_template")][0]
    node_ids = {n["id"] for n in result["nodes"]}
    assert anchor["metadata"]["enclosing_symbol"] == edge["source"]
    assert anchor["metadata"]["enclosing_symbol"] in node_ids
    assert anchor["metadata"]["enclosing_symbol_label"] == "go()"
    # the anchor id itself is repo-relative, not absolute-path-derived
    assert "/" not in anchor["id"] and "tmp_" not in anchor["id"]


# ── build / export round-trip ────────────────────────────────────────────────

def test_build_preserves_anchor_node_attributes(tmp_path):
    f = _write_js(tmp_path, "app.js", 'function go() {\n  console.info("job started");\n}\n')
    result = extract_js(f)
    G = build([result], dedup=False)
    anchor = _anchors(result)[0]
    attrs = G.nodes[anchor["id"]]
    assert attrs["type"] == "observability_anchor"
    assert attrs["anchor_kind"] == ANCHOR_KIND_LOG_TEMPLATE
    assert attrs["canonicalization_version"] == CANONICALIZATION_VERSION
    assert attrs["canonical_template"] == "job started"
    assert attrs["sha256"] == hashlib.sha256(b"job started").hexdigest()
    assert attrs["source_location"] == "L2"
    assert attrs["metadata"]["framework"] == "console"
    go_nid = [n["id"] for n in result["nodes"] if n["label"] == "go()"][0]
    assert G.has_edge(go_nid, anchor["id"])
    assert G.edges[go_nid, anchor["id"]]["relation"] == "emits_log_template"


def test_reindex_deletion_removes_stale_anchor(tmp_path):
    """Re-extracting a CHANGED file must REPLACE its prior anchors: an anchor
    whose callsite disappeared from the new version is gone, while unchanged
    files keep theirs."""
    root = tmp_path / "corpus"
    root.mkdir()
    graph_path = tmp_path / "graph.json"

    changed = root / "changed.ts"
    changed.write_text(
        'export function a() {\n  console.info("before");\n}\n', encoding="utf-8"
    )
    keep = root / "keep.ts"
    keep.write_text(
        'export function b() {\n  console.warn("kept");\n}\n', encoding="utf-8"
    )

    v1 = extract_js(changed)
    v1_keep = extract_js(keep)
    G0 = build([v1, v1_keep], dedup=False)
    graph_path.write_text(json.dumps(nx.node_link_data(G0, edges="edges")), encoding="utf-8")

    anchor_v1 = _anchors(v1)[0]
    keep_anchor = _anchors(v1_keep)[0]
    assert anchor_v1["canonical_template"] == "before"
    assert G0.has_node(anchor_v1["id"])

    # Edit changed.ts: the console.info callsite is deleted.
    changed.write_text(
        'export function a() {\n  return 1;\n}\n', encoding="utf-8"
    )
    v2 = extract_js(changed)
    assert _anchors(v2) == []
    G1 = build_merge([v2], graph_path, dedup=False, root=root)

    assert not G1.has_node(anchor_v1["id"]), "stale anchor must be dropped on reindex"
    assert not G1.has_edge(
        [n["id"] for n in v1["nodes"] if n["label"] == "a()"][0], anchor_v1["id"]
    ), "stale anchor edge must be dropped"
    assert G1.has_node(keep_anchor["id"]), "unchanged file's anchor must survive"


def test_reindex_edit_replaces_template_anchor(tmp_path):
    """Editing a static template yields a new anchor (new digest), and the old
    anchor disappears — no accumulation across incremental updates."""
    root = tmp_path / "corpus"
    root.mkdir()
    graph_path = tmp_path / "graph.json"

    f = root / "log.ts"
    f.write_text('export function a() {\n  console.info("v1 message");\n}\n', encoding="utf-8")
    v1 = extract_js(f)
    G0 = build([v1], dedup=False)
    graph_path.write_text(json.dumps(nx.node_link_data(G0, edges="edges")), encoding="utf-8")
    old_anchor = _anchors(v1)[0]
    assert G0.has_node(old_anchor["id"])

    f.write_text('export function a() {\n  console.info("v2 message");\n}\n', encoding="utf-8")
    v2 = extract_js(f)
    new_anchor = _anchors(v2)[0]
    assert new_anchor["id"] != old_anchor["id"]
    assert new_anchor["canonical_template"] == "v2 message"

    G1 = build_merge([v2], graph_path, dedup=False, root=root)
    assert not G1.has_node(old_anchor["id"]), "old template anchor must be replaced"
    assert G1.has_node(new_anchor["id"])
