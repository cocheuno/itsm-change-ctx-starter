# User Guide

A short walkthrough of how to run the Change Management Context Layer and how to read what it produces. For the design rationale and architecture, see `README.md`.

## 1. Set up

```bash
pip install -r requirements.txt
```

Tested on Python 3.13. No external services — all data lives in `data/*.json` and schemas in `schemas/*.json`.

**Windows note:** until the CLI encoding fix lands, prefix commands with `PYTHONIOENCODING=utf-8` so the unicode arrow in the trace doesn't crash on cp1252 terminals:

```bash
PYTHONIOENCODING=utf-8 python classify.py RFC-9812
```

## 2. Classify an RFC

```bash
python classify.py RFC-9812
```

Replace `RFC-9812` with any of the seven scenario IDs. The CLI prints two sections:

- **AGENT TRACE** — up to five steps, in order: `01_resolve`, `02_traverse`, `03_evaluate`, `04_recall`, `05_act`. Each entry shows which layer was called and what it returned. This is the audit log for the decision.
- **DECISION** — the final `classification`, `route`, and `reason`. Every word in the reason is grounded in something the trace shows above it.

If the agent short-circuits (kill-switch, emergency) or refuses (low-confidence CMDB edge, stale data, no affected CIs), the trace will be shorter — but a trace is *always* produced.

## 3. The seven scenarios

| RFC | What it tests | Expected outcome |
|---|---|---|
| `RFC-9812` | Clean cert rotation on a non-regulated service | `standard` / auto-approve |
| `RFC-9847` | Same change but on a DORA-regulated service | `normal` / CAB fast track |
| `RFC-9903` | CMDB edge confidence 0.72 (below threshold) | `refused` |
| `RFC-9920` | `planned_start_at` falls in the spring patch freeze | `normal` / CAB review |
| `RFC-9921` | Clean template, but 3-of-5 prior changes hit incidents | `normal` / CAB review |
| `RFC-9922` | Direct service is non-DORA, but DORA-regulated `payment-api` depends on it | `normal` / CAB review |
| `RFC-9923` | Submitted before freeze, planned during freeze | `normal` / CAB review |

`RFC-9930` also exists as an emergency declared by the submitter — the agent short-circuits straight to ECAB and never runs the reasoning loop.

## 4. Possible outcomes

| Classification | Route | What it means |
|---|---|---|
| `standard` | `auto_approve` | Template matched, no override fired — no human needed |
| `normal` | `CAB_review` or `CAB_fast_track` | Send to the change board with a pre-brief |
| `emergency` | `ECAB_review` | Human submitter declared `change_type: "emergency"` — the agent never declares this |
| `refused` | `CAB_review` | The agent does not have enough confident context to classify safely |

A refusal is **not** an approval. It means "send this to a human; I am not the right tool for this case."

## 5. Run all scenarios as tests

```bash
pytest -v
```

13 tests should pass: the 7 scenarios plus emergency short-circuit, kill-switch, schema validation, audit-log emission, trace presence, and the freeze-window fallback.

## 6. The audit log

Every decision — including refusals and emergencies — is appended to `data/audit_log.jsonl`, one JSON object per line. The file is gitignored. Tail it to watch the agent's behavior:

```bash
tail data/audit_log.jsonl
```

Each entry records `rfc_id`, `classification`, `route`, `reason`, and a UTC timestamp.

## 7. Tweak the policy

All thresholds live in `agent/config.py`:

| Knob | Default | What it gates |
|---|---|---|
| `MIN_EDGE_CONFIDENCE` | `0.80` | Minimum CMDB edge confidence to act on |
| `MAX_EDGE_AGE_DAYS` | `30` | Maximum staleness for a CMDB edge |
| `TEMPLATE_MATCH_THRESHOLD` | `0.20` | Minimum template score to auto-approve |
| `PRECEDENT_INCIDENT_RATE_THRESHOLD` | `0.20` | Prior-incident rate that escalates a clean template |
| `PRECEDENT_MIN_SAMPLE` | `3` | Minimum prior changes before precedent can gate |
| `KILL_SWITCH` | `False` | Set to `True` to refuse every classification |
| `AUDIT_LOG_PATH` | `"data/audit_log.jsonl"` | Set to `None` to disable audit emission |

Lower a threshold and re-run the scenarios to see classifications shift.

## 8. Add your own RFC

Edit `data/rfcs.json`:

```json
{
  "id": "RFC-9999",
  "title": "Your change",
  "submitter": "you",
  "submitted_at": "2026-05-04T12:00:00Z",
  "affected_cis": ["ci-dashboard-tls"],
  "change_type": null,
  "proposed_template_id": "TPL-CERT-ROTATE-STD"
}
```

Optional fields:

- `description` — free text. Documented as untrusted; the agent never reads it.
- `planned_start_at` — when the change is planned to execute. The freeze-window rule consults this when present, falling back to `submitted_at`.

Then run `python classify.py RFC-9999`.

If the RFC is malformed (missing required field, wrong type, invalid date), the agent fails loudly at intake — schema validation runs before any reasoning.

## 9. Generate a synthetic RFC corpus

For stress-testing the agent at scale, `tools/synth.py` produces schema-valid RFCs that reference the existing CMDB. Output is gitignored and lives at `data/synthetic/rfcs.json` by default.

```bash
python -m tools.synth --rfcs 100 --seed 42
```

Flags:

- `--rfcs N` — number of RFCs to generate (default 50)
- `--seed N` — random seed (default 0). Same seed produces the same corpus.
- `--out PATH` — output file (default `data/synthetic/rfcs.json`)
- `--cmdb PATH` — CMDB to draw CIs from (default `data/cmdb.json`)

Generated IDs start at `RFC-1000000` so they can never collide with the hand-crafted `RFC-99XX` range. Distributions (one vs. multi-CI, emergency rate, planned-start coverage, title patterns) are tuned for a realistic mix of outcomes — auto-approve, CAB review, refused, and the occasional emergency.

Classify a generated RFC the same way as a hand-crafted one:

```bash
python classify.py RFC-1000007
```

Note: `classify.py` reads from `data/rfcs.json`, not `data/synthetic/rfcs.json`. To run the agent over the synthetic corpus, either copy the entries you want into `data/rfcs.json`, or load and classify them programmatically (see `tests/test_synthetic.py` for an example).

### Full synthetic corpus (services + CMDB + RFCs)

For larger experiments, `--corpus` generates a complete drop-in replacement for `data/` — services and a CMDB with realistic freshness drift, plus matching RFCs.

```bash
python -m tools.synth --corpus --services 30 --rfcs 100 --seed 42
```

This writes to `data/synthetic/`: `services.json`, `cmdb.json`, `rfcs.json`, `event_log.json`, plus copies of the hand-crafted `templates.json` and `freeze_windows.json`. Edge confidence and `last_verified_at` follow realistic distributions (~70% fresh, ~25% borderline straddling the 30-day threshold, ~5% stale; ~80% high confidence, ~5% low). The agent's freshness and confidence rules fire organically across the corpus rather than only on the single hand-crafted RFC-9903.

To run the agent against the synthetic corpus, point `config.DATA_DIR` at it and call `relationships.invalidate_graph()` so the cached CMDB rebuilds — see `tests/test_synthetic_cmdb.py` for the pattern.

This is Step 1+4 of the data-realism upgrade roadmap from `AGENT_PRIMER.md`. The remaining step adds an eval-runner that turns the corpus into metrics.
