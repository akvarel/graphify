from __future__ import annotations

from pathlib import Path

from graphify.build import build_from_json
from graphify.extract import extract


V1_RELATIONS = {
    "FLOWS_TO",
    "PASSED_AS_ARGUMENT",
    "RETURNED_AS",
    "READ_FROM",
    "WRITTEN_TO",
    "TRANSFORMED_BY",
}
V1_KINDS = {"PARAMETER", "RETURN_VALUE", "LOCAL", "FIELD"}


def _extract(tmp_path: Path, body: str) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "Flow.java"
    p.write_text(body, encoding="utf-8")
    return extract([p], cache_root=tmp_path / "graphify-out")


def _values(result: dict) -> dict[tuple[str, str], str]:
    out = {}
    for n in result["nodes"]:
        if n.get("type") == "data_value":
            md = n.get("metadata", {})
            assert md.get("kind") in V1_KINDS
            assert md.get("provenance") == "STATIC_AST"
            out[(md.get("kind"), md.get("name"))] = n["id"]
    return out


def _value(result: dict, kind: str, name: str, owner_contains: str) -> str:
    return next(
        n["id"]
        for n in result["nodes"]
        if n.get("type") == "data_value"
        and n.get("metadata", {}).get("kind") == kind
        and n.get("metadata", {}).get("name") == name
        and owner_contains in n.get("metadata", {}).get("owner", "")
    )


def _edges(result: dict, relation: str) -> list[dict]:
    return [e for e in result["edges"] if e.get("relation") == relation]


def test_java_data_flow_emits_scoped_values_and_frozen_v1_edges(tmp_path: Path):
    result = _extract(tmp_path, """
        class Flow {
          int total;
          Flow(int seed) { this.total = seed; }
          int normalize(int raw) { return raw; }
          int run(int input) {
            int local = input;
            total = local;
            return normalize(total);
          }
        }
    """)
    vals = _values(result)

    assert vals[("FIELD", "total")]
    assert vals[("PARAMETER", "input")]
    assert vals[("LOCAL", "local")]
    assert vals[("RETURN_VALUE", "return")]
    assert (vals[("PARAMETER", "input")], vals[("LOCAL", "local")]) in {
        (e["source"], e["target"]) for e in _edges(result, "FLOWS_TO")
    }
    assert any(e["source"] == vals[("LOCAL", "local")] and e["target"] == vals[("FIELD", "total")] for e in _edges(result, "WRITTEN_TO"))
    assert any(e["source"] == vals[("FIELD", "total")] and e["target"] == vals[("PARAMETER", "raw")] for e in _edges(result, "READ_FROM"))
    assert any(e["source"] == vals[("FIELD", "total")] and e["target"] == vals[("PARAMETER", "raw")] for e in _edges(result, "PASSED_AS_ARGUMENT"))
    transformed = _edges(result, "TRANSFORMED_BY")
    normalize_return = _value(result, "RETURN_VALUE", "return", "normalize")
    run_return = _value(result, "RETURN_VALUE", "return", "run")
    assert any(
        e["source"] == vals[("FIELD", "total")]
        and e["target"] == normalize_return
        and e.get("metadata", {}).get("transformationSymbol") == "Flow.normalize(int)"
        for e in transformed
    )
    assert all(isinstance(e.get("metadata", {}).get("argumentIndex"), int) for e in _edges(result, "PASSED_AS_ARGUMENT"))
    assert any(e["source"] == normalize_return and e["target"] == run_return for e in _edges(result, "RETURNED_AS"))
    node_ids = {n["id"] for n in result["nodes"]}
    assert all(e["source"] in node_ids and e["target"] in node_ids for e in result["edges"] if e["relation"] not in {"imports", "imports_from", "re_exports"})
    for e in result["edges"]:
        if e["relation"] in V1_RELATIONS:
            assert e.get("confidence") == "EXTRACTED"
            assert e.get("metadata", {}).get("provenance") == "STATIC_AST"


def test_java_constructor_object_mapping_uses_v1_value_edges(tmp_path: Path):
    result = _extract(tmp_path, """
        class Box { Box(int seed) {} }
        class Flow { void run(int x) { Box b = new Box(x); } }
    """)
    vals = _values(result)
    assert any(e["source"] == vals[("PARAMETER", "x")] and e["target"] == vals[("PARAMETER", "seed")] for e in _edges(result, "PASSED_AS_ARGUMENT"))
    assert any(e["target"] == vals[("LOCAL", "b")] for e in _edges(result, "FLOWS_TO"))
    assert not _edges(result, "CONSTRUCTED_BY")


def test_java_ambiguous_and_reflective_calls_are_omitted(tmp_path: Path):
    result = _extract(tmp_path, """
        class A { int convert(int a) { return a; } }
        class B { int convert(int b) { return b; } }
        class Flow {
          int run(int x) throws Exception {
            Object dyn = Class.forName("A").getMethod("convert", int.class).invoke(null, x);
            return convert(x);
          }
        }
    """)
    assert not _edges(result, "PASSED_AS_ARGUMENT")
    assert not _edges(result, "TRANSFORMED_BY")


def test_java_data_flow_survives_build(tmp_path: Path):
    result = _extract(tmp_path, "class Flow { int id(int x) { int y = x; return y; } }")
    graph = build_from_json(result)
    assert graph.number_of_nodes() >= len(result["nodes"])
    assert any(n.get("type") == "data_value" for n in result["nodes"])
    assert any(data.get("relation") == "PASSED_AS_ARGUMENT" for _, _, data in graph.edges(data=True)) is False


def test_java_argument_relation_survives_build_without_parallel_edge_collision(tmp_path: Path):
    result = _extract(tmp_path, "class Flow { int field; int run() { return id(field); } int id(int raw) { return raw; } }")
    graph = build_from_json(result, directed=True, multigraph=True)
    expected = {"PASSED_AS_ARGUMENT", "READ_FROM"}
    assert expected <= {e.get("relation") for e in result["edges"]}
    assert expected <= {data.get("relation") for _, _, data in graph.edges(data=True)}


def test_java_field_return_relations_survive_multigraph_build(tmp_path: Path):
    result = _extract(tmp_path, "class Flow { int field; int run() { return field; } }")
    graph = build_from_json(result, directed=True, multigraph=True)
    expected = {"READ_FROM", "RETURNED_AS"}
    assert expected <= {e.get("relation") for e in result["edges"]}
    assert expected <= {data.get("relation") for _, _, data in graph.edges(data=True)}


def test_java_forward_declaration_and_this_receiver_are_resolved(tmp_path: Path):
    result = _extract(tmp_path, """
        class Flow {
          int run(int input) { return this.normalize(input); }
          int normalize(int raw) { return raw; }
        }
    """)
    input_id = _value(result, "PARAMETER", "input", "run")
    raw_id = _value(result, "PARAMETER", "raw", "normalize")
    normalize_return = _value(result, "RETURN_VALUE", "return", "normalize")
    assert any(e["source"] == input_id and e["target"] == raw_id for e in _edges(result, "PASSED_AS_ARGUMENT"))
    assert any(e["source"] == input_id and e["target"] == normalize_return for e in _edges(result, "TRANSFORMED_BY"))


def test_java_void_calls_have_arguments_but_no_return_transformation(tmp_path: Path):
    result = _extract(tmp_path, """
        class Flow {
          void run(int input) { this.consume(input); }
          void consume(int raw) {}
        }
    """)
    input_id = _value(result, "PARAMETER", "input", "run")
    raw_id = _value(result, "PARAMETER", "raw", "consume")
    assert any(e["source"] == input_id and e["target"] == raw_id for e in _edges(result, "PASSED_AS_ARGUMENT"))
    assert not _edges(result, "TRANSFORMED_BY")
    void_method_ids = {
        n["id"]
        for n in result["nodes"]
        if n.get("label") in {".run()", ".consume()"}
    }
    assert not any(
        n.get("type") == "data_value"
        and n.get("metadata", {}).get("kind") == "RETURN_VALUE"
        and n.get("metadata", {}).get("owner") in void_method_ids
        for n in result["nodes"]
    )


def test_java_fields_resolve_after_methods_and_use_read_write_relations(tmp_path: Path):
    result = _extract(tmp_path, """
        class Flow {
          int run(int input) {
            int copy = total;
            total = input;
            return copy;
          }
          int total;
        }
    """)
    field_id = _value(result, "FIELD", "total", "Flow.total")
    copy_id = _value(result, "LOCAL", "copy", "run")
    input_id = _value(result, "PARAMETER", "input", "run")
    assert any(e["source"] == field_id and e["target"] == copy_id for e in _edges(result, "READ_FROM"))
    assert any(e["source"] == input_id and e["target"] == field_id for e in _edges(result, "WRITTEN_TO"))
    assert not any(e["source"] == input_id and e["target"] == field_id for e in _edges(result, "FLOWS_TO"))


def test_java_overloads_require_exact_arity(tmp_path: Path):
    result = _extract(tmp_path, """
        class Flow {
          int run(int input) { return convert(input); }
          int convert(int raw) { return raw; }
          int convert(int left, int right) { return left; }
        }
    """)
    input_id = _value(result, "PARAMETER", "input", "run")
    single_raw = _value(result, "PARAMETER", "raw", "convert")
    assert any(e["source"] == input_id and e["target"] == single_raw for e in _edges(result, "PASSED_AS_ARGUMENT"))


def test_java_value_ids_are_portable_and_overload_returns_do_not_collapse(tmp_path: Path):
    body = """
        class Flow {
          int convert(int raw) { return raw; }
          int convert(int left, int right) { return left; }
        }
    """
    first = _extract(tmp_path / "first" / "src", body)
    second = _extract(tmp_path / "second" / "src", body)
    first_ids = {n["id"] for n in first["nodes"] if n.get("type") == "data_value"}
    second_ids = {n["id"] for n in second["nodes"] if n.get("type") == "data_value"}
    assert first_ids == second_ids
    returns = [
        n for n in first["nodes"]
        if n.get("type") == "data_value" and n.get("metadata", {}).get("kind") == "RETURN_VALUE"
    ]
    assert len({n["id"] for n in returns}) == 2
    assert all(n.get("confidence") == "EXTRACTED" and n.get("confidence_score") == 1.0 for n in returns)
    assert all(n.get("metadata", {}).get("owner", "").startswith("Flow.convert(") for n in returns)


def test_java_constructor_overloads_use_exact_arity_and_omit_same_arity_ambiguity(tmp_path: Path):
    result = _extract(tmp_path, """
        class Box {
          Box(int one) {}
          Box(int left, int right) {}
          Box(String ambiguous) {}
        }
        class Flow { void run(int x) { Box b = new Box(x); } }
    """)
    assert not _edges(result, "PASSED_AS_ARGUMENT")


def test_java_symbolic_and_lambda_values_are_not_guessed(tmp_path: Path):
    result = _extract(tmp_path, """
        interface Fn { int apply(int value); }
        class Flow {
          int run(int x, int y) {
            int sum = x + y;
            Fn captured = value -> id(x);
            return sum;
          }
          int id(int raw) { return raw; }
        }
    """)
    sum_id = _value(result, "LOCAL", "sum", "Flow.run(int,int)")
    captured_id = _value(result, "LOCAL", "captured", "Flow.run(int,int)")
    assert not any(e["target"] in {sum_id, captured_id} for e in _edges(result, "FLOWS_TO"))
    raw_id = _value(result, "PARAMETER", "raw", "Flow.id(int)")
    x_id = _value(result, "PARAMETER", "x", "Flow.run(int,int)")
    assert not any(e["source"] == x_id and e["target"] == raw_id for e in _edges(result, "PASSED_AS_ARGUMENT"))


def test_java_field_receivers_and_ordinary_invoke_method_are_resolved_safely(tmp_path: Path):
    result = _extract(tmp_path, """
        class Box { int value; }
        class Flow {
          Box box;
          int value;
          int invoke(int raw) { return raw; }
          int run(Flow other, Box box, int input) {
            this.value = input;
            int own = this.value;
            int foreign = other.value;
            int boxed = box.value;
            int nested = this.box.value;
            return invoke(input);
          }
        }
    """)
    input_id = _value(result, "PARAMETER", "input", "Flow.run(Flow,Box,int)")
    invoke_raw = _value(result, "PARAMETER", "raw", "Flow.invoke(int)")
    flow_field = _value(result, "FIELD", "value", "Flow.value")
    box_field = _value(result, "FIELD", "value", "Box.value")
    own = _value(result, "LOCAL", "own", "Flow.run(Flow,Box,int)")
    foreign = _value(result, "LOCAL", "foreign", "Flow.run(Flow,Box,int)")
    boxed = _value(result, "LOCAL", "boxed", "Flow.run(Flow,Box,int)")
    nested = _value(result, "LOCAL", "nested", "Flow.run(Flow,Box,int)")
    assert any(e["source"] == input_id and e["target"] == flow_field for e in _edges(result, "WRITTEN_TO"))
    assert any(e["source"] == flow_field and e["target"] == own for e in _edges(result, "READ_FROM"))
    assert any(e["source"] == flow_field and e["target"] == foreign for e in _edges(result, "READ_FROM"))
    assert any(e["source"] == box_field and e["target"] == boxed for e in _edges(result, "READ_FROM"))
    assert any(e["source"] == box_field and e["target"] == nested for e in _edges(result, "READ_FROM"))
    assert any(e["source"] == input_id and e["target"] == invoke_raw for e in _edges(result, "PASSED_AS_ARGUMENT"))


def test_java_transformations_require_proven_return_dependency(tmp_path: Path):
    result = _extract(tmp_path, """
        class Flow {
          int run(int a, int b) { int first = id(a, b); int second = viaLocal(a, b); return first; }
          int constant(int x) { return 42; }
          int id(int left, int right) { return left; }
          int viaLocal(int left, int right) { int copy = left; return copy; }
        }
    """)
    a_id = _value(result, "PARAMETER", "a", "Flow.run(int,int)")
    b_id = _value(result, "PARAMETER", "b", "Flow.run(int,int)")
    id_return = _value(result, "RETURN_VALUE", "return", "Flow.id(int,int)")
    via_return = _value(result, "RETURN_VALUE", "return", "Flow.viaLocal(int,int)")
    transformed = _edges(result, "TRANSFORMED_BY")
    assert any(e["source"] == a_id and e["target"] == id_return for e in transformed)
    assert any(e["source"] == a_id and e["target"] == via_return for e in transformed)
    assert not any(e["source"] == b_id and e["target"] in {id_return, via_return} for e in transformed)
    assert not any(e.get("metadata", {}).get("transformationSymbol") == "Flow.constant(int)" for e in transformed)


def test_java_same_named_locals_and_lexical_expiry_do_not_leak(tmp_path: Path):
    result = _extract(tmp_path, """
        class Flow {
          int value;
          int run(boolean flag, int a, int b) {
            if (flag) { int value = a; consume(value); }
            if (!flag) { int value = b; consume(value); }
            return value;
          }
          void consume(int raw) {}
        }
    """)
    locals_named_value = [n for n in result["nodes"] if n.get("type") == "data_value" and n.get("metadata", {}).get("kind") == "LOCAL" and n.get("metadata", {}).get("name") == "value"]
    assert len({n["id"] for n in locals_named_value}) == 2
    field_id = _value(result, "FIELD", "value", "Flow.value")
    run_return = _value(result, "RETURN_VALUE", "return", "Flow.run(boolean,int,int)")
    assert any(e["source"] == field_id and e["target"] == run_return for e in _edges(result, "RETURNED_AS"))
    assert not any(e["source"] in {n["id"] for n in locals_named_value} and e["target"] == run_return for e in _edges(result, "RETURNED_AS"))


def test_java_same_named_files_do_not_collide_but_checkout_ids_are_portable(tmp_path: Path):
    for root in [tmp_path / "one", tmp_path / "two"]:
        (root / "a").mkdir(parents=True)
        (root / "b").mkdir(parents=True)
        (root / "a" / "Flow.java").write_text("class Flow { int run(int x) { return x; } }", encoding="utf-8")
        (root / "b" / "Flow.java").write_text("class Flow { int run(int x) { return x; } }", encoding="utf-8")
    first = extract([tmp_path / "one" / "a" / "Flow.java", tmp_path / "one" / "b" / "Flow.java"], cache_root=tmp_path / "one" / "graphify-out")
    second = extract([tmp_path / "two" / "a" / "Flow.java", tmp_path / "two" / "b" / "Flow.java"], cache_root=tmp_path / "two" / "graphify-out")
    first_ids = {n["id"] for n in first["nodes"] if n.get("type") == "data_value"}
    second_ids = {n["id"] for n in second["nodes"] if n.get("type") == "data_value"}
    assert len(first_ids) == 4
    assert any("a_flow" in node_id for node_id in first_ids)
    assert any("b_flow" in node_id for node_id in first_ids)
    assert first_ids == second_ids


def test_java_same_line_synthetic_object_values_do_not_collapse(tmp_path: Path):
    result = _extract(tmp_path, "class Foo {} class Flow { void run() { Foo a = new Foo(); Foo b = new Foo(); } }")
    synthetic = [n for n in result["nodes"] if n.get("type") == "data_value" and str(n.get("metadata", {}).get("name", "")).startswith("new Foo@")]
    assert len({n["id"] for n in synthetic}) == 2


def test_java_failure_and_parse_recovery_are_machine_visible(tmp_path: Path, monkeypatch):
    import graphify.extractors.java_data_flow as java_data_flow

    result = _extract(tmp_path / "parse", "class Flow { int run(int x) { if ( return x; } }")
    diagnostics = [n for n in result["nodes"] if n.get("type") == "extraction_diagnostic"]
    assert any(n.get("metadata", {}).get("status") == "incomplete" for n in diagnostics)
    assert any(n.get("confidence_score") < 1.0 for n in result["nodes"] if n.get("type") == "data_value")

    def boom(_name: str):
        raise RuntimeError("forced")

    monkeypatch.setattr(java_data_flow.importlib, "import_module", boom)
    failed = java_data_flow.augment_java_data_flow(tmp_path / "missing.java", {"nodes": [], "edges": []})
    assert failed["data_flow"]["java"]["status"] == "failed"
    assert failed["data_flow"]["java"]["reason"] == "RuntimeError"


def test_java_field_initializer_uses_written_to(tmp_path: Path):
    result = _extract(tmp_path, "class Flow { int seed; int field = seed; }")
    seed_id = _value(result, "FIELD", "seed", "Flow.seed")
    field_id = _value(result, "FIELD", "field", "Flow.field")
    assert any(e["source"] == seed_id and e["target"] == field_id for e in _edges(result, "WRITTEN_TO"))
    assert not any(e["source"] == seed_id and e["target"] == field_id for e in _edges(result, "FLOWS_TO"))


def test_java_assignment_kill_prevents_stale_transform_dependency(tmp_path: Path):
    result = _extract(tmp_path, """
        class Flow {
          int run(int initial, int replacement) { return id(initial, replacement); }
          int id(int initial, int replacement) {
            int chosen = initial;
            chosen = replacement;
            return chosen;
          }
        }
    """)
    initial_id = _value(result, "PARAMETER", "initial", "Flow.run(int,int)")
    replacement_id = _value(result, "PARAMETER", "replacement", "Flow.run(int,int)")
    id_return = _value(result, "RETURN_VALUE", "return", "Flow.id(int,int)")
    transformed = _edges(result, "TRANSFORMED_BY")
    assert any(e["source"] == replacement_id and e["target"] == id_return for e in transformed)
    assert not any(e["source"] == initial_id and e["target"] == id_return for e in transformed)


def test_java_for_initializer_scope_expires_and_field_is_not_shadowed(tmp_path: Path):
    result = _extract(tmp_path, """
        class Flow {
          int i;
          int run() {
            for (int i = 0; i < 1; i++) { consume(i); }
            return i;
          }
          void consume(int raw) {}
        }
    """)
    field_id = _value(result, "FIELD", "i", "Flow.i")
    run_return = _value(result, "RETURN_VALUE", "return", "Flow.run()")
    loop_locals = [
        n["id"] for n in result["nodes"]
        if n.get("type") == "data_value"
        and n.get("metadata", {}).get("kind") == "LOCAL"
        and n.get("metadata", {}).get("name") == "i"
    ]
    assert loop_locals
    assert any(e["source"] == field_id and e["target"] == run_return for e in _edges(result, "RETURNED_AS"))
    assert not any(e["source"] in set(loop_locals) and e["target"] == run_return for e in _edges(result, "RETURNED_AS"))


def test_java_overload_metadata_keeps_exact_callee_identity(tmp_path: Path):
    result = _extract(tmp_path, """
        class Flow {
          int run(int one, int two) { return convert(one, two); }
          int convert(int raw) { return raw; }
          int convert(int left, int right) { return left; }
        }
    """)
    left_id = _value(result, "PARAMETER", "left", "Flow.convert(int,int)")
    edge = next(e for e in _edges(result, "PASSED_AS_ARGUMENT") if e["target"] == left_id)
    md = edge.get("metadata", {})
    assert md.get("calleeSymbol") == "Flow.convert(int,int)"
    assert md.get("callee", "").endswith("flow_convert_int_int")
    transformed = next(
        e for e in _edges(result, "TRANSFORMED_BY")
        if e.get("metadata", {}).get("transformationSymbol") == "Flow.convert(int,int)"
    )
    assert transformed.get("metadata", {}).get("callee", "").endswith("flow_convert_int_int")


def test_java_forward_field_initializer_waits_for_all_fields(tmp_path: Path):
    result = _extract(tmp_path, "class Flow { int snapshot = source; int source = 1; }")
    snapshot_id = _value(result, "FIELD", "snapshot", "Flow.snapshot")
    source_id = _value(result, "FIELD", "source", "Flow.source")
    assert any(e["source"] == source_id and e["target"] == snapshot_id for e in _edges(result, "WRITTEN_TO"))


def test_java_missing_tree_sitter_java_is_unsupported_not_failed(tmp_path: Path, monkeypatch):
    import graphify.extractors.java_data_flow as java_data_flow

    real_import = java_data_flow.importlib.import_module

    def missing_java(name: str):
        if name == "tree_sitter_java":
            raise ModuleNotFoundError("No module named 'tree_sitter_java'", name="tree_sitter_java")
        return real_import(name)

    monkeypatch.setattr(java_data_flow.importlib, "import_module", missing_java)
    result = java_data_flow.augment_java_data_flow(tmp_path / "Flow.java", {"nodes": [], "edges": []})
    assert result["data_flow"]["java"] == {"status": "unsupported", "reason": "tree_sitter_java_unavailable"}
