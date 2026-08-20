"""Gate 4B deterministic EntityManager and delete-boundary falsification tests.

The numbered scenarios map to task 08 mandatory cases. Fixtures intentionally use
only source/framework-contract facts and never provide framework implementations.
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
    paths = [root / name for name in (order or sorted(files))]
    return extract(paths, root=root, cache_root=tmp_path / "cache")


def _entity(namespace: str = "jakarta.persistence") -> str:
    return f"""
        package acme;
        import {namespace}.Entity;
        import {namespace}.Id;
        @Entity class Order {{ @Id Long id; }}
    """


def _em_files(body: str, namespace: str = "jakarta.persistence") -> dict[str, str]:
    return {
        "src/acme/Order.java": _entity(namespace),
        "src/acme/Use.java": f"""
            package acme;
            import {namespace}.EntityManager;
            class Use {{ EntityManager em; {body} }}
        """,
    }


def _repo_files(body: str, contract: str = "CrudRepository") -> dict[str, str]:
    contract_import = (
        "org.springframework.data.repository.CrudRepository"
        if contract == "CrudRepository"
        else "org.springframework.data.jpa.repository.JpaRepository"
    )
    return {
        "src/acme/Order.java": _entity(),
        "src/acme/OrderRepository.java": f"""
            package acme; import {contract_import};
            interface OrderRepository extends {contract}<Order, Long> {{}}
        """,
        "src/acme/Use.java": f"""
            package acme; class Use {{ OrderRepository repo; {body} }}
        """,
    }


def _boundaries(result: dict, operation: str | None = None) -> list[dict]:
    nodes = [node for node in result["nodes"] if node.get("type") == "persistence_boundary"]
    if operation is not None:
        nodes = [node for node in nodes if (node.get("metadata") or {}).get("operation") == operation]
    return nodes


def _persistence_edges(result: dict, operation: str | None = None) -> list[dict]:
    edges = [edge for edge in result["edges"]
             if (edge.get("metadata") or {}).get("persistenceBoundary")]
    if operation is not None:
        edges = [edge for edge in edges if (edge.get("metadata") or {}).get("operation") == operation]
    return edges


def _persistence_diagnostics(result: dict) -> list[dict]:
    return [node for node in result["nodes"]
            if (node.get("metadata") or {}).get("kind") == "persistence_resolution"]


def _cross_file_diagnostics(result: dict) -> list[dict]:
    return [node for node in result["nodes"]
            if (node.get("metadata") or {}).get("kind") == "cross_file_resolution"]


def _value(result: dict, *, kind: str, name: str, file_suffix: str = "Use.java") -> str:
    for node in result["nodes"]:
        metadata = node.get("metadata") or {}
        if (node.get("type") == "data_value"
                and str(node.get("source_file") or "").endswith(file_suffix)
                and metadata.get("kind") == kind
                and metadata.get("name") == name):
            return node["id"]
    raise AssertionError(f"missing value {file_suffix}/{kind}/{name}")


def _normalized_boundaries(result: dict) -> list[tuple]:
    return sorted(
        (node["id"], node["source_file"], node["source_location"],
         json.dumps(node["metadata"], sort_keys=True))
        for node in _boundaries(result)
    )


# 1, 2, 5. Exact jakarta/javax EntityManager persist with exact entity.
@pytest.mark.parametrize("namespace", ["jakarta.persistence", "javax.persistence"])
def test_4b_01_02_05_exact_entity_manager_persist(tmp_path: Path, namespace: str):
    result = _project(tmp_path, _em_files(
        "void go(Order order) { em.persist(order); }", namespace
    ))
    boundary = _boundaries(result, "persist")[0]
    metadata = boundary["metadata"]
    assert metadata["framework"] == "JPA_ENTITY_MANAGER"
    assert metadata["frameworkContractFqn"] == f"{namespace}.EntityManager"
    assert metadata["entityFqn"] == "acme.Order"
    assert metadata["operationKind"] == "PERSIST"
    assert metadata["persistenceDirection"] == "WRITE"
    start = _value(result, kind="PARAMETER", name="order")
    assert any(edge["source"] == start and edge["target"] == boundary["id"]
               for edge in _persistence_edges(result, "persist"))


# 3. Same simple name outside JPA is not EntityManager evidence.
def test_4b_03_custom_same_name_entity_manager_fails_closed(tmp_path: Path):
    files = {
        "src/acme/Order.java": _entity(),
        "src/acme/EntityManager.java": "package acme; class EntityManager { void persist(Order o){} }",
        "src/acme/Use.java": "package acme; class Use { EntityManager em; void go(Order o){ em.persist(o); } }",
    }
    result = _project(tmp_path, files)
    assert _boundaries(result, "persist") == []
    assert any(node["metadata"]["reason"] == "entity_manager_receiver_not_jpa"
               for node in _persistence_diagnostics(result))


# 4. Wildcard receiver ambiguity is diagnostic and has no positive edge.
def test_4b_04_ambiguous_entity_manager_receiver(tmp_path: Path):
    files = {
        "src/acme/Order.java": _entity(),
        "src/acme/Use.java": """
            package acme; import jakarta.persistence.*;
            class Use { EntityManager em; void go(Order o){ em.persist(o); } }
        """,
    }
    result = _project(tmp_path, files)
    assert _boundaries(result, "persist") == []
    diag = next(node for node in _persistence_diagnostics(result)
                if node["metadata"]["reason"] == "entity_manager_receiver_unresolved")
    assert diag["metadata"]["resolution"] == "AMBIGUOUS"


# 6. A non-entity argument cannot become a persist boundary.
def test_4b_06_persist_non_entity_fails_closed(tmp_path: Path):
    result = _project(tmp_path, _em_files(
        "void go(String value) { em.persist(value); }"
    ))
    assert _boundaries(result, "persist") == []
    assert any(node["metadata"]["reason"] == "persist_argument_not_exact_jpa_entity"
               for node in _persistence_diagnostics(result))


# 7, 8. Merge has input WRITE evidence and a distinct captured output.
def test_4b_07_08_merge_input_and_captured_return(tmp_path: Path):
    result = _project(tmp_path, _em_files(
        "Order go(Order order) { Order managed = em.merge(order); return managed; }"
    ))
    boundary = _boundaries(result, "merge")[0]
    input_id = _value(result, kind="PARAMETER", name="order")
    output_id = _value(result, kind="LOCAL", name="managed")
    edges = _persistence_edges(result, "merge")
    assert any(edge["source"] == input_id and edge["target"] == boundary["id"] for edge in edges)
    assert any(edge["source"] == boundary["id"] and edge["target"] == output_id for edge in edges)
    assert input_id != output_id


# 9. Ignored merge return invents no output value.
def test_4b_09_ignored_merge_has_input_only(tmp_path: Path):
    result = _project(tmp_path, _em_files(
        "void go(Order order) { em.merge(order); }"
    ))
    boundary = _boundaries(result, "merge")[0]
    edges = _persistence_edges(result, "merge")
    assert len(edges) == 1
    assert edges[0]["target"] == boundary["id"]


# 10. Exact class literal find has key input and captured result flow.
def test_4b_10_exact_find_class_literal_and_result(tmp_path: Path):
    result = _project(tmp_path, _em_files(
        "Order go(Long id) { Order found = em.find(Order.class, id); return found; }"
    ))
    boundary = _boundaries(result, "find")[0]
    id_value = _value(result, kind="PARAMETER", name="id")
    found = _value(result, kind="LOCAL", name="found")
    edges = _persistence_edges(result, "find")
    assert boundary["metadata"]["persistenceDirection"] == "READ"
    assert any(edge["source"] == id_value and edge["target"] == boundary["id"] for edge in edges)
    assert any(edge["source"] == boundary["id"] and edge["target"] == found for edge in edges)


# 11. Runtime Class expression is not exact entity identity.
def test_4b_11_dynamic_find_class_fails_closed(tmp_path: Path):
    result = _project(tmp_path, _em_files(
        "Order go(Class<Order> clazz, Long id) { return em.find(clazz, id); }"
    ))
    assert _boundaries(result, "find") == []
    assert any(node["metadata"]["reason"] == "find_entity_class_dynamic"
               for node in _persistence_diagnostics(result))


# 12. Ambiguous class literal across wildcard imports is diagnostic.
def test_4b_12_ambiguous_find_entity_class_literal(tmp_path: Path):
    files = {
        "src/a/Order.java": "package a; import jakarta.persistence.*; @Entity class Order { @Id Long id; }",
        "src/b/Order.java": "package b; import jakarta.persistence.*; @Entity class Order { @Id Long id; }",
        "src/use/Use.java": """
            package use; import a.*; import b.*; import jakarta.persistence.EntityManager;
            class Use { EntityManager em; Object go(Long id){ return em.find(Order.class,id); } }
        """,
    }
    result = _project(tmp_path, files)
    assert _boundaries(result, "find") == []
    diag = next(node for node in _persistence_diagnostics(result)
                if node["metadata"]["reason"] == "find_entity_class_ambiguous")
    assert diag["metadata"]["resolution"] == "AMBIGUOUS"
    assert diag["metadata"]["candidateCount"] == 2


# 13, 14. Remove is DELETE only for an exact supported entity.
@pytest.mark.parametrize("argument_type,positive", [("Order", True), ("String", False)])
def test_4b_13_14_remove_entity_contract(tmp_path: Path, argument_type: str, positive: bool):
    result = _project(tmp_path, _em_files(
        f"void go({argument_type} value) {{ em.remove(value); }}"
    ))
    boundaries = _boundaries(result, "remove")
    assert bool(boundaries) is positive
    if positive:
        assert boundaries[0]["metadata"]["persistenceDirection"] == "DELETE"
        assert len(_persistence_edges(result, "remove")) == 1
    else:
        assert any(node["metadata"]["reason"] == "remove_argument_not_exact_jpa_entity"
                   for node in _persistence_diagnostics(result))


# 15. Exact Spring Data delete(entity) has direct DELETE input evidence.
def test_4b_15_exact_repository_delete(tmp_path: Path):
    result = _project(tmp_path, _repo_files(
        "void go(Order order) { repo.delete(order); }"
    ))
    boundary = _boundaries(result, "delete")[0]
    assert boundary["metadata"]["framework"] == "SPRING_DATA"
    assert boundary["metadata"]["persistenceDirection"] == "DELETE"
    assert len(_persistence_edges(result, "delete")) == 1


# 16. Repository delete argument must match the entity generic.
def test_4b_16_repository_delete_type_mismatch(tmp_path: Path):
    files = _repo_files("void go(Other other) { repo.delete(other); }")
    files["src/acme/Other.java"] = "package acme; class Other {}"
    result = _project(tmp_path, files)
    assert _boundaries(result, "delete") == []
    assert any(node["metadata"]["reason"] == "delete_argument_entity_type_mismatch"
               for node in _persistence_diagnostics(result))


# 17. Exact deleteById retains repository/entity identity and key evidence.
def test_4b_17_exact_delete_by_id_key_evidence(tmp_path: Path):
    result = _project(tmp_path, _repo_files(
        "void go(Long id) { repo.deleteById(id); }", "JpaRepository"
    ))
    boundary = _boundaries(result, "deleteById")[0]
    key = _value(result, kind="PARAMETER", name="id")
    assert boundary["metadata"]["entityFqn"] == "acme.Order"
    assert boundary["metadata"]["repositoryFqn"] == "acme.OrderRepository"
    assert boundary["metadata"]["repositoryIdTypeFqn"] == "java.lang.Long"
    assert any(edge["source"] == key and edge["target"] == boundary["id"]
               for edge in _persistence_edges(result, "deleteById"))


def test_4b_delete_by_id_argument_must_match_repository_id_generic(tmp_path: Path):
    result = _project(tmp_path, _repo_files(
        "void go(String id) { repo.deleteById(id); }"
    ))
    assert _boundaries(result, "deleteById") == []
    diagnostic = next(node for node in _persistence_diagnostics(result)
                      if node["metadata"]["reason"] == "delete_by_id_argument_type_mismatch")
    assert diagnostic["metadata"]["repositoryIdTypeFqn"] == "java.lang.Long"


# 18, 19. Method/repository names alone provide no persistence semantics.
@pytest.mark.parametrize("source", [
    "package acme; class Thing { void deleteById(Long id){} } class Use { Thing x; void go(Long id){ x.deleteById(id); } }",
    "package acme; interface OrderRepository { void delete(Order o); } class Use { OrderRepository repo; void go(Order o){ repo.delete(o); } }",
])
def test_4b_18_19_delete_name_lookalikes_are_negative(tmp_path: Path, source: str):
    result = _project(tmp_path, {"src/acme/Order.java": _entity(), "src/acme/Use.java": source})
    assert _boundaries(result, "delete") == []
    assert _boundaries(result, "deleteById") == []
    assert _persistence_edges(result) == []


# 20. Duplicate exact calls remain source-identity distinct.
def test_4b_20_duplicate_call_sites_distinct(tmp_path: Path):
    result = _project(tmp_path, _em_files(
        "void go(Order a, Order b) { em.persist(a); em.persist(b); }"
    ))
    boundaries = _boundaries(result, "persist")
    assert len(boundaries) == 2
    assert len({node["id"] for node in boundaries}) == 2
    assert len({node["source_location"] for node in boundaries}) == 2


# 21. Parse-incomplete entity evidence cannot become complete.
def test_4b_21_parse_incomplete_remains_partial(tmp_path: Path):
    files = _em_files("void go(Order order) { em.persist(order); }")
    files["src/acme/Order.java"] = """
        package acme; import jakarta.persistence.*;
        @Entity class Order { @Id Long id;
    """
    result = _project(tmp_path, files)
    boundary = _boundaries(result, "persist")[0]
    edge = _persistence_edges(result, "persist")[0]
    assert boundary["metadata"]["analysisCompleteness"] == "PARTIAL"
    assert edge["metadata"]["analysisCompleteness"] == "PARTIAL"
    traversal = run_data_flow_query(
        result["nodes"], result["edges"],
        DataFlowQuery(start=_value(result, kind="PARAMETER", name="order")),
    )
    assert traversal.complete_supported_search is False
    assert traversal.search_coverage == "PARTIAL"


# 22. Positive EntityManager boundary and Gate 3 evidence are root portable.
def test_4b_22_entity_manager_checkout_root_portability(tmp_path: Path):
    source = tmp_path / "source"
    files = _em_files("void go(Order order) { em.persist(order); }")
    for relative, text in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    results = []
    identities = []
    for root_name in ("rootA", "rootB"):
        root = tmp_path / root_name / "project"
        shutil.copytree(source, root)
        result = extract(sorted(root.rglob("*.java")), root=root,
                         cache_root=tmp_path / f"cache-{root_name}")
        results.append(result)
        start = _value(result, kind="PARAMETER", name="order")
        target = _boundaries(result, "persist")[0]["id"]
        traversal = run_data_flow_query(
            result["nodes"], result["edges"], DataFlowQuery(start=start, target=target)
        )
        identities.append([path.path_identity for path in traversal.paths])
    assert _normalized_boundaries(results[0]) == _normalized_boundaries(results[1])
    assert identities[0] == identities[1]
    blob = json.dumps(_normalized_boundaries(results[0]))
    assert "rootA" not in blob and "rootB" not in blob


# 23. Unresolved persistence diagnostic and boundary evidence key are root portable.
def test_4b_23_diagnostic_checkout_root_portability(tmp_path: Path):
    files = {
        "src/acme/Order.java": _entity(),
        "src/acme/Use.java": """
            package acme; import jakarta.persistence.*;
            class Use { EntityManager em; void go(Order o){ em.persist(o); } }
        """,
    }
    events = []
    for root_name in ("rootA", "rootB"):
        result = _project(tmp_path / root_name, files)
        traversal = run_data_flow_query(
            result["nodes"], result["edges"],
            DataFlowQuery(start=_value(result, kind="PARAMETER", name="o")),
        )
        assert traversal.complete_supported_search is False
        events.append(traversal.boundary_events)
    assert events[0] == events[1]
    blob = json.dumps(events[0])
    assert "rootA" not in blob and "rootB" not in blob


# 24. Repeated extraction is deterministic.
def test_4b_24_repeated_extraction_stable(tmp_path: Path):
    files = _em_files("Order go(Order o){ return em.merge(o); }")
    first = _project(tmp_path / "first", files)
    second = _project(tmp_path / "second", files)
    assert _normalized_boundaries(first) == _normalized_boundaries(second)
    assert sorted((e["source"], e["target"], e["source_location"], e["metadata"])
                  for e in _persistence_edges(first)) == sorted(
        (e["source"], e["target"], e["source_location"], e["metadata"])
        for e in _persistence_edges(second))


# 25. File order does not affect identity or metadata.
def test_4b_25_shuffled_file_order_deterministic(tmp_path: Path):
    files = _repo_files("void go(Order o, Long id){ repo.delete(o); repo.deleteById(id); }")
    names = sorted(files)
    shuffled = list(names)
    random.Random(42).shuffle(shuffled)
    assert _normalized_boundaries(_project(tmp_path / "a", files, names)) == _normalized_boundaries(
        _project(tmp_path / "b", files, shuffled)
    )


# 26, 27. Gate 3 traverses a direct persistence boundary forward and backward.
def test_4b_26_27_forward_backward_traversal(tmp_path: Path):
    result = _project(tmp_path, _em_files(
        "void go(Order order) { em.remove(order); }"
    ))
    start = _value(result, kind="PARAMETER", name="order")
    boundary = _boundaries(result, "remove")[0]["id"]
    forward = run_data_flow_query(
        result["nodes"], result["edges"], DataFlowQuery(start=start, target=boundary)
    )
    backward = run_data_flow_query(
        result["nodes"], result["edges"], DataFlowQuery(start=boundary, direction="BACKWARD")
    )
    assert len(forward.paths) == 1
    assert any(path.steps[0].source == start for path in backward.paths)


# 28. Structural relations still cannot be injected.
def test_4b_28_structural_relation_injection_rejected(tmp_path: Path):
    result = _project(tmp_path, _em_files("void go(Order o){ em.persist(o); }"))
    traversal = run_data_flow_query(
        result["nodes"], result["edges"],
        DataFlowQuery(start=_value(result, kind="PARAMETER", name="o"),
                      allowed_relations=frozenset({"contains", "references"})),
    )
    assert traversal.paths == ()
    assert set(traversal.rejected_relations) == {"contains", "references"}


# 29. A reached unresolved persistence boundary prevents false complete search.
def test_4b_29_reached_unresolved_boundary_is_incomplete(tmp_path: Path):
    files = {
        "src/acme/Order.java": _entity(),
        "src/acme/Use.java": """
            package acme; import jakarta.persistence.*;
            class Use { EntityManager em; void go(Order o){ em.persist(o); } }
        """,
    }
    result = _project(tmp_path, files)
    traversal = run_data_flow_query(
        result["nodes"], result["edges"],
        DataFlowQuery(start=_value(result, kind="PARAMETER", name="o")),
    )
    assert traversal.boundary_events
    assert traversal.complete_supported_search is False


# 30. An unresolved persistence diagnostic in an unreachable file is irrelevant.
def test_4b_30_unrelated_diagnostic_does_not_degrade_search(tmp_path: Path):
    files = _em_files("void go(Order order) { em.persist(order); }")
    files["src/other/Bad.java"] = """
        package other; import jakarta.persistence.*;
        class Bad { EntityManager em; void bad(String x){ em.persist(x); } }
    """
    result = _project(tmp_path, files)
    start = _value(result, kind="PARAMETER", name="order")
    boundary = _boundaries(result, "persist")[0]["id"]
    traversal = run_data_flow_query(
        result["nodes"], result["edges"], DataFlowQuery(start=start, target=boundary)
    )
    assert traversal.complete_supported_search is True
    assert traversal.boundary_events == ()


# 31. Exact call suppression is source-location scoped, not method-name global.
def test_4b_31_exact_call_suppresses_only_its_generic_diagnostic(tmp_path: Path):
    files = _em_files("""
        Other other;
        void go(Order order) { em.persist(order); other.persist(order); }
    """)
    result = _project(tmp_path, files)
    assert len(_boundaries(result, "persist")) == 1
    remaining = [node for node in _cross_file_diagnostics(result)
                 if node["metadata"].get("method") == "persist"]
    assert len(remaining) == 1
    assert remaining[0]["metadata"]["receiverFqn"] == "acme.Other"


def test_4b_custom_entity_annotation_is_not_jpa_argument_evidence(tmp_path: Path):
    files = {
        "src/acme/Order.java": "package acme; @interface Entity {} @Entity class Order {}",
        "src/acme/Use.java": """
            package acme; import jakarta.persistence.EntityManager;
            class Use { EntityManager em; void go(Order order){ em.persist(order); } }
        """,
    }
    result = _project(tmp_path, files)
    assert _boundaries(result, "persist") == []
    assert any(node["metadata"]["reason"] == "persist_argument_not_exact_jpa_entity"
               for node in _persistence_diagnostics(result))


def test_4b_same_file_entity_manager_shadows_conflicting_import(tmp_path: Path):
    files = {
        "src/acme/Order.java": _entity(),
        "src/acme/Use.java": """
            package acme; import jakarta.persistence.EntityManager;
            class EntityManager { void persist(Order order){} }
            class Use { EntityManager em; void go(Order order){ em.persist(order); } }
        """,
    }
    result = _project(tmp_path, files)
    assert _boundaries(result, "persist") == []
    assert any(node["metadata"]["reason"] == "entity_manager_receiver_not_jpa"
               for node in _persistence_diagnostics(result))


# 32. No transitive/persistence-verdict relation is persisted.
def test_4b_32_no_forbidden_persisted_relations_or_verdicts(tmp_path: Path):
    files = _em_files("""
        Order go(Order order, Long id) {
          em.persist(order); Order merged = em.merge(order);
          Order found = em.find(Order.class,id); em.remove(merged); return found;
        }
    """)
    files["src/acme/OrderRepository.java"] = """
        package acme; import org.springframework.data.repository.CrudRepository;
        interface OrderRepository extends CrudRepository<Order, Long> {}
    """
    files["src/acme/Cleaner.java"] = """
        package acme; class Cleaner { OrderRepository repo;
          void clean(Order order, Long id){ repo.delete(order); repo.deleteById(id); }
        }
    """
    result = _project(tmp_path, files)
    forbidden = {"CAN_FLOW_TO", "REACHES", "TRANSITIVE_FLOWS_TO", "VERIFIED"}
    assert all(edge.get("relation") not in forbidden for edge in result["edges"])
    blob = json.dumps(result)
    assert '"persistenceVerdict"' not in blob
