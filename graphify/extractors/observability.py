"""Observability-callsite classification for JS/TS and Java static log callsites.

Runtime-observability anchors are extracted from plain ``.js``/``.ts``/``.tsx``/
``.mjs``/``.cjs``/``.mts``/``.cts`` files (``extract_js``) and ``.java`` files
(``extract_java``) as dedicated ``observability_anchor`` nodes connected to
their enclosing symbol. This module holds the pure, unit-testable helpers:
recognizing a logging call and recovering a *static* first-message template
when one exists.

Supported frameworks (deliberately conservative, no guessing):

- ``console``: ``console.debug/info/warn/error(...)`` (the method set the first
  slice targets; ``console.log`` is intentionally not anchored yet).
- ``logger``: member calls ``<recv>.debug/info/warn/error(...)`` where the
  receiver's final identifier segment is ``logger`` or ``log`` (any case), e.g.
  pino/winston-style ``logger.info(...)``, NestJS ``this.logger.warn(...)``.
- ``bugzero_loki``: BugZero's ``LokiClient`` — ``<loki>.log({...})`` and
  ``<loki>.push([{...}])`` where the receiver's final identifier segment starts
  with ``loki`` (``loki``, ``lokiClient``, ``LokiClient``, ``loki_client``) and
  the payload is an object/array literal carrying a ``message`` property.
- ``slf4j``: Java member calls ``<recv>.debug/info/warn/error(...)`` (tree-sitter
  ``method_invocation``) where the receiver's final identifier segment is
  ``logger`` or ``log`` (any case) — a plain ``log``/``logger`` identifier,
  ``this.logger`` field access, or ``Class.log`` static field access. A computed
  receiver (``getLogger()``) is never a match.

Message recovery rules ("never guessed"):

- A literal string argument is a static template (raw ``string_content`` text).
  For ``slf4j``, every SLF4J ``{}`` placeholder in that literal canonicalizes
  to ``<arg>`` (``log.info("Booking created. COR_ID: {}", id)`` ->
  ``Booking created. COR_ID: <arg>``).
- A template literal with no tag is static: every ``${...}`` substitution
  becomes ``<arg>`` (```` `user ${id} ready` ```` -> ``user <arg> ready``).
- A tagged template, a ``+`` concatenation, a call/identifier/member expression
  or any other computed message is DYNAMIC — the callsite is still anchored
  (``anchor_kind=DYNAMIC_LOG_CALLSITE``) but no canonical template is invented.
- A Loki object literal contributes its ``message`` value when it is itself a
  static string/template; the ``push([...])`` array form joins each element's
  static message with ``" | "`` and degrades to dynamic if any element is not
  statically recoverable.
- A call with no message-bearing argument is not anchored at all.

Whitespace rule (frozen convergence invariant): static canonical templates are
whitespace-normalized with ``normalize_template_whitespace`` — every run of
whitespace collapses to a single space and leading/trailing whitespace is
trimmed — exactly like Incident Context's runtime message normalization. A
template literal written with indentation or alignment therefore converges
with the runtime message it produces, and both sides fingerprint the same
collapsed text.
"""
from __future__ import annotations

import hashlib
import re
import textwrap

from graphify.extractors.base import _read_text

__all__ = [
    "CANONICALIZATION_VERSION",
    "ANCHOR_KIND_LOG_TEMPLATE",
    "ANCHOR_KIND_DYNAMIC_CALLSITE",
    "classify_log_callsite",
    "extract_log_message",
    "canonicalize_log_message",
    "normalize_template_whitespace",
    "shorten_anchor_label",
]

# Canonicalization contract for anchor canonical_template / fingerprint values.
# Bump only when the template/fingerprint derivation rules change in a way that
# would make values produced under an older version un-comparable with newer
# ones. The public fingerprint material is frozen by the Gate 1 contract
# (contracts-v1.md section 7):
#
#   sha256(canonicalization_version + "\n" + canonical_template)
#
# The version is mixed into the digest, so a version bump immediately yields
# different fingerprint values for identical templates.
CANONICALIZATION_VERSION = "runtime-code-canonicalization/v1"

ANCHOR_KIND_LOG_TEMPLATE = "LOG_TEMPLATE"
ANCHOR_KIND_DYNAMIC_CALLSITE = "DYNAMIC_LOG_CALLSITE"

# The level methods anchored for console/loggers (slice 1; console.log /
# logger.log are intentionally out of scope for now).
_LOG_LEVEL_METHODS = frozenset({"debug", "info", "warn", "error"})
_CONSOLE_RECEIVER = "console"

# Common logger receivers: the final identifier segment of the receiver must be
# `logger` or `log` (case-insensitive) — pino/winston/NestJS-style instances
# and Java SLF4J loggers (logback/Log4j/Lombok @Slf4j all name the field `log`
# or `logger`).
_LOGGER_RECEIVER_RE = re.compile(r"^(?:logger|log)$", re.IGNORECASE)

# BugZero LokiClient instances: `loki`, `lokiClient`, `LokiClient`,
# `loki_client`, `LOKI_CLIENT`, ...
_LOKI_RECEIVER_RE = re.compile(r"^loki(?:[_-]?client)?$", re.IGNORECASE)
_LOKI_METHODS = frozenset({"log", "push"})

# Wrapper expression types unwrapped before classifying a message expression:
# parens, TS `as`/`satisfies` casts, non-null `!`, and `<T>expr` type assertions.
_UNWRAP_TYPES = frozenset({
    "parenthesized_expression", "as_expression", "satisfies_expression",
    "non_null_expression", "type_assertion",
})


def _receiver_final_segment(obj, source: bytes) -> str | None:
    """Final identifier segment of a member-call receiver.

    ``logger`` -> ``logger``; ``this.logger`` -> ``logger``;
    ``config.lokiClient`` -> ``lokiClient``; anything computed (a call result,
    an index expression) -> None (never guessed).
    """
    if obj is None:
        return None
    if obj.type == "identifier":
        return _read_text(obj, source)
    if obj.type == "this":
        return None
    if obj.type == "member_expression":
        prop = obj.child_by_field_name("property")
        if prop is None:
            return None
        if prop.type == "property_identifier":
            return _read_text(prop, source)
        # Computed access `recv[KEY]` / `recv[0]` — dynamic receiver.
        return None
    return None


def _java_receiver_final_segment(obj, source: bytes) -> str | None:
    """Final identifier segment of a Java ``method_invocation`` receiver.

    ``log``/``logger`` -> itself; ``this.logger`` -> ``logger`` (field access
    on ``this``); ``Service.LOG`` -> ``LOG`` (static field access on a plain
    class name). Anything computed — a call result (``getLogger()``), a method
    chain (``ctx.get().log``), an array access — -> None (never guessed). The
    final-segment regex (``^log|logger$``) filters the rest, so arbitrary
    fields like ``client.info`` never classify.
    """
    if obj is None:
        return None
    if obj.type == "identifier":
        return _read_text(obj, source)
    if obj.type == "field_access":
        owner = obj.child_by_field_name("object")
        field = obj.child_by_field_name("field")
        if owner is None or field is None or field.type != "identifier":
            return None
        # `this.logger` and static `Class.log` are conservative; any other
        # owner shape (a call result, a chain) is computed.
        if owner.type in ("this", "identifier"):
            return _read_text(field, source)
        return None
    return None


def classify_log_callsite(node, source: bytes) -> tuple[str, str] | None:
    """Return ``(framework, method)`` when ``node`` is a recognized logging
    call, else None. ``node`` must be a tree-sitter ``call_expression``
    (JS/TS) or ``method_invocation`` (Java)."""
    if node is None:
        return None
    if node.type == "call_expression":
        fn = node.child_by_field_name("function")
        if fn is None or fn.type != "member_expression":
            return None
        prop = fn.child_by_field_name("property")
        obj = fn.child_by_field_name("object")
        if prop is None or obj is None:
            return None
        method = _read_text(prop, source)
        if not method:
            return None
        recv = _receiver_final_segment(obj, source)
        if recv == _CONSOLE_RECEIVER and method in _LOG_LEVEL_METHODS:
            return ("console", method)
        if recv is not None and _LOGGER_RECEIVER_RE.match(recv) and method in _LOG_LEVEL_METHODS:
            return ("logger", method)
        if recv is not None and _LOKI_RECEIVER_RE.match(recv) and method in _LOKI_METHODS:
            return ("bugzero_loki", method)
        return None
    if node.type == "method_invocation":
        method = _read_text(node.child_by_field_name("name"), source)
        if not method or method not in _LOG_LEVEL_METHODS:
            return None
        recv = _java_receiver_final_segment(node.child_by_field_name("object"), source)
        if recv is not None and _LOGGER_RECEIVER_RE.match(recv):
            return ("slf4j", method)
        return None
    return None


def _unwrap(expr):
    """Walk through parentheses / TS casts to the underlying expression."""
    while expr is not None and expr.type in _UNWRAP_TYPES:
        inner = expr.child_by_field_name("value")
        if inner is None:
            inner = expr.child_by_field_name("expression")
        if inner is None:
            for child in expr.children:
                if child.is_named and child.type not in ("type", "type_arguments"):
                    inner = child
                    break
        if inner is None or inner is expr:
            break
        expr = inner
    return expr


def _static_string_content(node, source: bytes) -> str | None:
    """Canonical text of a plain string literal node, or None."""
    content = next((c for c in node.children
                    if c.type in ("string_content", "string_fragment")), None)
    if content is not None:
        return _read_text(content, source)
    return None


def normalize_template_whitespace(text: str) -> str:
    """Collapse every run of whitespace to one space and trim the edges.

    This is the single whitespace rule for canonical templates, shared with
    Incident Context's runtime normalization (frozen convergence invariant).
    Source literals and runtime messages apply it identically so both forms
    converge to the same canonical template; the transformation is idempotent.
    """
    return re.sub(r"\s+", " ", text).strip()


def _canonicalize_template(node, source: bytes) -> str:
    """Canonical template of an untagged template literal: literal fragments
    verbatim, every ``${...}`` substitution collapsed to ``<arg>``."""
    parts: list[str] = []
    for child in node.children:
        if child.type in ("string_fragment", "string_content"):
            parts.append(_read_text(child, source))
        elif child.type == "template_substitution":
            parts.append("<arg>")
    return "".join(parts)


def _classify_message_expr(expr, source: bytes) -> tuple[str, str | None]:
    """Classify one message expression.

    Returns ``("static", canonical_template)`` when the message is statically
    recoverable, ``("dynamic", None)`` otherwise. Never invents content.
    """
    e = _unwrap(expr)
    if e is None:
        return ("dynamic", None)
    if e.type in ("string", "string_literal"):
        content = _static_string_content(e, source)
        return ("static", content if content is not None else "")
    if e.type == "template_string":
        # A tagged template (`tag`x``) has a `tag` field and a computed message;
        # an untagged one is a plain static template with substitutions.
        if e.child_by_field_name("tag") is not None:
            return ("dynamic", None)
        return ("static", _canonicalize_template(e, source))
    return ("dynamic", None)


def _extract_loki_message(arg, source: bytes) -> tuple[str, str | None]:
    """Recover the ``message`` value from a LokiClient.log/push payload.

    ``log({ level, message: "..." })`` reads the object literal's ``message``
    property; ``push([{ message: "..." }, ...])`` joins every element's static
    message with ``" | "`` and degrades to dynamic when any element is not
    statically recoverable (never guessed).
    """
    e = _unwrap(arg)
    if e is None:
        return ("dynamic", None)
    if e.type == "object":
        for child in e.children:
            if child.type != "pair":
                continue
            key = child.child_by_field_name("key")
            if key is None or key.type not in ("property_identifier", "string"):
                continue
            if _read_text(key, source) not in ("message",):
                continue
            val = child.child_by_field_name("value")
            if val is None:
                return ("dynamic", None)  # shorthand `{ message }` — computed
            return _classify_message_expr(val, source)
        return ("dynamic", None)  # object literal without a static message key
    if e.type == "array":
        messages: list[str] = []
        for child in e.children:
            if not child.is_named:
                continue
            if child.type != "object":
                return ("dynamic", None)
            kind, tmpl = _extract_loki_message(child, source)
            if kind != "static" or tmpl is None:
                return ("dynamic", None)
            messages.append(tmpl)
        if not messages:
            return ("dynamic", None)
        return ("static", " | ".join(messages))
    return ("dynamic", None)  # call / identifier / member — computed at runtime


def _slf4j_placeholders(text: str) -> str:
    """Canonicalize SLF4J ``{}`` placeholders to ``<arg>`` in a static template.

    The literal replacement is exact: no regex, no guessing, and text that
    merely contains ``{}`` (e.g. a JSON snippet in a message) canonicalizes
    like any other placeholder — the same position-independent rule the
    runtime canonicalization applies.
    """
    return text.replace("{}", "<arg>")


def extract_log_message(
    node, source: bytes, framework: str
) -> tuple[str, str | None] | None:
    """Recover the message of a classified logging call.

    Returns ``("static", canonical_template)``, ``("dynamic", None)``, or None
    when the callsite carries no recoverable message argument (then it is not
    anchored at all).  Static templates are whitespace-normalized exactly like
    Incident Context runtime messages (``normalize_template_whitespace``), so
    source and runtime forms converge to the same canonical template.  For
    ``slf4j``, SLF4J ``{}`` placeholders are canonicalized to ``<arg>`` before
    the whitespace rule so the source template and the runtime message
    fingerprint the same material.
    """
    args = node.child_by_field_name("arguments")
    if args is None:
        return None
    named = [c for c in args.children if c.is_named]
    if not named:
        return None
    if framework == "bugzero_loki":
        kind, template = _extract_loki_message(named[0], source)
    else:
        kind, template = _classify_message_expr(named[0], source)
    if kind == "static" and template is not None:
        if framework == "slf4j":
            template = _slf4j_placeholders(template)
        template = normalize_template_whitespace(template)
    return kind, template


def canonicalize_log_message(kind: str, template: str | None) -> str:
    """Canonical form of a message for fingerprinting.

    Static templates are fingerprinted verbatim (they are already canonical);
    dynamic callsites are fingerprinted on the fixed placeholder so the digest
    field stays stable across different dynamic messages.
    """
    if kind == "static" and template is not None:
        return template
    return "<dynamic-callsite>"


def sha256_hex(canonical_template: str) -> str:
    """Hex SHA-256 fingerprint of the versioned canonical-template material.

    The digest covers ``canonicalization_version + "\\n" + canonical_template``
    exactly as frozen by the Gate 1 contract (contracts-v1.md section 7), so the
    value matches the fingerprint Incident Context computes for the same
    template.  Line, file, repository, runtime values, timestamp, pod, request
    ID, and tenant are excluded.  The template is whitespace-normalized before
    hashing (``normalize_template_whitespace``), matching Incident Context's
    ``fingerprint_template``, so a padded source literal and its runtime
    message always yield the same digest.
    """
    normalized = normalize_template_whitespace(canonical_template)
    material = f"{CANONICALIZATION_VERSION}\n{normalized}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def shorten_anchor_label(text: str, width: int = 80) -> str:
    """Collapse whitespace and truncate an anchor label, cutting on a word
    boundary rather than mid-word (mirrors extract._shorten_rationale_label)."""
    label = textwrap.shorten(text, width=width, placeholder="…")
    if label in ("", "…"):
        flat = " ".join(text.split())
        label = flat if len(flat) <= width else flat[: width - 1] + "…"
    return label
