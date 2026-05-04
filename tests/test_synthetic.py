"""
Smoke test over a synthetic RFC corpus — Step 1 of the data-realism upgrade.

Asserts the agent can classify a generated corpus end-to-end without
crashing, and asserts the basic groundedness property — every decision
has a non-empty trace, every classification is one of the four declared
outcomes. Also asserts seed reproducibility: same seed, same corpus.

This test is fast (< 1s for the sizes used here). Future steps will
expand it into a property suite running over thousands of RFCs and a
larger CMDB.
"""
import pytest

from agent import config
from agent.harness import classify
from tools.synth import generate_rfcs


VALID_CLASSIFICATIONS = {"standard", "normal", "emergency", "refused"}


@pytest.fixture(autouse=True)
def _isolate_audit_log(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AUDIT_LOG_PATH", str(tmp_path / "audit_log.jsonl"))


def test_synthetic_corpus_classifies_without_crashes():
    """
    The corpus must classify cleanly: no exceptions, every decision in
    the four valid classifications, every result carrying a non-empty
    trace. The corpus must also be non-degenerate — at least two distinct
    outcomes across 50 RFCs, otherwise the generator is producing
    suspiciously narrow data.
    """
    rfcs = generate_rfcs(50, seed=42)
    seen = set()
    for rfc in rfcs:
        result = classify(rfc)
        cls = result["decision"]["classification"]
        assert cls in VALID_CLASSIFICATIONS
        assert "trace" in result
        assert len(result["trace"]) >= 1
        seen.add(cls)
    assert len(seen) >= 2, f"Synthetic corpus is degenerate — only saw {seen}"


def test_synthetic_corpus_is_seed_reproducible():
    """
    Same seed, same corpus — the same discipline as judging freshness
    against submitted_at instead of wall-clock time. A test that drifts
    run-to-run is worthless.
    """
    a = generate_rfcs(20, seed=123)
    b = generate_rfcs(20, seed=123)
    assert a == b


def test_synthetic_corpus_different_seeds_diverge():
    """Sanity check that the seed actually influences the output."""
    a = generate_rfcs(20, seed=1)
    b = generate_rfcs(20, seed=2)
    assert a != b
