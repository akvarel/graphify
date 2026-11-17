"""Private, local-only document corpus indexing and SQLite full-text search."""

from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import zipfile
import xml.etree.ElementTree as ET

from graphify.detect import _is_sensitive
from graphify.paths import write_json_atomic

LIMIT = 2_000_000
SKIP = {".git", ".svn", ".dropbox.cache", "__pycache__", "node_modules", "target"}
TEXT = {
    ".txt",
    ".md",
    ".rst",
    ".csv",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".yaml",
    ".yml",
    ".py",
    ".java",
    ".js",
    ".ts",
    ".sql",
    ".c",
    ".h",
    ".ini",
    ".log",
    ".css",
    ".tex",
}
SECRET = re.compile(
    r"-----BEGIN .*PRIVATE KEY-----|(?:api[_-]?key|password|passwd|secret|access[_-]?token)\s*[=:]\s*[\"\']?[^\s\"\']{8,}",
    re.I,
)


def extract(path):
    """Return bounded text and explicit extraction status, without network access."""
    suffix = path.suffix.lower()
    if path.stat().st_size > 50_000_000:
        return "", "size_limit"
    if suffix == ".pdf":
        result = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, timeout=30, check=True
        )
        data = result.stdout.decode("utf-8", errors="replace")
    elif suffix in {".odt", ".ods", ".odp", ".docx", ".xlsx", ".pptx"}:
        parts = []
        with zipfile.ZipFile(path) as archive:
            members = [
                n
                for n in archive.namelist()
                if n in {"content.xml", "word/document.xml", "xl/sharedStrings.xml"}
                or n.startswith(("xl/worksheets/", "ppt/slides/"))
                and n.endswith(".xml")
            ]
            if sum(archive.getinfo(n).file_size for n in members) > 20_000_000:
                raise ValueError("archive expansion limit")
            for name in members:
                raw = archive.read(name)
                decoded = raw.decode("utf-8-sig")
                if "<!DOCTYPE" in decoded.upper() or "<!ENTITY" in decoded.upper():
                    raise ValueError("unsafe XML")
                parts.append(" ".join(ET.fromstring(raw).itertext()))
        data = "\n".join(parts)
    elif suffix in TEXT:
        with path.open("rb") as stream:
            raw = stream.read(LIMIT + 1)
        if b"\x00" in raw:
            return "", "metadata_only"
        data = raw.decode("utf-8", errors="replace")
    else:
        return "", "metadata_only"
    if SECRET.search(data):
        return "", "excluded"
    return data[:LIMIT], "truncated" if len(data) > LIMIT else (
        "text" if data.strip() else "no_text"
    )


def build_index(root: Path, out: Path):
    """Refresh a private root-bound corpus. Unchanged source files are reused."""
    root, out = Path(root).resolve(), Path(out).resolve()
    if out == root:
        raise ValueError("index directory must differ from root")
    if not root.is_dir():
        raise ValueError("root must be an existing directory")
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    if out.stat().st_mode & 0o077:
        raise ValueError("index directory must be private")
    old_mask = os.umask(0o077)
    try:
        with (out / "index.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return _build(root, out)
    finally:
        os.umask(old_mask)


def _build(root, out):
    db = sqlite3.connect(out / "corpus.sqlite3")
    try:
        db.execute("CREATE TABLE IF NOT EXISTS settings (root TEXT)")
        previous = db.execute("SELECT root FROM settings").fetchone()
        if previous and previous[0] != str(root):
            raise ValueError("index belongs to another root")
        if not previous:
            db.execute("INSERT INTO settings VALUES (?)", (str(root),))
        db.execute(
            "CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, signature TEXT, status TEXT, content TEXT)"
        )
        db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(path, content, status UNINDEXED, tokenize='unicode61 remove_diacritics 2')"
        )
        old = {
            r[0]: r[1:] for r in db.execute("SELECT path, signature, status, content FROM files")
        }
        rows = []
        report = {
            "root": str(root),
            "files": 0,
            "text_files": 0,
            "metadata_files": 0,
            "excluded": 0,
            "errors": 0,
            "reused": 0,
        }
        statuses = Counter()
        for directory, dirs, names in os.walk(root, followlinks=False):
            base = Path(directory)
            dirs[:] = sorted(
                d
                for d in dirs
                if d not in SKIP
                and not (base / d).is_symlink()
                and (base / d).resolve() != out
                and not _is_sensitive((base / d).relative_to(root))
            )
            for name in sorted(names):
                path = base / name
                if path.is_symlink() or _is_sensitive(path.relative_to(root)):
                    report["excluded"] += 1
                    continue
                try:
                    stat = path.stat()
                    signature = f"{stat.st_mtime_ns}:{stat.st_ctime_ns}:{stat.st_size}"
                    existing = old.get(str(path))
                    if existing and existing[0] == signature:
                        _, status, text = existing
                        report["reused"] += 1
                    else:
                        text, status = extract(path)
                except (
                    OSError,
                    ValueError,
                    ET.ParseError,
                    zipfile.BadZipFile,
                    subprocess.SubprocessError,
                ):
                    signature, text, status = "", "", "extraction_error"
                if status == "excluded":
                    report["excluded"] += 1
                    continue
                rows.append((str(path), signature, status, text))
                statuses[status] += 1
                report["files"] += 1
                report["text_files"] += status in {"text", "truncated"}
                report["metadata_files"] += status == "metadata_only"
                report["errors"] += status == "extraction_error"
                if report["files"] % 100 == 0:
                    print(f"Indexed {report['files']} files", flush=True)
        with db:
            db.execute("DELETE FROM files")
            db.execute("DELETE FROM search")
            db.executemany("INSERT INTO files VALUES (?,?,?,?)", rows)
            db.executemany(
                "INSERT INTO search VALUES (?,?,?)",
                [(p, text, status) for p, _, status, text in rows],
            )
        nodes, links = [], []
        for path, _, status, text in rows:
            identity = hashlib.sha256(path.encode()).hexdigest()
            nodes.append(
                {
                    "id": identity,
                    "label": str(Path(path).relative_to(root)),
                    "source_file": path,
                    "type": "document",
                    "file_type": "document",
                    "status": status,
                }
            )
            for offset in range(0, len(text), 450):
                chunk = f"{identity}:{offset}"
                nodes.append(
                    {
                        "id": chunk,
                        "label": text[offset : offset + 450],
                        "source_file": path,
                        "type": "concept",
                        "file_type": "document",
                    }
                )
                links.append(
                    {
                        "source": identity,
                        "target": chunk,
                        "relation": "contains_text",
                        "confidence": "EXTRACTED",
                    }
                )
        report["failures"] = [
            str(Path(p).relative_to(root))
            for p, _, s, _ in rows
            if s in {"extraction_error", "no_text", "size_limit", "truncated"}
        ]
        report["statuses"] = dict(statuses)
        report["unsupported_extensions"] = dict(
            Counter(Path(p).suffix.lower() for p, _, s, _ in rows if s == "metadata_only")
        )
        write_json_atomic(
            out / "graph.json",
            {
                "directed": False,
                "multigraph": False,
                "graph": {"root": str(root), "local_only": True},
                "nodes": nodes,
                "links": links,
            },
            ensure_ascii=False,
        )
        write_json_atomic(out / "report.json", report, indent=2, ensure_ascii=False)
        return report
    finally:
        db.close()


def search_index(out: Path, query: str, limit=10):
    """Search literal Unicode words. User input cannot inject FTS operators."""
    words = re.findall(r"[^\W_]+", query, re.UNICODE)
    if not words:
        return []
    if any(character in query for character in '-."'):
        expression = '"' + " ".join(words[:30]) + '"'
    else:
        expression = " AND ".join(
            '"' + word + '"' + ("" if word.isdigit() else "*") for word in words[:30]
        )
    db = sqlite3.connect((Path(out) / "corpus.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return [
            dict(zip(("path", "snippet", "status"), row))
            for row in db.execute(
                "SELECT path, snippet(search, 1, '[', ']', ' … ', 40), status FROM search WHERE search MATCH ? ORDER BY rank LIMIT ?",
                (expression, max(1, min(limit, 100))),
            )
        ]
    finally:
        db.close()


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index")
    index.add_argument("root", type=Path)
    index.add_argument("--out", type=Path, required=True)
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--index", type=Path, required=True)
    status = sub.add_parser("status")
    status.add_argument("--index", type=Path, required=True)
    options = parser.parse_args(args)
    if options.command == "index":
        result = build_index(options.root, options.out)
    elif options.command == "search":
        result = search_index(options.index, options.query)
    else:
        result = json.loads((options.index / "report.json").read_text())
    print(json.dumps(result, ensure_ascii=False, indent=2))
