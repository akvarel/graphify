"""Task 35b RED-first source-authority and builder-integrity tests."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from graphify.data_flow_query import DataFlowTraversalResult
from graphify.structural_evidence import (
    GRAPHIFY_PROVIDER_ID,
    StructuralEvidenceContractError,
    StructuralEvidenceSnapshot,
    build_structural_evidence_snapshot,
    derive_git_source_authority,
    load_snapshot,
    serialize_snapshot,
    structural_evidence_fingerprint,
)
from tests.test_structural_evidence_contract import _graph, _unsafe_bound_for_contract_test
from graphify.data_flow_query import DataFlowQuery, run_data_flow_query


def _git_repo(path: Path, content: str = "one\n") -> Path:
    path.mkdir(parents=True)
    (path / "source.txt").write_text(content, encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Authority Fixture",
        "GIT_AUTHOR_EMAIL": "authority@example.com",
        "GIT_COMMITTER_NAME": "Authority Fixture",
        "GIT_COMMITTER_EMAIL": "authority@example.com",
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
    }
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "source"], cwd=path, check=True, env=env)
    return path


def _result() -> DataFlowTraversalResult:
    return run_data_flow_query(*_graph(), DataFlowQuery(start="A", max_depth=5))


def _snapshot(repo: Path, **kwargs) -> StructuralEvidenceSnapshot:
    authority = derive_git_source_authority(repo)
    return build_structural_evidence_snapshot(
        _unsafe_bound_for_contract_test(_result(), authority), **kwargs
    )


def test_builder_revision_input_is_confirmation_not_authority(tmp_path: Path):
    repo = _git_repo(tmp_path / "repo")
    authority = derive_git_source_authority(repo)
    actual = authority.source_revision
    snap = build_structural_evidence_snapshot(
        _unsafe_bound_for_contract_test(_result(), authority), source_revision=actual
    )
    assert snap.source_revision_scope.source_revision == actual
    with pytest.raises(StructuralEvidenceContractError, match="expected source revision"):
        build_structural_evidence_snapshot(
            _unsafe_bound_for_contract_test(_result(), authority), source_revision="b" * 40
        )


def test_dirty_worktree_cannot_claim_clean_git_commit(tmp_path: Path):
    repo = _git_repo(tmp_path / "repo")
    (repo / "source.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(StructuralEvidenceContractError, match="dirty"):
        derive_git_source_authority(repo)


def test_authority_is_captured_and_not_reread_after_checkout_moves(tmp_path: Path):
    repo = _git_repo(tmp_path / "repo")
    authority = derive_git_source_authority(repo)
    captured = authority.source_revision
    (repo / "source.txt").write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=A", "-c", "user.email=a@example.com", "commit", "-q", "-m", "two"],
        cwd=repo,
        check=True,
    )
    snap = build_structural_evidence_snapshot(
        _unsafe_bound_for_contract_test(_result(), authority)
    )
    assert snap.source_revision_scope.source_revision == captured


def test_authoritative_revision_change_changes_identity(tmp_path: Path):
    repo_a = _git_repo(tmp_path / "a", "one\n")
    repo_b = _git_repo(tmp_path / "b", "two\n")
    assert _snapshot(repo_a).fingerprint != _snapshot(repo_b).fingerprint


def test_same_authoritative_state_is_checkout_root_independent(tmp_path: Path):
    repo_a = _git_repo(tmp_path / "a")
    repo_b = _git_repo(tmp_path / "b")
    assert _snapshot(repo_a).fingerprint == _snapshot(repo_b).fingerprint


def test_builder_rejects_conflicting_same_df_candidates(tmp_path: Path):
    result = _result()
    path = result.paths[0]
    original = path.supporting_evidence[0]
    conflicting = replace(original, confidence_score=0.125)
    conflicting_path = replace(
        path,
        supporting_evidence=(conflicting, *path.supporting_evidence[1:]),
    )
    conflict_result = replace(result, paths=(path, conflicting_path))
    authority = derive_git_source_authority(_git_repo(tmp_path / "repo"))
    with pytest.raises(StructuralEvidenceContractError, match="conflicting structural evidence"):
        build_structural_evidence_snapshot(
            _unsafe_bound_for_contract_test(conflict_result, authority)
        )


def test_builder_collapses_identical_fact_path_and_blocker_duplicates(tmp_path: Path):
    result = _result()
    duplicate = replace(
        result,
        paths=result.paths + result.paths,
        boundary_events=result.boundary_events + result.boundary_events,
    )
    snap = build_structural_evidence_snapshot(
        _unsafe_bound_for_contract_test(
            duplicate, derive_git_source_authority(_git_repo(tmp_path / "repo"))
        )
    )
    assert len(snap.paths) == len(result.paths)
    assert len(snap.blockers) == len(result.boundary_events)
    assert len({fact.key for fact in snap.facts}) == len(snap.facts)


def test_builder_rejects_conflicting_same_path_identity(tmp_path: Path):
    result = _result()
    path = result.paths[0]
    conflicting = replace(path, path_exactness="PARTIAL")
    authority = derive_git_source_authority(_git_repo(tmp_path / "repo"))
    with pytest.raises(StructuralEvidenceContractError, match="conflicting structural evidence"):
        build_structural_evidence_snapshot(
            _unsafe_bound_for_contract_test(
                replace(result, paths=(path, conflicting)), authority
            )
        )


def test_builder_rejects_conflicting_same_blocker_identity(tmp_path: Path):
    result = _result()
    event = result.boundary_events[0]
    conflicting = {**event, "candidate_count": int(event.get("candidate_count", 0)) + 1}
    authority = derive_git_source_authority(_git_repo(tmp_path / "repo"))
    with pytest.raises(StructuralEvidenceContractError, match="conflicting structural evidence"):
        build_structural_evidence_snapshot(
            _unsafe_bound_for_contract_test(
                replace(result, boundary_events=(event, conflicting)), authority
            )
        )


def test_snapshot_rejects_path_basis_mismatch_and_missing_refs(tmp_path: Path):
    snap = _snapshot(_git_repo(tmp_path / "repo"))
    path = snap.paths[0]
    mismatch = replace(path, supporting_evidence_keys=tuple(reversed(path.supporting_evidence_keys)))
    with pytest.raises(StructuralEvidenceContractError, match="path identity"):
        replace(snap, paths=(mismatch,))
    missing = replace(path, path_identity=("df:" + "0" * 64,), supporting_evidence_keys=("df:" + "0" * 64,))
    with pytest.raises(StructuralEvidenceContractError, match="unknown df key"):
        replace(snap, paths=(missing,))


def test_snapshot_rejects_provider_scope_mismatch(tmp_path: Path):
    snap = _snapshot(_git_repo(tmp_path / "repo"))
    bad_scope = replace(snap.source_revision_scope, provider_id="other")
    with pytest.raises(StructuralEvidenceContractError, match="provider"):
        replace(snap, source_revision_scope=bad_scope)


def test_roundtrip_revalidates_cross_references_and_source_authority(tmp_path: Path):
    snap = _snapshot(_git_repo(tmp_path / "repo"))
    loaded = load_snapshot(serialize_snapshot(snap))
    assert loaded.source_revision_scope.to_dict() == snap.source_revision_scope.to_dict()
    doc = snap.to_dict()
    doc["paths"][0]["supporting_evidence_keys"] = ["df:" + "0" * 64]
    doc["paths"][0]["path_identity"] = ["df:" + "0" * 64]
    content = {key: value for key, value in doc.items() if key != "fingerprint"}
    doc["fingerprint"] = structural_evidence_fingerprint(content)
    with pytest.raises(StructuralEvidenceContractError):
        load_snapshot(json.dumps(doc))


def test_silence_remains_non_authoritative_absence(tmp_path: Path):
    result = _result()
    silent = replace(
        result,
        paths=(),
        complete_supported_search=False,
        search_coverage="PARTIAL",
        encountered_partial_evidence=True,
    )
    snap = build_structural_evidence_snapshot(
        _unsafe_bound_for_contract_test(
            silent, derive_git_source_authority(_git_repo(tmp_path / "repo"))
        )
    )
    assert snap.paths == ()
    assert snap.coverage.complete_supported_search is False
    assert snap.coverage.search_coverage == "PARTIAL"
    assert "verdict" not in json.dumps(snap.to_dict()).lower()


def test_source_scope_provider_matches_public_provider(tmp_path: Path):
    snap = _snapshot(_git_repo(tmp_path / "repo"))
    assert snap.provider_id == GRAPHIFY_PROVIDER_ID
    assert snap.source_revision_scope.provider_id == GRAPHIFY_PROVIDER_ID
