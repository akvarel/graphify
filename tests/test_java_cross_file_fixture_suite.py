from __future__ import annotations

import json
from pathlib import Path

import pytest

from graphify.extract import extract

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "java_cross_file"


def _load_manifest() -> list[dict]:
    manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text())
    return manifest["cases"]


def _xf_relations(result: dict) -> set[str]:
    return {
        e["relation"]
        for e in result["edges"]
        if (e.get("metadata") or {}).get("cross_file")
    }


@pytest.mark.parametrize("case", _load_manifest(), ids=lambda c: c["name"])
def test_fixture_case_public_boundary(tmp_path: Path, case: dict):
    """Each fixture case must yield exactly its expected cross-file relations at
    the public extraction boundary, and must NOT yield any forbidden relation.
    Negative cases (I, J) must emit no positive flow edge (fail closed)."""
    files = [FIXTURE_ROOT / case["name"] / f for f in case["files"]]
    for f in files:
        assert f.is_file(), f"missing fixture file {f}"
    # Pass the scan root so the public boundary produces canonical repo-relative
    # node ids (root-independent), not absolute-checkout-derived slugs.
    result = extract(files, root=FIXTURE_ROOT, cache_root=tmp_path / case["name"])
    actual = _xf_relations(result)

    for rel in case["expected"]:
        assert rel in actual, (
            f"{case['name']}: expected cross-file {rel} but got {sorted(actual)}"
        )
    for rel in case["forbidden"]:
        assert rel not in actual, (
            f"{case['name']}: forbidden cross-file {rel} present ({sorted(actual)})"
        )
    # All cross-file edges must reference real nodes in the graph.
    ids = {n["id"] for n in result["nodes"]}
    for e in result["edges"]:
        if (e.get("metadata") or {}).get("cross_file"):
            assert e["source"] in ids, f"{case['name']}: dangling source {e['source']}"
            assert e["target"] in ids, f"{case['name']}: dangling target {e['target']}"


def test_fixture_case_canonical_ids_are_checkout_root_independent(tmp_path: Path):
    """P0-1: extracting the exact same fixture tree from two different absolute
    roots (via the normal public boundary, passing the scan root) yields
    identical canonical data_value node ids, Java method/owner ids, and
    cross-file edge endpoints."""
    import shutil

    def collect(scan_root: Path) -> dict:
        files = sorted(scan_root.rglob("*.java"))
        result = extract(files, root=scan_root, cache_root=tmp_path / scan_root.name)
        dv_ids = {n["id"] for n in result["nodes"] if n.get("type") == "data_value"}
        fn_ids = {n["id"] for n in result["nodes"] if n.get("type") == "function"}
        xf_edges = {
            (e["relation"], e["source"], e["target"])
            for e in result["edges"]
            if (e.get("metadata") or {}).get("cross_file")
        }
        # No absolute checkout path may leak into any persisted id.
        abs_leak = {
            n["id"] for n in result["nodes"]
            if "rootA" in n["id"] or "rootB" in n["id"]
        }
        return {
            "dv": dv_ids, "fn": fn_ids, "xf": xf_edges, "abs_leak": abs_leak,
            "nodes": result["nodes"], "edges": result["edges"],
        }

    roots = []
    for name in ("rootA", "rootB"):
        scan = tmp_path / name / "proj"
        scan.mkdir(parents=True, exist_ok=True)
        for case in _load_manifest():
            src = FIXTURE_ROOT / case["name"]
            dst = scan / case["name"]
            shutil.copytree(src, dst)
        roots.append((name, scan))

    results = {name: collect(scan) for name, scan in roots}

    # 1) Canonical data_value / method ids identical across roots.
    assert results["rootA"]["dv"] == results["rootB"]["dv"]
    assert results["rootA"]["fn"] == results["rootB"]["fn"]
    # 2) Cross-file edge endpoints identical across roots.
    assert results["rootA"]["xf"] == results["rootB"]["xf"]
    assert results["rootA"]["xf"], "expected cross-file edges"
    # 3) No absolute checkout path leaks into persisted ids.
    assert not results["rootA"]["abs_leak"]
    assert not results["rootB"]["abs_leak"]
    # 4) Every positive cross-file edge endpoint exists in the graph (canonical).
    for res in results.values():
        ids = {n["id"] for n in res["nodes"]}
        for e in res["edges"]:
            if (e.get("metadata") or {}).get("cross_file"):
                assert e["source"] in ids and e["target"] in ids


def test_fixture_repo_relative_paths_do_not_collide(tmp_path: Path):
    """P0-1: same-named Java files at different repo-relative paths must keep
    distinct canonical data_value ids and cross-file edge endpoints."""
    body = "package acme; public class Flow { public double go(){ double b=1.0; return b; } }\n"
    for sub in ("a/x", "b/x"):
        p = tmp_path / "proj" / sub / "Flow.java"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    files = sorted((tmp_path / "proj").rglob("*.java"))
    result = extract(files, root=tmp_path / "proj", cache_root=tmp_path / "out")
    dv = {n["id"] for n in result["nodes"] if n.get("type") == "data_value"}
    assert len(dv) == 4, f"expected 4 distinct data_value ids (2 files x param+return), got {len(dv)}"
    # Each file's ids must embed its own repo-relative path segment.
    ids_a = {i for i in dv if "a_x" in i}
    ids_b = {i for i in dv if "b_x" in i}
    assert ids_a and ids_b and not (ids_a & ids_b)
