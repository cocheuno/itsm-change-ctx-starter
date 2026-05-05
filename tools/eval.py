"""
Evaluation runner — Step 5 of the data-realism upgrade.

Runs the agent over a corpus directory (same shape as `data/`) and
emits metrics: classification distribution, refusal reasons, per-rule
firing counts, and decision latency. Output is structured JSON plus
a human-readable markdown summary.

This is the seed of every later upgrade — replay-based regression,
A/B testing of policy changes, drift detection between agent and
CAB. None are possible without first being able to ask "what does
the agent do over a representative corpus?"

CLI:
    python -m tools.eval --corpus data/synthetic
    python -m tools.eval --corpus data --out metrics.json --report report.md
"""
import argparse
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from agent import config, relationships
from agent.harness import classify

ROOT_DIR = Path(__file__).parent.parent


@contextmanager
def _agent_pointed_at(corpus_dir: Path, *, audit_log_path: Path | None = None):
    """
    Temporarily redirect the agent at `corpus_dir`. Restores config and
    invalidates the relationships graph cache on exit so subsequent
    callers see clean state.

    By default, eval runs do NOT write to the agent's audit log —
    pollution of `data/audit_log.jsonl` with eval-run noise would
    corrupt the very signal the audit log exists to provide. Pass
    `audit_log_path` if you want eval decisions captured somewhere.
    """
    orig_data = config.DATA_DIR
    orig_audit = config.AUDIT_LOG_PATH
    config.DATA_DIR = corpus_dir
    config.AUDIT_LOG_PATH = str(audit_log_path) if audit_log_path else None
    relationships.invalidate_graph()
    try:
        yield
    finally:
        config.DATA_DIR = orig_data
        config.AUDIT_LOG_PATH = orig_audit
        relationships.invalidate_graph()


def run_eval(
    corpus_dir: Path,
    *,
    audit_log_path: Path | None = None,
) -> dict[str, Any]:
    """
    Classify every RFC in `corpus_dir/rfcs.json` and return a metrics dict.

    `corpus_dir` must have the same shape as `data/` — RFCs, services,
    cmdb, templates, freeze_windows, event_log all present.
    """
    rfcs_path = corpus_dir / "rfcs.json"
    with open(rfcs_path) as f:
        rfcs = json.load(f)["rfcs"]

    classifications: dict[str, int] = {
        "standard": 0,
        "normal": 0,
        "emergency": 0,
        "refused": 0,
    }
    routes: dict[str, int] = {}
    refusal_reasons: dict[str, int] = {
        "kill-switch": 0,
        "not_found": 0,
        "confidence": 0,
        "stale": 0,
        "no_affected_cis": 0,
        "other": 0,
    }
    rule_fires: dict[str, int] = {
        "dora_override": 0,
        "downstream_blast": 0,
        "freeze_window_planned": 0,
        "freeze_window_submitted": 0,
        "precedent_escalate": 0,
        "template_below_threshold": 0,
    }
    latencies_ms: list[float] = []

    with _agent_pointed_at(corpus_dir, audit_log_path=audit_log_path):
        for rfc in rfcs:
            t0 = time.perf_counter()
            result = classify(rfc)
            latencies_ms.append((time.perf_counter() - t0) * 1000)

            decision = result["decision"]
            cls = decision["classification"]
            classifications[cls] += 1
            route = decision.get("route", "unknown")
            routes[route] = routes.get(route, 0) + 1

            if cls == "refused":
                _bucket_refusal(decision["reason"], refusal_reasons)

            for entry in result["trace"]:
                if entry["step"] == "03_evaluate":
                    _tally_rule_fires(entry["result"], rule_fires)

    n = len(rfcs)
    return {
        "n_rfcs": n,
        "classifications": classifications,
        "classifications_pct": (
            {k: round(v / n * 100, 1) for k, v in classifications.items()} if n else {}
        ),
        "routes": routes,
        "refusal_reasons": refusal_reasons,
        "rule_fires": rule_fires,
        "latency_ms": _summarize_latencies(latencies_ms),
        "corpus_dir": str(corpus_dir),
    }


def _bucket_refusal(reason: str, buckets: dict[str, int]) -> None:
    r = reason.lower()
    if "kill-switch" in r:
        buckets["kill-switch"] += 1
    elif "not found" in r:
        buckets["not_found"] += 1
    elif "confidence" in r:
        buckets["confidence"] += 1
    elif "stale" in r or "days old" in r:
        buckets["stale"] += 1
    elif "no affected cis" in r:
        buckets["no_affected_cis"] += 1
    else:
        buckets["other"] += 1


def _tally_rule_fires(eval_result: dict, fires: dict[str, int]) -> None:
    if eval_result.get("dora_override", {}).get("override"):
        fires["dora_override"] += 1
    if eval_result.get("downstream_blast", {}).get("escalate"):
        fires["downstream_blast"] += 1
    fw = eval_result.get("freeze_window", {})
    if fw.get("in_freeze"):
        if fw.get("checked_field") == "planned_start_at":
            fires["freeze_window_planned"] += 1
        else:
            fires["freeze_window_submitted"] += 1
    if eval_result.get("precedent_check", {}).get("escalate"):
        fires["precedent_escalate"] += 1
    tm = eval_result.get("template_match", {})
    if tm.get("score", 0) < config.TEMPLATE_MATCH_THRESHOLD:
        fires["template_below_threshold"] += 1


def _summarize_latencies(latencies_ms: list[float]) -> dict[str, float]:
    if not latencies_ms:
        return {}
    sorted_ms = sorted(latencies_ms)
    n = len(sorted_ms)

    def pct(p: int) -> float:
        return sorted_ms[min(int(n * p / 100), n - 1)]

    return {
        "n": n,
        "mean_ms": round(sum(sorted_ms) / n, 2),
        "p50_ms": round(pct(50), 2),
        "p95_ms": round(pct(95), 2),
        "p99_ms": round(pct(99), 2),
        "total_ms": round(sum(sorted_ms), 2),
    }


def format_report(metrics: dict) -> str:
    """Render metrics as a markdown summary."""
    lines = [
        "# Agent Evaluation Report",
        "",
        f"Corpus: `{metrics['corpus_dir']}`",
        f"RFCs evaluated: {metrics['n_rfcs']}",
        "",
        "## Classification distribution",
        "",
        "| Class | Count | % |",
        "|---|---:|---:|",
    ]
    for cls in ["standard", "normal", "emergency", "refused"]:
        count = metrics["classifications"].get(cls, 0)
        pct = metrics["classifications_pct"].get(cls, 0.0)
        lines.append(f"| {cls} | {count} | {pct}% |")

    lines += ["", "## Routes", "", "| Route | Count |", "|---|---:|"]
    for route, count in sorted(metrics["routes"].items()):
        lines.append(f"| {route} | {count} |")

    lines += ["", "## Refusal reasons", "", "| Reason | Count |", "|---|---:|"]
    for reason, count in metrics["refusal_reasons"].items():
        lines.append(f"| {reason} | {count} |")

    lines += ["", "## Rule firings", "", "| Rule | Count |", "|---|---:|"]
    for rule, count in metrics["rule_fires"].items():
        lines.append(f"| {rule} | {count} |")

    lat = metrics["latency_ms"]
    if lat:
        lines += [
            "",
            "## Decision latency",
            "",
            f"- mean: {lat['mean_ms']} ms",
            f"- p50:  {lat['p50_ms']} ms",
            f"- p95:  {lat['p95_ms']} ms",
            f"- p99:  {lat['p99_ms']} ms",
            f"- total: {lat['total_ms']} ms over {lat['n']} decisions",
        ]

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the agent over a corpus and emit metrics. The corpus "
            "directory must contain rfcs.json plus the agent's data files "
            "(cmdb, services, templates, freeze_windows, event_log). "
            "Generate one with `python -m tools.synth --corpus`."
        )
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=ROOT_DIR / "data" / "synthetic",
        help="corpus directory (default data/synthetic)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write metrics JSON to this path",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="write human-readable markdown report to this path",
    )
    args = parser.parse_args(argv)

    if not (args.corpus / "rfcs.json").exists():
        print(
            f"error: {args.corpus}/rfcs.json not found. "
            "Generate one with `python -m tools.synth --corpus`."
        )
        return 1

    metrics = run_eval(args.corpus)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2)
        print(f"Wrote metrics JSON to {args.out}")

    report = format_report(metrics)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"Wrote markdown report to {args.report}")

    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
