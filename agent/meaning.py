"""
Meaning layer — canonical entity resolution.

This module answers: "what does this string actually refer to?"
It resolves ambiguous references (names, IDs) into canonical, typed entities.

In a production system this would sit behind a semantic layer or business
glossary service. Here it is backed by simple JSON files.
"""
import json
from typing import Optional

from agent import config, validation


def _load_services() -> list[dict]:
    with open(config.DATA_DIR / "services.json") as f:
        services = json.load(f)["services"]
    for svc in services:
        validation.validate_service(svc)
    return services


def _load_templates() -> list[dict]:
    with open(config.DATA_DIR / "templates.json") as f:
        templates = json.load(f)["templates"]
    for tpl in templates:
        validation.validate_template(tpl)
    return templates


def resolve_service(reference: str) -> Optional[dict]:
    """
    Resolve a service reference (by id or name) to the canonical Service entity.

    Returns None if the reference cannot be resolved — and that is a deliberate
    signal to the agent, NOT an error to retry.
    """
    services = _load_services()
    ref = reference.strip().lower()
    for svc in services:
        if svc["id"].lower() == ref or svc["name"].lower() == ref:
            return svc
    return None


def resolve_template(template_id: str) -> Optional[dict]:
    """Resolve a template id to its canonical definition."""
    templates = _load_templates()
    for tpl in templates:
        if tpl["id"] == template_id:
            return tpl
    return None


def all_templates() -> list[dict]:
    """Return all templates (used by rules to find best match)."""
    return _load_templates()
