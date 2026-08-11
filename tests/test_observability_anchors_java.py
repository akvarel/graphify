"""Runtime-observability anchor extraction for Java SLF4J log callsites.

Java slice of the observability-anchor contract: dedicated
``observability_anchor`` nodes (``anchor_kind`` LOG_TEMPLATE /
DYNAMIC_LOG_CALLSITE) with canonical templates and SHA-256 fingerprints for
statically recoverable SLF4J messages, connected to their enclosing Java
method / constructor / class via ``emits_log_template`` /
``has_dynamic_log_callsite`` edges.

Deliberately conservative (never guessed):

- Only plain ``log``/``logger`` receivers (final segment, any case), including
  ``this.logger`` and ``Class.log`` static field access, with the SLF4J level
  methods ``debug/info/warn/error``. A computed receiver (``getLogger()``), a
  different receiver (``client.info``), or ``log.trace`` is not anchored.
- A static Java string-literal first argument is a LOG_TEMPLATE; every SLF4J
  ``{}`` placeholder canonicalizes to ``<arg>``. A ``+`` concatenation, an
  identifier, a call, or any other computed message is DYNAMIC_LOG_CALLSITE.
- Whitespace collapses exactly like the shared contract
  (``normalize_template_whitespace``) and the fingerprint digest is the same
  versioned material (``runtime-code-canonicalization/v1``), so the JS/TS and
  Java slices converge on identical canonicalization.
"""
from __future__ import annotations

import hashlib

import networkx as nx

from graphify.build import build
from graphify.extract import extract_java
from graphify.extractors.observability import (
    ANCHOR_KIND_DYNAMIC_CALLSITE,
    ANCHOR_KIND_LOG_TEMPLATE,
    CANONICALIZATION_VERSION,
    classify_log_callsite,
    extract_log_message,
    normalize_template_whitespace,
    sha256_hex,
)
from graphify.validate import validate_extraction


def _anchors(result: dict) -> list[dict]:
    return [n for n in result.get("nodes", []) if n.get("type") == "observability_anchor"]


def _anchor_edges(result: dict, relation: str | None = None) -> list[dict]:
    rels = {"emits_log_template", "has_dynamic_log_callsite"}
    return [
        e for e in result.get("edges", [])
        if e["relation"] in rels and (relation is None or e["relation"] == relation)
    ]


def _write_java(tmp_path, name: str, src: str) -> object:
    p = tmp_path / name
    p.write_text(src, encoding="utf-8")
    return p


def _fingerprint(template: str) -> str:
    """Versioned fingerprint material from the Gate 1 contract
    (contracts-v1.md section 7): sha256(version + "\\n" + canonical_template)."""
    return hashlib.sha256(f"{CANONICALIZATION_VERSION}\n{template}".encode("utf-8")).hexdigest()


# ── classification helpers (pure) ────────────────────────────────────────────

def _first_java_call(src: str):
    """Parse a Java snippet and return the first method_invocation node."""
    import importlib
    from tree_sitter import Language, Parser

    mod = importlib.import_module("tree_sitter_java")
    language = Language(mod.language())
    source = src.encode("utf-8")
    root = Parser(language).parse(source).root_node

    def find(n):
        if n.type == "method_invocation":
            return n
        for c in n.children:
            found = find(c)
            if found is not None:
                return found
        return None

    return find(root), source


class TestClassifyJavaLogCallsite:
    def test_log_level_methods_recognized(self):
        for method in ("debug", "info", "warn", "error"):
            node, source = _first_java_call(f"class T {{ void m() {{ log.{method}(\"x\"); }} }}")
            assert classify_log_callsite(node, source) == ("slf4j", method)

    def test_logger_receivers_case_insensitive(self):
        for recv in ("log", "logger", "LOG", "LOGGER", "Logger"):
            node, source = _first_java_call(f"class T {{ void m() {{ {recv}.info(\"x\"); }} }}")
            assert classify_log_callsite(node, source) == ("slf4j", "info")

    def test_this_field_receivers(self):
        for recv in ("this.logger", "this.log"):
            node, source = _first_java_call(f"class T {{ void m() {{ {recv}.warn(\"x\"); }} }}")
            assert classify_log_callsite(node, source) == ("slf4j", "warn")

    def test_static_field_access_receiver(self):
        node, source = _first_java_call("class T { void m() { Service.LOG.info(\"x\"); } }")
        assert classify_log_callsite(node, source) == ("slf4j", "info")

    def test_computed_receiver_never_guessed(self):
        # getLogger(...) is a call result — the receiver is computed.
        node, source = _first_java_call(
            "class T { void m() { LoggerFactory.getLogger(T.class).info(\"x\"); } }"
        )
        assert classify_log_callsite(node, source) is None

    def test_trace_out_of_scope(self):
        node, source = _first_java_call("class T { void m() { log.trace(\"x\"); } }")
        assert classify_log_callsite(node, source) is None

    def test_arbitrary_receiver_not_a_logger(self):
        node, source = _first_java_call("class T { void m() { client.info(\"x\"); } }")
        assert classify_log_callsite(node, source) is None

    def test_plain_method_call_not_logging(self):
        node, source = _first_java_call("class T { void m() { doSomething(\"x\"); } }")
        assert classify_log_callsite(node, source) is None

    def test_object_creation_not_logging(self):
        node, source = _first_java_call("class T { void m() { new Foo(\"x\"); } }")
        # extract_log_message/classification are not applied to constructors.
        assert classify_log_callsite(node, source) is None


class TestJavaMessageRecovery:
    def test_static_string(self):
        node, source = _first_java_call('class T { void m() { log.info("job started"); } }')
        assert extract_log_message(node, source, "slf4j") == ("static", "job started")

    def test_slf4j_placeholder_becomes_arg(self):
        node, source = _first_java_call(
            'class T { void m() { log.info("Booking created. COR_ID: {}", id); } }'
        )
        assert extract_log_message(node, source, "slf4j") == (
            "static", "Booking created. COR_ID: <arg>",
        )

    def test_multiple_placeholders_each_become_arg(self):
        node, source = _first_java_call(
            'class T { void m() { log.info("a={} b={} c={}", a, b, c); } }'
        )
        assert extract_log_message(node, source, "slf4j") == ("static", "a=<arg> b=<arg> c=<arg>")

    def test_concatenation_is_dynamic(self):
        node, source = _first_java_call(
            'class T { void m() { log.error("oops " + ex.getMessage()); } }'
        )
        assert extract_log_message(node, source, "slf4j") == ("dynamic", None)

    def test_identifier_is_dynamic(self):
        node, source = _first_java_call("class T { void m() { log.info(msg); } }")
        assert extract_log_message(node, source, "slf4j") == ("dynamic", None)

    def test_call_result_is_dynamic(self):
        node, source = _first_java_call(
            "class T { void m() { log.info(buildMessage()); } }"
        )
        assert extract_log_message(node, source, "slf4j") == ("dynamic", None)

    def test_parenthesized_string_is_static(self):
        node, source = _first_java_call('class T { void m() { log.info(("x")); } }')
        assert extract_log_message(node, source, "slf4j") == ("static", "x")

    def test_no_arguments_not_anchored(self):
        node, source = _first_java_call("class T { void m() { log.info(); } }")
        assert extract_log_message(node, source, "slf4j") is None


# ── whitespace convergence (frozen invariant) ────────────────────────────────

class TestJavaWhitespaceConvergence:
    def test_leading_repeated_whitespace_collapses_in_static_string(self):
        node, source = _first_java_call('class T { void m() { log.info(" job   started "); } }')
        assert extract_log_message(node, source, "slf4j") == ("static", "job started")

    def test_placeholder_surrounded_by_whitespace_collapses(self):
        node, source = _first_java_call(
            'class T { void m() { log.warn("  user   {}   ready  ", id); } }'
        )
        assert extract_log_message(node, source, "slf4j") == ("static", "user <arg> ready")

    def test_normalize_helper_matches_runtime_rule(self):
        padded = "  Booking created. COR_ID: <arg>"
        assert normalize_template_whitespace(padded) == "Booking created. COR_ID: <arg>"
        assert normalize_template_whitespace(normalize_template_whitespace(padded)) == (
            normalize_template_whitespace(padded)
        )

    def test_fingerprint_digest_is_whitespace_invariant(self):
        assert sha256_hex(" job started ") == sha256_hex("job started")


# ── extraction end to end ────────────────────────────────────────────────────

def test_extract_avion_like_static_anchors(tmp_path):
    src = (
        "import org.slf4j.Logger;\n"
        "import org.slf4j.LoggerFactory;\n"
        "\n"
        "public class BookingService {\n"
        "    private static final Logger log = LoggerFactory.getLogger(BookingService.class);\n"
        "\n"
        "    public String createBooking(String id) {\n"
        '        log.info("Create booking started");\n'
        '        log.info("Booking created. COR_ID: {}", id);\n'
        "        return \"ok\";\n"
        "    }\n"
        "}\n"
    )
    f = _write_java(tmp_path, "BookingService.java", src)
    result = extract_java(f)
    assert validate_extraction(result) == []
    anchors = _anchors(result)
    assert len(anchors) == 2
    by_template = {a["canonical_template"]: a for a in anchors}
    assert set(by_template) == {"Create booking started", "Booking created. COR_ID: <arg>"}

    start = by_template["Create booking started"]
    assert start["anchor_kind"] == ANCHOR_KIND_LOG_TEMPLATE
    assert start["canonicalization_version"] == CANONICALIZATION_VERSION
    assert start["sha256"] == _fingerprint("Create booking started")
    assert start["source_file"] == str(f)
    assert start["source_location"] == "L8"
    md = start["metadata"]
    assert md["language"] == "java"
    assert md["framework"] == "slf4j"
    assert md["method"] == "info"
    create_nid = [n["id"] for n in result["nodes"] if n["label"] == ".createBooking()"][0]
    assert md["enclosing_symbol"] == create_nid
    assert md["enclosing_symbol_label"] == ".createBooking()"

    created = by_template["Booking created. COR_ID: <arg>"]
    assert created["anchor_kind"] == ANCHOR_KIND_LOG_TEMPLATE
    assert created["sha256"] == _fingerprint("Booking created. COR_ID: <arg>")
    assert created["metadata"]["enclosing_symbol"] == create_nid

    edges = _anchor_edges(result, "emits_log_template")
    assert {(e["source"], e["target"]) for e in edges} == {
        (create_nid, start["id"]),
        (create_nid, created["id"]),
    }


def test_golden_fingerprint_matches_frozen_contract(tmp_path):
    """Golden regression for the Gate 1 fingerprint contract on Java SLF4J:
    sha256(version + "\\n" + canonical_template). The digest below is computed
    from the frozen version string; it must stay byte-for-byte identical to what
    Incident Context produces for the same Avion-style template."""
    f = _write_java(
        tmp_path, "BookingService.java",
        "public class BookingService {\n"
        "    public void create() {\n"
        '        log.info("Create booking started");\n'
        "    }\n"
        "}\n",
    )
    result = extract_java(f)
    a = _anchors(result)[0]
    assert a["canonicalization_version"] == "runtime-code-canonicalization/v1"
    assert a["canonical_template"] == "Create booking started"
    assert a["sha256"] == "b0caa134b931bb60b20edb1c551d4531ff24ff0514bd1f7c6db407866d516b0b"
    assert a["sha256"] == _fingerprint("Create booking started")


def test_extract_slf4j_placeholder_fingerprint(tmp_path):
    f = _write_java(
        tmp_path, "BookingService.java",
        "public class BookingService {\n"
        "    public void create(String id) {\n"
        '        log.info("Booking created. COR_ID: {}", id);\n'
        "    }\n"
        "}\n",
    )
    result = extract_java(f)
    a = _anchors(result)[0]
    assert a["canonical_template"] == "Booking created. COR_ID: <arg>"
    assert a["sha256"] == "c5888f9e25b33bd47d710f41ff14757927d8fe12508a8b98013b149fd02c48f2"
    assert a["sha256"] == _fingerprint("Booking created. COR_ID: <arg>")


def test_extract_dynamic_message_never_guessed(tmp_path):
    f = _write_java(
        tmp_path, "App.java",
        "public class App {\n"
        "    public void fail(Exception ex) {\n"
        '        log.error("oops " + ex.getMessage());\n'
        "    }\n"
        "}\n",
    )
    result = extract_java(f)
    anchors = _anchors(result)
    assert len(anchors) == 1
    a = anchors[0]
    assert a["anchor_kind"] == ANCHOR_KIND_DYNAMIC_CALLSITE
    assert "canonical_template" not in a
    assert "sha256" not in a
    assert a["label"] == "dynamic_log_callsite"
    assert a["metadata"]["framework"] == "slf4j"
    assert a["metadata"]["language"] == "java"
    edges = _anchor_edges(result)
    assert edges and edges[0]["relation"] == "has_dynamic_log_callsite"


def test_extract_constructor_attribution(tmp_path):
    f = _write_java(
        tmp_path, "App.java",
        "public class App {\n"
        "    public App() {\n"
        '        this.logger.info("ctor");\n'
        "    }\n"
        "}\n",
    )
    result = extract_java(f)
    anchors = _anchors(result)
    assert len(anchors) == 1
    a = anchors[0]
    assert a["canonical_template"] == "ctor"
    ctor_nid = [n["id"] for n in result["nodes"] if n["label"] == ".App()"][0]
    assert a["metadata"]["enclosing_symbol"] == ctor_nid
    assert a["metadata"]["enclosing_symbol_label"] == ".App()"
    edges = _anchor_edges(result, "emits_log_template")
    assert edges and (edges[0]["source"], edges[0]["target"]) == (ctor_nid, a["id"])


def test_extract_static_initializer_attributed_to_class(tmp_path):
    f = _write_java(
        tmp_path, "App.java",
        "public class App {\n"
        "    static {\n"
        '        log.info("Service booting");\n'
        "    }\n"
        "}\n",
    )
    result = extract_java(f)
    anchors = _anchors(result)
    assert len(anchors) == 1
    a = anchors[0]
    assert a["canonical_template"] == "Service booting"
    assert a["sha256"] == _fingerprint("Service booting")
    class_nid = [n["id"] for n in result["nodes"] if n["label"] == "App"][0]
    assert a["metadata"]["enclosing_symbol"] == class_nid
    assert a["metadata"]["enclosing_symbol_label"] == "App"
    edges = _anchor_edges(result, "emits_log_template")
    assert edges and (edges[0]["source"], edges[0]["target"]) == (class_nid, a["id"])


def test_extract_logger_factory_initializer_not_anchored(tmp_path):
    """The ``LoggerFactory.getLogger(...)`` initializer itself is a
    method_invocation on a non-logger receiver — never an anchor, even though
    it lives in a field initializer."""
    f = _write_java(
        tmp_path, "App.java",
        "import org.slf4j.Logger;\n"
        "import org.slf4j.LoggerFactory;\n"
        "public class App {\n"
        "    private static final Logger log = LoggerFactory.getLogger(App.class);\n"
        "}\n",
    )
    result = extract_java(f)
    assert _anchors(result) == []


def test_extract_out_of_scope_patterns_not_anchored(tmp_path):
    f = _write_java(
        tmp_path, "Scope.java",
        "public class Scope {\n"
        "    void go() {\n"
        '        log.trace("trace is out of scope");\n'
        '        client.info("not a logger");\n'
        "        log.info();\n"
        "        new Foo(\"ctor\");\n"
        "    }\n"
        "}\n",
    )
    result = extract_java(f)
    assert _anchors(result) == []


def test_extract_duplicate_templates_are_distinct_callsites(tmp_path):
    f = _write_java(
        tmp_path, "Dup.java",
        "public class Dup {\n"
        "    void a() {\n"
        '        log.info("dup");\n'
        "    }\n"
        "    void b() {\n"
        '        log.info("dup");\n'
        "    }\n"
        "}\n",
    )
    result = extract_java(f)
    anchors = _anchors(result)
    assert len(anchors) == 2
    ids = {a["id"] for a in anchors}
    assert len(ids) == 2
    assert {a["source_location"] for a in anchors} == {"L3", "L6"}
    assert all(a["canonical_template"] == "dup" for a in anchors)


def test_extract_ids_deterministic_across_runs(tmp_path):
    src = (
        "public class Det {\n"
        "    void a() {\n"
        '        log.info("dup");\n'
        '        log.warn("x {}", n);\n'
        "    }\n"
        "}\n"
    )
    f = _write_java(tmp_path, "Det.java", src)
    r1 = extract_java(f)
    r2 = extract_java(f)
    ids1 = {a["id"] for a in _anchors(r1)}
    ids2 = {a["id"] for a in _anchors(r2)}
    assert ids1 == ids2
    assert len(ids1) == 2


def test_extract_enclosing_symbol_edges_method_and_constructor(tmp_path):
    src = (
        "public class Svc {\n"
        "    public Svc() {\n"
        '        log.debug("booted");\n'
        "    }\n"
        "    void run() {\n"
        '        log.info("running");\n'
        "    }\n"
        "}\n"
    )
    f = _write_java(tmp_path, "Svc.java", src)
    result = extract_java(f)
    by_label = {n["label"]: n["id"] for n in result["nodes"]}
    svc_nid = by_label["Svc"]
    ctor_nid = by_label[".Svc()"]
    run_nid = by_label[".run()"]
    edges = {(e["source"], e["target"]) for e in _anchor_edges(result, "emits_log_template")}
    assert len(edges) == 2
    anchors_by_line = {a["source_location"]: a for a in _anchors(result)}
    assert (ctor_nid, anchors_by_line["L3"]["id"]) in edges
    assert (run_nid, anchors_by_line["L6"]["id"]) in edges
    assert anchors_by_line["L3"]["metadata"]["enclosing_symbol_label"] == ".Svc()"
    assert anchors_by_line["L6"]["metadata"]["enclosing_symbol_label"] == ".run()"


def test_batch_extract_reconciles_enclosing_symbol_metadata(tmp_path):
    """extract() remaps absolute-path-derived ids to repo-relative form; the
    anchor's enclosing_symbol metadata must be reconciled to the canonical id
    (its own edge's source), never a stale pre-remap value."""
    from graphify.extract import extract

    root = tmp_path / "corpus"
    root.mkdir()
    f = root / "svc.java"
    f.write_text(
        "public class svc {\n"
        "    void go() {\n"
        '        log.info("hi");\n'
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    result = extract([f], root=root)
    anchor = _anchors(result)[0]
    edge = [e for e in _anchor_edges(result, "emits_log_template")][0]
    node_ids = {n["id"] for n in result["nodes"]}
    assert anchor["metadata"]["enclosing_symbol"] == edge["source"]
    assert anchor["metadata"]["enclosing_symbol"] in node_ids
    assert anchor["metadata"]["enclosing_symbol_label"] == ".go()"
    assert "/" not in anchor["id"] and "tmp_" not in anchor["id"]


# ── build / export round-trip ────────────────────────────────────────────────

def test_build_preserves_anchor_node_attributes(tmp_path):
    f = _write_java(
        tmp_path, "App.java",
        "public class App {\n"
        "    void go() {\n"
        '        log.info("job started");\n'
        "    }\n"
        "}\n",
    )
    result = extract_java(f)
    G = build([result], dedup=False)
    anchor = _anchors(result)[0]
    attrs = G.nodes[anchor["id"]]
    assert attrs["type"] == "observability_anchor"
    assert attrs["anchor_kind"] == ANCHOR_KIND_LOG_TEMPLATE
    assert attrs["canonicalization_version"] == CANONICALIZATION_VERSION
    assert attrs["canonical_template"] == "job started"
    assert attrs["sha256"] == _fingerprint("job started")
    assert attrs["source_location"] == "L3"
    assert attrs["metadata"]["framework"] == "slf4j"
    go_nid = [n["id"] for n in result["nodes"] if n["label"] == ".go()"][0]
    assert G.has_edge(go_nid, anchor["id"])
    assert G.edges[go_nid, anchor["id"]]["relation"] == "emits_log_template"
