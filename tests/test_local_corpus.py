"""Acceptance contracts for private local document indexing, without model calls."""
from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from graphify.local_corpus import build_index, search_index


def test_index_search_graph_and_incremental_refresh(tmp_path):
    root = tmp_path / "Dropbox"
    root.mkdir()
    invoice = root / "Rekins.txt"
    invoice.write_text("MB BUS rēķins 1-3-mb Summa 1579.05 EUR", encoding="utf-8")
    out = tmp_path / "index"
    first = build_index(root, out)
    assert first["text_files"] == 1
    hits = search_index(out, "MB BUS")
    assert hits[0]["path"] == str(invoice)
    assert "1579.05" in hits[0]["snippet"]
    graph = json.loads((out / "graph.json").read_text())
    assert any(n["source_file"] == str(invoice) for n in graph["nodes"])
    assert any("1579.05" in n["label"] for n in graph["nodes"])
    assert build_index(root, out)["reused"] == 1
    invoice.write_text("Replacement invoice 1851.30", encoding="utf-8")
    build_index(root, out)
    assert search_index(out, "replacement")
    assert not search_index(out, "1579")
    invoice.unlink()
    build_index(root, out)
    assert not search_index(out, "replacement")


def test_private_files_symlinks_and_cache_do_not_leak(tmp_path):
    root = tmp_path / "Dropbox"
    root.mkdir()
    (root / ".env").write_text("API_KEY=do-not-index")
    (root / "passwords.txt").write_text("do-not-index")
    cache = root / ".dropbox.cache"
    cache.mkdir()
    (cache / "stale.txt").write_text("do-not-index")
    outside = tmp_path / "outside.txt"
    outside.write_text("do-not-index")
    (root / "escape.txt").symlink_to(outside)
    (root / "config.txt").write_text("api_key = abcdefghijklmnopqrstuvwxyz")
    (root / "photo.jpg").write_bytes(b"not-an-image")
    out = tmp_path / "index"
    report = build_index(root, out)
    assert report["metadata_files"] == 1
    assert report["excluded"] >= 3
    assert search_index(out, "photo")[0]["status"] == "metadata_only"
    assert not search_index(out, "do-not-index")
    assert not search_index(out, "abcdefghijklmnopqrstuvwxyz")
    assert "abcdefghijklmnopqrstuvwxyz" not in (out / "graph.json").read_text()
    assert out.stat().st_mode & 0o077 == 0
    assert (out / "corpus.sqlite3").stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("suffix,member,xml", [
    (".odt", "content.xml", '<doc><p>Latvijas rēķins 3049.20</p></doc>'),
    (".ods", "content.xml", '<doc><cell>Invoice 1851.30</cell></doc>'),
    (".docx", "word/document.xml", '<doc><p>Agreement MB BUS</p></doc>'),
    (".xlsx", "xl/sharedStrings.xml", '<sst><si><t>MB BUS</t></si></sst>'),
])
def test_local_office_formats(tmp_path, suffix, member, xml):
    root = tmp_path / "Dropbox"
    root.mkdir()
    with zipfile.ZipFile(root / ("invoice" + suffix), "w") as archive:
        archive.writestr(member, xml)
    out = tmp_path / "index"
    assert build_index(root, out)["text_files"] == 1
    assert search_index(out, "invoice")


def test_broken_document_is_reported_without_losing_other_files(tmp_path):
    root = tmp_path / "Dropbox"
    root.mkdir()
    (root / "broken.ods").write_bytes(b"broken zip")
    (root / "good.txt").write_text("Useful searchable evidence")
    out = tmp_path / "index"
    report = build_index(root, out)
    assert report["errors"] == 1
    assert report["text_files"] == 1
    assert search_index(out, "useful")
    assert search_index(out, "broken")[0]["status"] == "extraction_error"


def test_unsafe_xml_and_secret_updates_remove_old_content(tmp_path):
    root = tmp_path / "Dropbox"
    root.mkdir()
    doc = root / "note.txt"
    doc.write_text("Old searchable content")
    out = tmp_path / "index"
    build_index(root, out)
    doc.write_text("password = abcdefghijklmnopqrstuvwxyz")
    build_index(root, out)
    assert not search_index(out, "searchable")
    with zipfile.ZipFile(root / "bomb.odt", "w") as archive:
        archive.writestr("content.xml", '<!DOCTYPE x [<!ENTITY x "secret">]><doc>&x;</doc>')
    assert build_index(root, out)["errors"] == 1


def test_search_literals_and_root_binding(tmp_path):
    root = tmp_path / "Dropbox"
    root.mkdir()
    (root / "1-3-mb.txt").write_text("Счёт клиенту MB BUS")
    out = tmp_path / "index"
    build_index(root, out)
    assert search_index(out, '"1-3-mb"')
    assert search_index(out, "счёт")
    assert search_index(out, "") == []
    other = tmp_path / "Other"
    other.mkdir()
    with pytest.raises(ValueError, match="root"):
        build_index(other, out)
    db = sqlite3.connect(out / "corpus.sqlite3")
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    db.close()
