from __future__ import annotations

import hashlib
import os
from pathlib import Path

from graphify.extract import extract, extract_java, extract_js, extract_php, extract_python
from graphify.extractors.observability import CANONICALIZATION_VERSION


def _templates(result: dict) -> list[dict]:
    return [n for n in result["nodes"] if n.get("type") == "text_template"]


def _template_edges(result: dict, relation: str | None = None) -> list[dict]:
    return [
        e for e in result["edges"]
        if e["relation"] in {"contains_text_template", "defines_text"}
        and (relation is None or e["relation"] == relation)
    ]


def _fp(template: str) -> str:
    return hashlib.sha256(f"{CANONICALIZATION_VERSION}\n{template}".encode()).hexdigest()


def test_js_text_templates_cover_constants_interpolation_duplicates_and_filtering(tmp_path):
    p = tmp_path / "app.ts"
    p.write_text(
        "import x from 'pkg';\n"
        "const GREETING = 'Hello world';\n"
        "function render(user) {\n"
        "  const local = `Hello ${user.name}`;\n"
        "  const duplicate = 'Hello world';\n"
        "  console.info('log event');\n"
        "  const secret = 'sk_live_1234567890abcdef';\n"
        "  return local + duplicate;\n"
        "}\n",
        encoding="utf-8",
    )

    result = extract_js(p)
    templates = _templates(result)
    canonical = [t["canonical_template"] for t in templates]

    assert canonical.count("Hello world") == 2
    assert "Hello <arg>" in canonical
    assert "pkg" not in canonical
    assert "log event" not in canonical
    assert not any("sk_live" in t.get("canonical_template", "") for t in templates)

    greeting = next(t for t in templates if t["metadata"].get("bound_name") == "GREETING")
    assert greeting["template_kind"] == "CONSTANT"
    assert greeting["sha256"] == _fp("Hello world")
    assert greeting["canonicalization_version"] == CANONICALIZATION_VERSION
    assert greeting["source_location"] == "L2"
    assert greeting["metadata"]["language"] == "typescript"

    owners = {e["target"]: e["source"] for e in _template_edges(result)}
    file_node = next(n for n in result["nodes"] if n["label"] == "app.ts")
    render_node = next(n for n in result["nodes"] if n["label"] == "render()")
    assert owners[greeting["id"]] == file_node["id"]
    assert any(e["relation"] == "defines_text" and e["target"] == greeting["id"] for e in _template_edges(result))
    assert owners[next(t for t in templates if t["canonical_template"] == "Hello <arg>")["id"]] == render_node["id"]
    assert len({t["id"] for t in templates}) == len(templates)


def test_python_text_templates_cover_docstrings_module_scope_and_fstrings(tmp_path):
    p = tmp_path / "sample.py"
    p.write_text(
        '"""module docs excluded"""\n'
        "TITLE = 'Dashboard title'\n"
        "def view(user):\n"
        "    '''function docs excluded'''\n"
        "    prompt = f'Welcome {user.name}'\n"
        "    logging.info('log event')\n"
        "    return prompt\n",
        encoding="utf-8",
    )
    result = extract_python(p)
    canonical = [t["canonical_template"] for t in _templates(result)]
    assert canonical == ["Dashboard title", "Welcome <arg>"]
    assert all("docs excluded" not in c for c in canonical)
    assert all("log event" not in c for c in canonical)
    assert next(t for t in _templates(result) if t["canonical_template"] == "Dashboard title")["metadata"]["bound_name"] == "TITLE"


def test_java_text_templates_cover_fields_methods_and_log_dedup(tmp_path):
    p = tmp_path / "Demo.java"
    p.write_text(
        "class Demo {\n"
        "  static final String NAME = \"Display Name\";\n"
        "  void run(String id) {\n"
        "    String msg = \"User \" + id;\n"
        "    logger.info(\"Log {}\", id);\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    result = extract_java(p)
    canonical = [t["canonical_template"] for t in _templates(result)]
    assert "Display Name" in canonical
    assert "User <arg>" in canonical
    assert "Log <arg>" not in canonical
    assert next(t for t in _templates(result) if t["canonical_template"] == "Display Name")["metadata"]["bound_name"] == "NAME"


def test_php_text_templates_cover_constants_interpolation_and_filters(tmp_path):
    p = tmp_path / "demo.php"
    p.write_text(
        "<?php\n"
        "const TITLE = 'Page title';\n"
        "function render($name) {\n"
        "  $body = \"Hello $name\";\n"
        "  error_log('log event');\n"
        "  $punct = ':';\n"
        "  return $body;\n"
        "}\n",
        encoding="utf-8",
    )
    result = extract_php(p)
    canonical = [t["canonical_template"] for t in _templates(result)]
    assert canonical == ["Page title", "Hello <arg>"]
    assert next(t for t in _templates(result) if t["canonical_template"] == "Page title")["metadata"]["bound_name"] == "TITLE"


def test_text_templates_skip_keys_annotations_types_and_log_descendants(tmp_path):
    js = tmp_path / "edge.ts"
    js.write_text(
        "const obj = {\"keyName\": \"Value text\", propName: \"Another value\"};\n"
        "class C { @Dec(\"anno\") m(){} }\n"
        "type T = \"literal-type\";\n"
        "console.log(\"outer\", format(\"inner literal\"));\n",
        encoding="utf-8",
    )
    js_canonical = [t["canonical_template"] for t in _templates(extract_js(js))]
    assert "keyName" not in js_canonical
    assert "anno" not in js_canonical
    assert "literal-type" not in js_canonical
    assert "outer" not in js_canonical
    assert "inner literal" not in js_canonical
    assert "Value text" in js_canonical
    assert "Another value" in js_canonical

    py = tmp_path / "edge.py"
    py.write_text(
        "from typing import Literal, Annotated\n"
        "MODE: Literal[\"fast\"] = \"fast\"\n"
        "def f(x: Annotated[str, \"meta\"]):\n"
        "    d = {\"key\": \"value text\"}\n"
        "    logging.info(\"outer %s\", format(\"inner literal\"))\n",
        encoding="utf-8",
    )
    py_canonical = [t["canonical_template"] for t in _templates(extract_python(py))]
    assert py_canonical.count("fast") == 1
    assert "meta" not in py_canonical
    assert "key" not in py_canonical
    assert "outer <arg>" not in py_canonical
    assert "inner literal" not in py_canonical
    assert "value text" in py_canonical

    java = tmp_path / "Edge.java"
    java.write_text(
        "class Edge {\n"
        "  @Named(\"bean\") String f;\n"
        "  void m(String x) {\n"
        "    var map = Map.of(\"key\", \"value text\");\n"
        "    logger.info(\"outer {}\", format(\"inner literal\"));\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    java_canonical = [t["canonical_template"] for t in _templates(extract_java(java))]
    assert "bean" not in java_canonical
    assert "outer <arg>" not in java_canonical
    assert "inner literal" not in java_canonical
    assert "value text" in java_canonical

    php = tmp_path / "edge.php"
    php.write_text(
        "<?php\n"
        "#[Route(\"/path\")] function f($x) {\n"
        "  $arr = [\"key\" => \"value text\"];\n"
        "  error_log(sprintf(\"inner %s\", $x));\n"
        "}\n",
        encoding="utf-8",
    )
    php_canonical = [t["canonical_template"] for t in _templates(extract_php(php))]
    assert "/path" not in php_canonical
    assert "key" not in php_canonical
    assert "inner %s" not in php_canonical
    assert "value text" in php_canonical


def test_concatenated_templates_preserve_all_literal_fragments(tmp_path):
    js = tmp_path / "concat.ts"
    js.write_text("const msg = \"A \" + id + \" B\";\n", encoding="utf-8")
    assert [t["canonical_template"] for t in _templates(extract_js(js))] == ["A <arg> B"]

    py = tmp_path / "concat.py"
    py.write_text("msg = \"A \" + id + \" B\"\n", encoding="utf-8")
    assert [t["canonical_template"] for t in _templates(extract_python(py))] == ["A <arg> B"]

    java = tmp_path / "Concat.java"
    java.write_text(
        "class Concat { void m(String id) { String msg = \"A \" + id + \" B\"; } }\n",
        encoding="utf-8",
    )
    assert [t["canonical_template"] for t in _templates(extract_java(java))] == ["A <arg> B"]

    php = tmp_path / "concat.php"
    php.write_text("<?php $msg = \"A \" . $id . \" B\";\n", encoding="utf-8")
    php_templates = _templates(extract_php(php))
    assert [t["canonical_template"] for t in php_templates] == ["A <arg> B"]
    assert php_templates[0]["metadata"]["bound_name"] == "msg"


def test_full_extract_reconciles_template_owner_metadata_after_id_remap(tmp_path):
    source = tmp_path / "app.py"
    source.write_text(
        "def render(user):\n"
        "    message = f'Welcome {user}'\n",
        encoding="utf-8",
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = extract([Path("app.py")], cache_root=tmp_path)
    finally:
        os.chdir(old_cwd)

    template = _templates(result)[0]
    edge = next(
        e for e in result["edges"]
        if e["target"] == template["id"] and e["relation"] == "defines_text"
    )
    assert template["metadata"]["enclosing_symbol"] == edge["source"]
    owner = next(n for n in result["nodes"] if n["id"] == edge["source"])
    assert template["metadata"]["enclosing_symbol_label"] == owner["label"]
