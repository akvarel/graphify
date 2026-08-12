import json
import os
import sys
from pathlib import Path

import networkx as nx
import numpy as np
import pytest
from networkx.readwrite import json_graph

from graphify.semantic_search import (
    DEFAULT_MODEL,
    HASHES_NAME,
    SemanticIndexStale,
    build_semantic_index,
    hybrid_rank,
    load_semantic_index,
    project_node_text,
    semantic_rank,
    should_index_node,
)


class FakeEmbedder:
    model_name = "fake/all-MiniLM-L6-v2"

    def embed(self, texts):
        rows = []
        for text in texts:
            t = text.lower()
            if "auth" in t or "login" in t:
                rows.append([1.0, 0.0, 0.0])
            elif "payment" in t or "invoice" in t:
                rows.append([0.0, 1.0, 0.0])
            else:
                rows.append([0.0, 0.0, 1.0])
        return np.asarray(rows, dtype=np.float32)


class CountingEmbedder(FakeEmbedder):
    def __init__(self, model_name="fake/all-MiniLM-L6-v2", width=3):
        self.model_name = model_name
        self.width = width
        self.calls = []

    def embed(self, texts):
        texts = list(texts)
        self.calls.append(texts)
        rows = []
        for text in texts:
            digest = sum(text.encode("utf-8")) or 1
            rows.append([float((digest + i) % 17 + 1) for i in range(self.width)])
        return np.asarray(rows, dtype=np.float32)


def make_graph():
    g = nx.Graph()
    g.add_node(
        "n-auth",
        label="LoginController",
        node_type="class",
        source_file="app/auth.py",
        canonical_template="handles user login",
        owner_label="AuthService",
        community_name="Authentication",
    )
    g.add_node(
        "n-pay",
        label="InvoiceWriter",
        node_type="function",
        source_file="billing/invoice.py",
        canonical_template="writes payment invoice",
        owner_label="BillingService",
        community_name="Payments",
    )
    g.add_node("n-secret", label="sk_live_secret", node_type="credential", raw="password=abc")
    g.add_edge("n-auth", "n-pay", relation="calls")
    return g


def test_default_model_is_small_cpu_fastembed_name():
    assert DEFAULT_MODEL == "sentence-transformers/all-MiniLM-L6-v2"


def test_project_node_text_is_deterministic_and_uses_safe_fields_only():
    g = make_graph()
    text1 = project_node_text("n-auth", g.nodes["n-auth"])
    text2 = project_node_text("n-auth", dict(reversed(list(g.nodes["n-auth"].items()))))
    assert text1 == text2
    assert "LoginController" in text1
    assert "Authentication" in text1
    assert "handles user login" in text1
    assert "password" not in project_node_text("n-secret", g.nodes["n-secret"])


def test_filtering_excludes_secret_like_nodes():
    g = make_graph()
    assert should_index_node("n-auth", g.nodes["n-auth"])
    assert not should_index_node("n-secret", g.nodes["n-secret"])


def test_index_serialization_validation_staleness_and_float16(tmp_path):
    g = make_graph()
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(json_graph.node_link_data(g, edges="links")))
    out = tmp_path / "graphify-out"
    index = build_semantic_index(g, graph_path=graph_path, out_dir=out, embedder=FakeEmbedder())
    assert index.vectors.dtype == np.float16
    assert index.ids == ["n-auth", "n-pay"]
    hashes = json.loads((out / HASHES_NAME).read_text())
    assert list(hashes) == index.ids
    assert all(isinstance(value, str) and len(value) == 64 for value in hashes.values())
    loaded = load_semantic_index(g, graph_path=graph_path, out_dir=out, model_name=FakeEmbedder.model_name)
    assert loaded.dimension == 3
    g.add_node("new", label="new")
    with pytest.raises(SemanticIndexStale):
        load_semantic_index(g, graph_path=graph_path, out_dir=out, model_name=FakeEmbedder.model_name)


def test_semantic_ranking_fake_embedder_and_bounded_top_k(tmp_path):
    g = make_graph()
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(json_graph.node_link_data(g, edges="links")))
    idx = build_semantic_index(g, graph_path=graph_path, out_dir=tmp_path, embedder=FakeEmbedder())
    ranked = semantic_rank(idx, "payment invoice", FakeEmbedder(), top_k=1)
    assert ranked == [(pytest.approx(1.0), "n-pay")]


def test_hybrid_combines_lexical_and_semantic_and_expands_context(tmp_path):
    g = make_graph()
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(json_graph.node_link_data(g, edges="links")))
    idx = build_semantic_index(g, graph_path=graph_path, out_dir=tmp_path, embedder=FakeEmbedder())
    ranked = hybrid_rank(g, idx, "login invoice", FakeEmbedder(), top_k=3, expand_context=True)
    ids = [nid for _, nid in ranked]
    assert ids[0] in {"n-auth", "n-pay"}
    assert "n-auth" in ids and "n-pay" in ids
    assert len(ranked) <= 3


def test_build_semantic_index_records_requested_model_with_fake_embedder(tmp_path):
    g = make_graph()
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(json_graph.node_link_data(g, edges="links")))
    idx = build_semantic_index(
        g,
        graph_path=graph_path,
        out_dir=tmp_path,
        model_name="custom/multilingual-minilm",
        embedder=FakeEmbedder(),
    )
    assert idx.metadata["model"] == FakeEmbedder.model_name


def test_incremental_no_change_reuses_vectors_and_embeds_zero_nodes(tmp_path):
    g = make_graph()
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(json_graph.node_link_data(g, edges="links")))
    first_embedder = CountingEmbedder()
    first = build_semantic_index(g, graph_path=graph_path, out_dir=tmp_path, embedder=first_embedder)
    second_embedder = CountingEmbedder()

    second = build_semantic_index(g, graph_path=graph_path, out_dir=tmp_path, embedder=second_embedder)

    assert second.ids == first.ids
    assert second_embedder.calls == []
    assert np.array_equal(second.vectors, first.vectors)
    assert second.metadata["mode"] == "incremental"
    assert second.metadata["reused_count"] == 2
    assert second.metadata["embedded_count"] == 0
    assert second.metadata["removed_count"] == 0


def test_incremental_no_change_does_not_construct_real_embedder(tmp_path, monkeypatch):
    g = make_graph()
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(json_graph.node_link_data(g, edges="links")))
    build_semantic_index(g, graph_path=graph_path, out_dir=tmp_path, embedder=CountingEmbedder())

    def fail_constructor(*args, **kwargs):
        raise AssertionError("unchanged incremental build must not load the model")

    monkeypatch.setattr("graphify.semantic_search.FastEmbedder", fail_constructor)
    idx = build_semantic_index(
        g,
        graph_path=graph_path,
        out_dir=tmp_path,
        model_name=CountingEmbedder.model_name,
    )
    assert idx.metadata["embedded_count"] == 0
    assert idx.metadata["reused_count"] == 2


def test_pre_hash_index_migrates_without_reembedding(tmp_path, monkeypatch):
    g = make_graph()
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(json_graph.node_link_data(g, edges="links")))
    first = build_semantic_index(g, graph_path=graph_path, out_dir=tmp_path, embedder=CountingEmbedder())
    (tmp_path / HASHES_NAME).unlink()

    def fail_constructor(*args, **kwargs):
        raise AssertionError("compatible legacy index must migrate without loading the model")

    monkeypatch.setattr("graphify.semantic_search.FastEmbedder", fail_constructor)
    migrated = build_semantic_index(
        g,
        graph_path=graph_path,
        out_dir=tmp_path,
        model_name=CountingEmbedder.model_name,
    )
    assert np.array_equal(migrated.vectors, first.vectors)
    assert migrated.metadata["reused_count"] == 2
    assert migrated.metadata["embedded_count"] == 0
    assert (tmp_path / HASHES_NAME).exists()


def test_incremental_changed_new_and_deleted_nodes_only_embeds_changed_set(tmp_path):
    g = make_graph()
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(json_graph.node_link_data(g, edges="links")))
    build_semantic_index(g, graph_path=graph_path, out_dir=tmp_path, embedder=CountingEmbedder())
    g.nodes["n-auth"]["canonical_template"] = "handles login and sessions changed"
    g.remove_node("n-pay")
    g.add_node("n-cache", label="CacheWriter", node_type="function", canonical_template="cache storage")
    embedder = CountingEmbedder()

    idx = build_semantic_index(g, graph_path=graph_path, out_dir=tmp_path, embedder=embedder)

    assert idx.ids == ["n-auth", "n-cache"]
    assert len(embedder.calls) == 1
    assert len(embedder.calls[0]) == 2
    assert idx.metadata["reused_count"] == 0
    assert idx.metadata["embedded_count"] == 2
    assert idx.metadata["removed_count"] == 1


def test_full_forces_reembed_even_when_unchanged(tmp_path):
    g = make_graph()
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(json_graph.node_link_data(g, edges="links")))
    build_semantic_index(g, graph_path=graph_path, out_dir=tmp_path, embedder=CountingEmbedder())
    embedder = CountingEmbedder()

    idx = build_semantic_index(g, graph_path=graph_path, out_dir=tmp_path, embedder=embedder, full=True)

    assert len(embedder.calls) == 1
    assert len(embedder.calls[0]) == 2
    assert idx.metadata["mode"] == "full"
    assert idx.metadata["reused_count"] == 0
    assert idx.metadata["embedded_count"] == 2


def test_model_or_dimension_mismatch_full_rebuilds_without_reuse(tmp_path):
    g = make_graph()
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(json_graph.node_link_data(g, edges="links")))
    build_semantic_index(g, graph_path=graph_path, out_dir=tmp_path, embedder=CountingEmbedder(model_name="fake/one", width=3))
    model_mismatch = CountingEmbedder(model_name="fake/two", width=3)
    idx_model = build_semantic_index(g, graph_path=graph_path, out_dir=tmp_path, embedder=model_mismatch)
    assert len(model_mismatch.calls[0]) == 2
    assert idx_model.metadata["mode"] == "full"
    assert idx_model.metadata["rebuild_reason"] == "model_mismatch"
    dim_mismatch = CountingEmbedder(model_name="fake/two", width=4)
    idx_dim = build_semantic_index(g, graph_path=graph_path, out_dir=tmp_path, embedder=dim_mismatch, expected_dimension=4)
    assert len(dim_mismatch.calls[0]) == 2
    assert idx_dim.metadata["dimension"] == 4


def test_embedding_failure_preserves_prior_index_artifacts(tmp_path):
    g = make_graph()
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(json_graph.node_link_data(g, edges="links")))
    build_semantic_index(g, graph_path=graph_path, out_dir=tmp_path, embedder=CountingEmbedder())
    before = {p.name: p.read_bytes() for p in tmp_path.glob("semantic-*")}
    g.add_node("n-new", label="new", node_type="function")

    class FailingEmbedder(CountingEmbedder):
        def embed(self, texts):
            raise RuntimeError("embedding exploded")

    with pytest.raises(RuntimeError):
        build_semantic_index(g, graph_path=graph_path, out_dir=tmp_path, embedder=FailingEmbedder())
    after = {p.name: p.read_bytes() for p in tmp_path.glob("semantic-*")}
    assert after == before


def test_offline_fastembed_overrides_false_env_and_uses_cache(tmp_path, monkeypatch):
    import types
    from graphify.semantic_search import FastEmbedder

    cache = tmp_path / "models"
    cache.mkdir()
    (cache / "model.onnx").write_text("placeholder")
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "0")

    class DummyTextEmbedding:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=DummyTextEmbedding))
    FastEmbedder(model_name=DEFAULT_MODEL, cache_dir=cache, offline=True)
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
    assert os.environ["FASTEMBED_CACHE_PATH"] == str(cache)


def test_offline_fastembed_requires_present_cache(tmp_path):
    from graphify.semantic_search import FastEmbedder, SemanticDependencyMissing

    with pytest.raises(SemanticDependencyMissing) as exc:
        FastEmbedder(model_name=DEFAULT_MODEL, cache_dir=tmp_path / "models", offline=True)
    assert "offline" in str(exc.value).lower()
    assert "prewarm" in str(exc.value).lower()


def test_cli_semantic_build_honors_model_flag(tmp_path, monkeypatch, capsys):
    import types

    g = make_graph()
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(json_graph.node_link_data(g, edges="links")))

    class DummyTextEmbedding:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def embed(self, texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=DummyTextEmbedding))
    monkeypatch.setattr(
        sys,
        "argv",
        ["graphify", "semantic", "build", "--graph", str(graph_path), "--model", "custom/model"],
    )
    from graphify.cli import dispatch_command

    dispatch_command("semantic")
    assert "custom/model" in capsys.readouterr().out
    meta = json.loads((tmp_path / "semantic-index.json").read_text())
    assert meta["model"] == "custom/model"
    assert meta["mode"] == "incremental"


def test_cli_semantic_build_full_forces_rebuild(tmp_path, monkeypatch, capsys):
    import types

    g = make_graph()
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(json_graph.node_link_data(g, edges="links")))

    class DummyTextEmbedding:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def embed(self, texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=DummyTextEmbedding))
    from graphify.cli import dispatch_command

    monkeypatch.setattr(sys, "argv", ["graphify", "semantic", "build", "--graph", str(graph_path)])
    dispatch_command("semantic")
    monkeypatch.setattr(sys, "argv", ["graphify", "semantic", "build", "--graph", str(graph_path), "--full"])
    dispatch_command("semantic")
    out = capsys.readouterr().out
    assert "mode full" in out
    meta = json.loads((tmp_path / "semantic-index.json").read_text())
    assert meta["mode"] == "full"
    assert meta["embedded_count"] == 2


def test_update_hook_warns_but_succeeds_when_existing_semantic_cache_unavailable(tmp_path, monkeypatch, capsys):
    from graphify import cli as cli_mod
    from graphify.cli import dispatch_command

    out = tmp_path / "graphify-out"
    out.mkdir()
    g = make_graph()
    (out / "graph.json").write_text(json.dumps(json_graph.node_link_data(g, edges="links")))
    (out / "semantic-index.json").write_text(json.dumps({"model": "fake/missing", "dimension": 3}))
    monkeypatch.setattr(cli_mod, "_GRAPHIFY_OUT", str(out))
    monkeypatch.setattr(sys, "argv", ["graphify", "update", str(tmp_path)])

    import graphify.watch

    monkeypatch.setattr(graphify.watch, "_rebuild_code", lambda *a, **k: True)
    dispatch_command("update")
    captured = capsys.readouterr()
    assert "Warning: semantic index not refreshed offline" in captured.err
    assert "Code graph updated" in captured.out

def test_cli_semantic_paths_and_missing_index_error(tmp_path, monkeypatch, capsys):
    g = make_graph()
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(json_graph.node_link_data(g, edges="links")))
    from graphify.cli import dispatch_command

    monkeypatch.setattr(
        sys,
        "argv",
        ["graphify", "semantic", "query", "login", "--graph", str(graph_path)],
    )
    with pytest.raises(SystemExit) as exc:
        dispatch_command("semantic")
    assert exc.value.code != 0
    assert "semantic index" in capsys.readouterr().err.lower()


def test_public_artifact_packaging_mentions_semantic():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text()
    assert "semantic" in text
    assert "fastembed" in text
    all_line = next(line for line in text.splitlines() if line.startswith("all ="))
    assert "fastembed" in all_line
