"""Bounded, deterministic, query-time data-flow traversal (Gate 3).

This module implements a *bounded interprocedural data-flow traversal* over the
direct source-derived evidence emitted by Gate 2 / Gate 2B. It turns isolated
direct facts (``A -> B``, ``B -> C``) into query-time *derived paths*
(``A => C through [A->B, B->C]``) that carry the direct evidence dependencies,
provenance, epistemic state, and termination reason a later GVR adapter needs.

Design invariants (see ``docs/data-flow/GATE-3-BOUNDED-TRAVERSAL-IMPLEMENTATION-REPORT.md``):

- Query-time only: no transitive facts (``CAN_FLOW_TO`` / ``REACHES`` /
  ``TRANSITIVE_FLOWS_TO`` / ``IMPACTS``) are persisted into the source graph.
  Every derived path exists only inside its traversal result.
- Bounded: ``max_depth``, ``max_paths`` and a global ``max_expansions`` budget
  are mandatory; a public query never defaults to a whole-graph walk.
- Deterministic: the same graph + same query yields the same normalized result
  regardless of node/edge insertion order, filesystem order, or checkout root.
- Evidence-backed: every path step references a deterministic, checkout-root
  independent *evidence key* of the exact direct edge it was derived from.
- Conservative epistemic propagation: MAY is never upgraded to PROVEN, PARTIAL
  is never upgraded to complete, multiple paths never become definite truth,
  and ambiguous/unresolved/unsupported boundaries are surfaced as machine-visible
  events rather than fabricated flow steps.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Sequence

# The explicit allowlist of value-flow relations eligible for traversal.
# CALLS / references / imports / contains / inherits are NOT value flow and are
# deliberately excluded (they connect nodes but do not by themselves move a value).
DEFAULT_DATA_FLOW_RELATIONS = frozenset({
    "FLOWS_TO",
    "PASSED_AS_ARGUMENT",
    "RETURNED_AS",
    "READ_FROM",
    "WRITTEN_TO",
    "TRANSFORMED_BY",
})

# Blocking resolution states: a boundary we cannot safely cross. These must be
# surfaced as boundary events, never traversed as flow.
BLOCKING_RESOLUTIONS = frozenset({"AMBIGUOUS", "UNRESOLVED", "UNSUPPORTED"})

Direction = Literal["FORWARD", "BACKWARD"]

_SAFE_DEFAULT_MAX_DEPTH = 4
_SAFE_DEFAULT_MAX_PATHS = 50
_SAFE_DEFAULT_MAX_EXPANSIONS = 2000


@dataclass(frozen=True)
class DataFlowQuery:
    """A bounded data-flow query.

    Bounds are mandatory and never default to an unbounded whole-graph walk.
    ``allowed_relations`` defaults to :data:`DEFAULT_DATA_FLOW_RELATIONS`.
    """

    start: str
    target: str | None = None
    direction: Direction = "FORWARD"
    max_depth: int = _SAFE_DEFAULT_MAX_DEPTH
    max_paths: int = _SAFE_DEFAULT_MAX_PATHS
    max_expansions: int = _SAFE_DEFAULT_MAX_EXPANSIONS
    allowed_relations: frozenset[str] = field(
        default_factory=lambda: frozenset(DEFAULT_DATA_FLOW_RELATIONS)
    )
    stop_nodes: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class DataFlowEvidenceRef:
    """A direct source-evidence reference backing one derived path step.

    ``key`` is the deterministic, checkout-root independent fingerprint of the
    exact direct edge. A later GVR adapter can reference this key without
    reparsing source code.
    """

    key: str
    relation: str
    source: str
    target: str
    source_file: str
    source_location: str
    provenance: str | None = None
    confidence_score: float | None = None
    argument_index: int | None = None
    receiver_confidence: str | None = None
    analysis_completeness: str | None = None


@dataclass(frozen=True)
class DataFlowPathStep:
    """One hop of a derived path, backed by exactly one direct evidence ref."""

    source: str
    target: str
    relation: str
    evidence: DataFlowEvidenceRef


@dataclass(frozen=True)
class DataFlowPath:
    """A derived path: an ordered sequence of direct-evidence hops.

    ``path_identity`` is the ordered tuple of evidence keys, which makes two
    paths distinct whenever they use different (even parallel) direct evidence,
    not just different node sequences.
    """

    steps: tuple[DataFlowPathStep, ...]
    supporting_evidence: tuple[DataFlowEvidenceRef, ...]
    path_identity: tuple[str, ...]
    path_exactness: str  # always EXACT_FOR_RETURNED_PATH; never implies global completeness
    path_receiver_confidence: str  # PROVEN iff every step is PROVEN, else MAY
    path_coverage: str  # COMPLETE_FOR_SUPPORTED_CONSTRUCT iff every step is complete


@dataclass(frozen=True)
class DataFlowTraversalResult:
    """The normalized, serializable result of a bounded traversal.

    ``complete_supported_search`` is True only when the search was fully bounded,
    never truncated, and encountered no blocking boundary and no PARTIAL coverage
    in the reachable region. A zero-path result is therefore only meaningful as
    "no path" when this flag is True.
    """

    paths: tuple[DataFlowPath, ...]
    start: str
    target: str | None
    direction: Direction
    visited_count: int
    expanded_count: int
    truncated: bool
    termination_reason: str
    query_bounds: dict[str, Any]
    boundary_events: tuple[dict[str, Any], ...]
    search_coverage: str  # COMPLETE_FOR_SUPPORTED_CONSTRUCT / PARTIAL / UNKNOWN
    complete_supported_search: bool
    start_node_found: bool
    target_node_found: bool | None


def _evidence_key(edge: dict[str, Any]) -> str:
    """Deterministic, checkout-root independent evidence fingerprint.

    Derived only from canonical, stable source-evidence fields: the canonical
    source/target node ids, the relation, the repo-relative ``source_file``, the
    source location, and (where present) provenance / argument index / callee.
    Volatile fields (absolute checkout paths, wall-clock timestamps, in-memory
    object ids, arbitrary dict ordering) are never included.
    """
    metadata = edge.get("metadata") or {}
    fields: list[tuple[str, str]] = [
        ("r", str(edge.get("relation") or "")),
        ("s", str(edge.get("source") or "")),
        ("t", str(edge.get("target") or "")),
        ("f", str(edge.get("source_file") or "")),
        ("l", str(edge.get("source_location") or "")),
        ("p", str(metadata.get("provenance") or "")),
    ]
    ai = metadata.get("argumentIndex")
    if ai is not None:
        fields.append(("ai", str(ai)))
    # NOTE: the Gate 2B cross-file edge metadata also carries a `callee` value
    # that embeds the absolute checkout-path slug (from the cache_root fallback)
    # and is therefore NOT checkout-root independent. The callee identity is
    # already captured canonically by the edge's own `target` node id (included
    # above), so `callee` is intentionally excluded from the evidence key.
    canonical = json.dumps(sorted(fields), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"df:{digest}"


def _frontier(edge: dict[str, Any], direction: Direction) -> str:
    """The node a path moves *into* after consuming ``edge``.

    Forward walks move toward ``target``; backward walks move toward ``source``
    (against the stored edge orientation, without inventing reverse facts in
    persistence).
    """
    if direction == "BACKWARD":
        return str(edge.get("source") or "")
    return str(edge.get("target") or "")


def _edge_expansion_key(edge: dict[str, Any], key: str) -> tuple[Any, ...]:
    """Deterministic edge expansion sort key for normalized traversal output."""
    return (
        str(edge.get("source") or ""),
        str(edge.get("target") or ""),
        str(edge.get("relation") or ""),
        str(edge.get("source_location") or ""),
        key,
    )


def _analysis_completeness(edge: dict[str, Any]) -> str:
    metadata = edge.get("metadata") or {}
    if (metadata.get("cross_file") or False):
        return str(metadata.get("analysisCompleteness") or "UNKNOWN")
    return "COMPLETE_FOR_SUPPORTED_CONSTRUCT"


def _receiver_confidence(edge: dict[str, Any]) -> str:
    metadata = edge.get("metadata") or {}
    if (metadata.get("cross_file") or False):
        return str(metadata.get("receiverConfidence") or "MAY")
    return "PROVEN"


def _build_edge_ref(edge: dict[str, Any]) -> DataFlowEvidenceRef:
    metadata = edge.get("metadata") or {}
    key = _evidence_key(edge)
    return DataFlowEvidenceRef(
        key=key,
        relation=str(edge.get("relation") or ""),
        source=str(edge.get("source") or ""),
        target=str(edge.get("target") or ""),
        source_file=str(edge.get("source_file") or ""),
        source_location=str(edge.get("source_location") or ""),
        provenance=str(metadata.get("provenance") or None) or None,
        confidence_score=(
            float(edge["confidence_score"])
            if isinstance(edge.get("confidence_score"), (int, float))
            else None
        ),
        argument_index=metadata.get("argumentIndex"),
        receiver_confidence=_receiver_confidence(edge),
        analysis_completeness=_analysis_completeness(edge),
    )


class _Adjacency:
    """One-time-per-query adjacency index with deterministic expansion order."""

    def __init__(self, edges: Sequence[dict[str, Any]], allowed: frozenset[str]) -> None:
        self._forward: dict[str, list[dict[str, Any]]] = {}
        self._reverse: dict[str, list[dict[str, Any]]] = {}
        self._allowed = allowed
        for edge in edges:
            rel = str(edge.get("relation") or "")
            if rel not in allowed:
                continue
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            key = _evidence_key(edge)
            entry = dict(edge)
            entry["_df_key"] = key
            self._forward.setdefault(source, []).append(entry)
            self._reverse.setdefault(target, []).append(entry)
        for bucket in self._forward.values():
            bucket.sort(key=lambda e: _edge_expansion_key(e, e["_df_key"]))
        for bucket in self._reverse.values():
            bucket.sort(key=lambda e: _edge_expansion_key(e, e["_df_key"]))

    def outgoing(self, node: str, direction: Direction) -> list[dict[str, Any]]:
        index = self._forward if direction == "FORWARD" else self._reverse
        return index.get(node, [])


def _collect_boundary_events(
    reachable_files: set[str], nodes: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Machine-visible blocking-boundary events.

    A blocking diagnostic (AMBIGUOUS / UNRESOLVED / UNSUPPORTED) is surfaced
    when the traversal reached at least one data value in the diagnostic's
    caller file. It is a coverage caveat (the search could not safely cross
    that boundary), never a fabricated flow step. Deterministic order by file,
    location, resolution, reason.
    """
    events: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("type") != "extraction_diagnostic":
            continue
        metadata = node.get("metadata") or {}
        if metadata.get("kind") != "cross_file_resolution":
            continue
        resolution = str(metadata.get("resolution") or "")
        if resolution not in BLOCKING_RESOLUTIONS:
            continue
        caller_file = str(metadata.get("callerFile") or "")
        # The diagnostic's callerFile is the per-file extractor's path (absolute
        # under the cache_root fallback), while node source_file is repo-relative.
        # Match when the callerFile is the same physical file as a reached node
        # (repo-relative path is a /-delimited suffix of the caller file).
        if not any(
            caller_file == sf or caller_file.endswith("/" + sf)
            for sf in reachable_files
        ):
            continue
        events.append({
            "type": "boundary_event",
            "resolution": resolution,
            "reason": str(metadata.get("reason") or resolution.lower()),
            "callerFile": caller_file,
            "callerLocation": str(metadata.get("callerLocation") or ""),
            "receiver": str(metadata.get("receiver") or ""),
            "receiverFqn": str(metadata.get("receiverFqn") or ""),
            "method": str(metadata.get("method") or ""),
            "arity": int(metadata.get("arity") or 0),
            "importContext": str(metadata.get("importContext") or "unknown"),
            "candidateCount": int(metadata.get("candidateCount") or 0),
        })
    events.sort(key=lambda e: (
        e["callerFile"], e["callerLocation"], e["resolution"], e["reason"]
    ))
    return events


def run_data_flow_query(
    nodes: Sequence[dict[str, Any]],
    edges: Sequence[dict[str, Any]],
    query: DataFlowQuery,
) -> DataFlowTraversalResult:
    """Run a bounded data-flow traversal over direct source evidence.

    ``nodes`` and ``edges`` are the public extraction graph lists. The query is
    evaluated query-time; nothing is persisted into the graph.
    """
    start = query.start
    target = query.target
    max_depth = max(0, int(query.max_depth))
    max_paths = max(1, int(query.max_paths))
    max_expansions = max(1, int(query.max_expansions))

    adjacency = _Adjacency(edges, query.allowed_relations)

    node_ids = {str(n.get("id") or "") for n in nodes}
    start_found = start in node_ids
    target_found = target in node_ids if target is not None else None

    # Boundary-event bookkeeping uses the files actually reached by the search.
    reachable_files: set[str] = set()
    if start_found:
        for n in nodes:
            if str(n.get("id") or "") == start:
                sf = str(n.get("source_file") or "")
                if sf:
                    reachable_files.add(sf)

    paths: list[DataFlowPath] = []
    seen_path_identities: set[tuple[str, ...]] = set()
    expanded = 0
    visited: set[str] = set()
    truncated = False
    termination_reason = "COMPLETE"
    cut_at_depth = False  # an edge existed beyond max_depth
    hit_path_cap = False
    hit_expansion_cap = False

    if start_found and start == target:
        # A trivial identity path (start == target) with no steps.
        paths.append(DataFlowPath(
            steps=(),
            supporting_evidence=(),
            path_identity=(),
            path_exactness="EXACT_FOR_RETURNED_PATH",
            path_receiver_confidence="PROVEN",
            path_coverage="COMPLETE_FOR_SUPPORTED_CONSTRUCT",
        ))
        visited.add(start)

    def _emit(flow_edges: list[dict[str, Any]]) -> None:
        """Record a derived path from flow-ordered direct edges (deduplicated)."""
        nonlocal visited
        path_identity = tuple(e["_df_key"] for e in flow_edges)
        if path_identity in seen_path_identities:
            return
        seen_path_identities.add(path_identity)
        refs = tuple(_build_edge_ref(e) for e in flow_edges)
        steps = tuple(
            DataFlowPathStep(
                source=ref.source, target=ref.target,
                relation=ref.relation, evidence=ref,
            )
            for ref in refs
        )
        receiver = (
            "PROVEN"
            if all(r.receiver_confidence == "PROVEN" for r in refs)
            else "MAY"
        )
        coverage = (
            "COMPLETE_FOR_SUPPORTED_CONSTRUCT"
            if all(
                r.analysis_completeness == "COMPLETE_FOR_SUPPORTED_CONSTRUCT"
                for r in refs
            )
            else "PARTIAL"
        )
        paths.append(DataFlowPath(
            steps=steps,
            supporting_evidence=refs,
            path_identity=path_identity,
            path_exactness="EXACT_FOR_RETURNED_PATH",
            path_receiver_confidence=receiver,
            path_coverage=coverage,
        ))
        visited.update(ref.source for ref in refs)
        if flow_edges:
            visited.add(_frontier(flow_edges[-1], query.direction))

    if start_found and start != target and max_depth > 0:
        # DFS over paths: each stack frame is (path of edges, visited-set, depth).
        # Using an explicit stack keeps it iterative (cycle-safe, bounded).
        stack: list[tuple[list[dict[str, Any]], frozenset[str], int]] = []
        start_visited = frozenset({start})
        for edge in adjacency.outgoing(start, query.direction):
            if expanded >= max_expansions:
                hit_expansion_cap = True
                break
            if _frontier(edge, query.direction) in start_visited:
                continue  # a self-loop makes no flow progress; skip
            expanded += 1
            stack.append(([edge], start_visited | {_frontier(edge, query.direction)}, 1))

        while stack and len(paths) < max_paths:
            path_edges, visited_set, depth = stack.pop()
            last_node = _frontier(path_edges[-1], query.direction)

            if last_node in node_ids:
                for n in nodes:
                    if str(n.get("id") or "") == last_node:
                        sf = str(n.get("source_file") or "")
                        if sf:
                            reachable_files.add(sf)
                        break

            reached_target = (target is not None and last_node == target)
            if target is None or reached_target:
                # Present steps in flow direction (forward for FORWARD walks,
                # reversed for BACKWARD walks) so a backward result is the same
                # flow chain a forward walk would find for the same direct facts.
                flow_edges = (
                    list(reversed(path_edges))
                    if query.direction == "BACKWARD"
                    else path_edges
                )
                _emit(flow_edges)

            if len(paths) >= max_paths:
                hit_path_cap = True
                break

            # Expand further (unless a terminal / stop node / depth bound).
            if reached_target:
                continue  # point-to-point: a path is complete at the target
            if depth >= max_depth:
                if adjacency.outgoing(last_node, query.direction):
                    cut_at_depth = True
                continue
            if last_node in query.stop_nodes:
                continue
            outgoing = adjacency.outgoing(last_node, query.direction)
            for edge in outgoing:
                if expanded >= max_expansions:
                    hit_expansion_cap = True
                    break
                tgt = _frontier(edge, query.direction)
                if tgt in visited_set:
                    continue  # cycle-safe: never revisit a node within one path
                expanded += 1
                stack.append((path_edges + [edge], visited_set | {tgt}, depth + 1))
            if hit_expansion_cap:
                break

    if hit_expansion_cap:
        truncated = True
        termination_reason = "MAX_EXPANSIONS"
    elif hit_path_cap:
        truncated = True
        termination_reason = "MAX_PATHS"
    elif cut_at_depth:
        truncated = True
        termination_reason = "MAX_DEPTH"

    boundary_events = tuple(_collect_boundary_events(reachable_files, nodes))
    has_blocking_boundary = len(boundary_events) > 0
    has_partial_coverage = any(p.path_coverage == "PARTIAL" for p in paths)
    if not paths and has_blocking_boundary:
        search_coverage = "PARTIAL"
    elif not paths and (truncated or has_blocking_boundary):
        search_coverage = "PARTIAL"
    elif has_partial_coverage or has_blocking_boundary:
        search_coverage = "PARTIAL"
    else:
        search_coverage = "COMPLETE_FOR_SUPPORTED_CONSTRUCT"

    complete_supported_search = (
        (not truncated)
        and (not has_blocking_boundary)
        and search_coverage == "COMPLETE_FOR_SUPPORTED_CONSTRUCT"
    )

    # Deterministic final ordering of paths by identity.
    paths.sort(key=lambda p: p.path_identity)

    return DataFlowTraversalResult(
        paths=tuple(paths),
        start=start,
        target=target,
        direction=query.direction,
        visited_count=len(visited),
        expanded_count=expanded,
        truncated=truncated,
        termination_reason=termination_reason,
        query_bounds={
            "direction": query.direction,
            "max_depth": query.max_depth,
            "max_paths": query.max_paths,
            "max_expansions": query.max_expansions,
            "allowed_relations": sorted(query.allowed_relations),
            "stop_nodes": sorted(query.stop_nodes),
        },
        boundary_events=boundary_events,
        search_coverage=search_coverage,
        complete_supported_search=complete_supported_search,
        start_node_found=start_found,
        target_node_found=target_found,
    )


def run_query(graph: dict[str, Any], query: DataFlowQuery) -> DataFlowTraversalResult:
    """Convenience wrapper accepting the public graph dict (``nodes``/``edges``)."""
    edges = graph.get("edges")
    if edges is None:
        edges = graph.get("links")
    return run_data_flow_query(
        list(graph.get("nodes") or []),
        list(edges or []),
        query,
    )
