# Change Management Context Layer

A working reference implementation of the agentic context layer from the ITSM Senior Track worked example. It classifies incoming Requests for Change (RFCs) as `standard` (auto-approve), `normal` (route to CAB), or `refused` (insufficient context) — and produces a full reasoning trace for every decision.

This is a **teaching artifact**, not production code. It uses lightweight libraries so it runs on any laptop without external services.

Two longer companion documents live alongside this one:

- `USAGE.md` — how to run the program, read the output, and use the synthetic-data and eval tools.
- `AGENT_PRIMER.md` — how the architecture works, written for a student who has heard "AI agent" but has not yet built one. Includes a worked example, a section on how the agent's natural-language output is generated, and a list of upgrades that would make the lab more realistic.

## What's inside

| Component | Lives in | What it answers |
|---|---|---|
| Meaning | `agent/meaning.py` + `schemas/` | What does this ID or name refer to? |
| Relationships | `agent/relationships.py` | How are these entities connected? |
| Rules | `agent/rules.py` | What policy applies to these facts? |
| History | `agent/history.py` | Has this kind of change happened before? |
| Harness | `agent/harness.py` | The five-step reasoning loop |
| Types | `agent/types.py` | Documented contracts each layer publishes |
| Validation | `agent/validation.py` | Enforce schemas at every entity boundary |
| Config | `agent/config.py` | All thresholds, policy knobs, and the `DATA_DIR` pointer in one place |

## Tools

| Tool | Lives in | What it does |
|---|---|---|
| Synthetic data generator | `tools/synth.py` | Generate schema-valid RFCs, services, CMDB with stochastic freshness/confidence drift, plus adversarial variants (malformed RFCs, unknown CIs, prompt-injection pairs). CLI has a `--corpus` mode for full data directories. |
| Evaluation runner | `tools/eval.py` | Classify a corpus and emit metrics — classification distribution, refusal reasons, per-rule firing counts, decision latency. CLI emits structured JSON and a markdown summary. |

## Quick start

```bash
pip install -r requirements.txt
python classify.py RFC-9812   # Auto-approve scenario
python classify.py RFC-9847   # DORA override scenario
python classify.py RFC-9903   # Low-confidence refusal scenario
python classify.py RFC-9920   # Freeze window scenario (planned execution in freeze)
python classify.py RFC-9921   # Precedent override scenario
python classify.py RFC-9922   # Downstream blast radius scenario
python classify.py RFC-9923   # Submission clean, planned execution in freeze
pytest -v                     # 36 tests: 7 scenarios + property tests + adversarial + eval

# Generate and evaluate a synthetic corpus
python -m tools.synth --corpus --services 30 --rfcs 100 --seed 42
python -m tools.eval  --corpus data/synthetic
```

## The seven scenarios

- **RFC-9812** — cert rotation on `internal-dashboard` (non-regulated) → `standard` / auto-approve
- **RFC-9847** — cert rotation on `payment-api` (DORA-regulated) → `normal` / CAB fast track. The DORA override fires even though the template matches.
- **RFC-9903** — cert rotation on `fraud-check` (stale CMDB edge, confidence 0.72) → `refused`. The agent refuses to act on unreliable dependency data.
- **RFC-9920** — cert rotation on `internal-dashboard` whose `planned_start_at` falls inside the spring patch freeze → `normal` / CAB review. Calendar policy beats template match.
- **RFC-9921** — cert rotation on `marketing-site` whose recent history is 3-of-5 incidents → `normal` / CAB review. The history layer earns its keep: a clean template plus a bumpy track record is a reason to escalate, not auto-approve.
- **RFC-9922** — cert rotation on `notification-service` (non-DORA, standard tier) on which DORA-regulated `payment-api` depends → `normal` / CAB review. Direct service is safe; blast radius is not.
- **RFC-9923** — cert rotation on `internal-dashboard` submitted *before* the spring patch freeze begins, but with `planned_start_at` inside it → `normal` / CAB review. Demonstrates that freeze policy must gate on planned execution time, not on submission time. Submission timing alone would let this auto-approve.

All design knobs (confidence thresholds, freshness window, precedent rate, template match floor, kill-switch, audit log path) live in `agent/config.py`.

## Boundary controls

- **Schema validation** — every RFC is validated against `schemas/change.json` at the entry to `classify()`; loaded services and templates are validated against their schemas. Malformed entities fail loudly before any reasoning runs.
- **Planned vs submitted time** — RFCs may carry an optional `planned_start_at`. The freeze-window rule consults it when present and falls back to `submitted_at` otherwise, so freeze policy gates on when the change runs, not on when the engineer typed it in. The trace records which timestamp the rule used.
- **Emergency short-circuit** — RFCs with `change_type: "emergency"` bypass the reasoning loop and route straight to the ECAB. Humans declare emergencies; the agent never does.
- **Kill-switch** — set `KILL_SWITCH = True` in `agent/config.py` and every classification refuses. A single flag stops the world.
- **Audit log** — every decision (including refusals and emergencies) is appended to `data/audit_log.jsonl`. The agent's own history becomes queryable context for the history layer.

## Where production would differ

- Replace JSON files with real systems: Neo4j for the graph, Open Policy Agent for rules, an event store for history.
- Replace keyword template matching with embeddings.
- Wire freshness thresholds to real data-pipeline SLAs.
- Wire `relationships.invalidate_graph()` to a CMDB-update event stream so the cached graph refreshes on change instead of staying static for the process lifetime.
- Move the kill-switch behind a centralised feature-flag service.
- Generate the prose pre-brief that goes to CAB with an LLM (the structured pre-brief dict already exists; the model only translates).

See `AGENT_PRIMER.md` Part II for the longer list of upgrades, grouped by what kind of realism each one buys.
