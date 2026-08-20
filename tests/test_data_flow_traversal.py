"""Gate 3 graph-level bounded data-flow traversal tests.

These tests use hand-authored graph fixtures (independent of the Java
extractor) so traversal correctness is not circularly dependent on source
extraction. They cover fixtures A-T from the Gate 3 task, the GVR-oriented
dependency tests, and an adversarial falsification pass.
"""

from __future__ import annotations

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
    nodes = [_node("A"), _node("B", loc="L2")]
    edges = [_edge("A", "B", "FLOWS_TO", loc="L1")]
    r = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=0))
    assert r.truncated is False


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
