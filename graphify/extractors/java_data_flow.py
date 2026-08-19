"""Static Java local/basic interprocedural data-flow extraction."""
from __future__ import annotations

import importlib
from collections import defaultdict
from pathlib import Path
from typing import Any

from graphify.extractors.base import _file_stem, _make_id, _read_text
from graphify.extractors.engine import _JAVA_BUILTIN_TYPES  # noqa: E402
from graphify.security import sanitize_metadata

DATA_VALUE_TYPE = "data_value"

_JAVA_PRIMITIVES = frozenset({
    "byte", "short", "int", "long", "float", "double", "char", "boolean", "void",
})


def augment_java_data_flow(path: Path, result: dict[str, Any]) -> dict[str, Any]:
    """Augment a Java extraction result with conservative static data-flow facts.

    Scope is intentionally local/basic interprocedural only.  The extractor emits
    nodes and edges only when both endpoints are source-derived and present in the
    same extraction result.  Ambiguous, reflective, dynamic, or name-only cross
    method calls are omitted.
    """
    status = result.setdefault("data_flow", {})
    status["java"] = {"status": "unsupported", "reason": "tree_sitter_java_unavailable"}

    def append_status_node(java_status: dict[str, Any], stem: str) -> None:
        nodes = result.setdefault("nodes", [])
        nid = _make_id(stem, "data_flow", "java", java_status.get("status", "unknown"))
        if any(node.get("id") == nid for node in nodes if isinstance(node, dict)):
            return
        nodes.append({
            "id": nid,
            "label": "Java data-flow extraction status",
            "file_type": "code",
            "type": "extraction_diagnostic",
            "source_file": str(path),
            "source_location": None,
            "confidence": "EXTRACTED",
            "confidence_score": 1.0,
            "metadata": sanitize_metadata({"language": "java", "capability": "data_flow", **java_status}),
        })

    if result.get("error"):
        if result.get("error") == "tree_sitter_java not installed":
            append_status_node(status["java"], path.stem)
        return result

    try:
        mod = importlib.import_module("tree_sitter_java")
        tree_sitter = importlib.import_module("tree_sitter")
        Language = tree_sitter.Language
        Parser = tree_sitter.Parser
        lang_fn = getattr(mod, "language", None)
        if lang_fn is None:
            append_status_node(status["java"], path.stem)
            return result
        parser = Parser(Language(lang_fn()))
        source = path.read_bytes()
        root = parser.parse(source).root_node
    except Exception as exc:
        if isinstance(exc, ModuleNotFoundError) and getattr(exc, "name", "") == "tree_sitter_java":
            status["java"] = {"status": "unsupported", "reason": "tree_sitter_java_unavailable"}
        else:
            status["java"] = {"status": "failed", "reason": type(exc).__name__}
        append_status_node(status["java"], path.stem)
        return result
    status["java"] = {"status": "incomplete" if root.has_error else "succeeded"}

    nodes: list[dict[str, Any]] = result.setdefault("nodes", [])
    edges: list[dict[str, Any]] = result.setdefault("edges", [])
    valid_ids = {n.get("id") for n in nodes}
    seen_nodes = set(valid_ids)
    seen_edges = {(e.get("source"), e.get("target"), e.get("relation"), e.get("source_location")) for e in edges}
    # Canonical repo-relative file identity: reuse Graphify's `_file_stem`
    # (full path, extension dropped) so same-named Java files at ANY
    # repository-relative depth get distinct, portable value identities.
    # `extract()`'s id-remap post-pass relativizes this to the scan root, so
    # the absolute checkout root can never leak into persisted IDs. This
    # replaces the prior sibling-only heuristic (P0-2).
    stem = _file_stem(path)
    str_path = str(path)

    append_status_node(status["java"], stem)
    seen_nodes.add(_make_id(stem, "data_flow", "java", status["java"].get("status", "unknown")))

    # Gate 2B: package + simple-name -> FQN imports for deterministic cross-file
    # receiver resolution (reuses the same import/package facts the resolution
    # layer uses, so no parallel resolver is introduced).
    java_package = ""
    java_imports: dict[str, str] = {}
    java_has_wildcard_import = False
    for child in root.children:
        if child.type == "package_declaration":
            java_package = _read_text(child, source)[len("package"):].strip().rstrip(";").strip()
        elif child.type == "import_declaration":
            body = _read_text(child, source)[len("import"):].strip().rstrip(";").strip()
            if body.startswith("static "):
                body = body[len("static "):].strip()
            if body.endswith(".*"):
                java_has_wildcard_import = True
                continue
            if "." not in body:
                continue
            simple = body.split(".")[-1]
            if simple and simple[0:1].isupper():
                java_imports.setdefault(simple, body)

    # Cross-file call records emitted by this file's extractor; a later
    # repository-wide pass links them to exact callees in other files. This is
    # how Gate 2B bridges caller arguments to cross-file callee parameters
    # without a second analyzer: the SAME per-file extractor that proves local
    # flow also records deterministic cross-file intent.
    cross_file_calls: list[dict[str, Any]] = []
    xf_by_call: dict[object, dict[str, Any]] = {}

    def line(n) -> int:
        return n.start_point[0] + 1

    def loc(n) -> str:
        return f"L{n.start_point[0] + 1}:C{n.start_point[1] + 1}"

    def span(n) -> str:
        return f"{n.start_byte}-{n.end_byte}"

    def add_value(owner_symbol: str, kind: str, name: str, n, java_type: str = "") -> str | None:
        if not owner_symbol or not name:
            return None
        discriminator = span(n) if kind in {"local", "field", "parameter"} or name.startswith("new ") else ""
        nid = _make_id(stem, "data_value", owner_symbol, kind, name, discriminator)
        if nid not in seen_nodes:
            seen_nodes.add(nid)
            frozen_kind = {"parameter": "PARAMETER", "return": "RETURN_VALUE", "local": "LOCAL", "field": "FIELD"}.get(kind, kind.upper())
            md = {
                "language": "java",
                "kind": frozen_kind,
                "owner": owner_symbol,
                "name": name,
                "package": java_package,
                "provenance": "STATIC_AST",
            }
            if java_type:
                md["java_type"] = java_type
            nodes.append({
                "id": nid, "label": f"{kind} {name}", "file_type": "code", "type": DATA_VALUE_TYPE,
                "source_file": str_path, "source_location": loc(n),
                "confidence": "EXTRACTED", "confidence_score": 0.8 if root.has_error else 1.0,
                "metadata": sanitize_metadata(md),
            })
        valid_ids.add(nid)
        return nid

    def add_edge(src: str | None, tgt: str | None, rel: str, n, md: dict[str, Any] | None = None) -> None:
        if not src or not tgt or src not in valid_ids or tgt not in valid_ids:
            return
        key = (src, tgt, rel, loc(n))
        if key in seen_edges:
            return
        seen_edges.add(key)
        score = 0.8 if root.has_error else 1.0
        # Receiver-ambiguous field flow (P0-1): a read/write through a named
        # receiver of a declared class type is not proven same-instance, so it is
        # explicitly downgraded (MAY) rather than presented as definite truth.
        if (md or {}).get("receiverConfidence") == "MAY":
            score = min(score, 0.5)
        edge = {"source": src, "target": tgt, "relation": rel, "confidence": "EXTRACTED", "confidence_score": score,
                "source_file": str_path, "source_location": loc(n), "weight": 1.0,
                "metadata": sanitize_metadata({"provenance": "STATIC_AST"} | (md or {}))}
        edges.append(edge)

    classes: dict[str, dict[str, Any]] = {}
    # simple class name -> qualified owner path (only when unique, so nested
    # classes with the same simple name stay independent and never collapse).
    simple_class: dict[str, str] = {}
    methods_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pending_bodies: list[tuple[object, dict[str, Any], str]] = []
    pending_transformations: list[tuple[str | None, dict[str, Any], int, object]] = []
    pending_field_initializers: list[tuple[object, str, str]] = []
    method_name_counts: dict[tuple[str, str], int] = defaultdict(int)

    def _class_qual(name: str) -> str | None:
        """Resolve a class reference to its deterministic qualified owner path.

        Qualified names are returned as-is; a simple name is honoured only when it
        maps to exactly one class in this file (P0-3), otherwise the reference is
        ambiguous and resolves to None (fail closed)."""
        if name in classes:
            return name
        return simple_class.get(name)

    def _resolve_receiver_type(t: str | None) -> str | None:
        """Resolve a receiver's declared type for cross-file call recording.

        Uses ``_class_qual`` (same-file owner) when deterministic; otherwise keeps
        the raw type name when it is plausibly a class (uppercase) so the call can
        be matched against a class declared in another file. Non-class types
        (``int``, ``String``, primitives) still resolve to None."""
        q = _class_qual(t or "")
        if q:
            return q
        t0 = (t or "").strip().split("<", 1)[0].strip()
        if t0 and t0[0:1].isupper():
            return t0
        return None

    def _receiver_fqn(target_cls: str) -> tuple[str | None, str]:
        """Map a cross-file receiver type to a package-qualified class FQN.

        Returns ``(fqn, state)`` where state is ``EXACT`` when the identity is
        deterministic (explicit import, same-package, or already-qualified) and
        ``AMBIGUOUS``/``UNRESOLVED``/``UNSUPPORTED`` otherwise. Only ``EXACT``
        receivers may be linked cross-file. ``UNSUPPORTED`` means the analyzer
        KNOWS the receiver is a JDK builtin/primitive (no in-repo class to
        link), which is deliberately distinguishable from ``UNRESOLVED`` (a
        plausible user class that cannot be located). This reuses the file's
        own import/package facts, so it is the same resolver the resolution
        layer uses, not a second one."""
        cls = (target_cls or "").strip()
        if not cls:
            return None, "UNRESOLVED"
        cls = cls.split("<", 1)[0].strip()
        simple = cls.rsplit(".", 1)[-1]
        if cls in java_imports:
            return java_imports[cls], "EXACT"
        # JDK builtin/primitive receiver: the analyzer knows this is not an
        # in-repo cross-file class boundary -> UNSUPPORTED (not UNRESOLVED).
        if simple in _JAVA_PRIMITIVES or simple in _JAVA_BUILTIN_TYPES:
            return None, "UNSUPPORTED"
        if "." in cls:
            return cls, "EXACT"
        # A wildcard import makes a bare simple-name receiver ambiguous: it could
        # resolve to a class in any wildcard-imported package, not just the
        # current package. Fail closed (J) rather than assume same-package.
        if java_has_wildcard_import:
            return None, "AMBIGUOUS"
        if java_package:
            return f"{java_package}.{cls}", "EXACT"
        # Default package: the simple name is the class's own FQN, but only the
        # defining file can prove it. Fail closed here; the cross-file pass links
        # default-package cases only when the class is actually declared.
        return cls, "UNRESOLVED"

    def _record_cross_file_call(
        call_node,
        target_cls: str,
        name: str,
        argument_count: int,
        method,
        cls: str,
        locals_map,
        obj,
        constructor: bool = False,
    ) -> None:
        """Record a deterministic cross-file call/constructor intent.

        Emitted by the same per-file extractor that proves local flow, so no
        second analyzer is introduced. The later repository pass matches these
        against exact callees in other files. Every ATTEMPT is recorded with a
        machine-visible ``receiverResolution`` (EXACT/AMBIGUOUS/UNRESOLVED/
        UNSUPPORTED) so an attempted-but-unresolved boundary stays distinct
        from "no flow exists"; positive flow edges are only ever asserted by
        the pass for an EXACT receiver with a unique callee."""
        if not target_cls:
            return
        fqn, state = _receiver_fqn(target_cls)
        t_simple = target_cls.split("<", 1)[0].strip()
        if "." in t_simple:
            import_ctx = "qualified"
        elif t_simple in java_imports:
            import_ctx = "explicit_import"
        elif java_has_wildcard_import:
            import_ctx = "wildcard"
        elif java_package:
            import_ctx = "same_package"
        else:
            import_ctx = "default_package"
        if obj is None or (obj is not None and name_of(obj) == "this"):
            conf = "PROVEN"
        elif obj is not None and (
            name_of(obj) in java_imports or (name_of(obj) or "")[0:1].isupper()
        ):
            conf = "PROVEN"  # explicit static class receiver (imported or same-package)
        else:
            conf = "MAY"
        reason = {
            "EXACT": "exact",
            "AMBIGUOUS": "wildcard_import_ambiguous" if java_has_wildcard_import else "receiver_ambiguous",
            "UNRESOLVED": "receiver_unresolved",
            "UNSUPPORTED": "receiver_unsupported_type",
        }.get(state, "unknown")
        rec = {
            "file": str_path,
            "package": java_package,
            "receiver": target_cls,
            "receiverFqn": fqn,
            "receiverResolution": state,
            "receiverConfidence": conf,
            "importContext": import_ctx,
            "reason": reason,
            "method": name,
            "constructor": constructor,
            "argCount": argument_count,
            "argValues": [],
            "returnSink": None,
            "returnSinkKind": None,
            "location": f"{loc(call_node)}:{span(call_node)}",
        }
        if state == "EXACT":
            # Argument value ids (with indices) are only needed when the pass may
            # assert PASSED_AS_ARGUMENT / TRANSFORMED_BY edges.
            for _i, _a in enumerate(args_of(call_node)):
                _v = expr_value(_a, method, cls, locals_map)
                if _v:
                    rec["argValues"].append({"index": _i, "value": _v})
        xf_by_call[call_node] = rec
        cross_file_calls.append(rec)

    def _capture_xf_sink(n, sink_id, sink_kind: str) -> None:
        """Attach a cross-file call's return sink (caller-side receiving value)."""
        if n is not None and n in xf_by_call:
            xf_by_call[n]["returnSink"] = sink_id
            xf_by_call[n]["returnSinkKind"] = sink_kind

    def named_child(n, *fields):
        for f in fields:
            c = n.child_by_field_name(f)
            if c is not None:
                return c
        return None

    def first_type_text(n) -> str:
        c = named_child(n, "type")
        return _read_text(c, source).split("<", 1)[0].strip() if c is not None else ""

    def collect_method_name_counts(n, qual: str | None = None) -> None:
        if n.type in {"class_declaration", "interface_declaration", "record_declaration", "enum_declaration", "annotation_type_declaration"}:
            name_node = named_child(n, "name")
            name = _read_text(name_node, source) if name_node is not None else ""
            child_qual = f"{qual}.{name}" if qual and name else (name or qual)
            for child in n.children:
                collect_method_name_counts(child, child_qual)
            return
        if n.type in {"method_declaration", "constructor_declaration"} and qual:
            name_node = named_child(n, "name")
            name = _read_text(name_node, source) if name_node is not None else qual.rsplit(".", 1)[-1]
            method_name_counts[(qual, name)] += 1
        for child in n.children:
            collect_method_name_counts(child, qual)

    def walk(n, qual: str | None = None):
        if n.type in {"class_declaration", "interface_declaration", "record_declaration", "enum_declaration", "annotation_type_declaration"}:
            name_node = named_child(n, "name")
            name = _read_text(name_node, source) if name_node is not None else ""
            if name:
                child_qual = f"{qual}.{name}" if qual else name
                cid = next((x.get("id") for x in nodes if x.get("label") == name and name.lower() in x.get("id", "")), _make_id(stem, name))
                classes[child_qual] = {"id": cid, "fields": {}, "methods": []}
                # Register the simple-name alias only when unique; a second class
                # with the same simple name makes the alias ambiguous and is dropped
                # so resolution fails closed instead of fabricating flow (P0-3).
                if name in simple_class and simple_class[name] != child_qual:
                    del simple_class[name]
                else:
                    simple_class[name] = child_qual
                for c in n.children:
                    walk(c, child_qual)
                return
        if n.type == "field_declaration" and qual and qual in classes:
            typ = first_type_text(n)
            for c in n.children:
                if c.type == "variable_declarator":
                    name_node = named_child(c, "name")
                    name = _read_text(name_node, source) if name_node is not None else ""
                    vid = add_value(f"{qual}.{name}", "field", name, name_node or c, typ)
                    classes[qual]["fields"][name] = {"id": vid, "type": typ}
                    if named_child(c, "value") is not None:
                        pending_field_initializers.append((c, qual, name))
        if n.type in {"method_declaration", "constructor_declaration"} and qual and qual in classes:
            name_node = named_child(n, "name")
            name = _read_text(name_node, source) if name_node is not None else qual.rsplit(".", 1)[-1]
            parameter_specs = []
            params = named_child(n, "parameters")
            if params:
                for p in params.children:
                    if p.type in {"formal_parameter", "spread_parameter"}:
                        pn = named_child(p, "name")
                        parameter_specs.append({
                            "node": p,
                            "name_node": pn,
                            "name": _read_text(pn, source) if pn is not None else "",
                            "type": first_type_text(p),
                        })
            signature = f"{qual}.{name}({','.join(spec['type'] for spec in parameter_specs)})"
            label = f".{name}()"
            exact_loc = loc(n)
            line_loc = f"L{line(n)}"
            structural_matches = [
                x.get("id")
                for x in nodes
                if x.get("label") == label
                and qual.lower() in x.get("id", "")
                and (
                    str(x.get("source_location", "")).startswith(exact_loc)
                    or str(x.get("source_location", "")).startswith(line_loc)
                )
            ]
            overloaded = method_name_counts.get((qual, name), 0) > 1
            mid = structural_matches[0] if len(structural_matches) == 1 and not overloaded else _make_id(stem, signature)
            if mid:
                method_md = sanitize_metadata({
                    "language": "java",
                    "kind": "method",
                    "symbol": signature,
                    "structural_overload_identity": True,
                    "param_types": [spec["type"] for spec in parameter_specs],
                    "package": java_package,
                })
                existing = next((x for x in nodes if x.get("id") == mid), None)
                if existing is not None:
                    # The generic extractor already emitted this method node
                    # (sometimes with type=None). Enrich it in place with
                    # data-flow identity instead of appending a duplicate, which
                    # a later node-id dedup would drop and thereby strip the
                    # symbol/param metadata the cross-file pass needs. Idempotent
                    # even when the id is already registered in seen_nodes.
                    existing["type"] = "function"
                    existing["metadata"] = {**(existing.get("metadata") or {}), **method_md}
                    seen_nodes.add(mid)
                    valid_ids.add(mid)
                elif mid not in seen_nodes:
                    seen_nodes.add(mid)
                    valid_ids.add(mid)
                    nodes.append({
                        "id": mid,
                        "label": f".{name}({','.join(spec['type'] for spec in parameter_specs)})",
                        "file_type": "code",
                        "type": "function",
                        "source_file": str_path,
                        "source_location": exact_loc,
                        "confidence": "EXTRACTED",
                        "confidence_score": 0.8 if root.has_error else 1.0,
                        "metadata": method_md,
                    })
                info = {
                    "id": mid,
                    "symbol": signature,
                    "name": name,
                    "class": qual,
                    "params": [],
                    "locals": [{}],
                    "param_return_deps": set(),
                    "returns": None,
                    "node": n,
                }
                classes[qual]["methods"].append(info)
                methods_by_name[name].append(info)
                return_type = first_type_text(n)
                ret = None
                if n.type == "method_declaration" and return_type != "void":
                    ret = add_value(signature, "return", "return", n, return_type)
                info["returns"] = ret
                for index, spec in enumerate(parameter_specs):
                    pid = add_value(
                        signature,
                        "parameter",
                        spec["name"],
                        spec["name_node"] or spec["node"],
                        spec["type"],
                    )
                    if pid:
                        for pn in nodes:
                            if pn.get("id") == pid and isinstance(pn.get("metadata"), dict):
                                pn["metadata"]["param_index"] = index
                                break
                    info["params"].append({"name": spec["name"], "id": pid, "type": spec["type"]})
                body = named_child(n, "body")
                if body:
                    pending_bodies.append((body, info, qual))
                return
        for c in n.children:
            walk(c, qual)

    def name_of(n) -> str:
        return _read_text(n, source).strip()

    def lookup_local(locals_map, name: str) -> dict[str, Any] | None:
        scopes = locals_map if isinstance(locals_map, list) else [locals_map]
        for scope in reversed(scopes):
            if name in scope:
                return scope[name]
        return None

    def bind_local(locals_map, name: str, value: dict[str, Any]) -> None:
        scopes = locals_map if isinstance(locals_map, list) else [locals_map]
        scopes[-1][name] = value

    def receiver_type(n, method, cls: str, locals_map) -> str | None:
        if n is None:
            return None
        if n.type == "this":
            return cls
        if n.type == "identifier":
            receiver = name_of(n)
            if receiver == "this":
                return cls
            qual = _class_qual(receiver)
            if qual:
                return qual
            local = lookup_local(locals_map, receiver)
            if local:
                return _class_qual(local.get("type") or "") or local.get("type")
            for param in (method or {}).get("params", []):
                if param["name"] == receiver:
                    return _class_qual(param.get("type") or "") or param.get("type")
            return _class_qual(classes.get(cls, {}).get("fields", {}).get(receiver, {}).get("type") or "") or classes.get(cls, {}).get("fields", {}).get(receiver, {}).get("type")
        if n.type == "field_access":
            return (field_info(n, method, cls, locals_map) or {}).get("type")
        return None

    def field_info(n, method, cls: str, locals_map) -> dict[str, Any] | None:
        field = named_child(n, "field")
        field_name = name_of(field) if field is not None else ""
        obj = named_child(n, "object")
        target_cls = cls if obj is None else receiver_type(obj, method, cls, locals_map)
        return classes.get(target_cls or "", {}).get("fields", {}).get(field_name)

    def field_value(n, method, cls: str, locals_map) -> str | None:
        return (field_info(n, method, cls, locals_map) or {}).get("id")

    def receiver_identity(obj, method, cls: str, locals_map) -> tuple[str, str]:
        """Return ``(receiver_path, confidence)`` for a field-access object expression.

        ``confidence`` is ``"PROVEN"`` when the receiver is deterministically the
        same instance (`this`/unqualified or a deterministic `this.<chain>` path)
        and ``"MAY"`` when it is a named receiver of a declared class type whose
        instance identity cannot be proven (P0-1). Unknown receivers stay ``MAY``
        with a deterministic access-site path so downstream traversal can
        distinguish proven same-receiver flow from unknown/alias flow.
        """
        if obj is None:
            return cls, "PROVEN"
        on = name_of(obj)
        if obj.type == "this":
            return cls, "PROVEN"
        if obj.type == "identifier":
            if _class_qual(on):
                return on, "PROVEN"  # static class receiver
            typ = receiver_type(obj, method, cls, locals_map)
            if typ in classes:
                return f"{typ}@{on}", "MAY"
            return f"@{on}", "MAY"
        if obj.type == "field_access":
            inner_obj = named_child(obj, "object")
            fld = named_child(obj, "field")
            inner_path, inner_conf = receiver_identity(inner_obj, method, cls, locals_map)
            fname = name_of(fld) if fld is not None else "?"
            return f"{inner_path}.{fname}", inner_conf
        return name_of(obj) or "?", "MAY"

    def field_edge_md(field_expr, method, cls: str, locals_map) -> dict[str, Any]:
        """Receiver/access-site metadata for a field-access expression (P0-1)."""
        obj = named_child(field_expr, "object") if field_expr is not None and field_expr.type == "field_access" else None
        path, conf = receiver_identity(obj, method, cls, locals_map)
        return {"receiver": path, "receiverConfidence": conf}

    def expr_value(n, method, cls: str, locals_map) -> str | None:
        if n is None:
            return None
        if n.type == "identifier":
            nm = name_of(n)
            local = lookup_local(locals_map, nm)
            if local:
                return local["current"] if "current" in local else local["id"]
            if method:
                for p in method["params"]:
                    if p["name"] == nm:
                        return p["id"]
            return classes.get(cls, {}).get("fields", {}).get(nm, {}).get("id")
        if n.type == "field_access":
            return field_value(n, method, cls, locals_map)
        if n.type == "method_invocation":
            return_value = resolve_call(n, method, cls, locals_map)
            return return_value.get("returns") if return_value else None
        if n.type == "object_creation_expression":
            return object_value(n, method, cls, locals_map)
        if n.type == "parenthesized_expression":
            children = [child for child in n.children if child.is_named]
            if len(children) == 1:
                return expr_value(children[0], method, cls, locals_map)
        return None

    def object_value(n, method, cls, locals_map):
        typ = first_type_text(n)
        if not method or not typ:
            return None
        oid = add_value(method["symbol"], "local", f"new {typ}@{loc(n)}:{span(n)}", n, typ)
        target_qual = _class_qual(typ)
        constructors = [
            candidate
            for candidate in classes.get(target_qual or "", {}).get("methods", [])
            if candidate["name"] == (target_qual or typ).rsplit(".", 1)[-1] and len(candidate.get("params", [])) == len(args_of(n))
        ]
        if len(constructors) == 1:
            wire_args(n, constructors[0], method, cls, locals_map)
        elif typ and not target_qual and typ[0:1].isupper():
            # Cross-file constructor (`new Invoice(price, currency)`): record it
            # so the repository-wide pass can link arguments to the exact
            # constructor in another file (Gate 2B). Fail closed otherwise.
            _record_cross_file_call(n, typ, typ.rsplit(".", 1)[-1], len(args_of(n)), method, cls, locals_map, None, constructor=True)
        return oid

    def resolve_call(n, method, cls, locals_map):
        name_node = named_child(n, "name")
        name = name_of(name_node) if name_node is not None else ""
        obj = named_child(n, "object")
        target_cls = cls if obj is None else None
        if obj is not None:
            on = name_of(obj)
            if on == "this":
                target_cls = cls
            else:
                target_cls = _class_qual(on)
            local = lookup_local(locals_map, on)
            if local:
                target_cls = _resolve_receiver_type(local.get("type")) or target_cls
            for p in (method or {}).get("params", []):
                if p["name"] == on:
                    target_cls = _resolve_receiver_type(p.get("type")) or target_cls
            if on in classes.get(cls, {}).get("fields", {}):
                target_cls = _resolve_receiver_type(classes[cls]["fields"][on].get("type")) or target_cls
            if not target_cls and on and on[0:1].isupper():
                # Static class receiver whose type is declared in another file
                # (e.g. `PricingService.calculate(x)`). Falls through to a
                # cross-file record when not a same-file class.
                target_cls = on
        argument_count = len(args_of(n))
        candidates = [
            candidate
            for candidate in methods_by_name.get(name, [])
            if target_cls
            and candidate["class"] == target_cls
            and len(candidate.get("params", [])) == argument_count
        ]
        if len(candidates) == 1:
            target = candidates[0]
            wire_args(n, target, method, cls, locals_map)
            return target
        # No exact same-file candidate. If the receiver resolves to a
        # deterministic cross-file class type, record the call intent so the
        # repository-wide pass can link it to the exact callee in another file
        # (Gate 2B). Name-only / unresolved receivers record nothing.
        if target_cls and target_cls not in classes:
            _record_cross_file_call(n, target_cls, name, argument_count, method, cls, locals_map, obj)
        return None

    def args_of(n):
        a = named_child(n, "arguments")
        return [c for c in a.children if c.is_named] if a else []

    def wire_args(call, target, method, cls, locals_map):
        for argument_index, (arg, param) in enumerate(zip(args_of(call), target.get("params", []))):
            src = expr_value(arg, method, cls, locals_map)
            call_metadata = {"argumentIndex": argument_index, "callee": target["id"], "calleeSymbol": target["symbol"]}
            add_edge(src, param.get("id"), "PASSED_AS_ARGUMENT", arg, call_metadata)
            if is_field_value(src):
                add_edge(src, param.get("id"), "READ_FROM", arg, {**call_metadata, **field_edge_md(arg, method, cls, locals_map)})
            if target.get("returns") and argument_index in target.get("param_return_deps", set()):
                add_edge(
                    src,
                    target["returns"],
                    "TRANSFORMED_BY",
                    call,
                    {"transformationSymbol": target["symbol"], "argumentIndex": argument_index, "callee": target["id"]},
                )
            elif target.get("returns"):
                pending_transformations.append((src, target, argument_index, call))

    def mark_return_dep(method, source_id: str | None, seen: set[str] | None = None) -> None:
        if not method or not source_id:
            return
        seen = seen or set()
        if source_id in seen:
            return
        seen.add(source_id)
        deps = method.setdefault("param_return_deps", set())
        param_ids = {param.get("id"): idx for idx, param in enumerate(method.get("params", []))}
        if source_id in param_ids:
            deps.add(param_ids[source_id])
            return
        for edge in edges:
            if edge.get("target") == source_id and edge.get("relation") in {"FLOWS_TO", "READ_FROM"}:
                mark_return_dep(method, edge.get("source"), seen)

    def is_field_value(value_id: str | None) -> bool:
        return bool(value_id) and any(
            value_id == field.get("id")
            for klass in classes.values()
            for field in klass.get("fields", {}).values()
        )

    def scan_body(n, method, cls):
        locals_map = method["locals"]
        if n.type == "lambda_expression":
            return
        if n.type in {"block", "constructor_body"} and n is not method.get("body"):
            locals_map.append({})
            try:
                for c in n.children:
                    scan_body(c, method, cls)
            finally:
                locals_map.pop()
            return
        if n.type == "local_variable_declaration":
            typ = first_type_text(n)
            for c in n.children:
                if c.type == "variable_declarator":
                    nn = named_child(c, "name")
                    nm = name_of(nn) if nn else ""
                    vid = add_value(method["symbol"], "local", nm, nn or c, typ)
                    source_expr = named_child(c, "value")
                    source_id = expr_value(source_expr, method, cls, locals_map)
                    _capture_xf_sink(source_expr, vid, "local")
                    bind_local(locals_map, nm, {"id": vid, "type": typ})
                    if is_field_value(source_id):
                        add_edge(source_id, vid, "READ_FROM", c, field_edge_md(source_expr, method, cls, locals_map))
                    else:
                        add_edge(source_id, vid, "FLOWS_TO", c)
        elif n.type in {"assignment_expression", "assignment"}:
            left = named_child(n, "left")
            right = named_child(n, "right")
            local_target = lookup_local(locals_map, name_of(left)) if left is not None and left.type == "identifier" else None
            target = local_target["id"] if local_target else expr_value(left, method, cls, locals_map)
            source_id = expr_value(right, method, cls, locals_map)
            writes_field = is_field_value(target)
            _capture_xf_sink(right, target, "field" if writes_field else "local")
            if writes_field:
                add_edge(source_id, target, "WRITTEN_TO", n, field_edge_md(left, method, cls, locals_map))
            else:
                add_edge(source_id, target, "FLOWS_TO", n)
            if local_target and not writes_field:
                local_target["current"] = source_id
        elif n.type == "return_statement":
            val = next((c for c in n.children if c.is_named), None)
            source_id = expr_value(val, method, cls, locals_map)
            _capture_xf_sink(val, method.get("returns"), "return")
            if is_field_value(source_id):
                add_edge(source_id, method.get("returns"), "READ_FROM", n, field_edge_md(val, method, cls, locals_map))
            add_edge(source_id, method.get("returns"), "RETURNED_AS", n)
            mark_return_dep(method, source_id)
        elif n.type == "method_invocation":
            resolve_call(n, method, cls, locals_map)
        elif n.type == "object_creation_expression":
            object_value(n, method, cls, locals_map)
        elif n.type == "for_statement":
            locals_map.append({})
            try:
                for c in n.children:
                    scan_body(c, method, cls)
            finally:
                locals_map.pop()
            return
        for c in n.children:
            scan_body(c, method, cls)

    collect_method_name_counts(root)
    walk(root)
    for declarator, cls, field_name in pending_field_initializers:
        field = classes.get(cls, {}).get("fields", {}).get(field_name)
        src = expr_value(named_child(declarator, "value"), None, cls, [{}])
        add_edge(src, (field or {}).get("id"), "WRITTEN_TO", declarator, field_edge_md(named_child(declarator, "name"), None, cls, [{}]))
    for body, method, cls in pending_bodies:
        method["body"] = body
        scan_body(body, method, cls)
    for src, target, argument_index, call in pending_transformations:
        if target.get("returns") and argument_index in target.get("param_return_deps", set()):
            add_edge(
                src,
                target["returns"],
                "TRANSFORMED_BY",
                call,
                {"transformationSymbol": target["symbol"], "argumentIndex": argument_index, "callee": target["id"]},
            )
    # Expose each method's param->return dependency indices and param value-node
    # ids so the cross-file pass can prove TRANSFORMED_BY without re-analysis.
    # The method's RETURN_VALUE data-value node reliably survives extract()'s
    # later node dedup (function nodes do not), so the stamps live there and on
    # the method node for other consumers.
    for klass in classes.values():
        for method in klass.get("methods", []):
            deps = sorted(method.get("param_return_deps", set()))
            ret_id = method.get("returns")
            if ret_id:
                for node in nodes:
                    if node.get("id") == ret_id and isinstance(node.get("metadata"), dict):
                        node["metadata"]["param_return_deps"] = deps
                        node["metadata"]["package"] = java_package
                        node["metadata"]["param_value_ids"] = [p["id"] for p in method.get("params", [])]
                        break
            mid = method.get("id")
            if mid:
                for node in nodes:
                    if node.get("id") == mid and isinstance(node.get("metadata"), dict):
                        node["metadata"]["param_return_deps"] = deps
                        node["metadata"]["param_value_ids"] = [p["id"] for p in method.get("params", [])]
                        node["metadata"]["return_value_id"] = ret_id
                        break
    if cross_file_calls:
        result.setdefault("data_flow", {}).setdefault("java", {}).setdefault("cross_file_calls", []).extend(cross_file_calls)
    return result
