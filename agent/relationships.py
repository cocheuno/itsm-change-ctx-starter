"""
Relationships layer — the knowledge graph.

This module answers: "how do these entities connect?"
Edges carry confidence scores; stale or low-confidence edges are a signal,
not a failure.

In production this would be Neo4j. Here it is NetworkX loaded from JSON.
"""
import json
from datetime import datetime, timezone
import networkx as nx

from agent import config


def _build_graph() -> nx.DiGraph:
    """Build the graph from the CMDB snapshot."""
    with open(config.DATA_DIR / "cmdb.json") as f:
        cmdb = json.load(f)

    G = nx.DiGraph()

    for ci in cmdb["cis"]:
        G.add_node(ci["id"], kind="ci", **ci)

    for edge in cmdb["ci_service_edges"]:
        G.add_edge(
            edge["ci_id"],
            edge["service_id"],
            kind="ci_to_service",
            confidence=edge["confidence"],
            last_verified=edge["last_verified"],
        )

    for dep in cmdb["service_dependencies"]:
        G.add_edge(
            dep["from"],
            dep["to"],
            kind="service_dependency",
            confidence=dep["confidence"],
            last_verified=dep.get("last_verified", "2026-04-01T00:00:00Z"),
        )

    return G


_GRAPH: nx.DiGraph | None = None


def graph() -> nx.DiGraph:
    """Lazy singleton."""
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = _build_graph()
    return _GRAPH


def invalidate_graph() -> None:
    """
    Drop the cached graph so the next graph() call rebuilds it.

    Production note: a real CMDB-backed system invalidates via event
    (a Kafka topic carrying CMDB updates) or TTL, not an explicit hook.
    This function is the seam where that wiring would attach. Tests
    use it after monkeypatching `config.DATA_DIR` to a synthetic
    corpus, so the next classification reads from the new location.
    """
    global _GRAPH
    _GRAPH = None


def affected_services(ci_ids: list[str], now: datetime | None = None) -> list[dict]:
    """
    Given a list of CI ids, return the services they affect.

    Each result includes a confidence score AND a freshness flag.
    The agent uses both to decide whether to trust the answer.

    `now` is the reference time for freshness. The harness passes the RFC's
    `submitted_at` so a classification on a given RFC is deterministic — running
    the same RFC tomorrow vs. today must produce the same verdict.
    """
    g = graph()
    results = []
    if now is None:
        now = datetime.now(timezone.utc)

    for ci_id in ci_ids:
        if ci_id not in g:
            results.append({"ci_id": ci_id, "service_id": None, "confidence": 0.0, "fresh": False, "reason": "unknown_ci"})
            continue

        for _, svc_id, edge_data in g.out_edges(ci_id, data=True):
            if edge_data["kind"] != "ci_to_service":
                continue

            last_verified = datetime.fromisoformat(edge_data["last_verified"].replace("Z", "+00:00"))
            age_days = (now - last_verified).days
            fresh = age_days <= config.MAX_EDGE_AGE_DAYS

            results.append({
                "ci_id": ci_id,
                "service_id": svc_id,
                "confidence": edge_data["confidence"],
                "fresh": fresh,
                "age_days": age_days,
            })

    return results


def downstream_services(service_id: str) -> list[dict]:
    """Services that depend on this service."""
    g = graph()
    results = []
    for src, _, edge_data in g.in_edges(service_id, data=True):
        if edge_data["kind"] == "service_dependency":
            results.append({"service_id": src, "confidence": edge_data["confidence"]})
    return results
