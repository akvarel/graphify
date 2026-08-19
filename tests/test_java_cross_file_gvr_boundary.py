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
