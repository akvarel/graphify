from __future__ import annotations

from pathlib import Path

from graphify.extract import extract


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _build(tmp_path: Path):
    service = _write(
        tmp_path / "src/acme/PricingService.java",
        "package acme;\n"
        "public class PricingService { public double calculate(double base){ return base; } }\n",
    )
    order = _write(
        tmp_path / "src/acme/Order.java",
        "package acme;\n"
        "public class Order { private PricingService ps; "
        "public double go(){ double b=1.0; double r=ps.calculate(b); return r; } }\n",
    )
    return extract([service, order], cache_root=tmp_path)


def test_source_layer_preserves_provenance_and_receiver_confidence(tmp_path: Path):
    result = _build(tmp_path)
    passed = [
        e for e in result["edges"]
        if e.get("relation") == "PASSED_AS_ARGUMENT" and (e.get("metadata") or {}).get("cross_file")
    ]
    assert passed
    md = passed[0]["metadata"]
    # Provenance and epistemic metadata survive the public boundary.
    assert md["provenance"] == "CROSS_FILE"
    assert md["receiverConfidence"] in {"PROVEN", "MAY"}
    assert md["analysisCompleteness"] in {"COMPLETE_FOR_SUPPORTED_CONSTRUCT", "PARTIAL"}
    # MAY is not upgraded to a definite same-instance flow.
    if md["receiverConfidence"] == "MAY":
        assert passed[0]["confidence_score"] == 0.5
        assert "PROVEN" != md["receiverConfidence"]


def test_exact_source_and_target_identity_exposed(tmp_path: Path):
    result = _build(tmp_path)
    passed = [
        e for e in result["edges"]
        if e.get("relation") == "PASSED_AS_ARGUMENT" and (e.get("metadata") or {}).get("cross_file")
    ]
    md = passed[0]["metadata"]
    # Exact target identity, argument index, and locations are present.
    assert md["calleeSymbol"].startswith("PricingService.calculate")
    assert md["argumentIndex"] == 0
    assert md.get("source_location")
    assert md.get("receiver")
    # Both endpoints are real nodes (not phantom/shadow).
    ids = {n["id"] for n in result["nodes"]}
    assert passed[0]["source"] in ids and passed[0]["target"] in ids


def test_no_gvr_verdict_states_in_source_layer(tmp_path: Path):
    result = _build(tmp_path)
    forbidden = {
        "gvr_verdict", "GVR_VERIFIED", "RUNTIME_VERIFIED", "DEPLOYMENT_VERIFIED",
        "ROOT_CAUSE_VERIFIED", "VERIFIED", "verdict",
    }
    for node in result["nodes"]:
        md = node.get("metadata") or {}
        for k in md:
            assert not any(f in k.upper() for f in forbidden), f"node {node['id']} leaked verdict key {k}"
        for v in md.values():
            if isinstance(v, str):
                assert not any(f in v.upper() for f in forbidden), (
                    f"node {node['id']} leaked verdict value {v!r}"
                )
    for edge in result["edges"]:
        md = edge.get("metadata") or {}
        for k in md:
            assert not any(f in k.upper() for f in forbidden), f"edge leaked verdict key {k}"
        for k in ("confidence",):
            assert k in edge
        assert "verdict" not in edge.get("confidence", "").lower()
    # `STATIC_AST + EXACT + score 1.0` is evidence, not a GVR verdict: no node
    # carries a verdict-typed state.
    assert all((n.get("metadata") or {}).get("provenance") != "GVR" for n in result["nodes"])


def test_no_global_completeness_claim_from_exact_local_fact(tmp_path: Path):
    result = _build(tmp_path)
    # An exact cross-file edge must not be labeled whole-program complete.
    for e in result["edges"]:
        md = e.get("metadata") or {}
        if md.get("provenance") == "CROSS_FILE":
            assert md.get("analysisCompleteness") != "COMPLETE_FOR_WHOLE_PROGRAM"
            assert "whole" not in str(md.get("analysisCompleteness", "")).lower()


def _cross_file_diags(result: dict) -> list[dict]:
    out = []
    for n in result["nodes"]:
        md = n.get("metadata") or {}
        if n.get("type") == "extraction_diagnostic" and md.get("kind") == "cross_file_resolution":
            out.append(md)
    return out


def _xf_edges(result: dict) -> list[dict]:
    return [e for e in result["edges"] if (e.get("metadata") or {}).get("cross_file")]


def test_wildcard_ambiguity_is_machine_visible_and_fails_closed(tmp_path: Path):
    # P0-2: wildcard-import ambiguity emits NO positive flow edge AND a
    # machine-visible AMBIGUOUS diagnostic.
    w = _write(tmp_path / "src/com/a/W.java",
               "package com.a; public class W { public int go(){ return 1; } }\n")
    user = _write(tmp_path / "src/x/WUser.java",
                  "package x; import com.a.*; "
                  "public class WUser { public int run(){ W w = new W(); return w.go(); } }\n")
    result = extract([w, user], root=tmp_path / "src", cache_root=tmp_path / "out")
    assert not _xf_edges(result), "wildcard ambiguity must not fabricate a positive edge"
    amb = [d for d in _cross_file_diags(result) if d["resolution"] == "AMBIGUOUS"]
    assert amb, "wildcard ambiguity must be machine-visible"
    assert any(d["reason"] == "wildcard_import_ambiguous" for d in amb)
    assert all(d["coverage"] == "PARTIAL" for d in amb)


def test_overload_ambiguity_is_machine_visible_and_fails_closed(tmp_path: Path):
    # P0-2: same-arity overload ambiguity emits no positive edge + AMBIGUOUS
    # diagnostic carrying the known candidate identities.
    calc = _write(tmp_path / "src/acme/Calc.java",
                  "package acme; public class Calc { public double f(double a){return a;} public double f(int a){return a;} }\n")
    user = _write(tmp_path / "src/acme/User.java",
                  "package acme; public class User { private Calc c; "
                  "public double go(){ double x=1.0; return c.f(x); } }\n")
    result = extract([calc, user], root=tmp_path / "src", cache_root=tmp_path / "out")
    assert not _xf_edges(result), "overload ambiguity must not fabricate a positive edge"
    amb = [d for d in _cross_file_diags(result) if d["resolution"] == "AMBIGUOUS"]
    assert amb
    overload = [d for d in amb if d["reason"] == "overload_ambiguity"]
    assert overload
    assert overload[0]["candidateCount"] == 2
    assert len(overload[0].get("candidates", [])) == 2


def test_default_package_unresolved_is_machine_visible(tmp_path: Path):
    # P0-2: an attempted default-package call emits UNRESOLVED evidence (distinct
    # from "no flow exists") with no positive edge.
    util = _write(tmp_path / "src/Util.java", "class Util { public int compute(){ return 1; } }\n")
    user = _write(tmp_path / "src/User.java",
                  "class User { public int run(){ Util u = new Util(); return u.compute(); } }\n")
    result = extract([util, user], root=tmp_path / "src", cache_root=tmp_path / "out")
    assert not _xf_edges(result)
    unres = [d for d in _cross_file_diags(result) if d["resolution"] == "UNRESOLVED"]
    assert unres, "default-package unresolved boundary must be machine-visible"
    assert any(d["reason"] == "receiver_unresolved" for d in unres)


def test_unsupported_builtin_is_distinct_from_unresolved(tmp_path: Path):
    # P0-2: the analyzer KNOWS a JDK builtin (String) receiver is not an in-repo
    # cross-file boundary -> UNSUPPORTED, distinguishable from UNRESOLVED.
    user = _write(tmp_path / "src/acme/U.java",
                  "package acme; public class U { public int run(){ String s = new String(); return s.length(); } }\n")
    result = extract([user], root=tmp_path / "src", cache_root=tmp_path / "out")
    uns = [d for d in _cross_file_diags(result) if d["resolution"] == "UNSUPPORTED"]
    assert uns, "JDK builtin receiver must be UNSUPPORTED"
    assert all(d["reason"] == "receiver_unsupported_type" for d in uns)
    assert not _xf_edges(result)


def test_exact_resolution_has_evidence_and_positive_edges(tmp_path: Path):
    # P0-2/P1-1: exact resolution emits EXACT evidence AND the expected positive
    # edges; statistics derive from the public graph.
    from graphify.extractors.resolution import cross_file_resolution_stats
    service = _write(tmp_path / "src/acme/PS.java",
                     "package acme; public class PS { public double calc(double b){ return b; } }\n")
    order = _write(tmp_path / "src/acme/Ord.java",
                   "package acme; public class Ord { private PS ps; "
                   "public double go(){ double b=1.0; return ps.calc(b); } }\n")
    result = extract([service, order], root=tmp_path / "src", cache_root=tmp_path / "out")
    exact = [d for d in _cross_file_diags(result) if d["resolution"] == "EXACT"]
    assert exact, "exact resolution must be machine-visible"
    assert _xf_edges(result), "exact resolution must emit positive edges"
    stats = cross_file_resolution_stats(result["nodes"], result["edges"])
    assert stats["exact"] >= 1
    assert stats["emitted"] >= 1
    assert stats["attempted"] == stats["exact"] + stats["ambiguous"] + stats["unresolved"] + stats["unsupported"]


def test_absence_of_boundary_is_distinct_from_attempted_unresolved(tmp_path: Path):
    # P0-2: a file with no cross-file boundary emits NO resolution diagnostic;
    # an attempted-but-unresolved boundary emits UNRESOLVED evidence.
    plain = _write(tmp_path / "src/acme/Plain.java",
                   "package acme; public class Plain { public int go(){ return 1; } }\n")
    r_plain = extract([plain], root=tmp_path / "src", cache_root=tmp_path / "outA")
    assert not _cross_file_diags(r_plain), "no attempted boundary -> no diagnostic"

    util = _write(tmp_path / "src/Util.java", "class Util { public int compute(){ return 1; } }\n")
    user = _write(tmp_path / "src/User.java",
                  "class User { public int run(){ Util u = new Util(); return u.compute(); } }\n")
    r_unres = extract([util, user], root=tmp_path / "src", cache_root=tmp_path / "outB")
    assert any(d["resolution"] == "UNRESOLVED" for d in _cross_file_diags(r_unres))


def test_resolution_diagnostics_are_bounded_nodes_not_transitive_facts(tmp_path: Path):
    # P0-2: diagnostics are nodes (never a positive relation/edge) and do not
    # create transitive graph facts.
    from graphify.extractors.resolution import cross_file_resolution_stats
    result = _build(tmp_path)
    diags = _cross_file_diags(result)
    assert diags
    # Every diagnostic is a node, not an edge.
    diag_ids = {d["callerFile"] + d["callerLocation"] for d in diags}
    # Diagnostics never appear as cross-file edge endpoints (no fabricated links).
    node_ids = {n["id"] for n in result["nodes"]}
    diag_node_ids = {
        n["id"] for n in result["nodes"]
        if n.get("type") == "extraction_diagnostic" and (n.get("metadata") or {}).get("kind") == "cross_file_resolution"
    }
    for e in _xf_edges(result):
        assert e["source"] not in diag_node_ids and e["target"] not in diag_node_ids
    # Statistics are bounded and consistent with the public evidence.
    stats = cross_file_resolution_stats(result["nodes"], result["edges"])
    assert stats["attempted"] == len(diags)
    assert stats["attempted"] <= 4  # bounded to actual attempted call sites
