from __future__ import annotations

from pathlib import Path

from graphify.extract import extract


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _data_value(result: dict, owner: str, kind: str, name: str | None = None) -> str | None:
    """Find a data_value node id by its metadata identity (remap-stable)."""
    for n in result["nodes"]:
        m = n.get("metadata") or {}
        if m.get("owner") == owner and m.get("kind") == kind and (name is None or m.get("name") == name):
            return n.get("id")
    return None


def _cross_file_edges(result: dict, relation: str) -> list[dict]:
    return [
        e for e in result["edges"]
        if e.get("relation") == relation and (e.get("metadata") or {}).get("cross_file")
    ]


def test_cross_file_instance_call_links_arg_param_return(tmp_path: Path):
    # Gate 2B: `ps.calculate(b)` in Order calls PricingService.calculate in
    # another file. Caller arg `b` must flow to the callee parameter, the callee
    # return (a passthrough) must flow to the caller's receiving local, and the
    # param->return passthrough must be proven as TRANSFORMED_BY. The instance
    # receiver has exact declaration resolution but no proven runtime-instance
    # or alias authority, so its evidence score remains conservative (0.5).
    service = _write(
        tmp_path / "src/acme/PricingService.java",
        "package acme;\n"
        "public class PricingService {\n"
        "  public double calculate(double base) { return base; }\n"
        "}\n",
    )
    order = _write(
        tmp_path / "src/acme/Order.java",
        "package acme;\n"
        "public class Order {\n"
        "  private PricingService ps;\n"
        "  public double go() {\n"
        "    double b = 100.0;\n"
        "    double r = ps.calculate(b);\n"
        "    return r;\n"
        "  }\n"
        "}\n",
    )
    result = extract([service, order], cache_root=tmp_path)
    ids = {n["id"] for n in result["nodes"]}
    base = _data_value(result, "Order.go()", "LOCAL", "b")
    price = _data_value(result, "PricingService.calculate(double)", "PARAMETER", "base")
    ret = _data_value(result, "PricingService.calculate(double)", "RETURN_VALUE")
    sink = _data_value(result, "Order.go()", "LOCAL", "r")
    assert base and price and ret and sink, (base, price, ret, sink)

    passed = [e for e in _cross_file_edges(result, "PASSED_AS_ARGUMENT")
              if (e["metadata"]).get("calleeSymbol", "").startswith("PricingService.calculate")]
    assert len(passed) == 1
    e = passed[0]
    assert e["source"] == base and e["target"] == price
    assert e["source"] in ids and e["target"] in ids
    assert "receiverConfidence" not in e["metadata"]
    assert e["metadata"] | {
        "declarationResolution": "EXACT",
        "receiverKind": "NAMED_FIELD",
        "receiverPath": "Order.ps",
        "fieldScope": "INSTANCE",
        "instanceAuthority": "UNKNOWN",
        "aliasAuthority": "MAY",
    } == e["metadata"]
    assert e["confidence_score"] == 0.5
    assert (e["metadata"]).get("argumentIndex") == 0

    # The callee returns its parameter directly -> TRANSFORMED_BY proven.
    transformed = [e for e in _cross_file_edges(result, "TRANSFORMED_BY")
                   if (e["metadata"]).get("calleeSymbol", "").startswith("PricingService.calculate")]
    assert len(transformed) == 1
    assert transformed[0]["source"] == base and transformed[0]["target"] == ret

    # The callee return flows to the caller's receiving local.
    flows = [e for e in _cross_file_edges(result, "FLOWS_TO")
             if (e["metadata"]).get("calleeSymbol", "").startswith("PricingService.calculate")]
    assert len(flows) == 1
    assert flows[0]["source"] == ret and flows[0]["target"] == sink


def test_cross_file_static_call_has_no_instance_or_alias_question(tmp_path: Path):
    # A static class receiver (`MathUtil.doubleIt(x)`) has exact declaration
    # resolution, but instance and alias authority are not applicable.
    util = _write(
        tmp_path / "src/acme/MathUtil.java",
        "package acme;\npublic class MathUtil { public static double doubleIt(double v) { return v; } }\n",
    )
    calc = _write(
        tmp_path / "src/acme/Calc.java",
        "package acme;\n"
        "public class Calc { public double go(){ double x=5.0; double y=MathUtil.doubleIt(x); return y; } }\n",
    )
    result = extract([util, calc], cache_root=tmp_path)
    x = _data_value(result, "Calc.go()", "LOCAL", "x")
    v = _data_value(result, "MathUtil.doubleIt(double)", "PARAMETER", "v")
    ret = _data_value(result, "MathUtil.doubleIt(double)", "RETURN_VALUE")
    y = _data_value(result, "Calc.go()", "LOCAL", "y")
    assert x and v and ret and y

    passed = [e for e in _cross_file_edges(result, "PASSED_AS_ARGUMENT")
              if (e["metadata"]).get("calleeSymbol", "").startswith("MathUtil.doubleIt")]
    assert len(passed) == 1
    assert passed[0]["source"] == x and passed[0]["target"] == v
    md = passed[0]["metadata"]
    assert "receiverConfidence" not in md
    assert md["declarationResolution"] == "EXACT"
    assert md["receiverKind"] == "STATIC_CLASS"
    assert md["receiverPath"] == "acme.MathUtil"
    assert md["fieldScope"] == "NOT_APPLICABLE"
    assert md["instanceAuthority"] == "NOT_APPLICABLE"
    assert md["aliasAuthority"] == "NOT_APPLICABLE"
    assert passed[0]["confidence_score"] == 1.0

    transformed = [e for e in _cross_file_edges(result, "TRANSFORMED_BY")
                   if (e["metadata"]).get("calleeSymbol", "").startswith("MathUtil.doubleIt")]
    assert len(transformed) == 1
    assert transformed[0]["source"] == x and transformed[0]["target"] == ret

    flows = [e for e in _cross_file_edges(result, "FLOWS_TO")
             if (e["metadata"]).get("calleeSymbol", "").startswith("MathUtil.doubleIt")]
    assert len(flows) == 1
    assert flows[0]["source"] == ret and flows[0]["target"] == y
    assert flows[0]["confidence_score"] == 1.0


def test_cross_file_constructor_argument_link(tmp_path: Path):
    # `new Invoice(amt)` constructs a cross-file class; the constructor argument
    # must link to the constructor parameter. There is no receiver instance or
    # alias question at a construction boundary.
    invoice = _write(
        tmp_path / "src/acme/Invoice.java",
        "package acme;\n"
        "public class Invoice {\n"
        "  private double amount;\n"
        "  public Invoice(double amount) { this.amount = amount; }\n"
        "  public double getAmount() { return amount; }\n"
        "}\n",
    )
    shop = _write(
        tmp_path / "src/acme/Shop.java",
        "package acme;\n"
        "public class Shop {\n"
        "  public double make(){\n"
        "    double amt = 50.0;\n"
        "    Invoice inv = new Invoice(amt);\n"
        "    return inv.getAmount();\n"
        "  }\n"
        "}\n",
    )
    result = extract([invoice, shop], cache_root=tmp_path)
    amt = _data_value(result, "Shop.make()", "LOCAL", "amt")
    ctor_param = _data_value(result, "Invoice.Invoice(double)", "PARAMETER", "amount")
    assert amt and ctor_param

    passed = [e for e in _cross_file_edges(result, "PASSED_AS_ARGUMENT")
              if (e["metadata"]).get("constructor")]
    assert len(passed) == 1
    e = passed[0]
    assert e["source"] == amt and e["target"] == ctor_param
    assert "receiverConfidence" not in e["metadata"]
    assert e["metadata"]["declarationResolution"] == "EXACT"
    assert e["metadata"]["receiverKind"] == "CONSTRUCTOR"
    assert e["metadata"]["receiverPath"] == "acme.Invoice"
    assert e["metadata"]["fieldScope"] == "NOT_APPLICABLE"
    assert e["metadata"]["instanceAuthority"] == "NOT_APPLICABLE"
    assert e["metadata"]["aliasAuthority"] == "NOT_APPLICABLE"
    assert e["confidence_score"] == 1.0


def test_cross_file_ambiguous_arity_fails_closed(tmp_path: Path):
    # Two overloads `f(double)` and `f(int)` are both single-arg: an `amb.f(v)`
    # with a `double` arg cannot be disambiguated from emitted facts alone, so
    # no cross-file data-flow edge is emitted (precision over recall).
    ambig = _write(
        tmp_path / "src/acme/Ambig.java",
        "package acme;\n"
        "public class Ambig { public double f(double a){ return a; } public double f(int a){ return a; } }\n",
    )
    user = _write(
        tmp_path / "src/acme/AmbigUser.java",
        "package acme;\n"
        "public class AmbigUser { private Ambig amb; public double g(){ double v=1.0; return amb.f(v); } }\n",
    )
    result = extract([ambig, user], cache_root=tmp_path)
    assert _cross_file_edges(result, "PASSED_AS_ARGUMENT") == []
    assert _cross_file_edges(result, "FLOWS_TO") == []


def test_cross_file_unresolved_receiver_fails_closed(tmp_path: Path):
    # `NosuchType.calc(v)` references a type declared nowhere in the corpus; no
    # data-flow edge may be fabricated by name alone.
    ghost = _write(
        tmp_path / "src/acme/Ghost.java",
        "package acme;\npublic class Ghost { public double z(double a){ return a; } }\n",
    )
    user = _write(
        tmp_path / "src/acme/GhostUser.java",
        "package acme;\n"
        "public class GhostUser { public double g(){ double v=1.0; return NosuchType.calc(v); } }\n",
    )
    result = extract([ghost, user], cache_root=tmp_path)
    assert _cross_file_edges(result, "PASSED_AS_ARGUMENT") == []
    assert _cross_file_edges(result, "FLOWS_TO") == []


def test_cross_file_import_disambiguates_same_named_class(tmp_path: Path):
    # Two `Widget` classes in different packages both define `size(double)`.
    # The caller's explicit `import com.a.Widget` must pick com.a's method; the
    # com.b twin must NOT be linked.
    wa = _write(
        tmp_path / "src/com/a/Widget.java",
        "package com.a;\npublic class Widget { public double size(double s){ return s; } }\n",
    )
    wb = _write(
        tmp_path / "src/com/b/Widget.java",
        "package com.b;\npublic class Widget { public double size(double s){ return s; } }\n",
    )
    user = _write(
        tmp_path / "src/com/x/User.java",
        "package com.x;\n"
        "import com.a.Widget;\n"
        "public class User { private Widget w; public double go(){ double v=1.0; return w.size(v); } }\n",
    )
    result = extract([wa, wb, user], cache_root=tmp_path)

    passed = [e for e in _cross_file_edges(result, "PASSED_AS_ARGUMENT")
              if (e["metadata"]).get("calleeSymbol", "").startswith("Widget.size")]
    assert len(passed) == 1
    tgt = next(n for n in result["nodes"] if n["id"] == passed[0]["target"])
    assert "com/a/Widget.java" in tgt.get("source_file", "")
    assert "com/b/Widget.java" not in tgt.get("source_file", "")


def test_cross_file_no_transformed_by_without_proven_dependency(tmp_path: Path):
    # The callee's return does NOT depend on its parameter (`return x` uses an
    # independent local), so the param may not be marked TRANSFORMED_BY even
    # though the argument is passed. Argument flow and return flow still exist.
    callee = _write(
        tmp_path / "src/acme/Calc.java",
        "package acme;\n"
        "public class Calc { public double f(double a){ double x=1.0; return x; } }\n",
    )
    caller = _write(
        tmp_path / "src/acme/User.java",
        "package acme;\n"
        "public class User { private Calc c; public double go(){ double v=2.0; return c.f(v); } }\n",
    )
    result = extract([callee, caller], cache_root=tmp_path)
    assert _cross_file_edges(result, "TRANSFORMED_BY") == []
    passed = [e for e in _cross_file_edges(result, "PASSED_AS_ARGUMENT")
              if (e["metadata"]).get("calleeSymbol", "").startswith("Calc.f")]
    assert len(passed) == 1


def test_cross_file_wildcard_import_fails_closed(tmp_path: Path):
    # J: a bare receiver type resolved only through a wildcard import cannot be
    # disambiguated from emitted facts; no data-flow edge may be fabricated.
    w = _write(
        tmp_path / "src/com/a/W.java",
        "package com.a;\npublic class W { public double g(double x){ return x; } }\n",
    )
    user = _write(
        tmp_path / "src/com/x/WUser.java",
        "package com.x;\nimport com.a.*;\n"
        "public class WUser { private W w; public double go(){ double v=1.0; return w.g(v); } }\n",
    )
    result = extract([w, user], cache_root=tmp_path)
    assert _cross_file_edges(result, "PASSED_AS_ARGUMENT") == []
    assert _cross_file_edges(result, "FLOWS_TO") == []


def test_cross_file_nested_duplicate_simple_names_do_not_collide(tmp_path: Path):
    # H: `Outer.Inner` in two different packages must not cross-link. Nested
    # receiver identities are not resolved to a package-qualified FQN here, so
    # the linkage fails closed (no fabricated cross-package edge).
    wa = _write(
        tmp_path / "src/com/a/Outer.java",
        "package com.a;\npublic class Outer { public static class Inner { public double calc(double x){ return x; } } }\n",
    )
    wb = _write(
        tmp_path / "src/com/b/Outer.java",
        "package com.b;\npublic class Outer { public static class Inner { public double calc(double x){ return x; } } }\n",
    )
    user = _write(
        tmp_path / "src/com/x/User.java",
        "package com.x;\nimport com.a.Outer;\n"
        "public class User { public double go(){ double v=1.0; Outer.Inner i = new Outer.Inner(); return i.calc(v); } }\n",
    )
    result = extract([wa, wb, user], cache_root=tmp_path)
    passed = [e for e in _cross_file_edges(result, "PASSED_AS_ARGUMENT")
              if (e["metadata"]).get("calleeSymbol", "").startswith("Inner.calc")]
    # Fail closed: no fabricated cross-package edge for the nested receiver.
    assert passed == []


def test_cross_file_parse_incomplete_target_propagates_partial(tmp_path: Path):
    # M: when the target file is parse-incomplete, cross-file evidence must be
    # machine-visible as PARTIAL, never presented as complete/full-trust.
    broken = _write(
        tmp_path / "src/acme/Broken.java",
        "package acme;\npublic class Broken { public double f(double a){ return a; \n",  # missing brace
    )
    user = _write(
        tmp_path / "src/acme/BUser.java",
        "package acme;\npublic class BUser { private Broken b; public double go(){ double v=2.0; return b.f(v); } }\n",
    )
    result = extract([broken, user], cache_root=tmp_path)
    passed = _cross_file_edges(result, "PASSED_AS_ARGUMENT")
    assert passed, "expected a cross-file PASSED_AS_ARGUMENT edge"
    assert (passed[0]["metadata"]).get("analysisCompleteness") == "PARTIAL"
    assert passed[0]["confidence_score"] < 1.0


def test_cross_file_removed_target_leaves_no_stale_edge(tmp_path: Path):
    # O: after a clean re-analysis where the target is gone, no stale cross-file
    # relation may survive (edges are derived fresh from the emitted corpus).
    callee = _write(
        tmp_path / "src/acme/Removed.java",
        "package acme;\npublic class Removed { public double f(double a){ return a; } }\n",
    )
    caller = _write(
        tmp_path / "src/acme/Caller.java",
        "package acme;\npublic class Caller { private Removed r; public double go(){ double v=1.0; return r.f(v); } }\n",
    )
    full = extract([callee, caller], cache_root=tmp_path / "c1")
    assert _cross_file_edges(full, "PASSED_AS_ARGUMENT")
    only_caller = extract([caller], cache_root=tmp_path / "c2")
    assert _cross_file_edges(only_caller, "PASSED_AS_ARGUMENT") == []


def test_cross_file_order_independent(tmp_path: Path):
    # K: the identical source set analyzed in a different traversal order must
    # yield the same normalized cross-file relationships.
    a = _write(
        tmp_path / "src/acme/PS.java",
        "package acme;\npublic class PS { public double calc(double b){ return b; } }\n",
    )
    b = _write(
        tmp_path / "src/acme/Ord.java",
        "package acme;\npublic class Ord { private PS ps; public double go(){ double b=1.0; return ps.calc(b); } }\n",
    )

    def norm(r: dict) -> set:
        return {
            (e["relation"], e["source"], e["target"])
            for e in r["edges"] if (e.get("metadata") or {}).get("cross_file")
        }

    r1 = extract([a, b], cache_root=tmp_path / "c1")
    r2 = extract([b, a], cache_root=tmp_path / "c2")
    assert norm(r1) == norm(r2)
    assert norm(r1)


def test_cross_file_checkout_root_portability(tmp_path: Path):
    # L: equivalent source trees under different absolute roots must produce
    # equivalent canonical data_value IDs and cross-file relationships, and no
    # absolute checkout path may leak into persisted ids.
    def build(base: Path) -> tuple[list[Path], Path]:
        src = base / "proj/src/acme"
        service = src / "PS.java"
        order = src / "Ord.java"
        service.parent.mkdir(parents=True, exist_ok=True)
        service.write_text("package acme;\npublic class PS { public double calc(double b){ return b; } }\n")
        order.write_text("package acme;\npublic class Ord { private PS ps; public double go(){ double b=1.0; return ps.calc(b); } }\n")
        return [service, order], base / "proj"

    def extract_root(base: Path) -> tuple[list[Path], Path]:
        files, src_root = build(base)
        return files, src_root

    files1, src1 = extract_root(tmp_path / "rootA")
    files2, src2 = extract_root(tmp_path / "rootB")

    def norm(r: dict) -> dict:
        dv = {n["id"] for n in r["nodes"] if n.get("type") == "data_value"}
        fn = {n["id"] for n in r["nodes"] if n.get("type") == "function"}
        xf = {
            (e["relation"], e["source"], e["target"])
            for e in r["edges"] if (e.get("metadata") or {}).get("cross_file")
        }
        abs_leak = {n["id"] for n in r["nodes"] if "rootA" in n["id"] or "rootB" in n["id"]}
        return {"dv": dv, "fn": fn, "xf": xf, "abs_leak": abs_leak, "nodes": r["nodes"], "edges": r["edges"]}

    r1 = norm(extract(files1, root=src1, cache_root=tmp_path / "out1"))
    r2 = norm(extract(files2, root=src2, cache_root=tmp_path / "out2"))
    # Same number of cross-file edges and IDENTICAL canonical ids/endpoints.
    assert r1["xf"] and len(r1["xf"]) == len(r2["xf"])
    assert r1["dv"] == r2["dv"]
    assert r1["fn"] == r2["fn"]
    assert r1["xf"] == r2["xf"]
    assert not r1["abs_leak"] and not r2["abs_leak"]
    # All endpoints valid in each graph and canonical (match a node id).
    for r in (r1, r2):
        ids = {n["id"] for n in r["nodes"]}
        for rel, s, t in r["xf"]:
            assert s in ids and t in ids


def test_cross_file_multi_parameter_dependency(tmp_path: Path):
    # E: `f(a,b){ return a; }` — argument 0 contributes to the return
    # (TRANSFORMED_BY), argument 1 must NOT (only PASSED_AS_ARGUMENT).
    calc = _write(
        tmp_path / "src/acme/Calc.java",
        "package acme;\npublic class Calc { public double f(double a, double b){ return a; } }\n",
    )
    user = _write(
        tmp_path / "src/acme/User.java",
        "package acme;\n"
        "public class User { private Calc c; public double go(){ double a1=1.0; double b1=2.0; return c.f(a1,b1); } }\n",
    )
    result = extract([calc, user], cache_root=tmp_path)
    a1 = _data_value(result, "User.go()", "LOCAL", "a1")
    b1 = _data_value(result, "User.go()", "LOCAL", "b1")
    pa = _data_value(result, "Calc.f(double,double)", "PARAMETER", "a")
    pb = _data_value(result, "Calc.f(double,double)", "PARAMETER", "b")
    ret = _data_value(result, "Calc.f(double,double)", "RETURN_VALUE")
    assert all([a1, b1, pa, pb, ret])

    transformed = [e for e in _cross_file_edges(result, "TRANSFORMED_BY")
                   if (e["metadata"]).get("calleeSymbol", "").startswith("Calc.f")]
    assert len(transformed) == 1
    assert transformed[0]["source"] == a1 and transformed[0]["target"] == ret
    # b must not be TRANSFORMED_BY (return does not depend on it).
    assert transformed[0]["source"] != b1
