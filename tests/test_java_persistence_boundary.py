"""Gate 4A deterministic Java/JPA persistence-boundary evidence tests.

Each test maps to one or more mandatory adversarial cases from task 07. Fixtures
are intentionally small and source-derived; no framework classes need to exist
because supported Spring Data/JPA identities are framework contracts, not local
source implementations.
"""
from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

import pytest

from graphify.data_flow_query import DataFlowQuery, run_data_flow_query
from graphify.extract import extract


def _project(tmp_path: Path, files: dict[str, str], order: list[str] | None = None) -> dict:
    root = tmp_path / "project"
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    names = order or sorted(files)
    paths = [root / name for name in names]
    return extract(paths, root=root, cache_root=tmp_path / "cache")


def _base_files(contract: str = "JpaRepository", entity_package: str = "acme") -> dict[str, str]:
    contract_import = (
        "org.springframework.data.jpa.repository.JpaRepository"
        if contract == "JpaRepository"
        else "org.springframework.data.repository.CrudRepository"
    )
    entity_import = f"import {entity_package}.Order;" if entity_package != "acme" else ""
    return {
        f"src/{entity_package.replace('.', '/')}/Order.java": f"""
            package {entity_package};
            import jakarta.persistence.*;
            @Entity @Table(name="orders")
            class Order {{
              @Id @Column(name="id") Long id;
              @Column(name="amount") int amount;
            }}
        """,
        "src/acme/OrderRepository.java": f"""
            package acme;
            import {contract_import};
            import {entity_package}.Order;
            interface OrderRepository extends {contract}<Order, Long> {{}}
        """,
        "src/acme/Use.java": f"""
            package acme;
            {entity_import}
            class Use {{
              OrderRepository repo;
              Order saveOne(Order order) {{ return repo.save(order); }}
              Order readOne(Long id) {{ Order result = repo.findById(id); return result; }}
            }}
        """,
    }


def _boundaries(result: dict, operation: str | None = None) -> list[dict]:
    nodes = [node for node in result["nodes"] if node.get("type") == "persistence_boundary"]
    if operation is not None:
        nodes = [node for node in nodes if (node.get("metadata") or {}).get("operation") == operation]
    return nodes


def _persistence_edges(result: dict) -> list[dict]:
    return [edge for edge in result["edges"]
            if (edge.get("metadata") or {}).get("persistenceBoundary")]


def _persistence_diagnostics(result: dict) -> list[dict]:
    return [node for node in result["nodes"]
            if (node.get("metadata") or {}).get("kind") == "persistence_resolution"]


def _nodes_with(result: dict, key: str, value=True) -> list[dict]:
    return [node for node in result["nodes"] if (node.get("metadata") or {}).get(key) == value]


def _value(result: dict, *, kind: str, name: str, file_suffix: str = "Use.java") -> str:
    for node in result["nodes"]:
        metadata = node.get("metadata") or {}
        if (node.get("type") == "data_value"
                and str(node.get("source_file") or "").endswith(file_suffix)
                and metadata.get("kind") == kind
                and metadata.get("name") == name):
            return node["id"]
    raise AssertionError(f"missing value {file_suffix}/{kind}/{name}")


# 1. explicit @Entity + literal @Table + field @Column mapping
@pytest.mark.parametrize("namespace", ["jakarta.persistence", "javax.persistence"])
def test_4a_01_explicit_entity_table_column_mapping(tmp_path: Path, namespace: str):
    files = _base_files()
    files["src/acme/Order.java"] = f"""
        package acme;
        import {namespace}.*;
        @Entity @Table(name="orders") class Order {{
          @Id @Column(name="id") Long id;
          @Column(name="amount") int amount;
        }}
    """
    result = _project(tmp_path, files)
    entity = _nodes_with(result, "persistenceEntity")[0]
    assert entity["metadata"]["entityFqn"] == "acme.Order"
    assert entity["metadata"]["tableMapping"] == "EXPLICIT"
    assert entity["metadata"]["tableName"] == "orders"
    attributes = {n["metadata"]["attributeName"]: n["metadata"]
                  for n in _nodes_with(result, "persistenceAttribute")}
    assert attributes["id"]["persistenceId"] is True
    assert attributes["id"]["columnName"] == "id"
    assert attributes["amount"]["columnName"] == "amount"


# 2. no @Table => no invented physical table
# 3. no @Column => no invented physical column
def test_4a_02_03_logical_mapping_does_not_invent_physical_names(tmp_path: Path):
    files = _base_files()
    files["src/acme/Order.java"] = """
        package acme;
        import jakarta.persistence.*;
        @Entity class Order { @Id Long id; int amount; }
    """
    result = _project(tmp_path, files)
    entity = _nodes_with(result, "persistenceEntity")[0]["metadata"]
    assert entity["tableMapping"] == "UNSPECIFIED"
    assert "tableName" not in entity
    for attribute in _nodes_with(result, "persistenceAttribute"):
        metadata = attribute["metadata"]
        assert metadata["columnMapping"] == "UNSPECIFIED"
        assert "columnName" not in metadata
    write = _boundaries(result, "save")[0]["metadata"]
    assert write["mappingCompleteness"] == "LOGICAL_ONLY"
    assert "tableName" not in write


# 4. non-literal table/column => UNKNOWN/UNSUPPORTED, no fabricated literal
def test_4a_04_non_literal_mapping_fails_closed(tmp_path: Path):
    files = _base_files()
    files["src/acme/Order.java"] = """
        package acme;
        import jakarta.persistence.*;
        class Names { static final String TABLE="orders", COL="id"; }
        @Entity @Table(name=Names.TABLE) class Order {
          @Id @Column(name=Names.COL) Long id;
        }
    """
    result = _project(tmp_path, files)
    entity = _nodes_with(result, "persistenceEntity")[0]["metadata"]
    assert entity["tableMapping"] == "UNKNOWN"
    assert "tableName" not in entity
    field = _nodes_with(result, "persistenceAttribute")[0]["metadata"]
    assert field["columnMapping"] == "UNKNOWN"
    assert "columnName" not in field
    reasons = {d["metadata"]["reason"] for d in _persistence_diagnostics(result)}
    assert "non_literal_or_unresolved_table_name" in reasons
    assert "non_literal_or_unresolved_column_name" in reasons


# 5. exact JpaRepository resolution
def test_4a_05_exact_jpa_repository(tmp_path: Path):
    result = _project(tmp_path, _base_files("JpaRepository"))
    repository = _nodes_with(result, "persistenceRepository")[0]["metadata"]
    assert repository["repositoryContractFqn"] == "org.springframework.data.jpa.repository.JpaRepository"
    assert repository["entityFqn"] == "acme.Order"
    assert repository["entityResolution"] == "EXACT"


# 6. exact CrudRepository resolution
def test_4a_06_exact_crud_repository(tmp_path: Path):
    result = _project(tmp_path, _base_files("CrudRepository"))
    repository = _nodes_with(result, "persistenceRepository")[0]["metadata"]
    assert repository["repositoryContractFqn"] == "org.springframework.data.repository.CrudRepository"


# 7. same simple entity in two packages + explicit import => exact entity
def test_4a_07_same_simple_entity_explicit_import(tmp_path: Path):
    files = _base_files(entity_package="com.a")
    files["src/com/b/Order.java"] = """
        package com.b; import jakarta.persistence.*;
        @Entity class Order { @Id Long id; }
    """
    result = _project(tmp_path, files)
    repository = _nodes_with(result, "persistenceRepository")[0]["metadata"]
    assert repository["entityFqn"] == "com.a.Order"
    assert all((b["metadata"]["entityFqn"] == "com.a.Order") for b in _boundaries(result))


# 8. same simple entity unresolved/ambiguous => no positive boundary
def test_4a_08_ambiguous_entity_no_boundary(tmp_path: Path):
    files = {
        "src/com/a/Order.java": "package com.a; import jakarta.persistence.*; @Entity class Order { @Id Long id; }",
        "src/com/b/Order.java": "package com.b; import jakarta.persistence.*; @Entity class Order { @Id Long id; }",
        "src/repo/OrderRepository.java": """
            package repo; import org.springframework.data.jpa.repository.JpaRepository;
            interface OrderRepository extends JpaRepository<Order, Long> {}
        """,
        "src/repo/Use.java": "package repo; class Use { OrderRepository repo; void go(Order o){ repo.save(o); } }",
    }
    result = _project(tmp_path, files)
    assert _boundaries(result) == []
    diagnostics = _persistence_diagnostics(result)
    assert any(d["metadata"]["resolution"] == "AMBIGUOUS" for d in diagnostics)


# 9. repository-like interface not extending a supported contract
# 10. unrelated class with save()
def test_4a_09_10_names_alone_are_not_persistence(tmp_path: Path):
    files = {
        "src/acme/Order.java": "package acme; class Order {}",
        "src/acme/OrderRepository.java": "package acme; interface OrderRepository {}",
        "src/acme/Store.java": "package acme; class Store { Order save(Order o){ return o; } }",
        "src/acme/Use.java": "package acme; class Use { Store store; Order go(Order o){ return store.save(o); } }",
    }
    result = _project(tmp_path, files)
    assert _boundaries(result) == []
    assert _persistence_edges(result) == []
    assert _nodes_with(result, "persistenceRepository") == []


# 11. exact save(entity) => write boundary
def test_4a_11_exact_save_write_boundary(tmp_path: Path):
    result = _project(tmp_path, _base_files())
    boundary = _boundaries(result, "save")
    assert len(boundary) == 1
    assert boundary[0]["metadata"]["boundaryKind"] == "PERSISTENCE_WRITE"
    assert boundary[0]["metadata"]["provenance"] == "FRAMEWORK_CONTRACT"
    write_edges = [e for e in _persistence_edges(result)
                   if e["target"] == boundary[0]["id"]]
    assert len(write_edges) == 1
    assert write_edges[0]["source"] == _value(result, kind="PARAMETER", name="order")


# 12. exact findById(id) => read boundary -> result local
def test_4a_12_exact_find_by_id_read_boundary(tmp_path: Path):
    result = _project(tmp_path, _base_files())
    boundary = _boundaries(result, "findById")
    assert len(boundary) == 1
    assert boundary[0]["metadata"]["boundaryKind"] == "PERSISTENCE_READ"
    read_edges = [e for e in _persistence_edges(result) if e["source"] == boundary[0]["id"]]
    assert len(read_edges) == 1
    assert read_edges[0]["target"] == _value(result, kind="LOCAL", name="result")


# 13. ambiguous receiver save => fail closed + diagnostic + Gate 3 incomplete
def test_4a_13_ambiguous_repository_receiver_fails_closed(tmp_path: Path):
    files = {
        "src/a/Order.java": "package a; import jakarta.persistence.*; @Entity class Order { @Id Long id; }",
        "src/a/Repo.java": "package a; import org.springframework.data.repository.CrudRepository; interface Repo extends CrudRepository<Order,Long>{}",
        "src/b/Order.java": "package b; import jakarta.persistence.*; @Entity class Order { @Id Long id; }",
        "src/b/Repo.java": "package b; import org.springframework.data.repository.CrudRepository; interface Repo extends CrudRepository<Order,Long>{}",
        "src/use/Use.java": "package use; import a.*; import b.*; class Use { Repo repo; void go(a.Order o){ repo.save(o); } }",
    }
    result = _project(tmp_path, files)
    assert _boundaries(result) == []
    diagnostics = [d for d in _persistence_diagnostics(result)
                   if d["metadata"].get("reason") == "repository_receiver_unresolved"]
    assert diagnostics and diagnostics[0]["metadata"]["resolution"] == "AMBIGUOUS"
    start = _value(result, kind="PARAMETER", name="o")
    traversal = run_data_flow_query(result["nodes"], result["edges"], DataFlowQuery(start=start))
    assert traversal.complete_supported_search is False
    assert any(e["diagnostic_kind"] == "persistence_resolution" for e in traversal.boundary_events)


# 14. property access => diagnostic, no field mapping, boundary partial
# 15. mixed/conflicting access => diagnostic
def test_4a_14_property_access_is_partial_and_no_field_mapping(tmp_path: Path):
    files = _base_files()
    files["src/acme/Order.java"] = """
        package acme; import jakarta.persistence.*;
        @Entity @Access(AccessType.PROPERTY) class Order {
          Long id; @Id Long getId(){ return id; }
        }
    """
    result = _project(tmp_path, files)
    entity = _nodes_with(result, "persistenceEntity")[0]["metadata"]
    assert entity["accessStrategy"] == "PROPERTY"
    assert _nodes_with(result, "persistenceAttribute") == []
    assert any(d["metadata"]["reason"] == "property_access_unsupported"
               for d in _persistence_diagnostics(result))
    assert _boundaries(result, "save")[0]["metadata"]["analysisCompleteness"] == "PARTIAL"


def test_4a_15_mixed_access_diagnostic(tmp_path: Path):
    files = _base_files()
    files["src/acme/Order.java"] = """
        package acme; import jakarta.persistence.*;
        @Entity class Order { @Id @Access(AccessType.PROPERTY) Long id; }
    """
    result = _project(tmp_path, files)
    entity = _nodes_with(result, "persistenceEntity")[0]["metadata"]
    assert entity["accessStrategy"] == "UNSUPPORTED"
    assert _nodes_with(result, "persistenceAttribute") == []
    assert any(d["metadata"]["reason"] == "mixed_access_unsupported"
               for d in _persistence_diagnostics(result))


# 16. parse-incomplete source => PARTIAL, never complete
def test_4a_16_parse_incomplete_propagates_partial(tmp_path: Path):
    files = _base_files()
    files["src/acme/Order.java"] = """
        package acme; import jakarta.persistence.*;
        @Entity class Order { @Id Long id;
    """  # missing class brace: tree-sitter retains declarations with ERROR
    result = _project(tmp_path, files)
    boundary = _boundaries(result, "save")[0]
    assert boundary["metadata"]["analysisCompleteness"] == "PARTIAL"
    edge = next(e for e in _persistence_edges(result) if e["target"] == boundary["id"])
    assert edge["metadata"]["analysisCompleteness"] == "PARTIAL"
    start = _value(result, kind="PARAMETER", name="order")
    traversal = run_data_flow_query(result["nodes"], result["edges"], DataFlowQuery(start=start))
    assert traversal.search_coverage == "PARTIAL"
    assert traversal.complete_supported_search is False


# 17. checkout-root portability
def test_4a_17_checkout_root_portability(tmp_path: Path):
    source = tmp_path / "source"
    files = _base_files()
    for relative, text in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    results = []
    for root_name in ("rootA", "rootB"):
        root = tmp_path / root_name / "project"
        shutil.copytree(source, root)
        results.append(extract(sorted(root.rglob("*.java")), root=root,
                               cache_root=tmp_path / f"cache-{root_name}"))
    a, b = results
    normalize_boundaries = lambda r: sorted(
        (n["id"], n["source_file"], n["source_location"], json.dumps(n["metadata"], sort_keys=True))
        for n in _boundaries(r)
    )
    assert normalize_boundaries(a) == normalize_boundaries(b)
    assert sorted((e["source"], e["target"], e["source_location"], json.dumps(e["metadata"], sort_keys=True))
                  for e in _persistence_edges(a)) == sorted(
        (e["source"], e["target"], e["source_location"], json.dumps(e["metadata"], sort_keys=True))
        for e in _persistence_edges(b))
    path_identities = []
    for result in results:
        start = _value(result, kind="PARAMETER", name="order")
        boundary = _boundaries(result, "save")[0]["id"]
        traversal = run_data_flow_query(
            result["nodes"], result["edges"],
            DataFlowQuery(start=start, target=boundary, max_depth=4),
        )
        path_identities.append([path.path_identity for path in traversal.paths])
    assert path_identities[0] == path_identities[1]
    blob = json.dumps(normalize_boundaries(a))
    assert "rootA" not in blob and "rootB" not in blob


def test_4a_persistence_diagnostic_checkout_root_portability(tmp_path: Path):
    source_files = {
        "src/a/Order.java": "package a; import jakarta.persistence.*; @Entity class Order { @Id Long id; }",
        "src/a/Repo.java": "package a; import org.springframework.data.repository.CrudRepository; interface Repo extends CrudRepository<Order,Long>{}",
        "src/b/Order.java": "package b; import jakarta.persistence.*; @Entity class Order { @Id Long id; }",
        "src/b/Repo.java": "package b; import org.springframework.data.repository.CrudRepository; interface Repo extends CrudRepository<Order,Long>{}",
        "src/use/Use.java": "package use; import a.*; import b.*; class Use { Repo repo; void go(a.Order o){ repo.save(o); } }",
    }
    normalized = []
    for root_name in ("rootA", "rootB"):
        root = tmp_path / root_name / "project"
        for relative, text in source_files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        result = extract(sorted(root.rglob("*.java")), root=root,
                         cache_root=tmp_path / f"diag-cache-{root_name}")
        normalized.append(sorted(
            (node["id"], node["source_file"], node["source_location"],
             json.dumps(node["metadata"], sort_keys=True))
            for node in _persistence_diagnostics(result)
        ))
    assert normalized[0] == normalized[1]
    assert "rootA" not in json.dumps(normalized[0])
    assert "rootB" not in json.dumps(normalized[1])


# 18. shuffled file order deterministic output
def test_4a_18_shuffled_file_order_deterministic(tmp_path: Path):
    files = _base_files()
    names = sorted(files)
    base = _project(tmp_path / "base", files, names)
    shuffled_names = list(names)
    random.Random(42).shuffle(shuffled_names)
    shuffled = _project(tmp_path / "shuffle", files, shuffled_names)
    key = lambda r: sorted((n["id"], n["source_file"], n["source_location"], n["metadata"])
                           for n in _boundaries(r))
    assert key(base) == key(shuffled)


# 19. Gate 3 forward traversal to write boundary
# 20. Gate 3 backward traversal from write boundary
def test_4a_19_20_gate3_forward_backward_write_boundary(tmp_path: Path):
    result = _project(tmp_path, _base_files())
    start = _value(result, kind="PARAMETER", name="order")
    boundary = _boundaries(result, "save")[0]["id"]
    forward = run_data_flow_query(result["nodes"], result["edges"],
                                  DataFlowQuery(start=start, target=boundary, max_depth=4))
    backward = run_data_flow_query(result["nodes"], result["edges"],
                                   DataFlowQuery(start=boundary, direction="BACKWARD", max_depth=4))
    assert len(forward.paths) == 1 and forward.paths[0].steps[-1].target == boundary
    assert forward.complete_supported_search is True
    assert any(path.steps[0].source == start for path in backward.paths)


# 21. structural relation cannot be injected into value-flow traversal
def test_4a_21_structural_relation_not_injectable(tmp_path: Path):
    result = _project(tmp_path, _base_files())
    entity = _nodes_with(result, "persistenceEntity")[0]["id"]
    traversal = run_data_flow_query(
        result["nodes"], result["edges"],
        DataFlowQuery(start=entity, allowed_relations=frozenset({"contains", "references"})),
    )
    assert traversal.paths == ()
    assert set(traversal.rejected_relations) == {"contains", "references"}


# 22. duplicate call sites are evidence-distinct by source location
# 23. same call site repeated extraction is stable
def test_4a_22_duplicate_call_sites_evidence_distinct(tmp_path: Path):
    files = _base_files()
    files["src/acme/Use.java"] = """
        package acme; class Use { OrderRepository repo;
          void go(Order a, Order b) { repo.save(a); repo.save(b); }
        }
    """
    result = _project(tmp_path, files)
    boundaries = _boundaries(result, "save")
    assert len(boundaries) == 2
    assert len({b["id"] for b in boundaries}) == 2
    assert len({b["source_location"] for b in boundaries}) == 2
    assert len({e["source"] for e in _persistence_edges(result)}) == 2


def test_4a_23_repeated_extraction_stable(tmp_path: Path):
    files = _base_files()
    first = _project(tmp_path / "first", files)
    second = _project(tmp_path / "second", files)
    assert sorted((n["id"], n["source_file"], n["source_location"], n["metadata"])
                  for n in _boundaries(first)) == sorted(
        (n["id"], n["source_file"], n["source_location"], n["metadata"])
        for n in _boundaries(second))


# 24. unsupported relationship annotations do not create value flow
def test_4a_24_relationship_annotation_no_value_flow(tmp_path: Path):
    files = _base_files()
    files["src/acme/Order.java"] = """
        package acme; import jakarta.persistence.*; import java.util.List;
        @Entity class Order { @Id Long id; @OneToMany List<Order> children; }
    """
    result = _project(tmp_path, files)
    relation_edges = [e for e in result["edges"]
                      if (e.get("metadata") or {}).get("entityFqn") == "acme.Order"
                      and (e.get("metadata") or {}).get("operation") not in {"save", "findById"}]
    assert relation_edges == []
    assert all(e["relation"] not in {"CAN_FLOW_TO", "REACHES", "TRANSITIVE_FLOWS_TO"}
               for e in result["edges"])


# 25. save on unresolved/imported type does not become Spring Data by name
def test_4a_25_unresolved_imported_save_not_spring_data(tmp_path: Path):
    files = {
        "src/acme/Order.java": "package acme; class Order {}",
        "src/acme/Use.java": """
            package acme; import third.party.OrderRepository;
            class Use { OrderRepository repo; void go(Order o){ repo.save(o); } }
        """,
    }
    result = _project(tmp_path, files)
    assert _boundaries(result) == []
    assert _persistence_edges(result) == []


def test_4a_save_argument_must_match_repository_entity(tmp_path: Path):
    files = _base_files()
    files["src/acme/Other.java"] = "package acme; class Other {}"
    files["src/acme/Use.java"] = """
        package acme; class Use { OrderRepository repo;
          void go(Other other){ repo.save(other); }
        }
    """
    result = _project(tmp_path, files)
    assert _boundaries(result) == []
    assert _persistence_edges(result) == []
    assert any(d["metadata"]["reason"] == "save_argument_entity_type_mismatch"
               for d in _persistence_diagnostics(result))


# Additional explicit negative: custom @Entity is not JPA evidence.
def test_4a_custom_entity_annotation_not_jpa(tmp_path: Path):
    files = {
        "src/acme/Entity.java": "package acme; @interface Entity {}",
        "src/acme/Order.java": "package acme; @Entity class Order { Long id; }",
    }
    result = _project(tmp_path, files)
    assert _nodes_with(result, "persistenceEntity") == []
    assert _boundaries(result) == []
