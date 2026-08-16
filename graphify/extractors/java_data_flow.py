"""Static Java local/basic interprocedural data-flow extraction."""
from __future__ import annotations

import importlib
from collections import defaultdict
from pathlib import Path
from typing import Any

from graphify.extractors.base import _make_id, _read_text
from graphify.security import sanitize_metadata

DATA_VALUE_TYPE = "data_value"


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
    sibling_same_name = False
    try:
        sibling_same_name = sum(1 for candidate in path.parent.parent.glob(f"*/{path.name}")) > 1
    except OSError:
        sibling_same_name = False
    stem = Path(path.parent.name, path.stem).as_posix() if sibling_same_name else path.stem
    str_path = str(path)

    append_status_node(status["java"], stem)
    seen_nodes.add(_make_id(stem, "data_flow", "java", status["java"].get("status", "unknown")))

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
        edge = {"source": src, "target": tgt, "relation": rel, "confidence": "EXTRACTED", "confidence_score": 0.8 if root.has_error else 1.0,
                "source_file": str_path, "source_location": loc(n), "weight": 1.0,
                "metadata": sanitize_metadata({"provenance": "STATIC_AST"} | (md or {}))}
        edges.append(edge)

    classes: dict[str, dict[str, Any]] = {}
    methods_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pending_bodies: list[tuple[object, dict[str, Any], str]] = []
    pending_transformations: list[tuple[str | None, dict[str, Any], int, object]] = []
    pending_field_initializers: list[tuple[object, str, str]] = []
    method_name_counts: dict[tuple[str, str], int] = defaultdict(int)

    def named_child(n, *fields):
        for f in fields:
            c = n.child_by_field_name(f)
            if c is not None:
                return c
        return None

    def first_type_text(n) -> str:
        c = named_child(n, "type")
        return _read_text(c, source).split("<", 1)[0].strip() if c is not None else ""

    def collect_method_name_counts(n, cls: str | None = None) -> None:
        if n.type in {"class_declaration", "interface_declaration", "record_declaration", "enum_declaration", "annotation_type_declaration"}:
            name_node = named_child(n, "name")
            name = _read_text(name_node, source) if name_node is not None else ""
            for child in n.children:
                collect_method_name_counts(child, name or cls)
            return
        if n.type in {"method_declaration", "constructor_declaration"} and cls:
            name_node = named_child(n, "name")
            name = _read_text(name_node, source) if name_node is not None else cls
            method_name_counts[(cls, name)] += 1
        for child in n.children:
            collect_method_name_counts(child, cls)

    def walk(n, cls: str | None = None):
        if n.type in {"class_declaration", "interface_declaration", "record_declaration", "enum_declaration", "annotation_type_declaration"}:
            name_node = named_child(n, "name")
            name = _read_text(name_node, source) if name_node is not None else ""
            if name:
                cid = next((x.get("id") for x in nodes if x.get("label") == name and name.lower() in x.get("id", "")), _make_id(stem, name))
                classes[name] = {"id": cid, "fields": {}, "methods": []}
                for c in n.children:
                    walk(c, name)
                return
        if n.type == "field_declaration" and cls and cls in classes:
            typ = first_type_text(n)
            for c in n.children:
                if c.type == "variable_declarator":
                    name_node = named_child(c, "name")
                    name = _read_text(name_node, source) if name_node is not None else ""
                    vid = add_value(f"{cls}.{name}", "field", name, name_node or c, typ)
                    classes[cls]["fields"][name] = {"id": vid, "type": typ}
                    if named_child(c, "value") is not None:
                        pending_field_initializers.append((c, cls, name))
        if n.type in {"method_declaration", "constructor_declaration"} and cls and cls in classes:
            name_node = named_child(n, "name")
            name = _read_text(name_node, source) if name_node is not None else cls
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
            signature = f"{cls}.{name}({','.join(spec['type'] for spec in parameter_specs)})"
            label = f".{name}()"
            exact_loc = loc(n)
            line_loc = f"L{line(n)}"
            structural_matches = [
                x.get("id")
                for x in nodes
                if x.get("label") == label
                and cls.lower() in x.get("id", "")
                and (
                    str(x.get("source_location", "")).startswith(exact_loc)
                    or str(x.get("source_location", "")).startswith(line_loc)
                )
            ]
            overloaded = method_name_counts.get((cls, name), 0) > 1
            mid = structural_matches[0] if len(structural_matches) == 1 and not overloaded else _make_id(stem, signature)
            if mid:
                if mid not in seen_nodes:
                    seen_nodes.add(mid)
                    nodes.append({
                        "id": mid,
                        "label": f".{name}({','.join(spec['type'] for spec in parameter_specs)})",
                        "file_type": "code",
                        "type": "function",
                        "source_file": str_path,
                        "source_location": exact_loc,
                        "confidence": "EXTRACTED",
                        "confidence_score": 0.8 if root.has_error else 1.0,
                        "metadata": sanitize_metadata({
                            "language": "java",
                            "kind": "method",
                            "symbol": signature,
                            "structural_overload_identity": True,
                        }),
                    })
                    valid_ids.add(mid)
                info = {
                    "id": mid,
                    "symbol": signature,
                    "name": name,
                    "class": cls,
                    "params": [],
                    "locals": [{}],
                    "param_return_deps": set(),
                    "returns": None,
                    "node": n,
                }
                classes[cls]["methods"].append(info)
                methods_by_name[name].append(info)
                return_type = first_type_text(n)
                ret = None
                if n.type == "method_declaration" and return_type != "void":
                    ret = add_value(signature, "return", "return", n, return_type)
                info["returns"] = ret
                for spec in parameter_specs:
                    pid = add_value(
                        signature,
                        "parameter",
                        spec["name"],
                        spec["name_node"] or spec["node"],
                        spec["type"],
                    )
                    info["params"].append({"name": spec["name"], "id": pid, "type": spec["type"]})
                body = named_child(n, "body")
                if body:
                    pending_bodies.append((body, info, cls))
                return
        for c in n.children:
            walk(c, cls)

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
            if receiver in classes:
                return receiver
            local = lookup_local(locals_map, receiver)
            if local:
                return local.get("type")
            for param in (method or {}).get("params", []):
                if param["name"] == receiver:
                    return param.get("type")
            return classes.get(cls, {}).get("fields", {}).get(receiver, {}).get("type")
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
        constructors = [
            candidate
            for candidate in classes.get(typ, {}).get("methods", [])
            if candidate["name"] == typ and len(candidate.get("params", [])) == len(args_of(n))
        ]
        if len(constructors) == 1:
            wire_args(n, constructors[0], method, cls, locals_map)
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
            elif on in classes:
                target_cls = on
            local = lookup_local(locals_map, on)
            if local and local.get("type") in classes:
                target_cls = local["type"]
            for p in (method or {}).get("params", []):
                if p["name"] == on and p.get("type") in classes:
                    target_cls = p["type"]
            if on in classes.get(cls, {}).get("fields", {}) and classes[cls]["fields"][on].get("type") in classes:
                target_cls = classes[cls]["fields"][on]["type"]
        argument_count = len(args_of(n))
        candidates = [
            candidate
            for candidate in methods_by_name.get(name, [])
            if target_cls
            and candidate["class"] == target_cls
            and len(candidate.get("params", [])) == argument_count
        ]
        if len(candidates) != 1:
            return None
        target = candidates[0]
        wire_args(n, target, method, cls, locals_map)
        return target

    def args_of(n):
        a = named_child(n, "arguments")
        return [c for c in a.children if c.is_named] if a else []

    def wire_args(call, target, method, cls, locals_map):
        for argument_index, (arg, param) in enumerate(zip(args_of(call), target.get("params", []))):
            src = expr_value(arg, method, cls, locals_map)
            call_metadata = {"argumentIndex": argument_index, "callee": target["id"], "calleeSymbol": target["symbol"]}
            add_edge(src, param.get("id"), "PASSED_AS_ARGUMENT", arg, call_metadata)
            if is_field_value(src):
                add_edge(src, param.get("id"), "READ_FROM", arg, call_metadata)
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
                    source_id = expr_value(named_child(c, "value"), method, cls, locals_map)
                    bind_local(locals_map, nm, {"id": vid, "type": typ})
                    add_edge(source_id, vid, "READ_FROM" if is_field_value(source_id) else "FLOWS_TO", c)
        elif n.type in {"assignment_expression", "assignment"}:
            left = named_child(n, "left")
            right = named_child(n, "right")
            local_target = lookup_local(locals_map, name_of(left)) if left is not None and left.type == "identifier" else None
            target = local_target["id"] if local_target else expr_value(left, method, cls, locals_map)
            source_id = expr_value(right, method, cls, locals_map)
            writes_field = is_field_value(target)
            add_edge(source_id, target, "WRITTEN_TO" if writes_field else "FLOWS_TO", n)
            if local_target and not writes_field:
                local_target["current"] = source_id
        elif n.type == "return_statement":
            val = next((c for c in n.children if c.is_named), None)
            source_id = expr_value(val, method, cls, locals_map)
            if is_field_value(source_id):
                add_edge(source_id, method.get("returns"), "READ_FROM", n)
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
        add_edge(src, (field or {}).get("id"), "WRITTEN_TO", declarator)
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
    return result
