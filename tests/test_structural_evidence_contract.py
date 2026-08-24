"""RED-first contract tests for Graphify's public StructuralEvidenceSnapshot.

A StructuralEvidenceSnapshot is the versioned, deterministic, source-revision-scoped
public contract through which Graphify exports structural source evidence (typed
facts, provenance, coverage, and blockers) to downstream consumers such as GVR.

These tests are written BEFORE the implementation defines the contract surface
they assert against. They must stay GREEN once the contract is implemented and
must never regress.

Design non-goals enforced here:
  * No truth verdicts are emitted (no PASS/FAIL/VERIFIED verdict fields).
  * df evidence keys remain GVR-compatible (identical to the algorithm in
    ``gvr.graphify_contract`` and to ``data_flow_query._evidence_key``).
  * The snapshot is checkout-root independent and changes when the source
    revision changes.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from graphify.data_flow_query import DataFlowQuery, run_data_flow_query
from graphify.extract import extract
from graphify.structural_evidence import (
    BOUNDARY_KEY_RE,
    DATA_FLOW_KEY_RE,
    DIAGNOSTIC_KEY_RE,
    GRAPHIFY_PROVIDER_ID,
    SOURCE_CLASS_GIT_COMMIT,
    STRUCTURAL_EVIDENCE_FINGERPRINT_FORMAT,
    STRUCTURAL_EVIDENCE_FORMAT,
    STRUCTURAL_EVIDENCE_SCHEMA_VERSION,
    StructuralCoverage,
    StructuralEvidenceBlocker,
    StructuralEvidenceContractError,
    StructuralEvidenceFact,
    StructuralEvidencePath,
    StructuralEvidenceSnapshot,
    StructuralEvidenceCoverageState,
    StructuralProvenance,
    StructuralReceiverConfidence,
    SourceRevisionScope,
    SOURCE_REVISION_RE,
    boundary_evidence_key,
    build_structural_evidence_snapshot,
    df_key,
    df_key_from_edge,
    is_valid_df_key,
    load_snapshot,
    serialize_snapshot,
    structural_evidence_fingerprint,
    validate_df_key,
    validate_snapshot,
)


# A small adversarial graph (fixtures A-C) producing a real flow path plus a
# blocking boundary event, so the snapshot exercises facts + paths + blockers
# in one deterministic traversal.
def _node(nid: str, f: str = "f.java", loc: str = "L1") -> dict[str, Any]:
    return {
        "id": nid,
        "type": "data_value",
        "source_file": f,
        "source_location": loc,
        "confidence": "EXTRACTED",
        "confidence_score": 1.0,
        "metadata": {},
    }


def _edge(
    s: str,
    t: str,
    rel: str,
    f: str = "f.java",
    loc: str = "L1",
    *,
    cross_file: bool = False,
    conf_score: float = 1.0,
    receiver_conf: str = "PROVEN",
    completeness: str = "COMPLETE_FOR_SUPPORTED_CONSTRUCT",
    argument_index: int | None = None,
) -> dict[str, Any]:
    md: dict[str, Any] = {"provenance": "CROSS_FILE" if cross_file else "STATIC_AST"}
    if cross_file:
        md.update(
            {
                "cross_file": True,
                "receiverConfidence": receiver_conf,
                "analysisCompleteness": completeness,
            }
        )
        if argument_index is not None:
            md["argumentIndex"] = argument_index
    return {
        "source": s,
        "target": t,
        "relation": rel,
        "source_file": f,
        "source_location": loc,
        "confidence": "EXTRACTED",
        "confidence_score": conf_score,
        "weight": 1.0,
        "metadata": md,
    }


def _diag(
    resolution: str, caller_file: str, caller_loc: str, reason: str
) -> dict[str, Any]:
    return {
        "id": f"diag_{caller_file}_{reason}",
        "type": "extraction_diagnostic",
        "source_file": caller_file,
        "source_location": caller_loc,
        "metadata": {
            "kind": "cross_file_resolution",
            "resolution": resolution,
            "reason": reason,
            "callerFile": caller_file,
            "callerLocation": caller_loc,
            "method": "m",
            "arity": 1,
            "receiver": "R",
            "receiverFqn": "pkg.R",
            "importContext": "wildcard",
            "candidateCount": 2,
        },
    }


def _graph():
    """A -> B -> C with a blocking diagnostic on B's file."""
    nodes = [_node("A"), _node("B", loc="L2"), _node("x", "Other.java", "L1")]
    edges = [
        _edge("A", "B", "FLOWS_TO", loc="L1"),
        _edge("B", "x", "FLOWS_TO", "f.java", loc="L2", cross_file=True, receiver_conf="MAY"),
    ]
    nodes.append(_diag("AMBIGUOUS", "f.java", "L2:C10", "wildcard_import_ambiguous"))
    return nodes, edges


# --------------------------------------------------------------------------- #
# 1. Version, format, namespace, and registry constants
# --------------------------------------------------------------------------- #
def test_version_and_format_constants():
    assert STRUCTURAL_EVIDENCE_SCHEMA_VERSION == 1
    assert STRUCTURAL_EVIDENCE_FORMAT == "graphify.structural_evidence.v1"
    assert STRUCTURAL_EVIDENCE_FINGERPRINT_FORMAT.startswith(
        "graphify.structural_evidence"
    )
    assert GRAPHIFY_PROVIDER_ID == "graphify"
    assert SOURCE_CLASS_GIT_COMMIT == "git.commit"


def test_evidence_key_regexes_are_anchored():
    assert re.fullmatch(DATA_FLOW_KEY_RE, "df:" + "0" * 64)
    assert re.fullmatch(BOUNDARY_KEY_RE, "bnd:" + "0" * 64)
    assert re.fullmatch(DIAGNOSTIC_KEY_RE, "diag:anything:here")
    # wrong length / prefix rejected
    assert not re.fullmatch(DATA_FLOW_KEY_RE, "df:" + "0" * 63)
    assert not re.fullmatch(DATA_FLOW_KEY_RE, "df:" + "g" * 64)
    assert not re.fullmatch(BOUNDARY_KEY_RE, "df:" + "0" * 64)


# --------------------------------------------------------------------------- #
# 2. df key primitives are deterministic + GVR-compatible + content-addressed
# --------------------------------------------------------------------------- #
def test_df_key_is_deterministic_and_content_addressed():
    key = df_key(
        relation="FLOWS_TO",
        source="A",
        target="B",
        source_file="f.java",
        source_location="L1",
        provenance="STATIC_AST",
    )
    assert key.startswith("df:")
    assert re.fullmatch(DATA_FLOW_KEY_RE, key)
    # Same inputs => same key (deterministic).
    assert key == df_key(
        relation="FLOWS_TO", source="A", target="B",
        source_file="f.java", source_location="L1", provenance="STATIC_AST",
    )
    # Different inputs => different key (content-addressed).
    assert key != df_key(
        relation="FLOWS_TO", source="A", target="B",
        source_file="f.java", source_location="L1", provenance="CROSS_FILE",
    )
    assert key != df_key(
        relation="FLOWS_TO", source="A", target="C",
        source_file="f.java", source_location="L1", provenance="STATIC_AST",
    )


def test_df_key_argument_index_is_content_addressed():
    base = df_key("FLOWS_TO", "A", "B", "f.java", "L1", "STATIC_AST")
    with_ai = df_key(
        "PASSED_AS_ARGUMENT", "A", "B", "f.java", "L1", "STATIC_AST", argument_index=2
    )
    assert with_ai != base
    without = df_key(
        "PASSED_AS_ARGUMENT", "A", "B", "f.java", "L1", "STATIC_AST", argument_index=None
    )
    # argument_index=None must NOT contribute a stray field to the key.
    assert without == df_key("PASSED_AS_ARGUMENT", "A", "B", "f.java", "L1", "STATIC_AST")


def test_df_key_matches_reference_sha256():
    """The df key must equal the documented GVR-compatible canonical sha256."""
    import hashlib

    fields = [("r", "FLOWS_TO"), ("s", "A"), ("t", "B"),
              ("f", "f.java"), ("l", "L1"), ("p", "STATIC_AST")]
    canonical = json.dumps(sorted(fields), sort_keys=True, separators=(",", ":"))
    expected = "df:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert df_key("FLOWS_TO", "A", "B", "f.java", "L1", "STATIC_AST") == expected


def test_df_key_from_edge_matches_data_flow_query_evidence_key():
    """Single source of truth: the contract df-key == data_flow_query._evidence_key."""
    from graphify.data_flow_query import _evidence_key

    edge = _edge("A", "B", "PASSED_AS_ARGUMENT", "f.java", "L1", argument_index=0)
    assert df_key_from_edge(edge) == _evidence_key(edge)
    edge_cf = _edge("A", "B", "FLOWS_TO", "f.java", "L1", cross_file=True,
                    argument_index=1, receiver_conf="PROVEN", conf_score=0.5)
    assert df_key_from_edge(edge_cf) == _evidence_key(edge_cf)


def test_validate_df_key_accepts_valid_content_addressed_key():
    edge = _edge("A", "B", "FLOWS_TO", "f.java", "L1")
    item = {
        "key": df_key_from_edge(edge),
        "relation": edge["relation"],
        "source": edge["source"],
        "target": edge["target"],
        "source_file": edge["source_file"],
        "source_location": edge["source_location"],
        "provenance": (edge["metadata"] or {}).get("provenance"),
        "argument_index": (edge["metadata"] or {}).get("argumentIndex"),
    }
    assert is_valid_df_key(item)
    assert validate_df_key(item) == item["key"]


def test_validate_df_key_rejects_bad_keys():
    item = {
        "key": "df:" + "0" * 64,
        "relation": "FLOWS_TO", "source": "A", "target": "B",
        "source_file": "f.java", "source_location": "L1",
        "provenance": "STATIC_AST",
    }
    # Wrong format
    bad = dict(item, key="not-a-key")
    assert not is_valid_df_key(bad)
    with pytest.raises(StructuralEvidenceContractError):
        validate_df_key(bad)
    # Non-content-addressed: key does not match its content
    bad = dict(item, key="df:" + "1" * 64)
    with pytest.raises(StructuralEvidenceContractError):
        validate_df_key(bad)
    # Missing required content fields
    for field in ("relation", "source", "target", "source_file", "source_location", "provenance"):
        bad = dict(item)
        bad[field] = ""
        with pytest.raises(StructuralEvidenceContractError):
            validate_df_key(bad)


def test_validate_df_key_rejects_non_mapping():
    assert not is_valid_df_key("not a mapping")  # type: ignore[arg-type]
    assert not is_valid_df_key(None)  # type: ignore[arg-type]
    with pytest.raises(StructuralEvidenceContractError):
        validate_df_key(["a", "b"])  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 3. Enums: typed provenance / coverage / receiver confidence
# --------------------------------------------------------------------------- #
def test_provenance_enum():
    assert StructuralProvenance.STATIC_AST.value == "STATIC_AST"
    assert StructuralProvenance.CROSS_FILE.value == "CROSS_FILE"
    assert StructuralProvenance.FRAMEWORK_CONTRACT.value == "FRAMEWORK_CONTRACT"


def test_coverage_enum():
    assert StructuralCoverage.COMPLETE.value == "COMPLETE_FOR_SUPPORTED_CONSTRUCT"
    assert StructuralCoverage.PARTIAL.value == "PARTIAL"
    assert StructuralCoverage.UNKNOWN.value == "UNKNOWN"


def test_receiver_confidence_enum():
    assert StructuralReceiverConfidence.PROVEN.value == "PROVEN"
    assert StructuralReceiverConfidence.MAY.value == "MAY"


# --------------------------------------------------------------------------- #
# 4. Typed facts: df-key required, GVR-compatible, deterministic fingerprint
# --------------------------------------------------------------------------- #
def _fact(**over) -> StructuralEvidenceFact:
    edge = _edge("A", "B", "FLOWS_TO", "f.java", "L1")
    base = dict(
        relation=edge["relation"], source=edge["source"], target=edge["target"],
        source_file=edge["source_file"], source_location=edge["source_location"],
        provenance="STATIC_AST", analysis_completeness="COMPLETE_FOR_SUPPORTED_CONSTRUCT",
        receiver_confidence="PROVEN", argument_index=None, confidence_score=1.0,
        path_identity=(),
    )
    base.update(over)
    return StructuralEvidenceFact(**base)


def test_fact_requires_df_key():
    with pytest.raises(StructuralEvidenceContractError):
        _fact(key="bnd:" + "0" * 64)


def test_fact_key_is_computed_content_addressed():
    f = _fact(key=None)
    assert f.key == df_key("FLOWS_TO", "A", "B", "f.java", "L1", "STATIC_AST")


def test_fact_to_dict_and_fingerprint_stable():
    f = _fact()
    d = f.to_dict()
    assert d["key"] == f.key
    # Fingerprints are stable and deterministic.
    assert structural_evidence_fingerprint(d) == structural_evidence_fingerprint(d)
    assert f.fingerprint == structural_evidence_fingerprint(f.to_dict())
    # key is GVR-compatible.
    assert validate_df_key(d) == d["key"]


def test_fact_is_frozen_and_hashable():
    f = _fact()
    with pytest.raises(Exception):
        f.relation = "X"  # type: ignore[misc]
    assert isinstance(hash(f), int)


# --------------------------------------------------------------------------- #
# 5. Blockers: bnd keys, deterministic
# --------------------------------------------------------------------------- #
def test_blocker_requires_bnd_key():
    with pytest.raises(StructuralEvidenceContractError):
        StructuralEvidenceBlocker(
            key="df:" + "0" * 64, diagnostic_key="diag:x",
            resolution="AMBIGUOUS", reason="r",
        )


def test_blocker_key_is_bnd_and_fingerprint_stable():
    node = _diag("AMBIGUOUS", "f.java", "L2:C10", "wildcard_import_ambiguous")
    blk = StructuralEvidenceBlocker(
        key=boundary_evidence_key(node),
        diagnostic_key="diag:" + node["id"],
        resolution="AMBIGUOUS", reason="wildcard_import_ambiguous",
        canonical_caller_file="f.java", caller_location="L2:C10",
        receiver_fqn="pkg.R", method="m", arity=1,
        import_context="wildcard", candidate_count=2,
        receiver_confidence="MAY", reason_code="wildcard_import_ambiguous",
    )
    assert BOUNDARY_KEY_RE.fullmatch(blk.key)
    d = blk.to_dict()
    assert structural_evidence_fingerprint(d) == blk.fingerprint
    assert blk.key == boundary_evidence_key(node)


def test_boundary_evidence_key_matches_data_flow_key():
    from graphify.data_flow_query import _boundary_evidence_key

    node = _diag("AMBIGUOUS", "f.java", "L2:C10", "wildcard_import_ambiguous")
    assert boundary_evidence_key(node) == _boundary_evidence_key(node)
    # root independent
    other = dict(node)
    other["metadata"] = dict(node["metadata"], callerFile="/abs/rootB/f.java")
    assert boundary_evidence_key(node) == boundary_evidence_key(other)


# --------------------------------------------------------------------------- #
# 6. Source revision scope: git.commit, root independent
# --------------------------------------------------------------------------- #
def test_source_revision_scope():
    scope = SourceRevisionScope(
        provider_id="graphify",
        source_class=SOURCE_CLASS_GIT_COMMIT,
        source_revision="a" * 40,
        repo_root="/abs/path/to/repo",
    )
    assert scope.source_class == SOURCE_CLASS_GIT_COMMIT
    d = scope.to_dict()
    assert d["source_class"] == "git.commit"
    assert d["source_revision"] == "a" * 40
    fp = scope.fingerprint
    # fingerprint is checkout-root independent
    other = SourceRevisionScope(
        provider_id="graphify", source_class=SOURCE_CLASS_GIT_COMMIT,
        source_revision="a" * 40, repo_root="/abs/path/to/other/repo",
    )
    assert other.fingerprint == fp
    # different revision => different fingerprint
    changed = SourceRevisionScope(
        provider_id="graphify", source_class=SOURCE_CLASS_GIT_COMMIT,
        source_revision="b" * 40, repo_root="/abs/path/to/repo",
    )
    assert changed.fingerprint != fp


# --------------------------------------------------------------------------- #
# 7. Snapshot: build, serialize, validate, deterministic, root independent
# --------------------------------------------------------------------------- #
def _snapshot(revision: str = "a" * 40, root: str = "/abs/repo") -> StructuralEvidenceSnapshot:
    nodes, edges = _graph()
    result = run_data_flow_query(nodes, edges, DataFlowQuery(start="A", max_depth=5))
    return build_structural_evidence_snapshot(
        result, repo_root=root, source_revision=revision,
    )


def test_snapshot_is_versioned_and_namespaced():
    snap = _snapshot()
    d = snap.to_dict()
    assert d["schema_version"] == STRUCTURAL_EVIDENCE_SCHEMA_VERSION
    assert d["format"] == STRUCTURAL_EVIDENCE_FORMAT
    assert d["provider_id"] == GRAPHIFY_PROVIDER_ID
    assert d["source_revision_scope"]["source_class"] == "git.commit"
    assert d["source_revision_scope"]["source_revision"] == "a" * 40


def test_snapshot_has_no_truth_verdicts():
    snap = _snapshot()
    blob = json.dumps(snap.to_dict(), default=str)
    # No verdict-bearing fields, no VERIFIED* family tokens ever emitted.
    for token in ("verdict", "VERDICT", "VERIFIED", "GVR_VERIFIED",
                  "RUNTIME_VERIFIED", "DEPLOYMENT_VERIFIED", "ROOT_CAUSE_VERIFIED"):
        assert token not in blob, f"snapshot must not emit truth token {token!r}"


def test_snapshot_fingerprint_is_deterministic_and_excludes_self():
    snap = _snapshot()
    d = snap.to_dict()
    assert d["fingerprint"] == snap.fingerprint
    # Removing the fingerprint field must NOT change a recomputation from content.
    content = {k: v for k, v in d.items() if k != "fingerprint"}
    assert structural_evidence_fingerprint(content) == snap.fingerprint


def test_snapshot_is_checkout_root_independent():
    a = _snapshot(root="/abs/rootA")
    b = _snapshot(root="/abs/rootB")
    assert a.fingerprint == b.fingerprint
    blob = json.dumps(a.to_dict(), default=str)
    assert "/abs/rootA" not in blob and "/abs/rootB" not in blob


def test_snapshot_changes_with_source_revision():
    a = _snapshot(revision="a" * 40)
    b = _snapshot(revision="b" * 40)
    assert a.fingerprint != b.fingerprint
    assert a.to_dict()["source_revision_scope"]["source_revision"] == "a" * 40
    assert b.to_dict()["source_revision_scope"]["source_revision"] == "b" * 40


def test_snapshot_facts_are_df_compatible():
    snap = _snapshot()
    for fact in snap.to_dict()["facts"]:
        assert DATA_FLOW_KEY_RE.fullmatch(fact["key"])
        # every fact validates through the GVR-compatible validator
        assert validate_df_key(fact) == fact["key"]


def test_snapshot_blockers_are_bnd_compatible():
    snap = _snapshot()
    for blk in snap.to_dict()["blockers"]:
        assert BOUNDARY_KEY_RE.fullmatch(blk["key"])


def test_snapshot_facts_are_deduplicated_and_sorted():
    snap = _snapshot()
    facts = snap.to_dict()["facts"]
    keys = [f["key"] for f in facts]
    assert len(keys) == len(set(keys))
    assert keys == sorted(keys)


def test_snapshot_to_gvr_traversal_dict_roundtrips_through_gvr():
    """The public dict must be consumable by GVR's graphify adapter unchanged."""
    gvr = pytest.importorskip("gvr")
    snap = _snapshot()
    doc = snap.to_gvr_traversal_dict()
    # Every supporting evidence item must carry a valid, content-addressed df key.
    for path in doc.get("paths", []):
        for item in path.get("supporting_evidence", []):
            assert validate_df_key(item) == item["key"]
    # GVR's adapter must accept the public contract without error.
    evidence = gvr.adapters.graphify.ingest_traversal_result(doc)
    assert evidence.complete_supported_search == snap.to_dict()["coverage"]["complete_supported_search"]


def test_snapshot_roundtrip_json():
    snap = _snapshot()
    text = serialize_snapshot(snap)
    loaded = load_snapshot(text)
    assert loaded.fingerprint == snap.fingerprint
    assert loaded.to_dict() == snap.to_dict()


def test_validate_snapshot_rejects_tampered_fingerprint():
    snap = _snapshot()
    doc = snap.to_dict()
    doc["fingerprint"] = "0" * 64
    with pytest.raises(StructuralEvidenceContractError):
        validate_snapshot(doc)


def test_validate_snapshot_rejects_bad_fact_key():
    snap = _snapshot()
    doc = snap.to_dict()
    doc["facts"][0]["key"] = "df:" + "1" * 64
    with pytest.raises(StructuralEvidenceContractError):
        validate_snapshot(doc)


# --------------------------------------------------------------------------- #
# 8. Generated real fixture: produced by real extraction, deterministic
# --------------------------------------------------------------------------- #
_FIXTURE_JSON = Path(__file__).parent / "fixtures" / "structural_evidence" / "structural_evidence_snapshot.json"
_FIXTURE_SOURCE = Path(__file__).parent / "fixtures" / "structural_evidence" / "fixture_source"


def _commit_source_fixture(tmp_path: Path) -> str:
    """Create a real git repo with Java source and return the commit SHA.

    Fixed author identity + dates make the commit SHA deterministic.
    """
    repo = tmp_path / "fixture_repo"
    src = repo / "src" / "acme"
    src.mkdir(parents=True)
    (src / "Pipeline.java").write_text(
        "package acme;\n"
        "class Pipeline {\n"
        "    Result run(Value in) { return collect(transform(in)); }\n"
        "    Value transform(Value v) { return v; }\n"
        "    Result collect(Value v) { return new Result(v); }\n"
        "}\n",
        encoding="utf-8",
    )
    env = {
        "GIT_AUTHOR_NAME": "Graphify Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.com",
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00",
        "GIT_COMMITTER_NAME": "Graphify Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.com",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00",
    }
    full_env = {**__import__("os").environ, **env}
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=full_env)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=full_env)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=repo, check=True, env=full_env)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, env=full_env,
        capture_output=True, text=True,
    ).stdout.strip()
    return sha, repo


def _generate_real_fixture_snapshot(tmp_path: Path) -> dict[str, Any]:
    revision, repo = _commit_source_fixture(tmp_path)
    files = sorted(repo.rglob("*.java"))
    result = extract(files, root=repo, cache_root=tmp_path / "cache")
    # Find the PARAMETER named "in" in run() to seed the forward query.
    start = None
    for node in result["nodes"]:
        md = node.get("metadata") or {}
        if (node.get("type") == "data_value"
                and md.get("kind") == "PARAMETER"
                and md.get("name") == "in"
                and node.get("source_file", "").endswith("Pipeline.java")):
            start = node["id"]
            break
    assert start is not None, "could not locate parameter 'in' for traversal seed"
    traversal = run_data_flow_query(
        result["nodes"], result["edges"], DataFlowQuery(start=start, max_depth=6)
    )
    snap = build_structural_evidence_snapshot(
        traversal, repo_root=str(repo), source_revision=revision,
    )
    return snap.to_dict()


def test_generated_real_fixture_is_deterministic(tmp_path_factory):
    d1 = _generate_real_fixture_snapshot(tmp_path_factory.mktemp("r1"))
    d2 = _generate_real_fixture_snapshot(tmp_path_factory.mktemp("r2"))
    assert d1["fingerprint"] == d2["fingerprint"]
    assert d1 == d2


def test_generated_real_fixture_validates_and_is_gvr_compatible(tmp_path_factory):
    doc = _generate_real_fixture_snapshot(tmp_path_factory.mktemp("real"))
    snap = validate_snapshot(doc)  # re-canonicalizes + validates
    assert snap["format"] == STRUCTURAL_EVIDENCE_FORMAT
    # Every fact carries a valid GVR-compatible df key.
    for fact in doc["facts"]:
        assert validate_df_key(fact) == fact["key"]
    # No truth verdicts leak into a real, extracted snapshot.
    blob = json.dumps(doc, default=str)
    assert "VERDICT" not in blob and "VERIFIED" not in blob


def test_committed_real_fixture_exists_and_is_valid():
    if not _FIXTURE_JSON.exists():
        pytest.fail("committed real fixture missing; regenerate via _generate_real_fixture_snapshot")
    doc = json.loads(_FIXTURE_JSON.read_text(encoding="utf-8"))
    snap = validate_snapshot(doc)
    assert snap["format"] == STRUCTURAL_EVIDENCE_FORMAT
    assert SOURCE_REVISION_RE.fullmatch(doc["source_revision_scope"]["source_revision"])


def test_committed_fixture_is_current(tmp_path_factory):
    """The committed fixture must equal the generator output (no drift)."""
    if not _FIXTURE_JSON.exists():
        pytest.fail("committed real fixture missing")
    expected = json.loads(_FIXTURE_JSON.read_text(encoding="utf-8"))
    generated = _generate_real_fixture_snapshot(tmp_path_factory.mktemp("curr"))
    assert generated == expected
