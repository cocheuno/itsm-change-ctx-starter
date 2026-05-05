"""
Tests for the evaluation runner — Step 5 of the data-realism upgrade.

The eval runner is the seed of every later upgrade (regression diffs,
A/B testing, drift detection). Its contracts must hold even when its
own consumers don't exist yet:
  - metrics shape is stable
  - counts add up
  - classification of the hand-crafted seven-scenario corpus matches
    the documented outcomes (cheap regression check on both eval and
    the seven scenarios at once)
  - run_eval doesn't leak state — config.DATA_DIR is restored on exit
"""
from pathlib import Path

import pytest

from agent import config
from tools.eval import format_report, run_eval
from tools.synth import generate_synthetic_corpus

REAL_DATA_DIR = Path(__file__).parent.parent / "data"


@pytest.fixture
def synth_corpus(tmp_path):
    out = tmp_path / "synth"
    generate_synthetic_corpus(out, seed=42, n_services=20, n_rfcs=40)
    return out


def test_run_eval_produces_metrics_with_expected_shape(synth_corpus):
    metrics = run_eval(synth_corpus)
    assert metrics["n_rfcs"] == 40
    assert sum(metrics["classifications"].values()) == 40
    for key in ["standard", "normal", "emergency", "refused"]:
        assert key in metrics["classifications"]
    assert metrics["latency_ms"]["n"] == 40
    assert metrics["latency_ms"]["mean_ms"] >= 0


def test_run_eval_against_hand_crafted_corpus_matches_documented_outcomes():
    """
    The seven scenarios in data/rfcs.json plus RFC-9930 (emergency)
    should evaluate to exactly: 1 standard, 5 normal, 1 emergency,
    1 refused. This regression-checks both the eval runner and the
    scenario classifications in one pass.
    """
    metrics = run_eval(REAL_DATA_DIR)
    assert metrics["n_rfcs"] == 8
    assert metrics["classifications"]["standard"] == 1
    assert metrics["classifications"]["normal"] == 5
    assert metrics["classifications"]["emergency"] == 1
    assert metrics["classifications"]["refused"] == 1
    # Refusal is RFC-9903's confidence failure
    assert metrics["refusal_reasons"]["confidence"] == 1
    # DORA fires once (RFC-9847), blast fires once (RFC-9922),
    # freeze fires twice (RFC-9920 planned, RFC-9923 planned),
    # precedent fires once (RFC-9921), template_below_threshold zero
    assert metrics["rule_fires"]["dora_override"] == 1
    assert metrics["rule_fires"]["downstream_blast"] == 1
    assert metrics["rule_fires"]["freeze_window_planned"] == 2
    assert metrics["rule_fires"]["precedent_escalate"] == 1


def test_run_eval_does_not_persist_data_dir_change(synth_corpus):
    """
    The context manager inside run_eval must restore config.DATA_DIR
    on exit so subsequent agent calls see the original location.
    """
    original = config.DATA_DIR
    run_eval(synth_corpus)
    assert config.DATA_DIR == original


def test_run_eval_does_not_pollute_audit_log_by_default(synth_corpus, tmp_path):
    """
    By default, eval runs do not write to any audit log — pollution
    of the production audit log with eval-run noise would corrupt the
    very signal it exists to provide.
    """
    fake_audit = tmp_path / "should_not_be_written.jsonl"
    config.AUDIT_LOG_PATH = str(fake_audit)
    try:
        run_eval(synth_corpus)
    finally:
        config.AUDIT_LOG_PATH = None
    assert not fake_audit.exists()


def test_format_report_produces_markdown(synth_corpus):
    metrics = run_eval(synth_corpus)
    report = format_report(metrics)
    assert "# Agent Evaluation Report" in report
    assert "## Classification distribution" in report
    assert "## Refusal reasons" in report
    assert "## Rule firings" in report
    assert "## Decision latency" in report
