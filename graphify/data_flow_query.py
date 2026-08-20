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

Remediation (supervising review ``05-gate3-supervising-review-remediation``):

- Receiver confidence is derived from the direct evidence itself (preserving an
  explicit ``receiverConfidence`` for both same-file and cross-file edges), never
  inferred from ``cross_file == false``.
- ``allowed_relations`` is intersected with the supported value-flow vocabulary;
  unsupported requested relations can never become value-flow steps and are
  surfaced in the result.
- Missing start/target nodes and ``max_depth == 0`` cutoffs are represented
  explicitly and can never be reported as a complete search.
- Same-file parse-incomplete evidence is never upgraded to complete.
- Epistemic state is tracked over the *explored search region*, not only over
  returned paths.
- Boundary events are first-class, checkout-root independent evidence
  references to the underlying diagnostic.
- ``MAX_PATHS`` means an actual cutoff, not merely ``len(paths) == cap``.
- Query inputs (direction and bounds) are validated at runtime.

Remediation (supervising review ``06-gate3b-termination-accounting-remediation``):

- Termination arbitration is centralized: a single classifier derives
  ``termination_reason`` from semantic facts (input resolution, and which bounds
  suppressed known eligible work) with an explicit documented precedence, never
  from incidental ``if`` ordering. ``COMPLETE`` is impossible whenever a known
  eligible frontier was suppressed by ``max_depth`` / ``max_paths`` /
  ``max_expansions``.
- Point-to-point target terminality: outgoing edges from a reached target are not
  pending query work, while alternate unexplored branches that could reach the
  target still count toward ``max_paths``.
- Cycle-filtered eligible frontier: all completeness/cutoff decisions use the
  same cycle-safe "can expand" definition as the walker; a cycle-only
  continuation is never mistaken for remaining work.
- ``visited_count`` truthfully counts unique graph nodes reached/examined
  (including dead-ends and non-target branches), independent of emitted paths;
  ``expanded_count`` counts accepted edge expansions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

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

# Public alias: the supported value-flow vocabulary. Caller-provided
# ``allowed_relations`` is intersected with this set so a caller can never turn a
# structural relation (e.g. CALLS) into a value-flow step (P0-2).
SUPPORTED_DATA_FLOW_RELATIONS = DEFAULT_DATA_FLOW_RELATIONS

# Relations that inherently name a receiver whose instance identity matters.
# When such an edge carries no explicit receiver-confidence metadata we default
# to MAY (fail closed) rather than assuming a proven receiver.
_RECEIVER_ORIENTED_RELATIONS = frozenset({"READ_FROM", "WRITTEN_TO", "PASSED_AS_ARGUMENT"})

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
    ``allowed_relations`` defaults to :data:`DEFAULT_DATA_FLOW_RELATIONS` and is
    intersected with :data:`SUPPORTED_DATA_FLOW_RELATIONS` at run time.
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
    never truncated, resolved its start (and, for point-to-point queries, its
    target), encountered no blocking boundary and no PARTIAL/UNKNOWN coverage in
    the explored region. A zero-path result is therefore only meaningful as
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
    # ---- remediation fields (supervising review 05) ----
    query_validity: bool = True
    input_resolution: str = "RESOLVED"  # RESOLVED / START_NODE_NOT_FOUND / TARGET_NODE_NOT_FOUND
    rejected_relations: tuple[str, ...] = ()
    encountered_partial_evidence: bool = False
    encountered_unknown_evidence: bool = False
    encountered_may_evidence: bool = False


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
    """Completeness of a direct edge's supported-construct evidence.

    An explicit ``analysisCompleteness`` metadata value is preserved for both
    same-file and cross-file edges (P0-5). When it is absent (hand-authored
    fixtures or legacy evidence) we use a conservative fallback: a direct fact
    with less-than-full confidence cannot be claimed complete for the supported
    construct. A full-trust edge with no completeness metadata is treated as
    complete for the supported construct.
    """
    metadata = edge.get("metadata") or {}
    explicit = metadata.get("analysisCompleteness")
    if explicit:
        return str(explicit)
    score = edge.get("confidence_score")
    if isinstance(score, (int, float)) and score < 1.0:
        return "PARTIAL"
    return "COMPLETE_FOR_SUPPORTED_CONSTRUCT"


def _receiver_confidence(edge: dict[str, Any]) -> str:
    """Receiver confidence of a direct edge, derived from the evidence itself.

    P0-1: an explicit ``receiverConfidence`` metadata value is preserved for both
    same-file and cross-file edges. When it is absent, only receiver-oriented
    relations (which name a receiver whose instance identity matters) default to
    ``MAY``; a pure value-flow hop between confirmed data nodes has no receiver
    to prove and remains ``PROVEN``. We never infer ``PROVEN`` merely from
    ``cross_file == false``.
    """
    metadata = edge.get("metadata") or {}
    value = metadata.get("receiverConfidence")
    if value in ("MAY", "PROVEN"):
        return str(value)
    if str(edge.get("relation") or "") in _RECEIVER_ORIENTED_RELATIONS:
        return "MAY"
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


def _record_edge_state(edge: dict[str, Any], state: dict[str, bool]) -> None:
    """Accumulate epistemic state over the explored search region (P0-6).

    Called for every direct edge actually consumed or reached during traversal,
    including branches that later dead-end without producing a returned path. An
    edge that is never reached (e.g. in an unrelated unreachable component) is
    never recorded and therefore cannot degrade search coverage.
    """
    completeness = _analysis_completeness(edge)
    if completeness == "PARTIAL":
        state["partial"] = True
    elif completeness == "UNKNOWN":
        state["unknown"] = True
    if _receiver_confidence(edge) == "MAY":
        state["may"] = True


def _boundary_evidence_key(node: dict[str, Any]) -> str:
    """Deterministic, checkout-root independent boundary evidence fingerprint.

    Derived only from canonical public evidence fields (repo-relative source
    file, caller location, resolution, method/arity, receiver FQN, reason). The
    diagnostic node id is intentionally *not* part of the fingerprint because its
    stability across a Graphify remap is not guaranteed; it is surfaced separately
    as ``diagnostic_node_id`` / ``diagnostic_evidence_key``. No absolute checkout
    path participates, so the key is identical across different checkout roots.
    """
    metadata = node.get("metadata") or {}
    fields: list[tuple[str, str]] = [
        ("sf", str(node.get("source_file") or "")),
        ("loc", str(metadata.get("callerLocation") or "")),
        ("res", str(metadata.get("resolution") or "")),
        ("method", str(metadata.get("method") or "")),
        ("arity", str(metadata.get("arity") or 0)),
        ("rfqn", str(metadata.get("receiverFqn") or "")),
        ("reason", str(metadata.get("reason") or "")),
    ]
    canonical = json.dumps(sorted(fields), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"bnd:{digest}"


def _collect_boundary_events(
    reachable_files: set[str], nodes: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Machine-visible blocking-boundary events as first-class evidence refs.

    A blocking diagnostic (AMBIGUOUS / UNRESOLVED / UNSUPPORTED) is surfaced
    when the traversal reached at least one data value in the diagnostic's
    caller file. It is a coverage caveat (the search could not safely cross
    that boundary), never a fabricated flow step. Each event exposes a
    deterministic, checkout-root independent evidence key and a stable reference
    to the underlying diagnostic node (P0-7). Deterministic order by canonical
    caller file, location, resolution, reason.
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
        caller_abs = str(metadata.get("callerFile") or "")
        # The diagnostic's callerFile is the per-file extractor's path (absolute
        # under the cache_root fallback), while node source_file is repo-relative.
        # Match when the callerFile is the same physical file as a reached node
        # (repo-relative path is a /-delimited suffix of the caller file).
        if not any(
            caller_abs == sf or caller_abs.endswith("/" + sf)
            for sf in reachable_files
        ):
            continue
        node_id = str(node.get("id") or "")
        # The canonical caller file is the repo-relative node source_file, never
        # the absolute extractor callerFile, so no checkout root leaks into the
        # public GVR-facing identity.
        canonical_sf = str(node.get("source_file") or "")
        events.append({
            "type": "boundary_event",
            "boundary_evidence_key": _boundary_evidence_key(node),
            "diagnostic_node_id": node_id,
            "diagnostic_evidence_key": f"diag:{node_id}",
            "canonical_caller_file": canonical_sf,
            "caller_location": str(metadata.get("callerLocation") or ""),
            "resolution": resolution,
            "reason": str(metadata.get("reason") or resolution.lower()),
            "receiver": str(metadata.get("receiver") or ""),
            "receiver_fqn": str(metadata.get("receiverFqn") or ""),
            "receiver_confidence": str(metadata.get("receiverConfidence") or "MAY"),
            "method": str(metadata.get("method") or ""),
            "arity": int(metadata.get("arity") or 0),
            "import_context": str(metadata.get("importContext") or "unknown"),
            "candidate_count": int(metadata.get("candidateCount") or 0),
        })
    events.sort(key=lambda e: (
        e["canonical_caller_file"], e["caller_location"], e["resolution"], e["reason"]
    ))
    return events


def _eligible_next(
    adjacency: _Adjacency,
    node: str,
    direction: Direction,
    visited_set: frozenset[str],
) -> list[dict[str, Any]]:
    """The edges a frame may actually expand, identical to walker rules (P0-3).

    A continuation is eligible iff its frontier node is not already in the
    current path's ``visited_set`` (which also excludes self-loops, since the
    current node is in its own path visited-set). All completeness/cutoff
    decisions must use this cycle-filtered definition, never raw adjacency
    existence, so a cycle-only frontier is never mistaken for remaining work.
    """
    return [
        e for e in adjacency.outgoing(node, direction)
        if _frontier(e, direction) not in visited_set
    ]


def _classify_termination(facts: dict[str, Any]) -> tuple[str, bool]:
    """Single deterministic termination classifier (P1-1).

    ``facts`` carries only semantic facts (input resolution, and which bounds
    suppressed known eligible work). Precedence is explicit and documented in
    ``GVR-DERIVED-EVIDENCE-CONTRACT.md``:

    1. input-resolution failure (missing start/target) -> not truncation;
    2. expansion budget exhausted while eligible work remains -> MAX_EXPANSIONS;
    3. depth cutoff with an eligible non-cycle continuation -> MAX_DEPTH;
    4. path cap prevented an additional result/work item -> MAX_PATHS;
    5. otherwise COMPLETE.

    ``COMPLETE`` is impossible whenever any known eligible frontier was
    suppressed by a bound, so ``complete_supported_search == True`` can never
    coexist with suppressed eligible work.
    """
    reason = str(facts.get("input_resolution") or "RESOLVED")
    if reason != "RESOLVED":
        return (reason, False)
    if facts.get("expansion_cap_prevented_work"):
        return ("MAX_EXPANSIONS", True)
    if facts.get("depth_cutoff_with_eligible"):
        return ("MAX_DEPTH", True)
    if facts.get("path_cap_prevented_work"):
        return ("MAX_PATHS", True)
    return ("COMPLETE", False)


def _validate_query(query: DataFlowQuery) -> None:
    """Validate public query inputs before traversal (P1-2).

    Invalid direction and out-of-range bounds are rejected deterministically
    (ValueError). ``max_depth >= 0``, ``max_paths >= 1``, ``max_expansions >= 1``.
    ``allowed_relations`` are not rejected here; they are intersected with the
    supported vocabulary (P0-2).
    """
    if query.direction not in ("FORWARD", "BACKWARD"):
        raise ValueError(f"invalid direction: {query.direction!r}")
    if int(query.max_depth) < 0:
        raise ValueError("max_depth must be >= 0")
    if int(query.max_paths) < 1:
        raise ValueError("max_paths must be >= 1")
    if int(query.max_expansions) < 1:
        raise ValueError("max_expansions must be >= 1")


def run_data_flow_query(
    nodes: Sequence[dict[str, Any]],
    edges: Sequence[dict[str, Any]],
    query: DataFlowQuery,
) -> DataFlowTraversalResult:
    """Run a bounded data-flow traversal over direct source evidence.

    ``nodes`` and ``edges`` are the public extraction graph lists. The query is
    evaluated query-time; nothing is persisted into the graph.
    """
    _validate_query(query)
    direction = query.direction
    start = query.start
    target = query.target
    max_depth = int(query.max_depth)
    max_paths = int(query.max_paths)
    max_expansions = int(query.max_expansions)

    # P0-2: effective allowlist is the intersection of the caller's requested
    # relations with the supported value-flow vocabulary. Structural relations
    # requested by a caller can never become value-flow steps.
    requested = frozenset(query.allowed_relations)
    effective_allowed = requested & SUPPORTED_DATA_FLOW_RELATIONS
    rejected_relations = tuple(sorted(requested - SUPPORTED_DATA_FLOW_RELATIONS))

    adjacency = _Adjacency(edges, effective_allowed)

    node_ids = {str(n.get("id") or "") for n in nodes}
    start_found = start in node_ids
    target_found = target in node_ids if target is not None else None

    # P0-3: missing start/target are input-resolution failures, never complete
    # searches. A missing target makes the target search UNKNOWN even when the
    # start exists (conservative: never "no path" proof).
    if not start_found:
        input_resolution = "START_NODE_NOT_FOUND"
    elif target is not None and not target_found:
        input_resolution = "TARGET_NODE_NOT_FOUND"
    else:
        input_resolution = "RESOLVED"

    # Node id -> repo-relative source_file map for O(1) reachability bookkeeping.
    node_file = {str(n.get("id") or ""): str(n.get("source_file") or "") for n in nodes}
    reachable_files: set[str] = set()
    visited_reached: set[str] = set()
    explored_state = {"partial": False, "unknown": False, "may": False}

    def _record_reached(node_id: str) -> None:
        """Mark a node as reached/examined and bookkeep its file for boundaries."""
        sf = node_file.get(node_id)
        if sf:
            reachable_files.add(sf)
        visited_reached.add(node_id)

    # TerminationFacts (P1-1): termination is derived from semantic facts, not
    # from the order of `if` statements. Each bound flag means "a known eligible
    # traversal work item was suppressed by a bound". Precedence is enforced in
    # _classify_termination, never by incidental statement ordering.
    facts: dict[str, Any] = {
        "input_resolution": input_resolution,
        "expansion_cap_prevented_work": False,
        "depth_cutoff_with_eligible": False,
        "path_cap_prevented_work": False,
    }

    paths: list[DataFlowPath] = []
    seen_path_identities: set[tuple[str, ...]] = set()
    expanded = 0

    def _emit(flow_edges: list[dict[str, Any]]) -> None:
        """Record a derived path from flow-ordered direct edges (deduplicated)."""
        path_identity = tuple(e["_df_key"] for e in flow_edges)
        if path_identity in seen_path_identities:
            return
        seen_path_identities.add(path_identity)
        for e in flow_edges:
            _record_edge_state(e, explored_state)
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

    if input_resolution == "RESOLVED":
        _record_reached(start)
        if start == target:
            # Identity path (start == target): the point-to-point question is
            # trivially answered. Outgoing edges beyond the reached target are
            # not pending query work (P0-2 target terminality), so this is
            # COMPLETE regardless of max_depth/max_paths.
            if len(paths) < max_paths:
                paths.append(DataFlowPath(
                    steps=(),
                    supporting_evidence=(),
                    path_identity=(),
                    path_exactness="EXACT_FOR_RETURNED_PATH",
                    path_receiver_confidence="PROVEN",
                    path_coverage="COMPLETE_FOR_SUPPORTED_CONSTRUCT",
                ))
            else:
                facts["path_cap_prevented_work"] = True
        elif max_depth == 0:
            # Zero-depth never traverses. Any eligible (non-cycle) edge in the
            # selected direction is a depth cutoff; the reached-frontier edges
            # contribute to explored-region epistemic state.
            for edge in adjacency.outgoing(start, direction):
                _record_edge_state(edge, explored_state)
            if _eligible_next(adjacency, start, direction, frozenset({start})):
                facts["depth_cutoff_with_eligible"] = True
        else:
            # DFS over paths: each frame is (path of edges, visited-set, depth).
            stack: list[tuple[list[dict[str, Any]], frozenset[str], int]] = []
            start_visited = frozenset({start})
            for edge in adjacency.outgoing(start, direction):
                if expanded >= max_expansions:
                    facts["expansion_cap_prevented_work"] = True
                    break
                f = _frontier(edge, direction)
                if f in start_visited:
                    continue  # a self-loop makes no flow progress; skip
                expanded += 1
                _record_edge_state(edge, explored_state)
                _record_reached(f)
                stack.append(([edge], start_visited | {f}, 1))

            while stack:
                path_edges, visited_set, depth = stack.pop()
                last_node = _frontier(path_edges[-1], direction)
                _record_reached(last_node)
                reached_target = (target is not None and last_node == target)

                # Present steps in flow direction (forward for FORWARD walks,
                # reversed for BACKWARD walks) so a backward result is the same
                # flow chain a forward walk would find for the same direct facts.
                if target is None or reached_target:
                    if len(paths) < max_paths:
                        flow_edges = (
                            list(reversed(path_edges))
                            if direction == "BACKWARD"
                            else path_edges
                        )
                        _emit(flow_edges)
                    else:
                        # A candidate path exists but the path budget prevented
                        # emitting it -> a real MAX_PATHS cutoff.
                        facts["path_cap_prevented_work"] = True

                # P0-2 target terminality: once the target is reached, its
                # outgoing edges are NOT pending work for this query. Other
                # stack branches (alternate paths to target) still matter.
                if reached_target:
                    continue

                if last_node in query.stop_nodes:
                    continue

                # P0-3: eligible next edges are cycle-filtered (frontier not
                # already in this path), identical to the rule the walker uses.
                # Raw adjacency existence is never used as a work proxy.
                eligible = _eligible_next(adjacency, last_node, direction, visited_set)
                if not eligible:
                    continue

                if depth >= max_depth:
                    # P0-1: an eligible non-cycle continuation exists beyond the
                    # depth budget -> a real MAX_DEPTH cutoff (never COMPLETE,
                    # even if the path cap was reached at the same time).
                    facts["depth_cutoff_with_eligible"] = True
                    continue

                for edge in eligible:
                    if expanded >= max_expansions:
                        facts["expansion_cap_prevented_work"] = True
                        break
                    expanded += 1
                    _record_edge_state(edge, explored_state)
                    f = _frontier(edge, direction)
                    _record_reached(f)
                    stack.append((path_edges + [edge], visited_set | {f}, depth + 1))

    # Single deterministic termination classifier (P1-1).
    termination_reason, truncated = _classify_termination(facts)

    boundary_events = tuple(_collect_boundary_events(reachable_files, nodes))
    has_blocking_boundary = len(boundary_events) > 0

    # Formal invariant (section 11): complete_supported_search is allowed only
    # when the start (and, when required, target) resolved, the search was fully
    # bounded, encountered no blocking boundary and no PARTIAL/UNKNOWN evidence
    # in the explored region. An exact returned path does NOT imply a complete
    # search; a complete search does NOT imply whole-program completeness.
    if input_resolution != "RESOLVED":
        search_coverage = "UNKNOWN"
    elif (
        truncated
        or has_blocking_boundary
        or explored_state["partial"]
        or explored_state["unknown"]
    ):
        search_coverage = "PARTIAL"
    else:
        search_coverage = "COMPLETE_FOR_SUPPORTED_CONSTRUCT"

    complete_supported_search = (
        input_resolution == "RESOLVED"
        and (not truncated)
        and (not has_blocking_boundary)
        and (not explored_state["partial"])
        and (not explored_state["unknown"])
        and search_coverage == "COMPLETE_FOR_SUPPORTED_CONSTRUCT"
    )

    # Deterministic final ordering of paths by identity.
    paths.sort(key=lambda p: p.path_identity)

    return DataFlowTraversalResult(
        paths=tuple(paths),
        start=start,
        target=target,
        direction=direction,
        visited_count=len(visited_reached),
        expanded_count=expanded,
        truncated=truncated,
        termination_reason=termination_reason,
        query_bounds={
            "direction": direction,
            "max_depth": max_depth,
            "max_paths": max_paths,
            "max_expansions": max_expansions,
            "requested_allowed_relations": sorted(requested),
            "effective_allowed_relations": sorted(effective_allowed),
            "rejected_relations": list(rejected_relations),
            "stop_nodes": sorted(query.stop_nodes),
        },
        boundary_events=boundary_events,
        search_coverage=search_coverage,
        complete_supported_search=complete_supported_search,
        start_node_found=start_found,
        target_node_found=target_found,
        query_validity=True,
        input_resolution=input_resolution,
        rejected_relations=rejected_relations,
        encountered_partial_evidence=explored_state["partial"],
        encountered_unknown_evidence=explored_state["unknown"],
        encountered_may_evidence=explored_state["may"],
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
