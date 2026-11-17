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


@pytest.mark.parametrize(
    "suffix,member,xml",
    [
        (".odt", "content.xml", "<doc><p>Latvijas rēķins 3049.20</p></doc>"),
        (".ods", "content.xml", "<doc><cell>Invoice 1851.30</cell></doc>"),
        (".docx", "word/document.xml", "<doc><p>Agreement MB BUS</p></doc>"),
        (".xlsx", "xl/sharedStrings.xml", "<sst><si><t>MB BUS</t></si></sst>"),
    ],
)
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


def test_cli_and_no_network(tmp_path, monkeypatch, capsys):
    import socket
    import sys
    from graphify.__main__ import main

    def forbidden(*args, **kwargs):
        raise AssertionError("Network must never be used")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    root = tmp_path / "documents"
    root.mkdir()
    (root / "evidence.txt").write_text("Searchable local evidence")
    out = tmp_path / "private-index"
    for arguments in [
        ["index", str(root), "--out", str(out)],
        ["search", "evidence", "--index", str(out)],
        ["status", "--index", str(out)],
    ]:
        monkeypatch.setattr(sys, "argv", ["graphify", "documents", *arguments])
        main()
        assert "evidence" in capsys.readouterr().out or arguments[0] != "search"


def test_limits_binary_empty_and_pdf_errors(tmp_path, monkeypatch):
    from graphify import local_corpus

    root = tmp_path / "documents"
    root.mkdir()
    (root / "binary.txt").write_bytes(b"binary\x00data")
    (root / "empty.txt").write_text("")
    (root / "large.txt").write_text("x" * 101)
    (root / "broken.pdf").write_bytes(b"not a PDF")
    monkeypatch.setattr(local_corpus, "LIMIT", 100)
    report = build_index(root, tmp_path / "private-index")
    assert report["statuses"] == {
        "metadata_only": 1,
        "no_text": 1,
        "truncated": 1,
        "extraction_error": 1,
    }
    assert len(report["failures"]) == 3


def test_private_root_guards_and_nested_output(tmp_path):
    root = tmp_path / "documents"
    root.mkdir()
    with pytest.raises(ValueError, match="differ"):
        build_index(root, root)
    with pytest.raises(ValueError, match="existing"):
        build_index(root / "absent", tmp_path / "index")
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="private"):
        build_index(root, public)
    out = root / "index"
    (root / "good.txt").write_text("Evidence")
    assert build_index(root, out)["files"] == 1
    assert build_index(root, out)["files"] == 1


def test_utf16_entity_document_rejected(tmp_path):
    root = tmp_path / "documents"
    root.mkdir()
    with zipfile.ZipFile(root / "unsafe.odt", "w") as archive:
        archive.writestr(
            "content.xml", '<!DOCTYPE x [<!ENTITY x "unsafe">]><doc>&x;</doc>'.encode("utf-16")
        )
    assert build_index(root, tmp_path / "index")["errors"] == 1


def test_invoice_identifier_is_not_independent_prefixes(tmp_path):
    root = tmp_path / "documents"
    root.mkdir()
    (root / "correct.txt").write_text("Invoice 1-4-mb")
    (root / "other.txt").write_text("Invoice 1-2-mb registration 40203162453")
    out = tmp_path / "index"
    build_index(root, out)
    assert [Path(hit["path"]).name for hit in search_index(out, "1-4-mb")] == ["correct.txt"]
    with pytest.raises(sqlite3.OperationalError):
        search_index(tmp_path / "absent", "invoice")
    assert not (tmp_path / "absent").exists()
