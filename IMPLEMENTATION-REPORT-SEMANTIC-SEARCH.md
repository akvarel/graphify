# Semantic and Hybrid Graph Search Implementation Report

## Architecture

Graphify now has an optional local CPU semantic index in `graphify.semantic_search`. The default installation path remains unchanged because `fastembed` is imported only when a real semantic build or query is requested. Tests inject a fake embedder and never download a model. The active backend is FastEmbed using ONNX Runtime. It implements a small `Embedder` protocol (`model_name`, `embed(texts)`) so a future bundled lat/WASM backend can use the same index and ranking code.

The index projects each graph node into deterministic text using only safe, useful fields: `label`, `node_type`, `type`, `file_type`, `source_file`, `canonical_template`, `owner_label`, and `community_name`. Nodes with secret-like keys, credential-like types, or common secret token patterns are excluded before embedding.

Artifacts are written under the graph output directory next to `graph.json`:

- `semantic-index.json` metadata
- `semantic-node-ids.json` stable node id order
- `semantic-vectors.npy` normalized float16 vectors

Writes are atomic. Loads use NumPy mmap and validate metadata before ranking.

## Staleness and validation

Metadata includes index format version, model name, vector dimension, graph node count, indexed node count, graph path, dtype, and a deterministic fingerprint over indexed node IDs plus projected text. Loading rejects missing, stale, model-mismatched, dimension-mismatched, or shape-mismatched indexes with actionable errors.

## Commands

Install optional dependencies only when semantic search is needed:

```bash
pip install "graphifyy[semantic]"
# or
uv tool install "graphifyy[semantic]"
```

Build the index:

```bash
graphify semantic build --graph graphify-out/graph.json
```

Query semantic-only ranking:

```bash
graphify semantic query "auth token validation" --graph graphify-out/graph.json --top-k 20
```

Query hybrid ranking, which combines vector similarity with existing lexical score:

```bash
graphify semantic query "auth token validation" --graph graphify-out/graph.json --hybrid --top-k 20
```

Optionally include one-hop graph context in the candidate set:

```bash
graphify semantic query "auth token validation" --graph graphify-out/graph.json --hybrid --expand-context
```

Default model: `sentence-transformers/all-MiniLM-L6-v2`, matching the bundled offline all-MiniLM-L6-v2 contract used by lat search. Multilingual MiniLM remains selectable by passing its FastEmbed model name with `--model`.

Prewarm and bundle a cache for offline use:

```bash
graphify semantic build --graph graphify-out/graph.json --model-cache .graphify-models
# copy .graphify-models with graphify-out/ to the offline environment
```

Offline mode never intentionally downloads. It requires a non-empty cache and sets offline environment guards before the backend is constructed:

```bash
graphify semantic query "auth token validation" --graph graphify-out/graph.json --offline --model-cache .graphify-models
```

Override when required:

```bash
graphify semantic build --model sentence-transformers/all-MiniLM-L6-v2
```

## Limitations

- Brute-force NumPy dot product is intended for local CPU search up to roughly 60k nodes.
- The semantic extra may download the FastEmbed model on first real non-offline use. Use `--model-cache` to prewarm and `--offline --model-cache` to require bundled resources. This implementation and its tests do not benchmark Avion and do not download the real model.
- FastEmbed depends on ONNX Runtime, so this path is not a no-native-binaries implementation. The embedder protocol is kept narrow for a future lat/WASM backend.
- The projection intentionally excludes raw content and unknown fields to reduce secret exposure risk. This trades recall for privacy.
- Hybrid ranking currently prints ranked nodes. It does not alter `graphify query` scoped subgraph rendering.

## Benchmark procedure

1. Build a fresh graph with `graphify update .` or `graphify extract --force`.
2. Install `graphifyy[semantic]` in a clean environment.
3. Run `time graphify semantic build --graph graphify-out/graph.json` and record indexed nodes, dimension, vector file size, and wall time.
4. Run representative queries with `--top-k 10`, `--top-k 100`, and `--hybrid --expand-context`.
5. Compare top results against `graphify query` and existing lexical expectations.
6. Record CPU, RAM, Python version, NumPy version, FastEmbed version, and model name.
