# Gate 2B — Akon Labs / GitNexus Reference Gap Analysis

**Inspected date:** 2026-08-19
**Sources inspected (current, not assumed):**
- Akon Labs product site: `https://www.akonlabs.com/`
- Akon Labs pricing / commercial boundary: `https://www.akonlabs.com/pricing`
- GitNexus source repository: `https://github.com/abhigyanpatwari/GitNexus`
- GitNexus architecture: `ARCHITECTURE.md` (fetched `main`, ~65 KB)
- GitNexus license: `LICENSE` — **PolyForm Noncommercial License 1.0.0**

## Licensing boundary (recorded explicitly)

GitNexus is licensed under **PolyForm Noncommercial 1.0.0**. Commercial use is
not permitted under this license. GitNexus is treated here strictly as a
**reference architecture**, not code to copy into a commercial BugZero
implementation unless licensing is independently cleared. Akon Labs (YC S26)
sells a managed SaaS / self-hosted **Enterprise** tier (commercial boundary) for
GitNexus; the open-source engine is the noncommercial OSS layer. None of the
code, algorithms' source, or storage schema from GitNexus is adopted or copied
into this gate.

## What Gate 2B actually does (the lens applied)

Gate 2B adds **deterministic cross-file Java caller→callee parameter/return
linkage** with exact identity, receiver PROVEN/MAY, and analysis-completeness
metadata. It does **not** build a full PDG, process nodes, community detection,
or GVR.

## Per-concept classification

Legend: `ALREADY_HAVE`, `USEFUL_IDEA`, `NOT_APPLICABLE`, `FUTURE_GATE`, `REJECT`.

| Concept | Classification | Notes |
| --- | --- | --- |
| Phased ingestion pipeline (scan → structure → parse → resolve → relate → cluster → trace) | `USEFUL_IDEA` | Graphify already has a multi-pass extract + post-pass resolution pipeline. GitNexus's explicit DAG-phase separation is a good architectural pattern, but adopting it would be a large refactor; recorded, not adopted in 2B. |
| Cross-file resolution (exact, non-name-only) | `ALREADY_HAVE` (after 2B) | Gate 2B implements exact FQN + method + arity matching; GitNexus's `crossFile`/`scopeResolution` phases solve the same class of problem. |
| Scope/type resolution with receiver typing (`receiver-bound calls`, interface fan-out, generic-instantiation-aware dispatch) | `USEFUL_IDEA` / `FUTURE_GATE` | GitNexus resolves compound receiver chains (`user.address.getCity().save()`) and interface dispatch with generic awareness. Gate 2B handles single-hop typed receivers conservatively; deeper receiver-chain folding and interface/generic dispatch are future-gate work. |
| Callable propagation (first-class function value flow) | `FUTURE_GATE` | GitNexus has a language-neutral callable-value-flow solver. Gate 3 may need bounded callable/indirect-call evidence; not in 2B scope. |
| Confidence / epistemic reporting (`epistemic: exact | lower-bound`, `ResolutionOutcome`, unresolved-receiver census) | `USEFUL_IDEA` | GitNexus exposes explicit epistemic status and unresolved-receiver summaries. Gate 2B exposes separated `declarationResolution`, `receiverKind`, `receiverPath`, `fieldScope`, `instanceAuthority`, `aliasAuthority`, `analysisCompleteness`, and resolution outcomes (`EXACT`/`AMBIGUOUS`/`UNRESOLVED`/`UNSUPPORTED`). A machine-readable unresolved-candidates census (like GitNexus's) is a useful future enhancement. |
| Process extraction / STEP_IN_PROCESS / Process nodes | `REJECT` for 2B | Out of scope per Gate 2B task (Section 25). GitNexus derives processes from communities/routes/tools. Recorded for a future gate; not implemented. |
| PDG / CFG / REACHING_DEF / CDG / taint | `FUTURE_GATE` / `REJECT` for 2B | GitNexus has an opt-in `--pdg` layer (CFG → REACHING_DEF → taint → CDG). Gate 2B explicitly does not build a PDG/SSA/taint engine. A future OSS gate could adopt the CFG side-channel + bounded reaching-definitions idea (GitNexus's "strictly intra-procedural, data flow never crosses the repo boundary" is aligned with 2B's no-transitive-closure rule). |
| LadybugDB (graph storage / Cypher) | `REJECT` / `NOT_APPLICABLE` for 2B | Graphify uses its own persisted graph representation; adopting LadybugDB/Cypher is a storage-architecture decision beyond this gate. Recorded. |
| BM25 + semantic + RRF retrieval | `NOT_APPLICABLE` for 2B | Retrieval/search is a query-layer feature; Gate 2B is evidence extraction. Future gate could add hybrid retrieval for traversal seed selection. |
| Incremental indexing strategy | `USEFUL_IDEA` / `FUTURE_GATE` | GitNexus keeps a `lastCommit` staleness indicator and re-indexes on commit. Graphify has a per-file cache. A whole-corpus staleness/index versioning improvement is future work. |
| Multi-repository Contract Registry / bridge graph | `FUTURE_GATE` | GitNexus cross-repo `ContractLink` joins at the symbol grain; data flow never crosses the repo boundary (matching 2B's boundary-respecting rule). Multi-repo linkage is outside 2B. |
| Impact analysis (blast radius, upstream/downstream) | `FUTURE_GATE` | GitNexus has `impact`/`api_impact`/`trace`. This is exactly the kind of traversal Gate 3 is slated for; not implemented in 2B. |
| MCP exposure | `NOT_APPLICABLE` for 2B (separate concern) | GitNexus exposes MCP tools. Graphify's MCP surface is out of scope for 2B. |
| Leiden community detection | `REJECT` for 2B | Out of scope (Section 25). Recorded for a future gate. |
| Deterministic "no embedding guesswork" stance | `ALREADY_HAVE` | GitNexus and Gate 2B share the core principle: exact deterministic static resolution, never semantic-similarity proof. Aligned. |

## Adopted vs recorded

**Adopted in Gate 2B:** nothing new from GitNexus's implementation was copied.
The principle of exact deterministic cross-file resolution and explicit
epistemic reporting is already Gate 2/2B design and is reinforced (not derived
from) GitNexus.

**Recorded for future gates:** phased-pipeline explicitness, receiver-chain
folding, interface/generic dispatch fan-out, callable-value flow, opt-in PDG
(CFG/REACHING_DEF/taint/CDG), unresolved-receiver census, incremental index
staleness, multi-repo contract bridges, impact traversal, and hybrid retrieval.

**Explicitly rejected for Gate 2B:** Process nodes, Leiden clustering, full
PDG, LadybugDB/Cypher migration, and any code adoption from the PolyForm
Noncommercial GitNexus repository.

## Notes on GitNexus benchmark claims

The Akon Labs site reports DeepSWE solve-rate improvements (+31.4 pts vs a bare
model; GitNexus 68.4% vs Graphify 54.0% vs bare 37.0%) on a single-issue
benchmark. These are the vendor's marketing numbers at inspection date and are
**not** treated as validated evidence for BugZero; they are recorded for
awareness only. Gate 2B's correctness is validated by its own public-boundary
tests and the full suite, not by third-party benchmark claims.
