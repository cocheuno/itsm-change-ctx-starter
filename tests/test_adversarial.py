"""
Adversarial input tests — Step 3 of the data-realism upgrade.

The synthetic property tests in test_synthetic.py prove the agent's
contracts hold over realistic input. These tests prove they hold under
*hostile* input: malformed RFCs that must fail at the schema boundary,
schema-valid RFCs with unknown CIs that must refuse, and prompt-injection
strings stuffed into the `description` field that the agent must ignore.

Two acceptable outcomes for hostile input: schema-validation error at
intake, or `refused` classification. Hostile input must never produce a
confidently-wrong classification.
"""
import pytest
from jsonschema import ValidationError

from agent import config
from agent.harness import classify
from tools.synth import (
    generate_malformed_rfcs,
    generate_prompt_injection_pairs,
    generate_unknown_ci_rfcs,
)


@pytest.fixture(autouse=True)
def _isolate_audit_log(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AUDIT_LOG_PATH", str(tmp_path / "audit_log.jsonl"))


def test_malformed_rfcs_raise_validation_error():
    """
    Schema-violating RFCs fail loudly at the boundary. Every case in the
    generator must raise ValidationError, not produce a decision — a
    malformed RFC that classified successfully would mean schema
    enforcement has a hole.
    """
    cases = generate_malformed_rfcs()
    assert len(cases) > 0, "generator returned no cases"
    for rfc, kind in cases:
        with pytest.raises(ValidationError):
            classify(rfc)


def test_unknown_ci_rfcs_refuse_cleanly():
    """
    Schema-valid RFCs that reference CIs the CMDB doesn't know must land
    on the 'not found in CMDB' rung of the refusal ladder. The agent must
    never invent a service for an unknown CI.
    """
    rfcs = generate_unknown_ci_rfcs(20, seed=42)
    for rfc in rfcs:
        result = classify(rfc)
        assert result["decision"]["classification"] == "refused"
        assert "not found" in result["decision"]["reason"].lower()


def test_prompt_injection_in_description_does_not_change_classification():
    """
    The `description` field is documented as untrusted free text and is
    never read by the agent. This test makes the boundary empirical:
    classify(clean) must equal classify(hostile) bit-for-bit when the
    only difference is the description. If a hostile description can
    move the verdict, the documented boundary is a lie.
    """
    pairs = generate_prompt_injection_pairs(20, seed=11)
    for clean, hostile in pairs:
        clean_result = classify(clean)
        hostile_result = classify(hostile)
        assert clean_result == hostile_result, (
            f"RFC {clean['id']}: hostile description changed the decision\n"
            f"  clean:   {clean_result['decision']}\n"
            f"  hostile: {hostile_result['decision']}"
        )
