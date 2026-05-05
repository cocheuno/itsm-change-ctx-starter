"""
Synthetic-CMDB tests — Step 4 of the data-realism upgrade.

Generates a full synthetic corpus (services + CMDB with stochastic
freshness drift + RFCs) and points the agent at it. Asserts the
freshness rule actually exercises across the corpus — not just on the
hand-crafted RFC-9903.

Without drift, the freshness rule was a single-scenario assertion.
With drift, "some refusals come from staleness, but not all" becomes a
property the corpus must satisfy. If the property breaks, the
generator's distribution has drifted (too fresh -> rule never fires;
too stale -> nothing classifies).
"""
import json

import pytest

from agent import config, relationships
from agent.harness import classify
from tools.synth import generate_synthetic_corpus


@pytest.fixture(autouse=True)
def _isolate_audit_log(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AUDIT_LOG_PATH", str(tmp_path / "audit_log.jsonl"))


@pytest.fixture
def synthetic_data_dir(tmp_path, monkeypatch):
    """
    Generate a fresh synthetic corpus into a tmp dir, point the agent at
    it, and rebuild the relationships graph against the synthetic CMDB.
    Teardown re-invalidates so subsequent tests see a clean cache.
    """
    out = tmp_path / "synth"
    generate_synthetic_corpus(out, seed=42, n_services=30, n_rfcs=80)
    monkeypatch.setattr(config, "DATA_DIR", out)
    relationships.invalidate_graph()
    yield out
    relationships.invalidate_graph()


def _classifications(rfcs):
    counts = {"standard": 0, "normal": 0, "refused": 0, "emergency": 0}
    refusal_reasons: list[str] = []
    for rfc in rfcs:
        result = classify(rfc)
        cls = result["decision"]["classification"]
        counts[cls] += 1
        if cls == "refused":
            refusal_reasons.append(result["decision"]["reason"].lower())
    return counts, refusal_reasons


def test_synthetic_corpus_classifies_against_synthetic_cmdb(synthetic_data_dir):
    """Every generated RFC must classify cleanly against the synthetic CMDB."""
    rfcs = json.load(open(synthetic_data_dir / "rfcs.json"))["rfcs"]
    for rfc in rfcs:
        result = classify(rfc)
        assert "trace" in result
        assert len(result["trace"]) >= 1
        assert result["decision"]["classification"] in {"standard", "normal", "emergency", "refused"}


def test_freshness_rule_exercises_across_synthetic_corpus(synthetic_data_dir):
    """
    The freshness rule must fire on *some* RFCs and not fire on others.
    A corpus where every edge is fresh tells us nothing about the rule;
    a corpus where every edge is stale tells us nothing about the
    happy path.
    """
    rfcs = json.load(open(synthetic_data_dir / "rfcs.json"))["rfcs"]
    counts, reasons = _classifications(rfcs)
    n = len(rfcs)

    stale_refusals = sum(1 for r in reasons if "stale" in r or "days old" in r)
    assert 0 < stale_refusals < n, (
        f"Stale refusals out of (0, {n}): got {stale_refusals}. "
        "Either drift distribution is too fresh (rule never fires) "
        "or too stale (nothing classifies)."
    )


def test_synthetic_corpus_produces_some_successful_classifications(synthetic_data_dir):
    """
    Beyond just refusing, the synthetic CMDB must let the happy path
    fire — proves the drift distribution leaves enough fresh edges for
    auto-approval and CAB-routing to occur.
    """
    rfcs = json.load(open(synthetic_data_dir / "rfcs.json"))["rfcs"]
    counts, _ = _classifications(rfcs)
    success = counts["standard"] + counts["normal"]
    assert success > 0, f"No successful classifications across {len(rfcs)} RFCs"
