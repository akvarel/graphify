"""Gate 3 Java integration traversal tests over real Gate 2B extraction output.

These tests run the bounded data-flow traversal over the graph produced by the
actual Java extractor + cross-file pass, so the traversal is validated end to
end on real source evidence (not hand-authored graph fixtures).

Fixture semantics note: the Gate 2B extractor captures a cross-file value-flow
chain as caller-arg --PASSED_AS_ARGUMENT--> callee-param,
callee-param --RETURNED_AS--> callee-return (and/or
caller-arg --TRANSFORMED_BY--> callee-return), and
callee-return --FLOWS_TO--> caller sink. Fixture case A_same_package therefore
yields the continuous 3-hop chain used below.
"""

from __future__ import annotations

import json
import random
import shutil
import tempfile
from pathlib import Path

from graphify.data_flow_query import DataFlowQuery, run_data_flow_query
from graphify.extract import extract

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "java_cross_file"


def _extract_case(case: str) -> dict:
    manifest_root = FIXTURE_ROOT / case
    files = sorted(manifest_root.rglob("*.java"))
    return extract(files, root=manifest_root, cache_root=Path(tempfile.mkdtemp()))


def _find_dv(result: dict, file_sub: str, kind: str, name: str) -> str:
    for n in result["nodes"]:
        if n.get("type") != "data_value":
            continue
        m = n.get("metadata") or {}
        if (n.get("source_file", "").endswith(file_sub) and m.get("kind") == kind
                and m.get("name") == name):
            return n["id"]
    raise AssertionError(f"no data_value {file_sub}/{kind}/{name}")


def _key_of(e: dict) -> str:
    from graphify.data_flow_query import _evidence_key
    return _evidence_key(e)


def test_integration_cross_file_chain_reaches_sink():
    """A: caller local -> callee param -> callee return -> caller local."""
    result = _extract_case("A_same_package")
    start = _find_dv(result, "Order.java", "LOCAL", "b")
    sink = _find_dv(result, "Order.java", "LOCAL", "r")
    r = run_data_flow_query(result["nodes"], result["edges"],
                            DataFlowQuery(start=start, target=sink, max_depth=6))
    reaches = [p for p in r.paths if p.steps and p.steps[-1].target == sink]
    assert reaches, "cross-file chain must reach the caller sink local"
    path = reaches[0]
    assert path.path_exactness == "EXACT_FOR_RETURNED_PATH"
    # every step references direct evidence present in the graph
    keys = {_key_of(e) for e in result["edges"]}
    for step in path.steps:
        assert step.evidence.key in keys
    # multi-hop chain (at least one non-PAA hop) is present
    assert any(len(p.steps) >= 3 for p in reaches), "expected a >=3 hop chain"


def test_integration_backward_from_sink():
    result = _extract_case("A_same_package")
    sink = _find_dv(result, "Order.java", "LOCAL", "r")
    r = run_data_flow_query(result["nodes"], result["edges"],
                            DataFlowQuery(start=sink, direction="BACKWARD", max_depth=6))
    assert any(len(p.steps) >= 2 for p in r.paths), "backward must find the chain"


def test_integration_constant_return_negative():
    """D: no path may claim the arg produced a constant-return callee result."""
    result = _extract_case("D_constant_return")
    assert not any((e.get("metadata") or {}).get("cross_file") and e["relation"] == "TRANSFORMED_BY"
                   for e in result["edges"])
    # the caller arg only reaches the callee parameter, never the return via flow
    start = _find_dv(result, "Use.java", "LOCAL", "x")
    r = run_data_flow_query(result["nodes"], result["edges"],
                            DataFlowQuery(start=start, max_depth=6))
    for p in r.paths:
        assert not any(s.relation in ("TRANSFORMED_BY", "RETURNED_AS") and
                       s.evidence.analysis_completeness == "COMPLETE_FOR_SUPPORTED_CONSTRUCT"
                       and s.target.startswith("const_") for s in p.steps)


def test_integration_receiver_may_preserved():
    """N: a MAY receiver path stays MAY, never PROVEN."""
    result = _extract_case("N_receiver_may")
    start = _find_dv(result, "Ord.java", "LOCAL", "b")
    r = run_data_flow_query(result["nodes"], result["edges"],
                            DataFlowQuery(start=start, max_depth=6))
    may_paths = [p for p in r.paths if any(s.evidence.receiver_confidence == "MAY" for s in p.steps)]
    assert may_paths, "expected at least one MAY receiver path"
    for p in may_paths:
        assert p.path_receiver_confidence == "MAY"
        assert p.path_receiver_confidence != "PROVEN"


def test_integration_overload_ambiguous_boundary():
    """I: overload ambiguity is a boundary event, never a fabricated path."""
    result = _extract_case("I_overload")
    assert not any((e.get("metadata") or {}).get("cross_file") for e in result["edges"])
    start = _find_dv(result, "AmbigUser.java", "LOCAL", "v")
    r = run_data_flow_query(result["nodes"], result["edges"],
                            DataFlowQuery(start=start, max_depth=6))
    res = [e["resolution"] for e in r.boundary_events]
    assert "AMBIGUOUS" in res
    assert r.complete_supported_search is False


def test_integration_wildcard_ambiguous_boundary():
    """J: wildcard ambiguity is a boundary event, never a fabricated path."""
    result = _extract_case("J_wildcard")
    assert not any((e.get("metadata") or {}).get("cross_file") for e in result["edges"])
    start = _find_dv(result, "WUser.java", "LOCAL", "v")
    r = run_data_flow_query(result["nodes"], result["edges"],
                            DataFlowQuery(start=start, max_depth=6))
    res = [e["resolution"] for e in r.boundary_events]
    assert "AMBIGUOUS" in res
    assert r.complete_supported_search is False


def test_integration_file_order_independence():
    result = _extract_case("A_same_package")
    start = _find_dv(result, "Order.java", "LOCAL", "b")
    sink = _find_dv(result, "Order.java", "LOCAL", "r")
    base = run_data_flow_query(result["nodes"], result["edges"],
                               DataFlowQuery(start=start, target=sink, max_depth=6))
    edges = list(result["edges"])
    nodes = list(result["nodes"])
    random.Random(3).shuffle(edges)
    nodes.reverse()
    shuffled = run_data_flow_query(nodes, edges, DataFlowQuery(start=start, target=sink, max_depth=6))
    assert [p.path_identity for p in base.paths] == [p.path_identity for p in shuffled.paths]


def test_integration_checkout_root_portability():
    """R: evidence keys/path identities identical across two checkout roots."""
    src = FIXTURE_ROOT / "A_same_package"
    results = []
    for tag in ("rootA", "rootB"):
        tmp = Path(tempfile.mkdtemp()) / tag
        shutil.copytree(src, tmp)
        result = extract(sorted(tmp.rglob("*.java")), root=tmp,
                         cache_root=Path(tempfile.mkdtemp()))
        start = _find_dv(result, "Order.java", "LOCAL", "b")
        sink = _find_dv(result, "Order.java", "LOCAL", "r")
        r = run_data_flow_query(result["nodes"], result["edges"],
                                DataFlowQuery(start=start, target=sink, max_depth=6))
        results.append(r)
    assert [p.path_identity for p in results[0].paths] == [p.path_identity for p in results[1].paths]
    k0 = {s.evidence.key for p in results[0].paths for s in p.steps}
    k1 = {s.evidence.key for p in results[1].paths for s in p.steps}
    assert k0 == k1


def test_integration_removed_direct_edge_changes_derived_path():
    """S: removing a supporting direct edge removes the derived path; nothing stale persists."""
    result = _extract_case("A_same_package")
    start = _find_dv(result, "Order.java", "LOCAL", "b")
    sink = _find_dv(result, "Order.java", "LOCAL", "r")
    full = run_data_flow_query(result["nodes"], result["edges"],
                               DataFlowQuery(start=start, target=sink, max_depth=6))
    assert any(p.steps[-1].target == sink for p in full.paths)
    # drop the FLOWS_TO edge that lands on the sink
    kept = [e for e in result["edges"] if not (e["relation"] == "FLOWS_TO" and e["target"] == sink)]
    reduced = run_data_flow_query(result["nodes"], kept, DataFlowQuery(start=start, target=sink, max_depth=6))
    assert not any(p.steps[-1].target == sink for p in reduced.paths)
    # no transitive start->sink persisted anywhere
    assert not any(e["source"] == start and e["target"] == sink for e in result["edges"])


def test_integration_point_to_point_truncation_no_completeness_claim():
    """T: zero target-paths under truncation does not claim complete supported search."""
    result = _extract_case("A_same_package")
    start = _find_dv(result, "Order.java", "LOCAL", "b")
    sink = _find_dv(result, "Order.java", "LOCAL", "r")
    r = run_data_flow_query(result["nodes"], result["edges"],
                            DataFlowQuery(start=start, target=sink, max_depth=1))
    # depth 1 cannot reach the multi-hop sink, so no target path
    assert not any(p.steps[-1].target == sink for p in r.paths)
    # search was bounded/truncated by depth, so it cannot claim complete coverage
    assert r.truncated is True or r.termination_reason in ("MAX_DEPTH",)
    assert r.complete_supported_search is False


def test_integration_same_file_receiver_may_preserved():
    """P0-1: a same-file receiver MAY edge stays MAY, never PROVEN."""
    result = _extract_case("O_same_file_receiver_may")
    start = _find_dv(result, "Ord.java", "FIELD", "v")
    r = run_data_flow_query(result["nodes"], result["edges"],
                            DataFlowQuery(start=start, max_depth=3))
    may_steps = [s for p in r.paths for s in p.steps
                 if s.evidence.receiver_confidence == "MAY"]
    assert may_steps, "expected a same-file MAY receiver step"
    for p in r.paths:
        assert p.path_receiver_confidence == "MAY"
        assert p.path_receiver_confidence != "PROVEN"


def test_integration_boundary_event_portability_across_roots():
    """P0-7: boundary events are checkout-root independent on real output."""
    src = FIXTURE_ROOT / "I_overload"
    results = []
    for tag in ("rootA", "rootB"):
        tmp = Path(tempfile.mkdtemp()) / tag
        shutil.copytree(src, tmp)
        result = extract(sorted(tmp.rglob("*.java")), root=tmp,
                         cache_root=Path(tempfile.mkdtemp()))
        start = _find_dv(result, "AmbigUser.java", "LOCAL", "v")
        r = run_data_flow_query(result["nodes"], result["edges"],
                                DataFlowQuery(start=start, max_depth=6))
        results.append(r)
    a, b = results
    assert a.boundary_events and b.boundary_events
    assert a.boundary_events == b.boundary_events
    blob = json.dumps(a.boundary_events)
    assert "/rootA/" not in blob and "/rootB/" not in blob
