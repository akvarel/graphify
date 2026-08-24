"""Task 35c RED-first analysis-result/source-authority binding tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from graphify.data_flow_query import DataFlowQuery, run_data_flow_query
from graphify.structural_evidence import (
    StructuralEvidenceContractError,
    build_bound_structural_index,
    build_structural_evidence_snapshot,
    derive_git_source_authority,
    load_snapshot,
    run_bound_data_flow_query,
    serialize_snapshot,
    structural_evidence_fingerprint,
)
from tests.test_structural_evidence_contract import _graph


_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Binding Fixture",
    "GIT_AUTHOR_EMAIL": "binding@example.com",
    "GIT_COMMITTER_NAME": "Binding Fixture",
    "GIT_COMMITTER_EMAIL": "binding@example.com",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
}


def _repo(path: Path, *, method: str = "transform") -> Path:
    src = path / "src" / "acme"
    src.mkdir(parents=True)
    (src / "Pipeline.java").write_text(
        "package acme;\n"
        "class Pipeline {\n"
        f"    Value run(Value in) {{ return {method}(in); }}\n"
        f"    Value {method}(Value v) {{ return v; }}\n"
        "}\n"
        "class Value {}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, env=_GIT_ENV)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, env=_GIT_ENV)
    subprocess.run(["git", "commit", "-q", "-m", "source"], cwd=path, check=True, env=_GIT_ENV)
    return path


def _query_for(index) -> DataFlowQuery:
    start = next(
        node["id"]
        for node in index.nodes
        if node.get("type") == "data_value"
        and (node.get("metadata") or {}).get("kind") == "PARAMETER"
        and (node.get("metadata") or {}).get("name") == "in"
    )
    return DataFlowQuery(start=start, max_depth=6)


def _analysis(repo: Path, cache: Path):
    authority = derive_git_source_authority(repo)
    index = build_bound_structural_index(
        authority,
        sorted(repo.rglob("*.java")),
        cache_root=cache,
    )
    return authority, index, run_bound_data_flow_query(index, _query_for(index))


def test_bound_source_analysis_builds_authoritative_snapshot(tmp_path: Path):
    repo = _repo(tmp_path / "a")
    authority, index, analysis = _analysis(repo, tmp_path / "cache-a")
    snapshot = build_structural_evidence_snapshot(analysis, source_revision=authority.source_revision)
    assert snapshot.source_revision_scope.source_revision == authority.source_revision
    assert snapshot.analysis_binding.index_fingerprint == index.index_fingerprint
    assert snapshot.analysis_binding.binding_fingerprint == analysis.binding_fingerprint


def test_cross_repo_authority_cannot_be_composed_with_bound_result(tmp_path: Path):
    repo_a = _repo(tmp_path / "a", method="transform")
    repo_b = _repo(tmp_path / "b", method="sanitize")
    _authority_a, _index_a, analysis_a = _analysis(repo_a, tmp_path / "cache-a")
    authority_b = derive_git_source_authority(repo_b)
    with pytest.raises(StructuralEvidenceContractError, match="source authority"):
        build_structural_evidence_snapshot(analysis_a, source_authority=authority_b)


def test_same_commit_and_index_at_two_roots_has_identical_identity(tmp_path: Path):
    repo_a = _repo(tmp_path / "a")
    repo_b = tmp_path / "b"
    shutil.copytree(repo_a, repo_b)
    _, index_a, analysis_a = _analysis(repo_a, tmp_path / "cache-a")
    _, index_b, analysis_b = _analysis(repo_b, tmp_path / "cache-b")
    snap_a = build_structural_evidence_snapshot(analysis_a)
    snap_b = build_structural_evidence_snapshot(analysis_b)
    assert index_a.index_fingerprint == index_b.index_fingerprint
    assert analysis_a.binding_fingerprint == analysis_b.binding_fingerprint
    assert snap_a.fingerprint == snap_b.fingerprint


def test_new_revision_requires_regenerated_analysis(tmp_path: Path):
    repo = _repo(tmp_path / "repo")
    authority_a, _index_a, analysis_a = _analysis(repo, tmp_path / "cache-a")
    snapshot_a = build_structural_evidence_snapshot(analysis_a)
    source = repo / "src" / "acme" / "Pipeline.java"
    source.write_text(source.read_text().replace("transform", "sanitize"), encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=_GIT_ENV)
    subprocess.run(["git", "commit", "-q", "-m", "revision-b"], cwd=repo, check=True, env=_GIT_ENV)
    authority_b, _index_b, analysis_b = _analysis(repo, tmp_path / "cache-b")
    snapshot_b = build_structural_evidence_snapshot(analysis_b)
    replayed_a = build_structural_evidence_snapshot(analysis_a)
    assert authority_a.source_revision != authority_b.source_revision
    assert analysis_a.binding_fingerprint != analysis_b.binding_fingerprint
    assert snapshot_a.fingerprint != snapshot_b.fingerprint
    assert replayed_a.fingerprint == snapshot_a.fingerprint
    with pytest.raises(StructuralEvidenceContractError, match="source authority"):
        build_structural_evidence_snapshot(analysis_a, source_authority=authority_b)


def test_source_change_after_authority_capture_fails_before_analysis(tmp_path: Path):
    repo = _repo(tmp_path / "repo")
    authority = derive_git_source_authority(repo)
    source = repo / "src" / "acme" / "Pipeline.java"
    source.write_text(source.read_text().replace("transform", "sanitize"), encoding="utf-8")
    with pytest.raises(StructuralEvidenceContractError, match="dirty|changed"):
        build_bound_structural_index(
            authority,
            sorted(repo.rglob("*.java")),
            cache_root=tmp_path / "cache",
        )


def test_unbound_synthetic_traversal_cannot_create_authoritative_snapshot(tmp_path: Path):
    repo = _repo(tmp_path / "repo")
    authority = derive_git_source_authority(repo)
    unbound = run_data_flow_query(*_graph(), DataFlowQuery(start="A", max_depth=5))
    with pytest.raises(StructuralEvidenceContractError, match="bound structural analysis"):
        build_structural_evidence_snapshot(cast(Any, unbound), source_authority=authority)


def test_bound_serialization_validates_internal_binding(tmp_path: Path):
    repo = _repo(tmp_path / "repo")
    _authority, _index, analysis = _analysis(repo, tmp_path / "cache")
    snapshot = build_structural_evidence_snapshot(analysis)
    loaded = load_snapshot(serialize_snapshot(snapshot))
    assert loaded.analysis_binding == snapshot.analysis_binding

    tampered = snapshot.to_dict()
    tampered["analysis_binding"]["index_fingerprint"] = "sha256:" + "0" * 64
    tampered["fingerprint"] = structural_evidence_fingerprint(
        {key: value for key, value in tampered.items() if key != "fingerprint"}
    )
    with pytest.raises(StructuralEvidenceContractError, match="binding"):
        load_snapshot(json.dumps(tampered))


def test_bound_result_detects_post_analysis_mutation(tmp_path: Path):
    repo = _repo(tmp_path / "repo")
    _authority, _index, analysis = _analysis(repo, tmp_path / "cache")
    analysis.result.query_bounds["max_depth"] = 999
    with pytest.raises(StructuralEvidenceContractError, match="binding"):
        build_structural_evidence_snapshot(analysis)
