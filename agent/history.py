"""
History layer — temporal recall.

This module answers: "has this kind of change happened before, and what happened?"
The agent uses this to attach precedent to its classification — a standard change
template match plus a clean history of 10 prior successes is much stronger evidence
than a template match alone.
"""
import json

from agent import config


def _load_events() -> list[dict]:
    with open(config.DATA_DIR / "event_log.json") as f:
        return json.load(f)["events"]


def similar_changes(service_id: str, template_id: str, k: int = 10) -> dict:
    """
    Return the k most recent RFCs for this (service, template) pair.

    Also summarize outcomes — how many succeeded, how many caused incidents.
    That summary is what actually feeds the agent's decision.
    """
    events = _load_events()
    matches = [
        e for e in events
        if e["type"] == "rfc_closed"
        and e.get("service_id") == service_id
        and e.get("template_id") == template_id
    ]
    matches.sort(key=lambda e: e["timestamp"], reverse=True)
    matches = matches[:k]

    success_count = sum(1 for e in matches if e["outcome"] == "success")
    incident_count = sum(1 for e in matches if e["outcome"] == "incident")
    linked_incidents = [e["linked_incident"] for e in matches if "linked_incident" in e]

    return {
        "found": len(matches),
        "success": success_count,
        "incident": incident_count,
        "linked_incidents": linked_incidents,
        "most_recent": matches[0] if matches else None,
    }
