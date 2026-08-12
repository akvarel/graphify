# Semantic and Hybrid Graph Search Implementation Report

## Architecture

Graphify now has an optional local CPU semantic index in `graphify.semantic_search`. The default installation path remains unchanged because `fastembed` is imported only when a real semantic build or query is requested. Tests inject a fake embedder and never download a model. The active backend is FastEmbed using ONNX Runtime. It implements a small `Embedder` protocol (`model_name`, `embed(texts)`) so a future bundled lat/WASM backend can use the same index and ranking code.

The index projects each graph node into deterministic text using only safe, useful fields: `label`, `node_type`, `type`, `file_type`, `source_file`, `canonical_template`, `owner_label`, and `community_name`. Nodes with secret-like keys, credential-like types, or common secret token patterns are excluded before embedding.

Artifacts are written under the graph output directory next to `graph.json`:

- `semantic-index.json` metadata
- `semantic-node-ids.json` stable node id order
- `semantic-node-hashes.json` deterministic projected-text hashes aligned to the IDs
- `semantic-vectors.npy` normalized float16 vectors

Builds are incremental by default. When the previous artifacts have the same index version, model name, and vector dimension, Graphify reuses unchanged float16 vectors by matching each node ID and projected-text hash. Only new or changed projected texts are embedded, and deleted or newly excluded nodes are removed from the stable sorted ID list. `--full` forces a complete rebuild. Artifacts are staged before replacement and prior artifacts are restored if embedding or artifact writing fails.

## Staleness and validation

Metadata includes index format version, model name, model cache path when provided, vector dimension, graph node count, indexed node count, graph path, dtype, mode, counts for reused/embedded/removed nodes, and a deterministic fingerprint over indexed node IDs plus projected text. Loading rejects missing, stale, model-mismatched, dimension-mismatched, or shape-mismatched indexes with actionable errors.

## Commands

Install optional dependencies only when semantic search is needed:

```bash
pip install "graphifyy[semantic]"
# or
uv tool install "graphifyy[semantic]"
```

The default build is incremental. It reuses vectors whose node ID and projected-text hash are unchanged:

```bash
graphify semantic build --graph graphify-out/graph.json
# Force all nodes through the model again:
graphify semantic build --graph graphify-out/graph.json --full
```

After `graphify update`, an existing semantic index is refreshed automatically in strict offline mode. Normal graph updates still succeed with a warning if the optional semantic dependencies or recorded model cache are unavailable. An index created before per-node hashes existed is migrated without re-embedding when its graph fingerprint and stable node order still match.

Build the index:

```bash
graphify semantic build --graph graphify-out/graph.json
```

Force a complete rebuild instead of incremental reuse:

```bash
graphify semantic build --graph graphify-out/graph.json --full
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

- Brute-force NumPy dot product is intended for local CPU search at the tested 60k-node scale. Larger graphs should use an ANN backend.
- The semantic extra may download the FastEmbed model on first real non-offline use. Use `--model-cache` to prewarm and `--offline --model-cache` to require bundled resources.
- FastEmbed depends on ONNX Runtime, so this path is not a no-native-binaries implementation. The embedder protocol is kept narrow for a future lat/WASM backend.
- The projection intentionally excludes raw content and unknown fields to reduce secret exposure risk. This trades recall for privacy.
- Hybrid ranking currently prints ranked nodes. It does not alter `graphify query` scoped subgraph rendering.

## Update integration

`graphify update` remains a deterministic AST-only graph refresh. If a semantic index already exists beside `graph.json`, update attempts a safe offline semantic refresh using the recorded model and cache path. It never auto-downloads a model. If the optional dependency or cache is unavailable, the command prints a warning and keeps the normal graph update successful.

## Benchmark procedure

1. Build a fresh graph with `graphify update .` or `graphify extract --force`.
2. Install `graphifyy[semantic]` in a clean environment.
3. Run `time graphify semantic build --graph graphify-out/graph.json` and record indexed nodes, dimension, vector file size, and wall time.
4. Run representative queries with `--top-k 10`, `--top-k 100`, and `--hybrid --expand-context`.
5. Compare top results against `graphify query` and existing lexical expectations.
6. Record CPU, RAM, Python version, NumPy version, FastEmbed version, and model name.

## Full Avion benchmark

Measured on 2026-08-12 using the aggregate graph of all Avion services:

- Graph: 60,590 nodes and 118,408 edges.
- Model: `sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions, CPU-only, prewarmed offline cache.
- Full semantic build: 10 minutes 16.98 seconds wall time.
- Throughput: approximately 98.2 indexed nodes/second.
- CPU utilization: 793%, approximately eight saturated CPU cores.
- Peak RSS: 2,461,108 KiB, approximately 2.35 GiB.
- Model cache: 182,206,126 bytes, approximately 173.8 MiB.
- Float16 vectors: 46,533,248 bytes, approximately 44.4 MiB.
- Node ID map: 5,813,158 bytes, approximately 5.54 MiB.
- Total persisted semantic index excluding model cache: approximately 49.9 MiB.

Cold CLI queries, including graph JSON load and model startup, took 7.6 to 8.1 seconds with roughly 655 MiB RSS. In one warm process, 20 hybrid queries had a median of 149 ms and p95 of 360 ms. The Float16 and Float32 indexes produced identical top-10 results for both tested English Avion queries.

The default English MiniLM model retrieved relevant booking and Redis error anchors for English queries. It did not provide acceptable Russian-to-English retrieval. Use `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` when cross-language Russian queries are required. A 2,000-node sample measured that multilingual model at about 90.7 nodes/second, with approximately 481 MiB model cache and 1.18 GiB sample-run RSS, so it trades more disk and slower indexing for multilingual recall.

### Incremental Avion benchmark

Using the same 60,590-node graph and persisted full index:

- No code/text changes: 60,590 vectors reused, 0 embedded, 0 removed; 7.82 seconds wall time; 601 MiB peak RSS.
- One projected node changed: 60,589 reused, 1 embedded, 0 removed; 7.06 seconds wall time; 853 MiB peak RSS.
- The remaining approximately seven seconds is dominated by loading and parsing the 103.9 MiB graph JSON, fingerprinting projected node text, loading the ID/hash maps, and atomically rewriting the roughly 50 MiB index. Model inference is no longer proportional to the whole graph.

Further optimization should avoid rewriting unchanged vector storage and replace full graph JSON parsing with a compact manifest or database-backed index. That would reduce no-change updates below the current seven-second floor.
