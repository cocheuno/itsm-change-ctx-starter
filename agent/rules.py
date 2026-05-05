"""
Rules layer — policy-as-code.

This module answers: "given the facts, what rules apply, and with what result?"
Rules are layered:
  - TEMPLATE rules (defeasible) — can approve a standard change
  - OVERRIDE rules (non-negotiable) — can force a change to normal regardless

The DORA override is the canonical example: even a perfect template match
cannot auto-approve a change to a DORA-regulated service.

In production, these would be Rego policies evaluated by OPA.
Here they are Python functions — the structure is what matters.
"""
import json
from datetime import datetime, timezone
from agent import config
from agent.meaning import all_templates

# Re-export so existing callers can still read `rules.TEMPLATE_MATCH_THRESHOLD`.
TEMPLATE_MATCH_THRESHOLD = config.TEMPLATE_MATCH_THRESHOLD


def match_template(rfc: dict, service: dict) -> dict:
    """
    Score the RFC against every template and pick the best match.

    Returns the best template with a score in [0, 1] — the fraction of the
    template's match patterns that appear in the RFC title. A score below
    `TEMPLATE_MATCH_THRESHOLD` means no template applies — route to CAB.

    The scoring is deliberately simple keyword overlap. Production systems
    would use embeddings; the design point here is "score, threshold, refuse",
    not the score function itself.
    """
    title = rfc["title"].lower()
    templates = all_templates()

    best = {"template_id": None, "score": 0.0, "template": None}
    for tpl in templates:
        if service["tier"] not in tpl["allowed_service_tiers"]:
            continue
        hits = sum(1 for pat in tpl["match_patterns"] if pat in title)
        score = hits / len(tpl["match_patterns"])
        if score > best["score"]:
            best = {"template_id": tpl["id"], "score": score, "template": tpl}
    return best


def check_freeze_window(rfc: dict) -> dict:
    """
    Does the RFC's planned execution fall in an active freeze window?

    Real CAB freeze policy gates on when the change *runs*, not when the
    engineer typed it in. We consult `planned_start_at` if present, falling
    back to `submitted_at` for legacy RFCs that don't carry the field. The
    result records `checked_field` and `checked_at` so the trace explains
    exactly which timestamp the rule keyed on.
    """
    with open(config.DATA_DIR / "freeze_windows.json") as f:
        data = json.load(f)

    if rfc.get("planned_start_at"):
        timestamp_str = rfc["planned_start_at"]
        checked_field = "planned_start_at"
    else:
        timestamp_str = rfc["submitted_at"]
        checked_field = "submitted_at"

    when = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    for fw in data["freeze_windows"]:
        start = datetime.fromisoformat(fw["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(fw["end"].replace("Z", "+00:00"))
        if start <= when <= end:
            return {
                "in_freeze": True,
                "window": fw["name"],
                "reason": fw["reason"],
                "checked_field": checked_field,
                "checked_at": timestamp_str,
            }

    return {
        "in_freeze": False,
        "checked_field": checked_field,
        "checked_at": timestamp_str,
    }


def check_dora_override(service: dict) -> dict:
    """
    The DORA rule: if a change touches a DORA-regulated service, the classification
    is FORCED to normal regardless of template match.

    This is a non-negotiable override. Regulatory context overrides operational efficiency.
    Always.
    """
    if service.get("dora_regulated", False):
        return {
            "override": True,
            "rule": "DORA-CRITICAL-FUNCTION",
            "reason": f"Service {service['id']} ({service['name']}) is DORA-regulated. "
                      f"All changes to this service require CAB review.",
        }
    return {"override": False}


def check_downstream_blast(service: dict, downstream: list[dict]) -> dict:
    """
    The blast-radius rule: even if the directly affected service is safe to
    auto-approve, escalate when something downstream is DORA-regulated or
    classified as `critical`. The relationships layer already knows the graph;
    this rule encodes the policy of "treat blast radius as part of the change."
    """
    triggers = []
    for d in downstream:
        if d.get("dora_regulated"):
            triggers.append({"service_id": d["id"], "name": d["name"], "why": "downstream_dora"})
        elif d.get("tier") == "critical":
            triggers.append({"service_id": d["id"], "name": d["name"], "why": "downstream_critical"})
    if triggers:
        return {"escalate": True, "triggers": triggers}
    return {"escalate": False, "triggers": []}


def check_precedent(prior: dict) -> dict:
    """
    The precedent rule: a clean template plus a clean track record is much
    stronger evidence than a clean template alone. A clean template plus a
    history of incidents is a reason to escalate, not auto-approve.

    We require a minimum sample size before precedent can gate — one bad day
    out of one prior change is not a trend.
    """
    found = prior.get("found", 0)
    incidents = prior.get("incident", 0)
    if found < config.PRECEDENT_MIN_SAMPLE:
        return {"escalate": False, "reason": "insufficient_sample", "sample_size": found}
    rate = incidents / found
    if rate > config.PRECEDENT_INCIDENT_RATE_THRESHOLD:
        return {
            "escalate": True,
            "incident_rate": rate,
            "incidents": incidents,
            "sample_size": found,
        }
    return {"escalate": False, "incident_rate": rate, "sample_size": found}


def evaluate_all(rfc: dict, service: dict) -> dict:
    """Run every rule for this RFC/service pair and return a consolidated result."""
    return {
        "template_match": match_template(rfc, service),
        "freeze_window": check_freeze_window(rfc),
        "dora_override": check_dora_override(service),
    }
