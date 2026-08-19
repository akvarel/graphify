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
    Negative cases (I, J) must emit nothing (fail closed)."""
    files = [FIXTURE_ROOT / case["name"] / f for f in case["files"]]
    for f in files:
        assert f.is_file(), f"missing fixture file {f}"
    result = extract(files, cache_root=tmp_path / case["name"])
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
