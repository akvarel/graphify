"""Optional local CPU semantic and hybrid graph search.

This module is importable without the optional ``semantic`` extra. The fastembed
import is delayed until the real embedder is requested, so default installs keep
working and tests can inject deterministic fake embedders.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

import networkx as nx
import numpy as np

from graphify.serve import _score_nodes, _search_tokens

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
INDEX_VERSION = 1
META_NAME = "semantic-index.json"
VECTORS_NAME = "semantic-vectors.npy"
IDS_NAME = "semantic-node-ids.json"
HASHES_NAME = "semantic-node-hashes.json"
MAX_QUERY_TOP_K = 1000
_SAFE_FIELDS = (
    "label",
    "node_type",
    "type",
    "file_type",
    "source_file",
    "canonical_template",
    "owner_label",
    "community_name",
)
_SECRET_KEY_RE = re.compile(r"(secret|token|credential|password|passwd|api[_-]?key|private[_-]?key)", re.I)
_SECRET_VALUE_RE = re.compile(r"\b(sk_live_|sk_test_|xox[baprs]-|gh[pousr]_|AKIA[0-9A-Z]{16})", re.I)


class SemanticDependencyMissing(RuntimeError):
    pass


class SemanticIndexMissing(RuntimeError):
    pass


class SemanticIndexStale(RuntimeError):
    pass


class Embedder(Protocol):
    model_name: str

    def embed(self, texts: Iterable[str]): ...


@dataclass(frozen=True)
class SemanticIndex:
    ids: list[str]
    vectors: np.ndarray
    metadata: dict

    @property
    def dimension(self) -> int:
        return int(self.metadata.get("dimension") or (self.vectors.shape[1] if self.vectors.ndim == 2 else 0))


class FastEmbedder:
    """FastEmbed/ONNX Runtime embedder behind the common Embedder protocol.

    ``offline=True`` never initiates a model download: callers must provide a
    prewarmed ``cache_dir`` containing the requested model. A future lat/WASM
    backend can implement the same ``model_name`` + ``embed(texts)`` protocol.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        cache_dir: str | Path | None = None,
        offline: bool = False,
    ):
        self.model_name = model_name
        self.cache_dir = Path(cache_dir).expanduser() if cache_dir is not None else None
        self.offline = offline
        if offline:
            if self.cache_dir is None or not self.cache_dir.exists() or not any(self.cache_dir.rglob("*")):
                raise SemanticDependencyMissing(
                    "semantic search is in offline mode but the model cache is absent or empty. "
                    "Prewarm with `graphify semantic build --model-cache <dir>` on a networked machine, "
                    "bundle that directory, then rerun with `--offline --model-cache <dir>`."
                )
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        if self.cache_dir is not None:
            os.environ["FASTEMBED_CACHE_PATH"] = str(self.cache_dir)
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise SemanticDependencyMissing(
                "semantic search requires optional dependencies. Install with `pip install graphifyy[semantic]`."
            ) from exc
        kwargs = {"model_name": model_name}
        if self.cache_dir is not None:
            kwargs["cache_dir"] = str(self.cache_dir)
        try:
            self._model = TextEmbedding(**kwargs)
        except TypeError:
            self._model = TextEmbedding(model_name=model_name)

    def embed(self, texts: Iterable[str]):
        return np.asarray(list(self._model.embed(list(texts))), dtype=np.float32)


def should_index_node(node_id: str, data: dict) -> bool:
    if any(_SECRET_KEY_RE.search(str(k)) for k in data):
        return False
    haystack = " ".join(str(data.get(k, "")) for k in ("label", "node_type", "type")) + " " + str(node_id)
    if _SECRET_VALUE_RE.search(haystack) or _SECRET_KEY_RE.search(str(data.get("node_type", data.get("type", "")))):
        return False
    return bool(project_node_text(node_id, data))


def project_node_text(node_id: str, data: dict) -> str:
    parts: list[str] = []
    for key in _SAFE_FIELDS:
        value = data.get(key)
        if value is None or isinstance(value, (dict, list, tuple, set)):
            continue
        text = str(value).strip()
        if not text or _SECRET_VALUE_RE.search(text):
            continue
        if _SECRET_KEY_RE.search(key) and key not in _SAFE_FIELDS:
            continue
        parts.append(f"{key}: {text}")
    return " | ".join(parts)


def graph_fingerprint(G: nx.Graph) -> str:
    h = hashlib.sha256()
    for nid, data in sorted(G.nodes(data=True), key=lambda x: str(x[0])):
        if not should_index_node(str(nid), data):
            continue
        h.update(str(nid).encode())
        h.update(b"\0")
        h.update(project_node_text(str(nid), data).encode("utf-8", "replace"))
        h.update(b"\n")
    return h.hexdigest()


def node_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _index_paths(out_dir: Path) -> tuple[Path, Path, Path, Path]:
    return out_dir / META_NAME, out_dir / VECTORS_NAME, out_dir / IDS_NAME, out_dir / HASHES_NAME


def _normalize_rows(arr: np.ndarray) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _load_reusable_index(out: Path, model_name: str, expected_dimension: int | None) -> tuple[dict, list[str], dict[str, str], np.ndarray, str | None]:
    meta_path, vec_path, ids_path, hashes_path = _index_paths(out)
    if not (meta_path.exists() and vec_path.exists() and ids_path.exists() and hashes_path.exists()):
        return {}, [], {}, np.zeros((0, 0), dtype=np.float16), "missing"
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        ids = list(map(str, json.loads(ids_path.read_text(encoding="utf-8"))))
        hashes_raw = json.loads(hashes_path.read_text(encoding="utf-8"))
        hashes = {str(k): str(v) for k, v in hashes_raw.items()}
        vectors = np.asarray(np.load(vec_path, allow_pickle=False), dtype=np.float16)
    except Exception:
        return {}, [], {}, np.zeros((0, 0), dtype=np.float16), "unreadable"
    if metadata.get("version") != INDEX_VERSION:
        return metadata, [], {}, np.zeros((0, 0), dtype=np.float16), "version_mismatch"
    if metadata.get("model") != model_name:
        return metadata, [], {}, np.zeros((0, 0), dtype=np.float16), "model_mismatch"
    dim = int(metadata.get("dimension") or 0)
    if expected_dimension is not None and dim != int(expected_dimension):
        return metadata, [], {}, np.zeros((0, 0), dtype=np.float16), "dimension_mismatch"
    if vectors.ndim != 2 or vectors.shape[0] != len(ids) or (dim and vectors.shape[1] != dim):
        return metadata, [], {}, np.zeros((0, 0), dtype=np.float16), "shape_mismatch"
    return metadata, ids, hashes, vectors, None


def _atomic_replace_artifacts(out: Path, artifacts: dict[str, bytes]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    staged: dict[str, Path] = {}
    backups: dict[str, bytes | None] = {}
    try:
        for name, data in artifacts.items():
            target = out / name
            backups[name] = target.read_bytes() if target.exists() else None
            fd, tmp = tempfile.mkstemp(prefix=f".{name}.", dir=str(out))
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            staged[name] = Path(tmp)
        for name, tmp in staged.items():
            os.replace(tmp, out / name)
    except Exception:
        for name, old in backups.items():
            target = out / name
            if old is None:
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
            else:
                _atomic_write_bytes(target, old)
        raise
    finally:
        for tmp in staged.values():
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass


def build_semantic_index(
    G: nx.Graph,
    *,
    graph_path: str | Path,
    out_dir: str | Path | None = None,
    model_name: str = DEFAULT_MODEL,
    model_cache: str | Path | None = None,
    offline: bool = False,
    embedder: Embedder | None = None,
    full: bool = False,
    expected_dimension: int | None = None,
) -> SemanticIndex:
    graph_path = Path(graph_path)
    out = Path(out_dir) if out_dir is not None else graph_path.parent
    emb = embedder or FastEmbedder(model_name, cache_dir=model_cache, offline=offline)
    ids: list[str] = []
    texts_by_id: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for nid, data in sorted(G.nodes(data=True), key=lambda x: str(x[0])):
        sid = str(nid)
        if should_index_node(sid, data):
            ids.append(sid)
            text = project_node_text(sid, data)
            texts_by_id[sid] = text
            hashes[sid] = node_text_hash(text)
    model = getattr(emb, "model_name", model_name)
    old_meta, old_ids, old_hashes, old_vectors, reason = _load_reusable_index(out, model, expected_dimension)
    mode = "full" if full or reason not in (None, "missing") else "incremental"
    if full:
        reason = "forced"
    old_pos = {nid: i for i, nid in enumerate(old_ids)} if mode == "incremental" else {}
    new_vectors: list[np.ndarray | None] = []
    embed_ids: list[str] = []
    embed_texts: list[str] = []
    reused = 0
    for sid in ids:
        pos = old_pos.get(sid)
        if pos is not None and old_hashes.get(sid) == hashes[sid]:
            new_vectors.append(np.asarray(old_vectors[pos], dtype=np.float16))
            reused += 1
        else:
            new_vectors.append(None)
            embed_ids.append(sid)
            embed_texts.append(texts_by_id[sid])
    embedded16 = np.zeros((0, expected_dimension or 0), dtype=np.float16)
    if embed_texts:
        embedded = _normalize_rows(np.asarray(emb.embed(embed_texts), dtype=np.float32))
        embedded16 = embedded.astype(np.float16)
    if embed_texts and expected_dimension is not None and embedded16.shape[1] != int(expected_dimension):
        raise SemanticIndexStale("semantic embedder dimension does not match expected dimension")
    dim = int(embedded16.shape[1]) if embed_texts else (int(old_vectors.shape[1]) if old_vectors.ndim == 2 and reused else int(expected_dimension or 0))
    embed_iter = iter(embedded16)
    rows = [next(embed_iter) if row is None else row for row in new_vectors]
    vectors16 = np.vstack(rows).astype(np.float16) if rows else np.zeros((0, dim), dtype=np.float16)
    removed = len(set(old_ids) - set(ids)) if old_ids else 0
    metadata = {
        "version": INDEX_VERSION,
        "model": model,
        "dimension": int(vectors16.shape[1]) if vectors16.ndim == 2 else 0,
        "graph_fingerprint": graph_fingerprint(G),
        "node_count": G.number_of_nodes(),
        "indexed_count": len(ids),
        "reused_count": reused,
        "embedded_count": len(embed_ids),
        "removed_count": removed,
        "mode": mode,
        "rebuild_reason": reason,
        "model_cache": str(model_cache) if model_cache is not None else None,
        "graph_path": str(graph_path.resolve()),
        "dtype": "float16",
        "vectors": VECTORS_NAME,
        "ids": IDS_NAME,
        "hashes": HASHES_NAME,
    }
    import io
    bio = io.BytesIO()
    np.save(bio, vectors16, allow_pickle=False)
    _atomic_replace_artifacts(out, {
        VECTORS_NAME: bio.getvalue(),
        IDS_NAME: (json.dumps(ids, ensure_ascii=False) + "\n").encode(),
        HASHES_NAME: (json.dumps(hashes, ensure_ascii=False, sort_keys=False) + "\n").encode(),
        META_NAME: (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode(),
    })
    return SemanticIndex(ids=ids, vectors=vectors16, metadata=metadata)


def load_semantic_index(
    G: nx.Graph,
    *,
    graph_path: str | Path,
    out_dir: str | Path | None = None,
    model_name: str = DEFAULT_MODEL,
) -> SemanticIndex:
    out = Path(out_dir) if out_dir is not None else Path(graph_path).parent
    meta_path, vec_path, ids_path, hashes_path = _index_paths(out)
    if not meta_path.exists() or not vec_path.exists() or not ids_path.exists() or not hashes_path.exists():
        raise SemanticIndexMissing("semantic index is missing; run `graphify semantic build --graph <graph.json>` first")
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    ids = json.loads(ids_path.read_text(encoding="utf-8"))
    vectors = np.load(vec_path, allow_pickle=False, mmap_mode="r")
    expected_fp = graph_fingerprint(G)
    if metadata.get("version") != INDEX_VERSION:
        raise SemanticIndexStale("semantic index format is stale; rebuild it")
    if metadata.get("model") != model_name:
        raise SemanticIndexStale("semantic index model differs from requested model; rebuild it")
    if metadata.get("graph_fingerprint") != expected_fp or metadata.get("node_count") != G.number_of_nodes():
        raise SemanticIndexStale("semantic index is stale for this graph; rebuild it")
    if vectors.ndim != 2 or vectors.shape[0] != len(ids) or vectors.shape[1] != int(metadata.get("dimension", -1)):
        raise SemanticIndexStale("semantic index vector shape does not match metadata; rebuild it")
    return SemanticIndex(ids=list(map(str, ids)), vectors=vectors, metadata=metadata)


def semantic_rank(index: SemanticIndex, query: str, embedder: Embedder, *, top_k: int = 10) -> list[tuple[float, str]]:
    k = max(0, min(int(top_k), MAX_QUERY_TOP_K, len(index.ids)))
    if k == 0 or not index.ids:
        return []
    q = _normalize_rows(np.asarray(embedder.embed([query]), dtype=np.float32))[0]
    sims = np.asarray(index.vectors, dtype=np.float32) @ q
    if k < len(sims):
        cand = np.argpartition(-sims, k - 1)[:k]
    else:
        cand = np.arange(len(sims))
    ordered = sorted(cand, key=lambda i: (-float(sims[i]), index.ids[int(i)]))
    return [(float(sims[int(i)]), index.ids[int(i)]) for i in ordered[:k]]


def hybrid_rank(
    G: nx.Graph,
    index: SemanticIndex,
    query: str,
    embedder: Embedder,
    *,
    top_k: int = 10,
    semantic_weight: float = 0.65,
    lexical_weight: float = 0.35,
    expand_context: bool = False,
) -> list[tuple[float, str]]:
    k = max(0, min(int(top_k), MAX_QUERY_TOP_K))
    if k == 0:
        return []
    semantic = dict(semantic_rank(index, query, embedder, top_k=min(MAX_QUERY_TOP_K, max(k * 5, k))))
    terms = _search_tokens(query)
    lexical_pairs = _score_nodes(G, terms)[: max(k * 10, 50)] if terms else []
    max_lex = max((s for s, _ in lexical_pairs), default=1.0) or 1.0
    scores: dict[str, float] = {nid: semantic_weight * ((sim + 1.0) / 2.0) for sim, nid in semantic.items()}
    for score, nid in lexical_pairs:
        scores[nid] = scores.get(nid, 0.0) + lexical_weight * (score / max_lex)
    if expand_context:
        for nid, base in list(scores.items()):
            if nid not in G:
                continue
            for nb in G.neighbors(nid):
                sid = str(nb)
                scores[sid] = max(scores.get(sid, 0.0), base * 0.85)
    return sorted(((score, nid) for nid, score in scores.items()), key=lambda x: (-x[0], x[1]))[:k]
