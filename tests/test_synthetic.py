"""
Property-based tests over a synthetic RFC corpus — Step 2 of the
data-realism upgrade.

The scenario tests in test_scenarios.py prove the agent handles seven
specific cases. These tests prove the agent's *contracts* (trace
structure, decision shape, boundary controls, audit-log integrity)
hold over a corpus of synthetic RFCs the agent has never seen.

Property tests catch invariant violations scenario tests cannot. They
are also where the synthetic generator earns its keep — a generator
that produces non-degenerate corpora is what makes these tests
meaningful.
"""
import json

import pytest

from agent import config
from agent.harness import classify
from tools.synth import generate_rfcs


VALID_CLASSIFICATIONS = {"standard", "normal", "emergency", "refused"}
VALID_TRACE_STEPS = [
    "00_intake",
    "01_resolve",
    "02_traverse",
    "03_evaluate",
    "04_recall",
    "05_act",
]
TERMINAL_TRACE_STEPS = {"00_intake", "05_act"}
_STEP_ORDER = {step: i for i, step in enumerate(VALID_TRACE_STEPS)}


@pytest.fixture(autouse=True)
def _isolate_audit_log(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AUDIT_LOG_PATH", str(tmp_path / "audit_log.jsonl"))


@pytest.fixture
def corpus():
    """50-RFC synthetic corpus with a stable seed so failures are reproducible."""
    return generate_rfcs(50, seed=2026)


@pytest.fixture
def classified_corpus(corpus):
    """(rfc, result) pairs so multiple property tests share the classification work."""
    return [(rfc, classify(rfc)) for rfc in corpus]


# ---------- generator-level properties ----------

def test_synthetic_corpus_is_seed_reproducible():
    """Same seed, same corpus. A test that drifts run-to-run is worthless."""
    a = generate_rfcs(20, seed=123)
    b = generate_rfcs(20, seed=123)
    assert a == b


def test_synthetic_corpus_different_seeds_diverge():
    """Sanity check that the seed actually influences the output."""
    a = generate_rfcs(20, seed=1)
    b = generate_rfcs(20, seed=2)
    assert a != b


def test_corpus_is_non_degenerate(classified_corpus):
    """At least two distinct outcomes appear; otherwise the generator is broken."""
    seen = {result["decision"]["classification"] for _, result in classified_corpus}
    assert len(seen) >= 2, f"Synthetic corpus is degenerate — only saw {seen}"


# ---------- decision-shape properties ----------

def test_decision_always_has_classification_route_reason(classified_corpus):
    """
    Every decision must carry the three fields a CAB reviewer needs: what
    (classification), where to send it (route), and why (reason). A blank
    field is a contract violation regardless of whether the classification
    itself is correct.
    """
    for _, result in classified_corpus:
        decision = result["decision"]
        assert decision["classification"] in VALID_CLASSIFICATIONS
        assert isinstance(decision.get("route"), str) and decision["route"]
        assert isinstance(decision.get("reason"), str) and decision["reason"]


# ---------- trace-shape properties ----------

def test_trace_entries_have_required_shape(classified_corpus):
    """Every trace entry is a (step, action, result) triple with a known step."""
    for rfc, result in classified_corpus:
        trace = result["trace"]
        assert len(trace) >= 1, f"RFC {rfc['id']} produced empty trace"
        for entry in trace:
            assert entry["step"] in _STEP_ORDER, f"Unexpected step {entry['step']!r}"
            assert isinstance(entry["action"], str) and entry["action"]
            assert "result" in entry


def test_trace_steps_are_monotonically_non_decreasing(classified_corpus):
    """
    Trace steps must walk the reasoning loop in order. A 04_recall
    entry appearing after a 05_act entry would mean the trace lies
    about what the agent did. Groundedness is non-negotiable.
    """
    for rfc, result in classified_corpus:
        steps = [e["step"] for e in result["trace"]]
        positions = [_STEP_ORDER[s] for s in steps]
        for a, b in zip(positions, positions[1:]):
            assert a <= b, f"RFC {rfc['id']} trace went backwards: {steps}"


def test_trace_ends_at_terminal_step(classified_corpus):
    """
    The final trace entry is always 00_intake (emergency short-circuit)
    or 05_act (every other path, including refusals). Anything else
    means the agent returned without committing to a decision.
    """
    for rfc, result in classified_corpus:
        trace = result["trace"]
        assert len(trace) >= 1
        assert trace[-1]["step"] in TERMINAL_TRACE_STEPS, (
            f"RFC {rfc['id']} ended at {trace[-1]['step']!r}, not a terminal step"
        )


# ---------- boundary-control properties ----------

def test_emergency_short_circuit_holds_on_corpus(classified_corpus):
    """
    Synthetic RFCs with change_type='emergency' must short-circuit cleanly:
    classification='emergency', route='ECAB_review', and the trace has
    exactly one entry at 00_intake. The agent never reasons over an
    emergency — that property must hold across a corpus, not just on
    the one hand-crafted RFC-9930.
    """
    emergencies = [
        (rfc, result)
        for rfc, result in classified_corpus
        if rfc.get("change_type") == "emergency"
    ]
    if not emergencies:
        pytest.skip("Corpus seed produced no emergencies — increase n or change seed")
    for rfc, result in emergencies:
        assert result["decision"]["classification"] == "emergency"
        assert result["decision"]["route"] == "ECAB_review"
        assert [e["step"] for e in result["trace"]] == ["00_intake"]


def test_refusals_end_at_05_act_with_action_refuse(classified_corpus):
    """
    Every refusal records action='refuse' as its final trace entry.
    The earlier-step entries explain why; the act step records that the
    agent declined to classify. A refusal that does not end this way is
    a refusal that does not match its own documented contract.
    """
    for rfc, result in classified_corpus:
        if result["decision"]["classification"] != "refused":
            continue
        last = result["trace"][-1]
        assert last["step"] == "05_act", f"RFC {rfc['id']} refusal ended at {last['step']!r}"
        assert last["action"] == "refuse"


def test_kill_switch_dominates_synthetic_corpus(corpus, monkeypatch):
    """
    With the kill-switch engaged, every RFC — emergency, routine,
    anything — must refuse. The kill-switch is the stop-the-world
    lever; nothing escapes it. Twenty samples is enough; the property
    is binary.
    """
    monkeypatch.setattr(config, "KILL_SWITCH", True)
    for rfc in corpus[:20]:
        result = classify(rfc)
        assert result["decision"]["classification"] == "refused"
        assert "kill-switch" in result["decision"]["reason"].lower()


# ---------- distributional / audit-log properties ----------

def test_refusal_rate_is_not_degenerate(classified_corpus):
    """
    A corpus where the agent refuses everything (or refuses nothing)
    is not useful. The bounds are deliberately wide — this guards
    against degeneracy, not against bad tuning. Bad tuning is the
    eval-runner's job in step 5.
    """
    refused = sum(
        1 for _, result in classified_corpus
        if result["decision"]["classification"] == "refused"
    )
    rate = refused / len(classified_corpus)
    assert 0.05 < rate < 0.95, f"Refusal rate {rate:.2f} is suspiciously extreme"


def test_audit_log_has_one_entry_per_classify(tmp_path, monkeypatch, corpus):
    """
    Drift between the number of classify() calls and audit-log entries
    means the system has lost decisions — a compliance bug. Override
    the autouse audit-log path to a file this test can read back.
    """
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(config, "AUDIT_LOG_PATH", str(audit_path))
    sample = corpus[:30]
    for rfc in sample:
        classify(rfc)
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == len(sample)
    entries = [json.loads(line) for line in lines]
    assert [e["rfc_id"] for e in entries] == [r["id"] for r in sample]
