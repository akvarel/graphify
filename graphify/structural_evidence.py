"""Graphify Structural Evidence Snapshot public contract (v1).

A :class:`StructuralEvidenceSnapshot` is the versioned, deterministic,
source-revision-scoped public contract through which Graphify exports structural
source evidence to downstream consumers (notably the Global Verification Runtime,
GVR). It carries only *source-derived* facts, provenance, coverage, and blockers
together with a deterministic source-revision scope and a stable content
fingerprint. It deliberately records **no** verification verdicts (``PASS`` /
``FAIL`` / ``VERIFIED``); `confidence` describes evidence strength, never truth.

Contract pillars
----------------
* **Versioned & namespaced.** ``schema_version``/``format`` identify the
  envelope; fact/blocker/path/format fingerprints live under stable
  ``graphify.structural_evidence.*`` namespaces.
* **Deterministic & checkout-root independent.** Node ids, repo-relative
  ``source_file``, and the ``git.commit`` source revision are the only identity
  inputs. Absolute checkout paths never participate, so the same source at two
  checkout roots yields byte-identical snapshots (same fingerprint).
* **Source-revision scoped.** Every snapshot is sealed to a
  ``(source_class, source_revision)`` pair (``git.commit`` + commit SHA); a
  revision change advances the scope fingerprint.
* **df-compatible.** Fact identity uses the public ``df:<sha256>`` evidence key
  whose canonicalization is identical to :func:`graphify.data_flow_query._evidence_key`
  and to GVR's :func:`gvr.graphify_contract.validate_graphify_df_evidence`, so a
  snapshot's facts validate against the GVR df-key validator without
  reparsing source. Boundary events use the parallel ``bnd:<sha256>`` key.
* **Typed facts / provenance / coverage / blockers.** Facts carry typed enums
  (:class:`StructuralProvenance`, :class:`StructuralCoverage`,
  :class:`StructuralReceiverConfidence`); search-level coverage is a typed
  :class:`StructuralEvidenceCoverageState`; blocking boundaries are first-class
  :class:`StructuralEvidenceBlocker` records.

The canonical JSON for any fingerprint uses sorted keys, ``", "`` item / ``":"``
key separators, ``ensure_ascii=False`` and ``allow_nan=False`` -- the exact same
canonicalization graphify already uses for ``_evidence_key`` so the two cannot
diverge (single source of truth).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

__all__ = [
    # contract metadata
    "STRUCTURAL_EVIDENCE_SCHEMA_VERSION",
    "STRUCTURAL_EVIDENCE_FORMAT",
    "STRUCTURAL_EVIDENCE_FINGERPRINT_FORMAT",
    "GRAPHIFY_PROVIDER_ID",
    "SOURCE_CLASS_GIT_COMMIT",
    "DEFAULT_ANALYZER_REVISION",
    # key / namespace primitives
    "DATA_FLOW_KEY_RE",
    "BOUNDARY_KEY_RE",
    "DIAGNOSTIC_KEY_RE",
    "SOURCE_REVISION_RE",
    "DATA_FLOW_KEY_PREFIX",
    "BOUNDARY_KEY_PREFIX",
    "DIAGNOSTIC_KEY_PREFIX",
    # enums
    "StructuralProvenance",
    "StructuralCoverage",
    "StructuralReceiverConfidence",
    # errors
    "StructuralEvidenceContractError",
    # fact / blocker / path types
    "StructuralEvidenceFact",
    "StructuralEvidenceBlocker",
    "StructuralEvidencePath",
    # coverage + scope
    "StructuralEvidenceCoverageState",
    "SourceRevisionScope",
    # snapshot
    "StructuralEvidenceSnapshot",
    # primitives
    "df_key",
    "df_key_from_edge",
    "validate_df_key",
    "is_valid_df_key",
    "boundary_evidence_key",
    "structural_evidence_fingerprint",
    # builders / serializers
    "build_structural_evidence_snapshot",
    "serialize_snapshot",
    "load_snapshot",
    "validate_snapshot",
]

# --------------------------------------------------------------------------- #
# Contract metadata / namespaces
# --------------------------------------------------------------------------- #
STRUCTURAL_EVIDENCE_SCHEMA_VERSION: int = 1
STRUCTURAL_EVIDENCE_FORMAT: str = "graphify.structural_evidence.v1"
STRUCTURAL_EVIDENCE_FINGERPRINT_FORMAT: str = "graphify.structural_evidence.fingerprint.v1"

GRAPHIFY_PROVIDER_ID: str = "graphify"
SOURCE_CLASS_GIT_COMMIT: str = "git.commit"
# Analyzer identity is deliberately separate from the analyzed source revision.
# This value follows the package release and must be advanced when analyzer
# semantics change, without pretending that the analyzed git commit changed.
DEFAULT_ANALYZER_REVISION: str = "graphifyy/0.9.48"

DATA_FLOW_KEY_PREFIX: str = "df:"
BOUNDARY_KEY_PREFIX: str = "bnd:"
DIAGNOSTIC_KEY_PREFIX: str = "diag:"

_CORRELATION_ONLY_QUERY_KEYS = frozenset({
    "correlation_id",
    "observation_id",
    "query_id",
    "request_id",
    "run_id",
})

DATA_FLOW_KEY_RE: re.Pattern[str] = re.compile(r"^df:[0-9a-f]{64}$")
BOUNDARY_KEY_RE: re.Pattern[str] = re.compile(r"^bnd:[0-9a-f]{64}$")
DIAGNOSTIC_KEY_RE: re.Pattern[str] = re.compile(r"^diag:.+$")
SOURCE_REVISION_RE: re.Pattern[str] = re.compile(r"[0-9a-f]{40,64}")


class StructuralEvidenceContractError(ValueError):
    """Raised when structural-evidence content cannot be canonically sealed."""


# --------------------------------------------------------------------------- #
# Typed enums (fact-level provenance / coverage / receiver confidence)
# --------------------------------------------------------------------------- #
class StructuralProvenance(str, Enum):
    """Origin of a direct source fact (mirrors the source-evidence contract)."""

    STATIC_AST = "STATIC_AST"
    CROSS_FILE = "CROSS_FILE"
    FRAMEWORK_CONTRACT = "FRAMEWORK_CONTRACT"


class StructuralCoverage(str, Enum):
    """Coverage of the construct a fact belongs to."""

    COMPLETE = "COMPLETE_FOR_SUPPORTED_CONSTRUCT"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class StructuralReceiverConfidence(str, Enum):
    """Whether a fact's receiver identity is proven from the evidence."""

    PROVEN = "PROVEN"
    MAY = "MAY"


# --------------------------------------------------------------------------- #
# Canonical fingerprint primitive
# --------------------------------------------------------------------------- #
_CANONICAL_SEPARATORS = (",", ":")


def _canonical_json(value: Any, fingerprint_format: str) -> str:
    """Deterministic canonical JSON envelope for a fingerprint.

    Sorted keys, compact separators, strict UTF-8 and no ``NaN``/``Infinity`` --
    the same stable encoding graphify already uses for evidence keys, so the
    contract module and the traversal engine cannot drift apart.
    """
    envelope = {
        "content": _canonical_value(value),
        "format": fingerprint_format,
    }
    return json.dumps(
        envelope,
        sort_keys=True,
        separators=_CANONICAL_SEPARATORS,
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


def _canonical_value(value: Any) -> Any:
    """Coerce arbitrary values to a JSON-transportable, order-stable tree.

    Mappings are ordered by UTF-8 key bytes (deterministic across runs),
    sequences are preserved in order, and enums/frozen dataclasses are reduced
    to their canonical dict form via ``to_dict`` when available.
    """
    if isinstance(value, Mapping):
        items: list[list[Any]] = []
        for key in value:
            items.append(
                [str(key), _canonical_value(value[key])]
            )
        items.sort(key=lambda kv: kv[0].encode("utf-8"))
        return {"$map": items}
    if isinstance(value, (list, tuple)):
        return {"$list": [_canonical_value(v) for v in value]}
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            raise StructuralEvidenceContractError("non-finite float in structural evidence")
        return value
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _canonical_value(value.to_dict())
    return str(value)


def structural_evidence_fingerprint(
    value: Any, *, fingerprint_format: str = STRUCTURAL_EVIDENCE_FINGERPRINT_FORMAT
) -> str:
    """Deterministic sha256 fingerprint over a canonical value tree."""
    return hashlib.sha256(
        _canonical_json(value, fingerprint_format).encode("utf-8")
    ).hexdigest()


# --------------------------------------------------------------------------- #
# df evidence-key primitives (GVR-compatible, single source of truth)
# --------------------------------------------------------------------------- #
_GRAPHIFY_DF_KEY_FIELDS = ("relation", "source", "target", "source_file", "source_location", "provenance")


def _df_field_pairs(
    relation: str,
    source: str,
    target: str,
    source_file: str,
    source_location: str,
    provenance: str,
    argument_index: int | None,
) -> list[tuple[str, str]]:
    """Canonical field pairs shared by the contract df-key and GVR's validator."""
    fields: list[tuple[str, str]] = [
        ("r", str(relation or "")),
        ("s", str(source or "")),
        ("t", str(target or "")),
        ("f", str(source_file or "")),
        ("l", str(source_location or "")),
        ("p", str(provenance or "")),
    ]
    if argument_index is not None:
        fields.append(("ai", str(argument_index)))
    return fields


def df_key(
    relation: str,
    source: str,
    target: str,
    source_file: str,
    source_location: str,
    provenance: str,
    argument_index: int | None = None,
) -> str:
    """Deterministic, checkout-root independent ``df:<sha256>`` evidence key.

    The canonicalization is identical to GVR's
    :func:`gvr.graphify_contract.expected_graphify_df_key`, so a Graphify fact
    and a GVR-validated item with the same public fields produce the same key.
    ``argument_index`` is only folded in when present (positional arg mapping).
    """
    fields = _df_field_pairs(
        relation, source, target, source_file, source_location, provenance, argument_index
    )
    canonical = json.dumps(sorted(fields), sort_keys=True, separators=_CANONICAL_SEPARATORS)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return DATA_FLOW_KEY_PREFIX + digest


def df_key_from_edge(edge: Mapping[str, Any]) -> str:
    """Compute the public ``df:`` key from a Graphify extraction edge dict.

    Reads ``metadata.provenance`` and ``metadata.argumentIndex`` (the edge's
    internal camelCase form) and delegates to :func:`df_key`, guaranteeing the
    contract key is byte-identical to :func:`graphify.data_flow_query._evidence_key`.
    """
    metadata = edge.get("metadata") or {}
    return df_key(
        relation=edge.get("relation") or "",
        source=edge.get("source") or "",
        target=edge.get("target") or "",
        source_file=edge.get("source_file") or "",
        source_location=edge.get("source_location") or "",
        provenance=metadata.get("provenance") or "",
        argument_index=metadata.get("argumentIndex"),
    )


def validate_df_key(item: Mapping[str, Any]) -> str:
    """Validate that the public ``df:`` key is content-addressed to its fields.

    Mirrors :func:`gvr.graphify_contract.validate_graphify_df_evidence` exactly
    so Graphify can self-certify df compatibility without importing GVR: the key
    format, required content fields, and content-addressed equality are all
    checked here.
    """
    if not isinstance(item, Mapping):
        raise StructuralEvidenceContractError("graphify df evidence must be a mapping")
    key = str(item.get("key") or "")
    if not DATA_FLOW_KEY_RE.fullmatch(key):
        raise StructuralEvidenceContractError(
            "graphify df evidence key must be df:<64 lowercase sha256 hex>"
        )
    missing = [f for f in _GRAPHIFY_DF_KEY_FIELDS if not str(item.get(f) or "")]
    if missing:
        raise StructuralEvidenceContractError(
            "graphify df evidence is missing content-addressed fields: " + ", ".join(missing)
        )
    expected = df_key(
        relation=item["relation"],
        source=item["source"],
        target=item["target"],
        source_file=item["source_file"],
        source_location=item["source_location"],
        provenance=item["provenance"],
        argument_index=item.get("argument_index"),
    )
    if key != expected:
        raise StructuralEvidenceContractError("graphify df evidence key is not content-addressed")
    return key


def is_valid_df_key(item: Any) -> bool:
    try:
        validate_df_key(item)
        return True
    except StructuralEvidenceContractError:
        return False


def boundary_evidence_key(node: Mapping[str, Any]) -> str:
    """Deterministic, checkout-root independent ``bnd:<sha256>`` boundary key.

    Derived only from canonical public evidence fields (repo-relative source
    file, caller location, resolution, method/arity, receiver FQN, reason).
    The diagnostic node id is intentionally excluded from the fingerprint (its
    stability across a Graphify remap is not guaranteed); it is surfaced
    separately as the diagnostic evidence key. Identical to
    :func:`graphify.data_flow_query._boundary_evidence_key`.
    """
    metadata = node.get("metadata") or {}
    fields: list[tuple[str, str]] = [
        ("sf", str(node.get("source_file") or "")),
        ("loc", str(metadata.get("callerLocation") or "")),
        ("kind", str(metadata.get("kind") or "")),
        ("cap", str(metadata.get("capability") or "")),
        ("framework", str(metadata.get("framework") or "")),
        ("res", str(metadata.get("resolution") or "")),
        ("method", str(metadata.get("method") or "")),
        ("arity", str(metadata.get("arity") or 0)),
        ("rfqn", str(metadata.get("receiverFqn") or "")),
        ("repo", str(metadata.get("repositoryFqn") or "")),
        ("entity", str(metadata.get("entityFqn") or "")),
        ("reason", str(metadata.get("reason") or "")),
    ]
    canonical = json.dumps(sorted(fields), sort_keys=True, separators=_CANONICAL_SEPARATORS)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return BOUNDARY_KEY_PREFIX + digest


# --------------------------------------------------------------------------- #
# Typed fact
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StructuralEvidenceFact:
    """A typed, source-derived direct evidence fact with a public ``df:`` key.

    ``key`` is the deterministic, checkout-root independent content-addressed
    evidence key. When omitted (``None``) it is derived from the other public
    fields; a supplied key is validated to be GVR-content-addressed.
    """

    relation: str
    source: str
    target: str
    source_file: str
    source_location: str
    provenance: str
    analysis_completeness: str
    receiver_confidence: str
    argument_index: int | None = None
    confidence_score: float | None = None
    path_identity: tuple[tuple[str, ...], ...] = field(default_factory=tuple)
    key: str | None = None

    def __post_init__(self) -> None:
        provenance = str(self.provenance or "")
        if provenance not in {p.value for p in StructuralProvenance}:
            raise StructuralEvidenceContractError(f"unknown structural provenance: {provenance!r}")
        completeness = str(self.analysis_completeness or "")
        if completeness not in {c.value for c in StructuralCoverage}:
            raise StructuralEvidenceContractError(f"unknown analysis completeness: {completeness!r}")
        receiver = str(self.receiver_confidence or "")
        if receiver not in {c.value for c in StructuralReceiverConfidence}:
            raise StructuralEvidenceContractError(f"unknown receiver confidence: {receiver!r}")
        key = self.key
        if key is None:
            object.__setattr__(self, "key", df_key(
                relation=self.relation, source=self.source, target=self.target,
                source_file=self.source_file, source_location=self.source_location,
                provenance=provenance, argument_index=self.argument_index,
            ))
        else:
            if not DATA_FLOW_KEY_RE.fullmatch(key):
                raise StructuralEvidenceContractError("StructuralEvidenceFact.key must be a df:<sha256> key")
            if key != df_key(
                relation=self.relation, source=self.source, target=self.target,
                source_file=self.source_file, source_location=self.source_location,
                provenance=provenance, argument_index=self.argument_index,
            ):
                raise StructuralEvidenceContractError("StructuralEvidenceFact.key is not content-addressed")

    @property
    def fingerprint(self) -> str:
        return structural_evidence_fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "relation": self.relation,
            "source": self.source,
            "target": self.target,
            "source_file": self.source_file,
            "source_location": self.source_location,
            "provenance": self.provenance,
            "analysis_completeness": self.analysis_completeness,
            "receiver_confidence": self.receiver_confidence,
            "argument_index": self.argument_index,
            "confidence_score": self.confidence_score,
            "path_identity": [list(path) for path in self.path_identity],
        }


# --------------------------------------------------------------------------- #
# Typed blocker (boundary / resolution event)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StructuralEvidenceBlocker:
    """A machine-visible blocking boundary as first-class evidence.

    A blocker records a resolution the analyzer attempted but could not safely
    cross (``AMBIGUOUS`` / ``UNRESOLVED`` / ``UNSUPPORTED``). It is a coverage
    caveat, never a fabricated flow step. ``key`` is the deterministic
    ``bnd:<sha256>`` boundary evidence key; ``diagnostic_key`` is the parallel
    ``diag:`` reference to the underlying diagnostic node.
    """

    key: str
    diagnostic_key: str
    resolution: str
    reason: str
    reason_code: str = ""
    canonical_caller_file: str = ""
    caller_location: str = ""
    receiver_fqn: str = ""
    method: str = ""
    arity: int = 0
    import_context: str = ""
    candidate_count: int = 0
    receiver_confidence: str = "MAY"
    framework: str = ""
    capability: str = ""
    diagnostic_kind: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not BOUNDARY_KEY_RE.fullmatch(self.key):
            raise StructuralEvidenceContractError(
                "StructuralEvidenceBlocker.key must be a bnd:<sha256> key"
            )
        if not DIAGNOSTIC_KEY_RE.fullmatch(self.diagnostic_key):
            raise StructuralEvidenceContractError(
                "StructuralEvidenceBlocker.diagnostic_key must be a diag:<id> key"
            )
        object.__setattr__(self, "details", _snapshot_map(self.details))

    @property
    def fingerprint(self) -> str:
        return structural_evidence_fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "diagnostic_key": self.diagnostic_key,
            "resolution": self.resolution,
            "reason": self.reason,
            "reason_code": self.reason_code,
            "canonical_caller_file": self.canonical_caller_file,
            "caller_location": self.caller_location,
            "receiver_fqn": self.receiver_fqn,
            "method": self.method,
            "arity": self.arity,
            "import_context": self.import_context,
            "candidate_count": self.candidate_count,
            "receiver_confidence": self.receiver_confidence,
            "framework": self.framework,
            "capability": self.capability,
            "diagnostic_kind": self.diagnostic_kind,
            "details": dict(self.details),
        }

    def to_gvr_boundary_event(self) -> dict[str, Any]:
        """Project the blocker into the public traversal-result boundary event shape."""
        return {
            "boundary_evidence_key": self.key,
            "diagnostic_evidence_key": self.diagnostic_key,
            "canonical_caller_file": self.canonical_caller_file,
            "caller_location": self.caller_location,
            "resolution": self.resolution,
            "reason": self.reason,
            "reason_code": self.reason_code,
            "receiver": self.receiver_fqn,
            "receiver_fqn": self.receiver_fqn,
            "receiver_confidence": self.receiver_confidence,
            "method": self.method,
            "arity": self.arity,
            "import_context": self.import_context,
            "candidate_count": self.candidate_count,
            "framework": self.framework,
            "capability": self.capability,
            "diagnostic_kind": self.diagnostic_kind,
        }


# --------------------------------------------------------------------------- #
# Typed derived path (query-time, snapshotted)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StructuralEvidencePath:
    """A derived path: ordered direct-evidence hops, snapshotted for the snapshot.

    Each path is identified by its ordered evidence keys (``path_identity``) and
    never fabricates evidence: it only references facts already present in the
    snapshot.
    """

    path_identity: tuple[str, ...]
    supporting_evidence_keys: tuple[str, ...]
    exactness: str
    receiver_confidence: str
    coverage: str

    def __post_init__(self) -> None:
        for k in self.path_identity:
            if not DATA_FLOW_KEY_RE.fullmatch(k):
                raise StructuralEvidenceContractError(
                    "StructuralEvidencePath.path_identity must contain df:<sha256> keys only"
                )

    @property
    def fingerprint(self) -> str:
        return structural_evidence_fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "path_identity": list(self.path_identity),
            "supporting_evidence_keys": list(self.supporting_evidence_keys),
            "exactness": self.exactness,
            "receiver_confidence": self.receiver_confidence,
            "coverage": self.coverage,
        }


# --------------------------------------------------------------------------- #
# Search-level coverage state
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StructuralEvidenceCoverageState:
    """Epistemic state of the traversal that produced this snapshot's facts."""

    search_coverage: str
    complete_supported_search: bool
    termination_reason: str
    truncated: bool
    input_resolution: str
    query_validity: bool
    start_node_found: bool
    target_node_found: bool | None
    encountered_partial_evidence: bool
    encountered_unknown_evidence: bool
    encountered_may_evidence: bool
    query_bounds: Mapping[str, Any]
    rejected_relations: tuple[str, ...]
    visited_count: int
    expanded_count: int

    def __post_init__(self) -> None:
        if self.search_coverage not in {c.value for c in StructuralCoverage}:
            raise StructuralEvidenceContractError(f"unknown search coverage: {self.search_coverage!r}")
        object.__setattr__(self, "query_bounds", _snapshot_map(self.query_bounds))

    @property
    def fingerprint(self) -> str:
        return structural_evidence_fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "search_coverage": self.search_coverage,
            "complete_supported_search": self.complete_supported_search,
            "termination_reason": self.termination_reason,
            "truncated": self.truncated,
            "input_resolution": self.input_resolution,
            "query_validity": self.query_validity,
            "start_node_found": self.start_node_found,
            "target_node_found": self.target_node_found,
            "encountered_partial_evidence": self.encountered_partial_evidence,
            "encountered_unknown_evidence": self.encountered_unknown_evidence,
            "encountered_may_evidence": self.encountered_may_evidence,
            "query_bounds": dict(self.query_bounds),
            "rejected_relations": list(self.rejected_relations),
            "visited_count": self.visited_count,
            "expanded_count": self.expanded_count,
        }


# --------------------------------------------------------------------------- #
# Source revision scope (checkout-root independent identity)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SourceRevisionScope:
    """Deterministic, checkout-root independent source identity.

    ``repo_root`` is a local locator (excluded from the fingerprint, exactly like
    the diagnostic node id) so the same revision mapped from two checkout roots
    yields an identical scope.
    """

    provider_id: str
    source_class: str
    source_revision: str
    repo_root: str = ""

    def __post_init__(self) -> None:
        if self.source_class != SOURCE_CLASS_GIT_COMMIT:
            raise StructuralEvidenceContractError(
                f"unsupported source_class: {self.source_class!r} (only {SOURCE_CLASS_GIT_COMMIT!r})"
            )
        if not SOURCE_REVISION_RE.fullmatch(str(self.source_revision or "")):
            raise StructuralEvidenceContractError("source_revision must be a git commit SHA")
        object.__setattr__(self, "repo_root", str(self.repo_root))

    @property
    def fingerprint(self) -> str:
        # Excludes repo_root: checkout-root independent. A scope identifies only the
        # source (provider + class + revision); repo_root is a local materialization
        # locator and is never part of public evidence identity.
        return structural_evidence_fingerprint(
            {
                "provider_id": self.provider_id,
                "source_class": self.source_class,
                "source_revision": self.source_revision,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        # repo_root is intentionally omitted: it must not leak an absolute checkout
        # path into the public, fingerprinted contract.
        return {
            "provider_id": self.provider_id,
            "source_class": self.source_class,
            "source_revision": self.source_revision,
        }


# --------------------------------------------------------------------------- #
# The versioned, deterministic, source-revision-scoped public snapshot
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StructuralEvidenceSnapshot:
    """Versioned, deterministic, source-revision-scoped structural evidence.

    A snapshot is the canonical public envelope for source-derived structural
    evidence. It never carries a verification verdict. Its ``fingerprint`` is
    derived entirely from content (schema, scope, query, coverage, facts, paths,
    blockers) and is therefore stable and checkout-root independent.
    """

    schema_version: int
    format: str
    provider_id: str
    analyzer_revision: str
    source_revision_scope: SourceRevisionScope
    query: Mapping[str, Any]
    coverage: StructuralEvidenceCoverageState
    facts: tuple[StructuralEvidenceFact, ...]
    paths: tuple[StructuralEvidencePath, ...]
    blockers: tuple[StructuralEvidenceBlocker, ...]

    def __post_init__(self) -> None:
        if self.schema_version != STRUCTURAL_EVIDENCE_SCHEMA_VERSION:
            raise StructuralEvidenceContractError(
                f"unsupported schema_version: {self.schema_version!r}"
            )
        if self.format != STRUCTURAL_EVIDENCE_FORMAT:
            raise StructuralEvidenceContractError(f"unsupported format: {self.format!r}")
        if self.provider_id != GRAPHIFY_PROVIDER_ID:
            raise StructuralEvidenceContractError(f"unsupported provider_id: {self.provider_id!r}")
        if not str(self.analyzer_revision or "").strip():
            raise StructuralEvidenceContractError("analyzer_revision must be non-empty")
        if "/" not in self.analyzer_revision:
            raise StructuralEvidenceContractError(
                "analyzer_revision must be namespaced as <analyzer>/<revision>"
            )
        # Deterministic ordering and fail-closed identity deduplication. Identical
        # duplicates collapse; conflicting content under one immutable key is an
        # analyzer contract violation and must never be silently overwritten.
        object.__setattr__(self, "facts", _dedup_by_identity(self.facts, identity=lambda f: f.key))
        object.__setattr__(
            self, "paths", _dedup_by_identity(self.paths, identity=lambda p: p.path_identity)
        )
        object.__setattr__(
            self, "blockers", _dedup_by_identity(self.blockers, identity=lambda b: b.key)
        )
        object.__setattr__(self, "query", _semantic_query(self.query))

    @property
    def fingerprint(self) -> str:
        return structural_evidence_fingerprint(self._content_dict())

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "format": self.format,
            "provider_id": self.provider_id,
            "analyzer_revision": self.analyzer_revision,
            "source_revision_scope": self.source_revision_scope.to_dict(),
            "query": dict(self.query),
            "coverage": self.coverage.to_dict(),
            "facts": [f.to_dict() for f in self.facts],
            "paths": [p.to_dict() for p in self.paths],
            "blockers": [b.to_dict() for b in self.blockers],
        }

    def to_dict(self) -> dict[str, Any]:
        content = self._content_dict()
        content["fingerprint"] = self.fingerprint
        return content

    def to_gvr_traversal_dict(self) -> dict[str, Any]:
        """Public dict consumable by GVR's graphify adapter unchanged.

        Every ``supporting_evidence`` item carries a validated, content-addressed
        ``df:`` key; every boundary event carries its ``bnd:`` / ``diag:`` key.
        """
        fact_by_key = {f.key: f for f in self.facts}
        paths: list[dict[str, Any]] = []
        for p in self.paths:
            supporting = []
            for k in p.supporting_evidence_keys:
                fact = fact_by_key.get(k)
                if fact is None:
                    raise StructuralEvidenceContractError(
                        f"path references unknown df key: {k}"
                    )
                item = fact.to_dict()
                # Re-assert df compatibility through the canonical validator.
                validate_df_key(item)
                supporting.append(item)
            paths.append({
                "path_identity": list(p.path_identity),
                "supporting_evidence": supporting,
                "path_exactness": p.exactness,
                "path_receiver_confidence": p.receiver_confidence,
                "path_coverage": p.coverage,
            })
        return {
            "paths": paths,
            "boundary_events": [b.to_gvr_boundary_event() for b in self.blockers],
            "complete_supported_search": self.coverage.complete_supported_search,
            "search_coverage": self.coverage.search_coverage,
            "termination_reason": self.coverage.termination_reason,
            "start": self.query.get("start", ""),
            "target": None if self.query.get("target") is None else self.query.get("target"),
            "direction": self.query.get("direction", "UNKNOWN"),
            "truncated": self.coverage.truncated,
            "input_resolution": self.coverage.input_resolution,
            "query_validity": self.coverage.query_validity,
            "start_node_found": self.coverage.start_node_found,
            "target_node_found": self.coverage.target_node_found,
            "query_bounds": dict(self.coverage.query_bounds),
            "rejected_relations": list(self.coverage.rejected_relations),
            "encountered_partial_evidence": self.coverage.encountered_partial_evidence,
            "encountered_unknown_evidence": self.coverage.encountered_unknown_evidence,
            "encountered_may_evidence": self.coverage.encountered_may_evidence,
        }

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> "StructuralEvidenceSnapshot":
        if not isinstance(doc, Mapping):
            raise StructuralEvidenceContractError("snapshot document must be a mapping")
        if doc.get("schema_version") != STRUCTURAL_EVIDENCE_SCHEMA_VERSION:
            raise StructuralEvidenceContractError("snapshot schema_version mismatch")
        if doc.get("format") != STRUCTURAL_EVIDENCE_FORMAT:
            raise StructuralEvidenceContractError("snapshot format mismatch")
        if doc.get("provider_id") != GRAPHIFY_PROVIDER_ID:
            raise StructuralEvidenceContractError("snapshot provider_id mismatch")
        scope = SourceRevisionScope(**doc["source_revision_scope"])  # type: ignore[arg-type]
        coverage = _coverage_from_dict(doc["coverage"])
        facts = tuple(_fact_from_dict(item) for item in doc.get("facts", []))
        paths = tuple(_path_from_dict(item) for item in doc.get("paths", []))
        blockers = tuple(_blocker_from_dict(item) for item in doc.get("blockers", []))
        query = doc.get("query", {})
        snapshot = cls(
            schema_version=STRUCTURAL_EVIDENCE_SCHEMA_VERSION,
            format=STRUCTURAL_EVIDENCE_FORMAT,
            provider_id=GRAPHIFY_PROVIDER_ID,
            analyzer_revision=str(doc.get("analyzer_revision") or ""),
            source_revision_scope=scope,
            query=query,
            coverage=coverage,
            facts=facts,
            paths=paths,
            blockers=blockers,
        )
        if doc.get("fingerprint") != snapshot.fingerprint:
            raise StructuralEvidenceContractError("snapshot fingerprint mismatch")
        return snapshot


# --------------------------------------------------------------------------- #
# Builders / serializers
# --------------------------------------------------------------------------- #
def build_structural_evidence_snapshot(
    result: Any,
    *,
    repo_root: str,
    source_revision: str,
    source_class: str = SOURCE_CLASS_GIT_COMMIT,
    analyzer_revision: str = DEFAULT_ANALYZER_REVISION,
    snapshot_query: Mapping[str, Any] | None = None,
) -> StructuralEvidenceSnapshot:
    """Build a deterministic StructuralEvidenceSnapshot from a traversal result.

    ``result`` is the public :class:`graphify.data_flow_query.DataFlowTraversalResult`
    (or any compatible mapping exposing ``paths``, ``boundary_events``, the
    coverage/epistemic fields and ``query_bounds``). Source-derived facts are
    collected from each path's ``supporting_evidence`` and de-duplicated by their
    public ``df:`` key; blocking boundaries become :class:`StructuralEvidenceBlocker`
    records. No truth verdict is computed or emitted.
    """
    scope = SourceRevisionScope(
        provider_id=GRAPHIFY_PROVIDER_ID,
        source_class=source_class,
        source_revision=source_revision,
        repo_root=str(repo_root),
    )

    # Collect unique facts across all paths, keyed by their public df key.
    facts_by_key: dict[str, StructuralEvidenceFact] = {}
    fact_paths: dict[str, list[tuple[str, ...]]] = {}
    for path in result.paths:
        identity = tuple(path.path_identity)
        ref_map = {ref.key: ref for ref in path.supporting_evidence}
        for ref in path.supporting_evidence:
            facts_by_key.setdefault(
                ref.key,
                _fact_from_evidence_ref(ref),
            )
            fact_paths.setdefault(ref.key, []).append(identity)
        # Re-keyed facts must not drift from their supporting evidence identity.
        for k in identity:
            if k not in ref_map:
                raise StructuralEvidenceContractError(
                    f"path_identity references unknown evidence key: {k}"
                )

    facts: list[StructuralEvidenceFact] = []
    for key in sorted(facts_by_key):
        fact = facts_by_key[key]
        paths_using = tuple(sorted(fact_paths[key]))
        object.__setattr__(fact, "path_identity", paths_using)
        facts.append(fact)

    paths = tuple(
        StructuralEvidencePath(
            path_identity=tuple(path.path_identity),
            supporting_evidence_keys=tuple(ref.key for ref in path.supporting_evidence),
            exactness=path.path_exactness,
            receiver_confidence=path.path_receiver_confidence,
            coverage=path.path_coverage,
        )
        for path in result.paths
    )

    blockers = tuple(
        _blocker_from_boundary_event(event) for event in result.boundary_events
    )

    coverage = StructuralEvidenceCoverageState(
        search_coverage=result.search_coverage,
        complete_supported_search=result.complete_supported_search,
        termination_reason=result.termination_reason,
        truncated=result.truncated,
        input_resolution=result.input_resolution,
        query_validity=result.query_validity,
        start_node_found=result.start_node_found,
        target_node_found=result.target_node_found,
        encountered_partial_evidence=result.encountered_partial_evidence,
        encountered_unknown_evidence=result.encountered_unknown_evidence,
        encountered_may_evidence=result.encountered_may_evidence,
        query_bounds=result.query_bounds,
        rejected_relations=tuple(result.rejected_relations),
        visited_count=result.visited_count,
        expanded_count=result.expanded_count,
    )

    if snapshot_query is not None:
        query = dict(snapshot_query)
    else:
        query = dict(result.query_bounds) if isinstance(result.query_bounds, Mapping) else {}
        query.setdefault("start", result.start)
        query.setdefault("target", result.target)
        query.setdefault("direction", result.direction)

    return StructuralEvidenceSnapshot(
        schema_version=STRUCTURAL_EVIDENCE_SCHEMA_VERSION,
        format=STRUCTURAL_EVIDENCE_FORMAT,
        provider_id=GRAPHIFY_PROVIDER_ID,
        analyzer_revision=analyzer_revision,
        source_revision_scope=scope,
        query=query,
        coverage=coverage,
        facts=tuple(facts),
        paths=paths,
        blockers=blockers,
    )


def serialize_snapshot(snapshot: StructuralEvidenceSnapshot) -> str:
    """Deterministic JSON serialization (sorted keys, canonical separators)."""
    return json.dumps(
        snapshot.to_dict(),
        sort_keys=True,
        separators=_CANONICAL_SEPARATORS,
        ensure_ascii=False,
        allow_nan=False,
    )


def load_snapshot(text: str) -> StructuralEvidenceSnapshot:
    """Load a snapshot from its deterministic JSON serialization."""
    return StructuralEvidenceSnapshot.from_dict(json.loads(text))


def validate_snapshot(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a serialized snapshot document and return its canonical form."""
    return StructuralEvidenceSnapshot.from_dict(doc).to_dict()


# --------------------------------------------------------------------------- #
# Internal reconstruction helpers
# --------------------------------------------------------------------------- #
def _fact_from_evidence_ref(ref: Any) -> StructuralEvidenceFact:
    provenance = str(ref.provenance or "") or "STATIC_AST"
    return StructuralEvidenceFact(
        key=ref.key,
        relation=ref.relation,
        source=ref.source,
        target=ref.target,
        source_file=ref.source_file,
        source_location=ref.source_location,
        provenance=provenance,
        analysis_completeness=ref.analysis_completeness,
        receiver_confidence=ref.receiver_confidence,
        argument_index=ref.argument_index,
        confidence_score=ref.confidence_score,
        path_identity=(),
    )


def _blocker_from_boundary_event(event: Mapping[str, Any]) -> StructuralEvidenceBlocker:
    diagnostic_key = str(
        event.get("diagnostic_evidence_key")
        or (DIAGNOSTIC_KEY_PREFIX + str(event.get("diagnostic_node_id") or ""))
    )
    resolution = str(event.get("resolution") or "")
    return StructuralEvidenceBlocker(
        key=str(event.get("boundary_evidence_key") or ""),
        diagnostic_key=diagnostic_key,
        resolution=resolution,
        reason=str(event.get("reason") or resolution.lower()),
        reason_code=str(event.get("reason") or resolution.lower()),
        canonical_caller_file=str(event.get("canonical_caller_file") or ""),
        caller_location=str(event.get("caller_location") or ""),
        receiver_fqn=str(event.get("receiver_fqn") or event.get("receiver") or ""),
        method=str(event.get("method") or ""),
        arity=int(event.get("arity") or 0),
        import_context=str(event.get("import_context") or "unknown"),
        candidate_count=int(event.get("candidate_count") or 0),
        receiver_confidence=str(event.get("receiver_confidence") or "MAY"),
        framework=str(event.get("framework") or ""),
        capability=str(event.get("capability") or ""),
        diagnostic_kind=str(event.get("diagnostic_kind") or ""),
        details=dict(event),
    )


def _fact_from_dict(d: Mapping[str, Any]) -> StructuralEvidenceFact:
    return StructuralEvidenceFact(
        key=str(d.get("key") or ""),
        relation=str(d.get("relation") or ""),
        source=str(d.get("source") or ""),
        target=str(d.get("target") or ""),
        source_file=str(d.get("source_file") or ""),
        source_location=str(d.get("source_location") or ""),
        provenance=str(d.get("provenance") or "STATIC_AST"),
        analysis_completeness=str(d.get("analysis_completeness") or d.get("coverage") or ""),
        receiver_confidence=str(d.get("receiver_confidence") or ""),
        argument_index=(
            int(d["argument_index"]) if d.get("argument_index") is not None else None
        ),
        confidence_score=(
            float(d["confidence_score"]) if d.get("confidence_score") is not None else None
        ),
        path_identity=tuple(tuple(p) for p in d.get("path_identity", [])),
    )


def _path_from_dict(d: Mapping[str, Any]) -> StructuralEvidencePath:
    return StructuralEvidencePath(
        path_identity=tuple(d.get("path_identity", [])),
        supporting_evidence_keys=tuple(d.get("supporting_evidence_keys", [])),
        exactness=str(d.get("exactness") or ""),
        receiver_confidence=str(d.get("receiver_confidence") or ""),
        coverage=str(d.get("coverage") or ""),
    )


def _blocker_from_dict(d: Mapping[str, Any]) -> StructuralEvidenceBlocker:
    return StructuralEvidenceBlocker(
        key=str(d.get("key") or ""),
        diagnostic_key=str(d.get("diagnostic_key") or ""),
        resolution=str(d.get("resolution") or ""),
        reason=str(d.get("reason") or ""),
        reason_code=str(d.get("reason_code") or d.get("reason") or ""),
        canonical_caller_file=str(d.get("canonical_caller_file") or ""),
        caller_location=str(d.get("caller_location") or ""),
        receiver_fqn=str(d.get("receiver_fqn") or ""),
        method=str(d.get("method") or ""),
        arity=int(d.get("arity") or 0),
        import_context=str(d.get("import_context") or "unknown"),
        candidate_count=int(d.get("candidate_count") or 0),
        receiver_confidence=str(d.get("receiver_confidence") or "MAY"),
        framework=str(d.get("framework") or ""),
        capability=str(d.get("capability") or ""),
        diagnostic_kind=str(d.get("diagnostic_kind") or ""),
        details=d.get("details", {}) if isinstance(d.get("details"), Mapping) else {},
    )


def _coverage_from_dict(d: Mapping[str, Any]) -> StructuralEvidenceCoverageState:
    return StructuralEvidenceCoverageState(
        search_coverage=str(d.get("search_coverage") or ""),
        complete_supported_search=bool(d.get("complete_supported_search")),
        termination_reason=str(d.get("termination_reason") or ""),
        truncated=bool(d.get("truncated")),
        input_resolution=str(d.get("input_resolution") or ""),
        query_validity=bool(d.get("query_validity")),
        start_node_found=bool(d.get("start_node_found")),
        target_node_found=d.get("target_node_found"),
        encountered_partial_evidence=bool(d.get("encountered_partial_evidence")),
        encountered_unknown_evidence=bool(d.get("encountered_unknown_evidence")),
        encountered_may_evidence=bool(d.get("encountered_may_evidence")),
        query_bounds=d.get("query_bounds", {}) if isinstance(d.get("query_bounds"), Mapping) else {},
        rejected_relations=tuple(d.get("rejected_relations", [])),
        visited_count=int(d.get("visited_count") or 0),
        expanded_count=int(d.get("expanded_count") or 0),
    )


def _snapshot_map(value: Any) -> Any:
    """Return a stable, frozen-ish snapshot of a mapping (deep-copied scalars)."""
    if isinstance(value, Mapping):
        return {str(k): _snapshot_map(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_snapshot_map(v) for v in value]
    return value


def _semantic_query(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Snapshot semantic query inputs without observation/run correlation."""
    return {
        str(key): _snapshot_map(item)
        for key, item in value.items()
        if str(key) not in _CORRELATION_ONLY_QUERY_KEYS
    }


def _dedup_by_identity(items: Sequence[Any], *, identity: Any) -> tuple[Any, ...]:
    by_identity: dict[Any, tuple[str, Any]] = {}
    for item in items:
        item_identity = identity(item)
        token = serialize_snapshot_value(item.to_dict() if hasattr(item, "to_dict") else item)
        prior = by_identity.get(item_identity)
        if prior is not None and prior[0] != token:
            raise StructuralEvidenceContractError(
                f"conflicting structural evidence for immutable identity: {item_identity!r}"
            )
        by_identity[item_identity] = (token, item)
    return tuple(by_identity[item_identity][1] for item_identity in sorted(by_identity))


def serialize_snapshot_value(value: Any) -> str:
    """Canonical JSON used only to compare duplicate identity payloads."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=_CANONICAL_SEPARATORS,
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )
