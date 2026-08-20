"""Gate 3 graph-level bounded data-flow traversal tests.

These tests use hand-authored graph fixtures (independent of the Java
extractor) so traversal correctness is not circularly dependent on source
extraction. They cover fixtures A-T from the Gate 3 task, the GVR-oriented
dependency tests, and an adversarial falsification pass.
"""

from __future__ import annotations

import json
import random

from graphify.data_flow_query import (
    DEFAULT_DATA_FLOW_RELATIONS,
    DataFlowEvidenceRef,
    DataFlowPath,
    DataFlowQuery,
    DataFlowTraversalResult,
    run_data_flow_query,
)


def _node(nid: str, f: str = "f.java", loc: str = "L1", kind: str = "data_value") -> dict:
    return {
        "id": nid, "type": kind, "source_file": f, "source_location": loc,
        "confidence": "EXTRACTED", "confidence_score": 1.0, "metadata": {},
    }


def _diag(
    resolution: str, caller_file: str, caller_loc: str,
    reason: str, method: str = "m", arity: int = 1,
) -> dict:
    return {
        "id": f"diag_{caller_file}_{method}_{arity}",
        "type": "extraction_diagnostic", "source_file": caller_file,
        "source_location": caller_loc,
        "metadata": {
            "kind": "cross_file_resolution", "resolution": resolution,
            "reason": reason, "callerFile": caller_file,
            "callerLocation": caller_loc, "method": method, "arity": arity,
            "receiver": "R", "receiverFqn": "pkg.R",
            "importContext": "wildcard", "candidateCount": 2,
        },
    }


def _edge(
    s: str, t: str, rel: str, f: str = "f.java", loc: str = "L1",
    *, cross_file: bool = False, conf_score: float = 1.0,
    receiver_conf: str = "PROVEN", completeness: str = "COMPLETE_FOR_SUPPORTED_CONSTRUCT",
    argument_index: int | None = None, callee: str | None = None,
) -> dict:
    md: dict = {"provenance": "CROSS_FILE" if cross_file else "STATIC_AST"}
    if cross_file:
        md.update({
            "cross_file": True, "receiverConfidence": receiver_conf,
            "analysisCompleteness": completeness,
        })
        if argument_index is not None:
            md["argumentIndex"] = argument_index
        if callee is not None:
            md["callee"] = callee
    return {
        "source": s, "target": t, "relation": rel, "source_file": f,
        "source_location": loc, "confidence": "EXTRACTED",
        "confidence_score": conf_score, "weight": 1.0, "metadata": md,
    }


def _path_keys(r: DataFlowTraversalResult) -> list[tuple[str, ...]]:
    return [p.path_identity for p in r.paths]


def _step_node_seqs(r: DataFlowTraversalResult) -> list[list[str]]:
    return [[s.source for s in p.steps] + [p.steps[-1].target if p.steps else p.start]
            for p in r.paths]


# ---------------------------------------------------------------- A. same-file chain
def test_a_same_file_linear_chain_forward_and_backward():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    fwd = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=5))
    # paths A->B and A->B->C
    assert {tuple(p.path_identity) for p in fwd.paths}
    seqs = {tuple(s) for s in _step_node_seqs(fwd)}
    assert ("A", "B") in seqs and ("A", "B", "C") in seqs
    assert fwd.complete_supported_search is True and fwd.truncated is False
    bwd = run_data_flow_query(nodes, edges, DataFlowQuery(start="C", direction="BACKWARD", max_depth=5))
    bseqs = {tuple(s) for s in _step_node_seqs(bwd)}
    assert ("B", "C") in bseqs and ("A", "B", "C") in bseqs
    # forward and backward over the same direct facts are mutually consistent
    assert ("A", "B", "C") in seqs and ("A", "B", "C") in bseqs


# ---------------------------------------------------------------- B. cross-file chain
def test_b_cross_file_linear_chain_supports_evidence_dependencies():
    # caller local ->(PAA) callee param ->(FLOWS_TO) callee local ->(RETURNED_AS) callee return ->(FLOWS_TO) caller local
    nodes = [
        _node("caller_local", "Order.java", "L1"),
        _node("callee_param", "Service.java", "L1"),
        _node("callee_local", "Service.java", "L2"),
        _node("callee_return", "Service.java", "L3"),
        _node("caller_sink", "Order.java", "L2"),
    ]
    edges = [
        _edge("caller_local", "callee_param", "PASSED_AS_ARGUMENT", "Order.java",
              cross_file=True, argument_index=0, callee="Service.compute(int)"),
        _edge("callee_param", "callee_local", "FLOWS_TO", "Service.java"),
        _edge("callee_local", "callee_return", "RETURNED_AS", "Service.java"),
        _edge("callee_return", "caller_sink", "FLOWS_TO", "Order.java", loc="L2",
              cross_file=True, argument_index=0, callee="Service.compute(int)"),
    ]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="caller_local", max_depth=5))
    full = [p for p in r.paths if p.steps[-1].target == "caller_sink"]
    assert len(full) == 1
    path = full[0]
    assert len(path.steps) == 4
    assert all(isinstance(s.evidence, DataFlowEvidenceRef) for s in path.steps)
    # every step references a real direct edge dependency
    input_keys = {_edge_key(e) for e in edges}
    for step in path.steps:
        assert step.evidence.key in input_keys


def _edge_key(e: dict) -> str:
    from graphify.data_flow_query import _evidence_key
    return _evidence_key(e)


# ---------------------------------------------------------------- C. multi-hop, no persisted transitive
def test_c_multi_hop_across_three_files_no_persisted_transitive():
    nodes = [_node("A", "a.java"), _node("B", "b.java"), _node("C", "c.java")]
    edges = [
        _edge("A", "B", "PASSED_AS_ARGUMENT", "a.java", cross_file=True, callee="B.b(int)"),
        _edge("B", "C", "PASSED_AS_ARGUMENT", "b.java", cross_file=True, callee="C.c(int)"),
    ]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=5))
    reach_c = [p for p in r.paths if p.steps[-1].target == "C"]
    assert reach_c, "A=>C derived path must be found"
    # No transitive edge is persisted into the input graph
    assert not any(e["relation"] in ("CAN_FLOW_TO", "REACHES", "TRANSITIVE_FLOWS_TO")
                   for e in edges)
    assert not any(e["source"] == "A" and e["target"] == "C" for e in edges)


# ---------------------------------------------------------------- D. branching
def test_d_branching_returns_both_paths_deterministically():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("A", "C", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=2))
    targets = {p.steps[-1].target for p in r.paths}
    assert targets == {"B", "C"}
    assert r.complete_supported_search is True


# ---------------------------------------------------------------- E. merge (backward)
def test_e_merge_backward_finds_both_contributors():
    nodes = [_node("B", loc="L1"), _node("C", loc="L2"), _node("D", loc="L3")]
    edges = [_edge("B", "D", "FLOWS_TO", loc="L1"), _edge("C", "D", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="D", direction="BACKWARD", max_depth=2))
    sources = {p.steps[0].source for p in r.paths if p.steps}
    assert sources == {"B", "C"}


# ---------------------------------------------------------------- F. constant-return negative
def test_f_constant_return_negative_does_not_reach_return():
    # callee has no param->return TRANSFORMED_BY, so arg must not reach return
    nodes = [_node("arg", "Order.java"), _node("param", "Calc.java", "L1"),
             _node("ret", "Calc.java", "L2")]
    edges = [
        _edge("arg", "param", "PASSED_AS_ARGUMENT", "Order.java", cross_file=True,
              callee="Calc.constant()", argument_index=0),
        # NO TRANSFORMED_BY / RETURNED_AS: constant-return callee
    ]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="arg", max_depth=5))
    for p in r.paths:
        assert p.steps[-1].target != "ret"
    assert not any(p.steps[-1].target == "ret" for p in r.paths)


# ---------------------------------------------------------------- G. receiver MAY
def test_g_receiver_may_path_never_proven():
    nodes = [_node("a", "O.java"), _node("p", "S.java", "L1"), _node("r", "S.java", "L2")]
    edges = [
        _edge("a", "p", "PASSED_AS_ARGUMENT", "O.java", cross_file=True,
              receiver_conf="MAY", callee="S.calc(int)", argument_index=0),
        _edge("p", "r", "FLOWS_TO", "S.java"),
    ]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="a", max_depth=5))
    for p in r.paths:
        if p.steps[-1].target == "r":
            assert p.path_receiver_confidence == "MAY"
            assert p.path_receiver_confidence != "PROVEN"


# ---------------------------------------------------------------- H. parse-incomplete target
def test_h_parse_incomplete_target_is_partial():
    nodes = [_node("a", "O.java"), _node("p", "S.java", "L1"), _node("r", "S.java", "L2")]
    edges = [
        _edge("a", "p", "PASSED_AS_ARGUMENT", "O.java", cross_file=True,
              completeness="PARTIAL", callee="S.calc(int)", argument_index=0),
        _edge("p", "r", "FLOWS_TO", "S.java"),
    ]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="a", max_depth=5))
    for p in r.paths:
        assert p.path_coverage == "PARTIAL"
    assert r.search_coverage == "PARTIAL"


def _boundary_result(resolution: str, reason: str) -> DataFlowTraversalResult:
    nodes = [_node("v", "User.java", "L4"), _node("x", "Other.java", "L1")]
    edges = [_edge("v", "x", "FLOWS_TO", "User.java", loc="L4")]
    diag = _diag(resolution, "User.java", "L4:C10", reason)
    return run_data_flow_query(nodes + [diag], edges, DataFlowQuery(start="v", max_depth=5))


# ---------------------------------------------------------------- I/J/K boundary events
def test_i_wildcard_ambiguous_boundary_event_no_fabricated_path():
    r = _boundary_result("AMBIGUOUS", "wildcard_import_ambiguous")
    assert [e["resolution"] for e in r.boundary_events] == ["AMBIGUOUS"]
    # no fabricated path through the boundary
    assert all(p.steps[-1].target == "x" for p in r.paths)
    # boundary blocks completeness
    assert r.complete_supported_search is False


def test_j_overload_ambiguous_boundary_event():
    r = _boundary_result("AMBIGUOUS", "overload_ambiguity")
    assert [e["resolution"] for e in r.boundary_events] == ["AMBIGUOUS"]
    assert r.complete_supported_search is False


def test_k_unsupported_boundary_event():
    r = _boundary_result("UNSUPPORTED", "receiver_unsupported_type")
    assert [e["resolution"] for e in r.boundary_events] == ["UNSUPPORTED"]
    assert r.complete_supported_search is False


# ---------------------------------------------------------------- L. cycle
def test_l_cycle_terminates_and_respects_bounds():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [
        _edge("A", "B", "FLOWS_TO", loc="L1"),
        _edge("B", "A", "FLOWS_TO", loc="L2"),  # cycle A<->B
        _edge("B", "C", "FLOWS_TO", loc="L2"),
    ]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=4, max_paths=20))
    assert r.truncated is False
    seqs = {tuple(s) for s in _step_node_seqs(r)}
    # A->B, A->B->C must appear; no path repeats a node
    assert ("A", "B") in seqs and ("A", "B", "C") in seqs
    for seq in seqs:
        assert len(set(seq)) == len(seq), f"cycle repeated node in {seq}"


# ---------------------------------------------------------------- M/N/O truncation
def test_m_max_depth_truncation_machine_visible():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3"), _node("D", loc="L4")]
    edges = [
        _edge("A", "B", "FLOWS_TO", loc="L1"),
        _edge("B", "C", "FLOWS_TO", loc="L2"),
        _edge("C", "D", "FLOWS_TO", loc="L3"),
    ]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=2))
    assert r.truncated is True and r.termination_reason == "MAX_DEPTH"
    assert r.complete_supported_search is False


def test_n_max_paths_truncation():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3"), _node("D", loc="L4")]
    edges = [_edge("A", n, "FLOWS_TO", loc="L1") for n in ("B", "C", "D")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=1, max_paths=2))
    assert r.truncated is True and r.termination_reason == "MAX_PATHS"
    assert len(r.paths) == 2


def test_o_max_expansions_truncation():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3"), _node("D", loc="L4")]
    edges = [
        _edge("A", "B", "FLOWS_TO", loc="L1"),
        _edge("B", "C", "FLOWS_TO", loc="L2"),
        _edge("C", "D", "FLOWS_TO", loc="L3"),
    ]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=5, max_expansions=1))
    assert r.truncated is True and r.termination_reason == "MAX_EXPANSIONS"


# ---------------------------------------------------------------- P. parallel relations
def test_p_parallel_relations_distinct_path_identity():
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [
        _edge("A", "B", "READ_FROM", loc="L1"),
        _edge("A", "B", "FLOWS_TO", loc="L1"),
    ]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=2))
    identities = _path_keys(r)
    assert len(identities) == 2
    assert len(set(identities)) == 2  # same node sequence, different evidence


# ---------------------------------------------------------------- Q. file-order independence
def test_q_file_order_independence():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3"), _node("D", loc="L4")]
    edges = [
        _edge("A", "B", "FLOWS_TO", loc="L1"),
        _edge("B", "C", "FLOWS_TO", loc="L2"),
        _edge("A", "D", "FLOWS_TO", loc="L1"),
    ]
    base = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=5))
    shuffled_edges = list(edges)
    random.Random(7).shuffle(shuffled_edges)
    shuffled = run_data_flow_query(list(reversed(nodes)), shuffled_edges, DataFlowQuery(start="A", max_depth=5))
    assert _path_keys(base) == _path_keys(shuffled)
    assert [p.path_identity for p in base.paths] == [p.path_identity for p in shuffled.paths]


# ---------------------------------------------------------------- R. checkout-root portability
def test_r_checkout_root_portability():
    # Evidence keys ignore absolute metadata paths; only canonical fields count.
    e1 = _edge("A", "B", "FLOWS_TO", "src/x.java", loc="L1")
    e1 = {**e1, "metadata": {**e1["metadata"], "source_file": "/rootA/repo/src/x.java"}}
    e2 = _edge("A", "B", "FLOWS_TO", "src/x.java", loc="L1")
    e2 = {**e2, "metadata": {**e2["metadata"], "source_file": "/rootB/repo/src/x.java"}}
    assert _edge_key(e1) == _edge_key(e2)
    r1 = run_data_flow_query([_node("A"), _node("B", loc="L2")], [e1], DataFlowQuery(start="A", max_depth=2))
    r2 = run_data_flow_query([_node("A"), _node("B", loc="L2")], [e2], DataFlowQuery(start="A", max_depth=2))
    assert _path_keys(r1) == _path_keys(r2)


# ---------------------------------------------------------------- S. removed direct edge
def test_s_removed_direct_edge_changes_derived_path():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    full_edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    r_full = run_data_flow_query(nodes, full_edges, DataFlowQuery(start="A", target="C", max_depth=5))
    assert any(p.steps[-1].target == "C" for p in r_full.paths)
    # remove the supporting edge B->C
    reduced = run_data_flow_query(nodes, full_edges[:1], DataFlowQuery(start="A", target="C", max_depth=5))
    assert not any(p.steps[-1].target == "C" for p in reduced.paths)
    # no stale persisted transitive relation
    remaining = full_edges[:1]
    assert not any(e["source"] == "A" and e["target"] == "C" for e in remaining)


# ---------------------------------------------------------------- T. no-path complete vs incomplete
def test_t_no_path_complete_supported_search_distinction():
    # Complete supported search, no path A->C
    nodes = [_node("A"), _node("C", loc="L3")]
    edges = []  # nothing
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="C", max_depth=5))
    assert r.paths == () and r.complete_supported_search is True
    # Incomplete search (truncation) must NOT claim complete supported search
    nodes2 = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3"), _node("D", loc="L4")]
    edges2 = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    r2 = run_data_flow_query(nodes2, edges2, DataFlowQuery(start="A", target="C", max_depth=1))
    assert not any(p.steps[-1].target == "C" for p in r2.paths)
    assert r2.complete_supported_search is False


# ================================================================ GVR-oriented dependency tests
def test_gvr1_every_step_points_to_direct_evidence():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    input_keys = {_edge_key(e) for e in edges}
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=5))
    for p in r.paths:
        assert p.supporting_evidence
        for step in p.steps:
            assert step.evidence.key in input_keys
        assert len(p.supporting_evidence) == len(p.steps)


def test_gvr2_dependency_keys_deterministic():
    nodes = [_node("A"), _node("B", loc="L2")]
    e = _edge("A", "B", "FLOWS_TO", loc="L1")
    assert _edge_key(e) == _edge_key(dict(e))
    r = run_data_flow_query(nodes, [e], DataFlowQuery(start="A", max_depth=2))
    k1 = _path_keys(r)
    r2 = run_data_flow_query(nodes, [e], DataFlowQuery(start="A", max_depth=2))
    assert k1 == _path_keys(r2)


def test_gvr3_derived_path_has_no_gvr_verdict():
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=2))
    for p in r.paths:
        assert "VERIFIED" not in str(getattr(p, "path_exactness", ""))
        assert not hasattr(p, "gvr_verdict")
        assert p.path_exactness == "EXACT_FOR_RETURNED_PATH"


def test_gvr4_removing_supporting_edge_removes_derived_path():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="C", max_depth=5))
    assert any(p.steps[-1].target == "C" for p in r.paths)
    r2 = run_data_flow_query(nodes, edges[:1], DataFlowQuery(start="A", target="C", max_depth=5))
    assert not any(p.steps[-1].target == "C" for p in r2.paths)


def test_gvr5_ambiguity_on_frontier_preserved():
    r = _boundary_result("AMBIGUOUS", "wildcard_import_ambiguous")
    assert any(e["resolution"] == "AMBIGUOUS" for e in r.boundary_events)


def test_gvr6_truncation_preserved():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=1))
    assert r.truncated is True and r.termination_reason == "MAX_DEPTH"


def test_gvr7_may_never_upgraded():
    nodes = [_node("a", "O.java"), _node("p", "S.java", "L1"), _node("r", "S.java", "L2")]
    edges = [
        _edge("a", "p", "PASSED_AS_ARGUMENT", "O.java", cross_file=True,
              receiver_conf="MAY", callee="S.calc(int)", argument_index=0),
        _edge("p", "r", "FLOWS_TO", "S.java"),
    ]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="a", max_depth=5))
    assert all(p.path_receiver_confidence == "MAY" for p in r.paths)


def test_gvr8_partial_never_upgraded():
    nodes = [_node("a", "O.java"), _node("p", "S.java", "L1")]
    edges = [_edge("a", "p", "PASSED_AS_ARGUMENT", "O.java", cross_file=True,
                   completeness="PARTIAL", callee="S.calc(int)", argument_index=0)]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="a", max_depth=2))
    assert all(p.path_coverage == "PARTIAL" for p in r.paths)


def test_gvr9_exact_path_does_not_imply_global_completeness():
    nodes = [_node("v", "User.java", "L4"), _node("x", "Other.java", "L1")]
    edges = [_edge("v", "x", "FLOWS_TO", "User.java", loc="L4")]
    diag = _diag("AMBIGUOUS", "User.java", "L4:C10", "overload_ambiguity")
    r = run_data_flow_query(nodes + [diag], edges, DataFlowQuery(start="v", max_depth=5))
    assert any(p.path_exactness == "EXACT_FOR_RETURNED_PATH" for p in r.paths)
    assert r.complete_supported_search is False  # exact path, incomplete search


def test_gvr10_sufficient_for_claim_dependency_without_reparse():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="C", max_depth=5))
    p = [p for p in r.paths if p.steps and p.steps[-1].target == "C"][0]
    # A future adapter can express: Claim A CAN_FLOW_TO C, supports P1, P1 depends_on E1,E2,E3
    assert p.steps[0].source == "A" and p.steps[-1].target == "C"
    assert len(p.supporting_evidence) == 2
    for ref in p.supporting_evidence:
        assert ref.key and ref.relation and ref.source and ref.target


# ================================================================ adversarial falsification
def test_adv_diamond_dag_all_paths():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3"), _node("D", loc="L4")]
    edges = [
        _edge("A", "B", "FLOWS_TO", loc="L1"), _edge("A", "C", "FLOWS_TO", loc="L1"),
        _edge("B", "D", "FLOWS_TO", loc="L2"), _edge("C", "D", "FLOWS_TO", loc="L3"),
    ]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="D", max_depth=5))
    reaches_d = {tuple(s) for s in _step_node_seqs(r) if s[-1] == "D"}
    assert ("A", "B", "D") in reaches_d and ("A", "C", "D") in reaches_d


def test_adv_repeated_node_different_evidence_paths():
    # A->B and A->C->B : B reachable via two evidence-distinct paths
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [
        _edge("A", "B", "FLOWS_TO", loc="L1"),
        _edge("A", "C", "FLOWS_TO", loc="L1"),
        _edge("C", "B", "FLOWS_TO", loc="L3"),
    ]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="B", max_depth=5))
    seqs = {tuple(s) for s in _step_node_seqs(r) if s[-1] == "B"}
    assert ("A", "B") in seqs and ("A", "C", "B") in seqs


def test_adv_self_loop_terminates():
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "A", "FLOWS_TO", loc="L1"), _edge("A", "B", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=3))
    assert r.truncated is False or r.termination_reason in ("COMPLETE", "MAX_DEPTH")
    for p in r.paths:
        seq = [s.source for s in p.steps] + ([p.steps[-1].target] if p.steps else ["A"])
        assert len(set(seq)) == len(seq)  # no node repeats within a path


def test_adv_zero_depth_query():
    # Corrected semantics (P0-4): an eligible outgoing edge exists beyond the
    # zero-depth budget, so the search was cut off -> MAX_DEPTH, incomplete.
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=0))
    assert r.truncated is True and r.termination_reason == "MAX_DEPTH"
    assert r.complete_supported_search is False


def test_adv_start_equals_target_identity_path():
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="A", max_depth=3))
    assert len(r.paths) == 1 and r.paths[0].steps == ()


def test_adv_nonexistent_start_and_target_graceful():
    nodes = [_node("A")]
    edges = []
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="NOPE", target="NOPE2", max_depth=3))
    assert r.start_node_found is False and r.target_node_found is False
    assert r.paths == () and r.truncated is False


def test_adv_unknown_edge_relation_not_traversed():
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "CALLS", loc="L1")]  # CALLS is NOT value flow
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=2))
    assert r.paths == ()


def test_adv_exact_path_plus_unrelated_ambiguous_boundary():
    # ambiguous boundary in an unrelated file NOT reached by traversal => not surfaced
    nodes = [_node("A", "a.java"), _node("B", "a.java", "L2"), _node("u", "Unrelated.java", "L1")]
    diag = _diag("AMBIGUOUS", "Unrelated.java", "L1:C5", "wildcard_import_ambiguous")
    edges = [_edge("A", "B", "FLOWS_TO", "a.java", loc="L1")]
    r = run_data_flow_query(nodes + [diag], edges, DataFlowQuery(start="A", max_depth=2))
    assert r.boundary_events == ()  # unrelated boundary not reached
    assert any(p.steps[-1].target == "B" for p in r.paths)


def test_adv_duplicate_direct_edges_dedup():
    nodes = [_node("A"), _node("B", loc="L2")]
    e = _edge("A", "B", "FLOWS_TO", loc="L1")
    r = run_data_flow_query(nodes, [e, dict(e), dict(e)], DataFlowQuery(start="A", max_depth=2))
    assert len(_path_keys(r)) == 1  # identical evidence deduplicated


def test_adv_huge_branching_tiny_max_paths():
    nodes = [_node("A")] + [_node(f"B{i}", loc=f"L{i}") for i in range(50)]
    edges = [_edge("A", f"B{i}", "FLOWS_TO", loc="L1") for i in range(50)]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=1, max_paths=3))
    assert len(r.paths) == 3 and r.termination_reason == "MAX_PATHS"


def test_adv_max_expansions_hit_before_max_depth():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=5, max_expansions=1))
    assert r.termination_reason == "MAX_EXPANSIONS"


def test_adv_deterministic_under_shuffled_input():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3"), _node("D", loc="L4")]
    edges = [
        _edge("A", "B", "FLOWS_TO", loc="L1"),
        _edge("B", "C", "FLOWS_TO", loc="L2"),
        _edge("A", "D", "FLOWS_TO", loc="L1"),
    ]
    base = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=5))
    for seed in range(5):
        sedges = list(edges)
        random.Random(seed).shuffle(sedges)
        got = run_data_flow_query(list(reversed(nodes)), sedges, DataFlowQuery(start="A", max_depth=5))
        assert [p.path_identity for p in base.paths] == [p.path_identity for p in got.paths]


def test_adv_diagnostic_boundary_adjacent_to_valid_path():
    nodes = [_node("v", "User.java", "L4"), _node("x", "Other.java", "L1"),
             _node("z", "User.java", "L5")]
    edges = [
        _edge("v", "x", "FLOWS_TO", "User.java", loc="L4"),
        _edge("v", "z", "FLOWS_TO", "User.java", loc="L4"),
    ]
    diag = _diag("UNSUPPORTED", "User.java", "L4:C20", "receiver_unsupported_type")
    r = run_data_flow_query(nodes + [diag], edges, DataFlowQuery(start="v", max_depth=2))
    # valid paths still present; unsupported boundary surfaced
    assert [e["resolution"] for e in r.boundary_events] == ["UNSUPPORTED"]
    assert any(p.steps[-1].target == "x" for p in r.paths)


def test_adv_backward_consistency():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    fwd = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=5))
    bwd = run_data_flow_query(nodes, edges, DataFlowQuery(start="C", direction="BACKWARD", max_depth=5))
    # the same 2-hop relationship is reachable both directions
    assert any(p.steps[-1].target == "C" and len(p.steps) == 2 for p in fwd.paths)
    assert any(len(p.steps) == 2 for p in bwd.paths)


# ================================================================ Supervising-review falsification
# Section 12 mandatory adversarial cases + P0/P1 regressions
# (05-gate3-supervising-review-remediation). Every false-positive completeness
# claim and every uncertainty upgrade is a blocker.

# ---- P0-1: same-file receiver MAY must never become PROVEN ----
def test_remed_same_file_receiver_may_not_laundered():
    # Hand-authored same-file READ_FROM edge, receiverConfidence=MAY,
    # cross_file absent/false. Must stay MAY at step and path level.
    nodes = [_node("a", "O.java"), _node("f", "O.java", "L2")]
    edge = {
        "source": "a", "target": "f", "relation": "READ_FROM",
        "source_file": "O.java", "source_location": "L2",
        "confidence": "EXTRACTED", "confidence_score": 0.5,
        "weight": 1.0,
        "metadata": {"provenance": "STATIC_AST", "receiverConfidence": "MAY"},
    }
    r = run_data_flow_query(nodes, [edge], DataFlowQuery(start="a", max_depth=2))
    assert len(r.paths) == 1
    step = r.paths[0].steps[0]
    assert step.evidence.receiver_confidence == "MAY"
    assert r.paths[0].path_receiver_confidence == "MAY"
    assert r.paths[0].path_receiver_confidence != "PROVEN"


def test_remed_same_file_receiver_may_written_to():
    nodes = [_node("a", "O.java"), _node("f", "O.java", "L2")]
    edge = {
        "source": "a", "target": "f", "relation": "WRITTEN_TO",
        "source_file": "O.java", "source_location": "L2",
        "confidence": "EXTRACTED", "confidence_score": 0.5,
        "weight": 1.0,
        "metadata": {"provenance": "STATIC_AST", "receiverConfidence": "MAY"},
    }
    r = run_data_flow_query(nodes, [edge], DataFlowQuery(start="a", max_depth=2))
    assert r.paths[0].path_receiver_confidence == "MAY"


def test_remed_same_file_explicit_proven_receiver_stays_proven():
    nodes = [_node("a", "O.java"), _node("f", "O.java", "L2")]
    edge = {
        "source": "a", "target": "f", "relation": "READ_FROM",
        "source_file": "O.java", "source_location": "L2",
        "confidence": "EXTRACTED", "confidence_score": 1.0,
        "weight": 1.0,
        "metadata": {"provenance": "STATIC_AST", "receiverConfidence": "PROVEN"},
    }
    r = run_data_flow_query(nodes, [edge], DataFlowQuery(start="a", max_depth=2))
    assert r.paths[0].path_receiver_confidence == "PROVEN"


def test_remed_receiver_oriented_no_metadata_defaults_may():
    # A receiver-oriented relation with no receiverConfidence metadata cannot be
    # assumed PROVEN (P0-1: never infer PROVEN from cross_file == false).
    nodes = [_node("a", "O.java"), _node("f", "O.java", "L2")]
    edge = _edge("a", "f", "READ_FROM", "O.java", loc="L2")  # same-file, no metadata
    r = run_data_flow_query(nodes, [edge], DataFlowQuery(start="a", max_depth=2))
    assert r.paths[0].path_receiver_confidence == "MAY"


def test_remed_any_may_step_forces_path_may():
    # A chain where one hop is receiver MAY forces the whole path to MAY.
    nodes = [_node("a", "O.java"), _node("b", "O.java", "L2"), _node("c", "O.java", "L3")]
    e1 = {
        "source": "a", "target": "b", "relation": "FLOWS_TO",
        "source_file": "O.java", "source_location": "L1",
        "confidence": "EXTRACTED", "confidence_score": 1.0, "weight": 1.0,
        "metadata": {"provenance": "STATIC_AST"},
    }
    e2 = {
        "source": "b", "target": "c", "relation": "FLOWS_TO",
        "source_file": "O.java", "source_location": "L3",
        "confidence": "EXTRACTED", "confidence_score": 1.0, "weight": 1.0,
        "metadata": {"provenance": "STATIC_AST", "receiverConfidence": "MAY"},
    }
    r = run_data_flow_query(nodes, [e1, e2], DataFlowQuery(start="a", max_depth=5))
    two_hop = [p for p in r.paths if len(p.steps) == 2]
    assert two_hop and two_hop[0].path_receiver_confidence == "MAY"


# ---- P0-2: custom allowed_relations cannot turn CALLS/structural into value flow ----
def test_remed_calls_allowlist_injection_no_path():
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "CALLS", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(
        start="A", max_depth=2, allowed_relations=frozenset({"CALLS"})))
    assert r.paths == ()
    assert "CALLS" in r.rejected_relations
    assert "CALLS" not in r.query_bounds["effective_allowed_relations"]


def test_remed_mixed_valid_invalid_relations_only_valid_traversed():
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(
        start="A", max_depth=2, allowed_relations=frozenset({"FLOWS_TO", "CALLS"})))
    assert len(r.paths) == 1  # only FLOWS_TO traversed
    assert r.rejected_relations == ("CALLS",)


def test_remed_structural_relations_never_value_flow():
    for rel in ("imports", "references", "contains", "method", "inherits", "uses"):
        nodes = [_node("A"), _node("B", loc="L2")]
        edges = [_edge("A", "B", rel, loc="L1")]
        r = run_data_flow_query(nodes, edges, DataFlowQuery(
            start="A", max_depth=2, allowed_relations=frozenset({rel})))
        assert r.paths == (), f"{rel} must not become a value-flow step"
        assert rel in r.rejected_relations


# ---- P0-3: missing start/target never a complete search ----
def test_remed_missing_start_not_complete():
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="NOPE", max_depth=3))
    assert r.start_node_found is False
    assert r.paths == ()
    assert r.truncated is False
    assert r.termination_reason == "START_NODE_NOT_FOUND"
    assert r.input_resolution == "START_NODE_NOT_FOUND"
    assert r.search_coverage == "UNKNOWN"
    assert r.complete_supported_search is False


def test_remed_present_start_missing_target_not_complete():
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="NOPE2", max_depth=3))
    assert r.start_node_found is True and r.target_node_found is False
    assert r.paths == ()
    assert r.termination_reason == "TARGET_NODE_NOT_FOUND"
    assert r.input_resolution == "TARGET_NODE_NOT_FOUND"
    assert r.search_coverage == "UNKNOWN"
    assert r.complete_supported_search is False


def test_remed_both_missing_not_complete():
    nodes = [_node("A")]
    edges = []
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="NOPE", target="NOPE2", max_depth=3))
    assert r.start_node_found is False and r.target_node_found is False
    assert r.complete_supported_search is False
    assert r.search_coverage == "UNKNOWN"


# ---- P0-4: zero-depth semantics ----
def test_remed_zero_depth_no_eligible_edge_complete():
    nodes = [_node("A")]
    r = run_data_flow_query(nodes, [], DataFlowQuery(start="A", max_depth=0))
    assert r.truncated is False and r.termination_reason == "COMPLETE"
    assert r.complete_supported_search is True  # zero-expansion complete


def test_remed_zero_depth_with_edge_max_depth():
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=0))
    assert r.truncated is True and r.termination_reason == "MAX_DEPTH"
    assert r.complete_supported_search is False


def test_remed_zero_depth_backward_with_edge_max_depth():
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(
        start="B", direction="BACKWARD", max_depth=0))
    assert r.truncated is True and r.termination_reason == "MAX_DEPTH"
    assert r.complete_supported_search is False


def test_remed_zero_depth_identity_path_defined():
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="A", max_depth=0))
    # identity path is emitted and explicitly defined even at zero depth
    assert len(r.paths) == 1 and r.paths[0].steps == ()


# ---- P0-5: same-file degraded/partial evidence never upgraded to complete ----
def test_remed_same_file_partial_not_complete():
    nodes = [_node("a", "O.java"), _node("f", "O.java", "L2")]
    edge = {
        "source": "a", "target": "f", "relation": "FLOWS_TO",
        "source_file": "O.java", "source_location": "L2",
        "confidence": "EXTRACTED", "confidence_score": 0.8, "weight": 1.0,
        "metadata": {"provenance": "STATIC_AST", "analysisCompleteness": "PARTIAL"},
    }
    r = run_data_flow_query(nodes, [edge], DataFlowQuery(start="a", max_depth=2))
    assert r.paths[0].path_coverage == "PARTIAL"
    assert r.search_coverage == "PARTIAL"
    assert r.complete_supported_search is False


def test_remed_same_file_degraded_score_no_completeness_metadata_partial():
    # Same-file edge with degraded confidence_score and no explicit completeness
    # metadata: conservative fallback must not upgrade it to complete (P0-5).
    nodes = [_node("a", "O.java"), _node("f", "O.java", "L2")]
    edge = {
        "source": "a", "target": "f", "relation": "FLOWS_TO",
        "source_file": "O.java", "source_location": "L2",
        "confidence": "EXTRACTED", "confidence_score": 0.8, "weight": 1.0,
        "metadata": {"provenance": "STATIC_AST"},
    }
    r = run_data_flow_query(nodes, [edge], DataFlowQuery(start="a", max_depth=2))
    assert r.paths[0].path_coverage == "PARTIAL"


def test_remed_same_file_exact_remains_complete():
    nodes = [_node("a", "O.java"), _node("f", "O.java", "L2")]
    edge = _edge("a", "f", "FLOWS_TO", "O.java", loc="L2")
    r = run_data_flow_query(nodes, [edge], DataFlowQuery(start="a", max_depth=2))
    assert r.paths[0].path_coverage == "COMPLETE_FOR_SUPPORTED_CONSTRUCT"
    assert r.complete_supported_search is True


def test_remed_cross_file_partial_stays_partial():
    nodes = [_node("a", "O.java"), _node("p", "S.java", "L1")]
    edges = [_edge("a", "p", "PASSED_AS_ARGUMENT", "O.java", cross_file=True,
                   completeness="PARTIAL", callee="S.calc(int)", argument_index=0)]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="a", max_depth=2))
    assert r.paths[0].path_coverage == "PARTIAL"
    assert r.complete_supported_search is False


def test_remed_mixed_complete_partial_hop_stays_partial():
    nodes = [_node("a", "O.java"), _node("b", "O.java", "L2"), _node("c", "O.java", "L3")]
    e1 = _edge("a", "b", "FLOWS_TO", "O.java", loc="L1")  # complete
    e2 = {
        "source": "b", "target": "c", "relation": "FLOWS_TO",
        "source_file": "O.java", "source_location": "L3",
        "confidence": "EXTRACTED", "confidence_score": 0.8, "weight": 1.0,
        "metadata": {"provenance": "STATIC_AST", "analysisCompleteness": "PARTIAL"},
    }
    r = run_data_flow_query(nodes, [e1, e2], DataFlowQuery(start="a", max_depth=5))
    two_hop = [p for p in r.paths if len(p.steps) == 2]
    assert two_hop and two_hop[0].path_coverage == "PARTIAL"


# ---- P0-6: explored PARTIAL dead-end branch must degrade search ----
def test_remed_explored_partial_dead_end_degrades_search():
    # A->B (complete) -> X (PARTIAL dead end); target Z not reached.
    nodes = [_node("A"), _node("B", loc="L2"), _node("X", loc="L3"), _node("Z", loc="L9")]
    edges = [
        _edge("A", "B", "FLOWS_TO", loc="L1"),
        {
            "source": "B", "target": "X", "relation": "FLOWS_TO",
            "source_file": "f.java", "source_location": "L2",
            "confidence": "EXTRACTED", "confidence_score": 0.8, "weight": 1.0,
            "metadata": {"provenance": "STATIC_AST", "analysisCompleteness": "PARTIAL"},
        },
    ]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="Z", max_depth=5))
    assert not any(p.steps[-1].target == "Z" for p in r.paths)
    assert r.search_coverage == "PARTIAL"
    assert r.complete_supported_search is False


def test_remed_unrelated_partial_component_does_not_degrade():
    # A->B complete; a separate unreachable component has a PARTIAL edge.
    nodes = [_node("A", "a.java"), _node("B", "a.java", "L2"),
             _node("U", "unrel.java"), _node("V", "unrel.java", "L2")]
    edges = [
        _edge("A", "B", "FLOWS_TO", "a.java", loc="L1"),
        {
            "source": "U", "target": "V", "relation": "FLOWS_TO",
            "source_file": "unrel.java", "source_location": "L2",
            "confidence": "EXTRACTED", "confidence_score": 0.8, "weight": 1.0,
            "metadata": {"provenance": "STATIC_AST", "analysisCompleteness": "PARTIAL"},
        },
    ]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=2))
    assert r.complete_supported_search is True  # unrelated PARTIAL not reached
    assert r.encountered_partial_evidence is False


def test_remed_exact_path_plus_explored_partial_branch_coexists():
    # A->B exact; A->C->X explored PARTIAL branch; target B reached exactly.
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3"), _node("X", loc="L4")]
    edges = [
        _edge("A", "B", "FLOWS_TO", loc="L1"),
        _edge("A", "C", "FLOWS_TO", loc="L1"),
        {
            "source": "C", "target": "X", "relation": "FLOWS_TO",
            "source_file": "f.java", "source_location": "L3",
            "confidence": "EXTRACTED", "confidence_score": 0.8, "weight": 1.0,
            "metadata": {"provenance": "STATIC_AST", "analysisCompleteness": "PARTIAL"},
        },
    ]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=5))
    assert any(p.steps[-1].target == "B" for p in r.paths)
    assert any(p.path_exactness == "EXACT_FOR_RETURNED_PATH" for p in r.paths)
    assert r.search_coverage == "PARTIAL"  # exact path coexists with partial search
    assert r.complete_supported_search is False


def test_remed_explored_partial_dead_end_backward_degrades_search():
    # Backward from X through a partial branch.
    nodes = [_node("A"), _node("B", loc="L2"), _node("X", loc="L3")]
    edges = [
        _edge("A", "B", "FLOWS_TO", loc="L1"),
        {
            "source": "B", "target": "X", "relation": "FLOWS_TO",
            "source_file": "f.java", "source_location": "L2",
            "confidence": "EXTRACTED", "confidence_score": 0.8, "weight": 1.0,
            "metadata": {"provenance": "STATIC_AST", "analysisCompleteness": "PARTIAL"},
        },
    ]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="X", direction="BACKWARD", max_depth=5))
    assert r.search_coverage == "PARTIAL"
    assert r.complete_supported_search is False


# ---- P0-7: boundary-event portability + stable evidence dependency ----
def test_remed_boundary_event_has_stable_evidence_dependency():
    nodes = [_node("v", "User.java", "L4"), _node("x", "Other.java", "L1")]
    edges = [_edge("v", "x", "FLOWS_TO", "User.java", loc="L4")]
    diag = _diag("AMBIGUOUS", "User.java", "L4:C10", "wildcard_import_ambiguous")
    r = run_data_flow_query(nodes + [diag], edges, DataFlowQuery(start="v", max_depth=5))
    assert len(r.boundary_events) == 1
    ev = r.boundary_events[0]
    assert ev["boundary_evidence_key"].startswith("bnd:")
    assert ev["diagnostic_evidence_key"].startswith("diag:")
    assert ev["diagnostic_node_id"]
    assert ev["canonical_caller_file"] == "User.java"
    assert ev["resolution"] == "AMBIGUOUS"


def test_remed_boundary_event_checkout_root_portable():
    # Same diagnostic surfaced from two checkout roots must produce identical
    # boundary-event identity (no absolute checkout path leaks).
    nodes = [_node("v", "User.java", "L4"), _node("x", "Other.java", "L1")]
    edges = [_edge("v", "x", "FLOWS_TO", "User.java", loc="L4")]
    results = []
    for root in ("/rootA/project", "/rootB/project"):
        diag = {
            # Real Gate 2 diagnostic node ids derive from the file stem (not the
            # absolute checkout path), so the node id is root-independent here.
            "id": "diag_cross_file_data_flow_go_1_L4_C10",
            "type": "extraction_diagnostic", "source_file": "User.java",
            "source_location": "L4:C10",
            "metadata": {
                "kind": "cross_file_resolution", "resolution": "AMBIGUOUS",
                "reason": "wildcard_import_ambiguous", "callerFile": f"{root}/User.java",
                "callerLocation": "L4:C10", "method": "go", "arity": 1,
                "receiver": "R", "receiverFqn": "pkg.R", "importContext": "wildcard",
                "candidateCount": 2,
            },
        }
        r = run_data_flow_query(nodes + [diag], edges, DataFlowQuery(start="v", max_depth=5))
        results.append(r)
    a, b = results
    assert a.boundary_events[0] == b.boundary_events[0]
    assert a.boundary_events[0]["diagnostic_node_id"] == b.boundary_events[0]["diagnostic_node_id"]
    # no absolute checkout root leaks into the public GVR-facing identity
    blob = json.dumps(a.boundary_events[0])
    assert "/rootA/" not in blob and "/rootB/" not in blob


# ---- P1-1: MAX_PATHS means an actual cutoff ----
def test_remed_exactly_at_max_paths_complete():
    # A->B, A->B->C = exactly 2 paths, cap 2 => COMPLETE (not truncated).
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=5, max_paths=2))
    assert r.termination_reason == "COMPLETE"
    assert r.truncated is False
    assert len(r.paths) == 2


def test_remed_one_over_max_paths_truncated():
    nodes = [_node("A")] + [_node(f"B{i}", loc=f"L{i}") for i in range(4)]
    edges = [_edge("A", f"B{i}", "FLOWS_TO", loc="L1") for i in range(4)]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=1, max_paths=3))
    assert r.termination_reason == "MAX_PATHS"
    assert r.truncated is True
    assert len(r.paths) == 3


def test_remed_max_paths_deterministic_under_input_order():
    nodes = [_node("A")] + [_node(f"B{i}", loc=f"L{i}") for i in range(4)]
    edges = [_edge("A", f"B{i}", "FLOWS_TO", loc="L1") for i in range(4)]
    base = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=1, max_paths=3))
    sedges = list(edges)
    random.Random(1).shuffle(sedges)
    shuffled = run_data_flow_query(nodes, sedges, DataFlowQuery(start="A", max_depth=1, max_paths=3))
    assert [p.path_identity for p in base.paths] == [p.path_identity for p in shuffled.paths]
    assert base.termination_reason == shuffled.termination_reason == "MAX_PATHS"


# ---- P1-2: validate direction and bounds at runtime ----
def test_remed_invalid_direction_rejected():
    import pytest as _pt
    # Intentionally passes an invalid runtime value that the Literal type would
    # normally forbid; the module must reject it at runtime (P1-2).
    with _pt.raises(ValueError):
        run_data_flow_query([_node("A")], [], DataFlowQuery(start="A", direction="SIDEWAYS"))  # type: ignore[arg-type]


def test_remed_invalid_negative_depth_rejected():
    import pytest as _pt
    with _pt.raises(ValueError):
        run_data_flow_query([_node("A")], [], DataFlowQuery(start="A", max_depth=-1))


def test_remed_invalid_zero_max_paths_rejected():
    import pytest as _pt
    with _pt.raises(ValueError):
        run_data_flow_query([_node("A")], [], DataFlowQuery(start="A", max_paths=0))


def test_remed_invalid_zero_max_expansions_rejected():
    import pytest as _pt
    with _pt.raises(ValueError):
        run_data_flow_query([_node("A")], [], DataFlowQuery(start="A", max_expansions=0))


# ---- Shuffled order + parallel evidence-distinct (section 12 items 19-20) ----
def test_remed_shuffled_node_edge_order_stable():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3"), _node("D", loc="L4")]
    edges = [
        _edge("A", "B", "FLOWS_TO", loc="L1"),
        _edge("B", "C", "FLOWS_TO", loc="L2"),
        _edge("A", "D", "FLOWS_TO", loc="L1"),
        _edge("C", "D", "FLOWS_TO", loc="L3"),
    ]
    base = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=5))
    for seed in range(5):
        sedges = list(edges)
        random.Random(seed).shuffle(sedges)
        got = run_data_flow_query(list(reversed(nodes)), sedges, DataFlowQuery(start="A", max_depth=5))
        assert [p.path_identity for p in base.paths] == [p.path_identity for p in got.paths]


def test_remed_parallel_evidence_distinct_edges_stay_distinct():
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [
        _edge("A", "B", "READ_FROM", loc="L1"),
        _edge("A", "B", "FLOWS_TO", loc="L1"),
        _edge("A", "B", "WRITTEN_TO", loc="L1"),
    ]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=2))
    assert len(r.paths) == 3
    assert len({p.path_identity for p in r.paths}) == 3


def test_remed_explored_may_surfaced():
    nodes = [_node("a", "O.java"), _node("p", "S.java", "L1")]
    edges = [_edge("a", "p", "PASSED_AS_ARGUMENT", "O.java", cross_file=True,
                   receiver_conf="MAY", callee="S.calc(int)", argument_index=0)]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="a", max_depth=2))
    assert r.encountered_may_evidence is True


# ================================================================ Gate 3B: termination + accounting
# (06-gate3b-termination-accounting-remediation). P0-1..P0-4 and P1-2 pairwise
# interaction falsification matrix.

def _sem(r: DataFlowTraversalResult, paths: int, term: str, truncated: bool,
         complete: bool, visited: int, expanded: int) -> None:
    assert len(r.paths) == paths, (len(r.paths), r.termination_reason)
    assert r.termination_reason == term, r.termination_reason
    assert r.truncated is truncated, r.termination_reason
    assert r.complete_supported_search is complete
    assert r.visited_count == visited, r.visited_count
    assert r.expanded_count == expanded, r.expanded_count


# ---- P0-1: max_paths must not hide a real MAX_DEPTH cutoff ----
def test_3b_depth_and_path_cap_not_false_complete():
    # A->B->C, max_depth=1, max_paths=1, all-paths. B->C exists beyond depth,
    # so termination must be a real cutoff (MAX_DEPTH), never COMPLETE.
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=1, max_paths=1))
    _sem(r, paths=1, term="MAX_DEPTH", truncated=True, complete=False, visited=2, expanded=1)


def test_3b_depth_and_path_cap_backward_symmetric():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(
        start="C", direction="BACKWARD", max_depth=1, max_paths=1))
    _sem(r, paths=1, term="MAX_DEPTH", truncated=True, complete=False, visited=2, expanded=1)


def test_3b_path_cap_alone_with_no_continuation_complete():
    # A->B, max_depth=1, max_paths=1, B has no continuation => exactly one path,
    # all work exhausted => COMPLETE is allowed.
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=1, max_paths=1))
    _sem(r, paths=1, term="COMPLETE", truncated=False, complete=True, visited=2, expanded=1)


def test_3b_depth_path_both_active_deterministic():
    # Both depth cutoff and path cap could apply; precedence must be MAX_DEPTH.
    nodes = [_node("A")] + [_node(f"B{i}", loc=f"L{i}") for i in range(1, 4)]
    nodes += [_node("C", loc="L9")]
    edges = [_edge("A", "B1", "FLOWS_TO", loc="L1"), _edge("B1", "C", "FLOWS_TO", loc="L2")]
    edges += [_edge("A", "B2", "FLOWS_TO", loc="L1"), _edge("A", "B3", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=1, max_paths=2))
    assert r.termination_reason == "MAX_DEPTH"  # depth (3) precedes path cap (4)
    assert r.truncated is True and r.complete_supported_search is False


def test_3b_depth_path_cap_shuffled_order_stable():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    base = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=1, max_paths=1))
    for seed in range(5):
        sedges = list(edges)
        random.Random(seed).shuffle(sedges)
        got = run_data_flow_query(list(reversed(nodes)), sedges,
                                  DataFlowQuery(start="A", max_depth=1, max_paths=1))
        assert got.termination_reason == base.termination_reason == "MAX_DEPTH"
        assert got.complete_supported_search == base.complete_supported_search is False


# ---- P0-2: point-to-point target terminality ----
def test_3b_point_to_point_reached_target_not_pending_work():
    # A->B->C, target B, max_paths=1: B->C is NOT pending work for "A->B".
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="B", max_depth=5, max_paths=1))
    _sem(r, paths=1, term="COMPLETE", truncated=False, complete=True, visited=2, expanded=1)


def test_3b_point_to_point_alternate_target_path_triggers_max_paths():
    # Diamond A->B and A->X->B, target B, max_paths=1 => a second target path
    # remains => MAX_PATHS.
    nodes = [_node("A"), _node("B", loc="L2"), _node("X", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("A", "X", "FLOWS_TO", loc="L1"),
             _edge("X", "B", "FLOWS_TO", loc="L3")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="B", max_depth=5, max_paths=1))
    _sem(r, paths=1, term="MAX_PATHS", truncated=True, complete=False, visited=3, expanded=3)


def test_3b_point_to_point_diamond_paths2_complete():
    nodes = [_node("A"), _node("B", loc="L2"), _node("X", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("A", "X", "FLOWS_TO", loc="L1"),
             _edge("X", "B", "FLOWS_TO", loc="L3")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="B", max_depth=5, max_paths=2))
    _sem(r, paths=2, term="COMPLETE", truncated=False, complete=True, visited=3, expanded=3)


def test_3b_point_to_point_reached_target_with_cycle_no_false_truncation():
    # A->B target B, plus B->A cycle. Outgoing target edges must not create
    # false MAX_PATHS/MAX_DEPTH.
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "A", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="B", max_depth=5, max_paths=1))
    _sem(r, paths=1, term="COMPLETE", truncated=False, complete=True, visited=2, expanded=1)


def test_3b_point_to_point_backward_symmetric():
    # Single target path backward: chain A->B, from B to target A, max_paths=1
    # => COMPLETE. The diamond (two target paths) is asserted as MAX_PATHS.
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(
        start="B", target="A", direction="BACKWARD", max_depth=5, max_paths=1))
    _sem(r, paths=1, term="COMPLETE", truncated=False, complete=True, visited=2, expanded=1)
    # Diamond backward has two target paths (A->B and A->X->B) => MAX_PATHS at cap 1.
    nodes2 = [_node("A"), _node("B", loc="L2"), _node("X", loc="L3")]
    edges2 = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("A", "X", "FLOWS_TO", loc="L1"),
              _edge("X", "B", "FLOWS_TO", loc="L3")]
    r2 = run_data_flow_query(nodes2, edges2, DataFlowQuery(
        start="B", target="A", direction="BACKWARD", max_depth=5, max_paths=1))
    assert r2.termination_reason == "MAX_PATHS"
    assert r2.truncated is True and r2.complete_supported_search is False


# ---- P0-3: cycle-only continuations are not remaining work ----
def test_3b_cycle_only_no_false_depth_cutoff():
    # A->B, B->A, max_depth=2: the only continuation is cycle-forbidden.
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "A", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=2, max_paths=2))
    _sem(r, paths=1, term="COMPLETE", truncated=False, complete=True, visited=2, expanded=1)


def test_3b_cycle_only_no_false_path_cap():
    # A->B, B->A plus A->C alternate; max_paths=1. The cycle must not count as
    # work; MAX_PATHS comes only from the real alternate A->C.
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "A", "FLOWS_TO", loc="L2"),
             _edge("A", "C", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=5, max_paths=1))
    _sem(r, paths=1, term="MAX_PATHS", truncated=True, complete=False, visited=3, expanded=2)


def test_3b_one_cycle_plus_valid_continuation_detects_work():
    # A->B, B->A (cycle), B->C (valid continuation): valid work is explored.
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "A", "FLOWS_TO", loc="L2"),
             _edge("B", "C", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=5, max_paths=10))
    assert r.termination_reason == "COMPLETE"
    seqs = {tuple(s) for s in _step_node_seqs(r)}
    assert ("A", "B") in seqs and ("A", "B", "C") in seqs
    assert r.complete_supported_search is True


def test_3b_self_loop_only_complete_after_cycle_filter():
    nodes = [_node("A")]
    edges = [_edge("A", "A", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=5))
    _sem(r, paths=0, term="COMPLETE", truncated=False, complete=True, visited=1, expanded=0)


def test_3b_cycle_filter_backward_symmetric():
    # Backward: A->B, B->A cycle-only. Start B backward.
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "A", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="B", direction="BACKWARD", max_depth=2))
    _sem(r, paths=1, term="COMPLETE", truncated=False, complete=True, visited=2, expanded=1)


# ---- P0-4: visited_count / expanded_count truthful accounting ----
def test_3b_p2p_no_match_visited_reflects_explored_nodes():
    # A->B->C, target Z not present => visited reflects reached nodes A,B,C.
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3"), _node("Z", loc="L9")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="Z", max_depth=5))
    assert r.visited_count == 3  # A, B, C reached
    assert r.expanded_count == 2
    assert r.complete_supported_search is True  # complete search, no path


def test_3b_p2p_no_match_explored_partial_branch_visited_gt_zero():
    # Explored PARTIAL dead-end with no returned path: visited > 0.
    nodes = [_node("A"), _node("B", loc="L2"), _node("X", loc="L3"), _node("Z", loc="L9")]
    edges = [
        _edge("A", "B", "FLOWS_TO", loc="L1"),
        {"source": "B", "target": "X", "relation": "FLOWS_TO",
         "source_file": "f.java", "source_location": "L2",
         "confidence": "EXTRACTED", "confidence_score": 0.8, "weight": 1.0,
         "metadata": {"provenance": "STATIC_AST", "analysisCompleteness": "PARTIAL"}},
    ]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="Z", max_depth=5))
    assert r.visited_count == 3  # A, B, X reached even though no target path
    assert r.complete_supported_search is False  # explored PARTIAL degrades search
    assert r.search_coverage == "PARTIAL"


def test_3b_diamond_unique_node_count_not_path_multiplicative():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3"), _node("D", loc="L4")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("A", "C", "FLOWS_TO", loc="L1"),
             _edge("B", "D", "FLOWS_TO", loc="L2"), _edge("C", "D", "FLOWS_TO", loc="L3")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="D", max_depth=5))
    assert r.visited_count == 4  # unique nodes A,B,C,D, not path-multiplicative
    assert len(r.paths) == 2


def test_3b_cycle_no_double_count():
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "A", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=5))
    assert r.visited_count == 2  # A, B counted once despite cycle
    assert r.expanded_count == 1  # only A->B accepted; B->A cycle-rejected


def test_3b_max_expansions_cutoff_counts_only_reached():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3"), _node("D", loc="L4")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2"),
             _edge("C", "D", "FLOWS_TO", loc="L3")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=5, max_expansions=1))
    assert r.termination_reason == "MAX_EXPANSIONS"
    assert r.visited_count == 2  # A (start) + B (frontier of the single expansion)
    assert r.expanded_count == 1


def test_3b_backward_visited_accounting():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="C", direction="BACKWARD", max_depth=5))
    assert r.visited_count == 3  # C, B, A reached backward
    assert r.expanded_count == 2


def test_3b_identity_path_visited_one():
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="A", max_depth=3))
    _sem(r, paths=1, term="COMPLETE", truncated=False, complete=True, visited=1, expanded=0)


def test_3b_missing_start_visited_zero():
    nodes = [_node("A")]
    edges = [_edge("A", "A", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="NOPE", max_depth=3))
    assert r.visited_count == 0
    assert r.expanded_count == 0


# ---- P1-2: pairwise interaction falsification matrix ----
def test_3b_matrix_target_x_max_depth():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="C", max_depth=1))
    _sem(r, paths=0, term="MAX_DEPTH", truncated=True, complete=False, visited=2, expanded=1)


def test_3b_matrix_target_x_max_paths():
    nodes = [_node("A"), _node("B", loc="L2"), _node("X", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("A", "X", "FLOWS_TO", loc="L1"),
             _edge("X", "B", "FLOWS_TO", loc="L3")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="B", max_paths=1))
    _sem(r, paths=1, term="MAX_PATHS", truncated=True, complete=False, visited=3, expanded=3)


def test_3b_matrix_target_x_max_expansions():
    nodes = [_node("A"), _node("B", loc="L2"), _node("X", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("A", "X", "FLOWS_TO", loc="L1"),
             _edge("X", "B", "FLOWS_TO", loc="L3")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", target="B", max_expansions=2))
    _sem(r, paths=1, term="MAX_EXPANSIONS", truncated=True, complete=False, visited=3, expanded=2)


def test_3b_matrix_depth_x_paths():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=1, max_paths=1))
    _sem(r, paths=1, term="MAX_DEPTH", truncated=True, complete=False, visited=2, expanded=1)


def test_3b_matrix_depth_x_expansions():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=1, max_expansions=5))
    _sem(r, paths=1, term="MAX_DEPTH", truncated=True, complete=False, visited=2, expanded=1)


def test_3b_matrix_paths_x_expansions():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3"), _node("D", loc="L4")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("A", "C", "FLOWS_TO", loc="L1"),
             _edge("A", "D", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_paths=2, max_expansions=2))
    _sem(r, paths=2, term="MAX_EXPANSIONS", truncated=True, complete=False, visited=3, expanded=2)


def test_3b_matrix_cycles_x_depth():
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "A", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=1))
    _sem(r, paths=1, term="COMPLETE", truncated=False, complete=True, visited=2, expanded=1)


def test_3b_matrix_cycles_x_paths():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "A", "FLOWS_TO", loc="L2"),
             _edge("A", "C", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_paths=1))
    _sem(r, paths=1, term="MAX_PATHS", truncated=True, complete=False, visited=3, expanded=2)


def test_3b_matrix_stop_nodes_x_depth():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=2, stop_nodes=frozenset({"B"})))
    assert r.termination_reason == "COMPLETE"
    assert r.complete_supported_search is True  # stopped at B by design, not truncated
    seqs = {tuple(s) for s in _step_node_seqs(r)}
    assert ("A", "B") in seqs and ("A", "B", "C") not in seqs


def test_3b_matrix_stop_nodes_x_paths():
    nodes = [_node("A"), _node("B", loc="L2"), _node("C", loc="L3"), _node("D", loc="L4")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1"), _edge("B", "C", "FLOWS_TO", loc="L2"),
             _edge("A", "D", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_paths=1, stop_nodes=frozenset({"B"})))
    assert r.termination_reason == "MAX_PATHS"  # alternate A->D target path remains
    assert r.truncated is True and r.complete_supported_search is False
