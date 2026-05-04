"""
Synthetic data generator — Step 1 of the data-realism upgrade roadmap.

Produces schema-valid RFCs at configurable scale, seeded for
reproducibility. Output is written to a side directory (default
data/synthetic/) so the seven hand-crafted scenarios in data/rfcs.json
stay intact as pedagogical anchors.

Generated RFCs reference real CIs from the existing CMDB, so the agent
can classify them end-to-end without any path-injection refactor.
Subsequent steps will extend this generator to produce a full
synthetic corpus (services, CMDB, templates, freeze windows, event log)
at scale.

CLI:
    python -m tools.synth --rfcs 50 --seed 42

The generator validates every RFC against schemas/change.json before
returning it. A schema-validation failure is a generator bug, not a
data problem — the test suite would catch it.
"""
import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent import validation

ROOT_DIR = Path(__file__).parent.parent
DEFAULT_CMDB_PATH = ROOT_DIR / "data" / "cmdb.json"
DEFAULT_OUT_PATH = ROOT_DIR / "data" / "synthetic" / "rfcs.json"

_SYNTH_SUBMITTERS = [
    "alice.synth", "bob.synth", "carol.synth", "dave.synth",
    "erin.synth", "frank.synth", "grace.synth", "heidi.synth",
    "ivan.synth", "judy.synth",
]

# Title patterns paired with the template id they're meant to plausibly
# match. A `None` template id flags a title that intentionally matches
# nothing — the agent should route those to CAB on the template-threshold
# rule, exercising that path in the corpus.
_TITLE_PATTERNS: list[tuple[str, str | None]] = [
    ("Certificate rotation for {ci}", "TPL-CERT-ROTATE-STD"),
    ("TLS cert renewal on {ci}", "TPL-CERT-ROTATE-STD"),
    ("SSL renewal — {ci}", "TPL-CERT-ROTATE-STD"),
    ("Log rotation config update for {ci}", "TPL-LOG-ROTATE"),
    ("Adjust log retention on {ci}", "TPL-LOG-ROTATE"),
    ("Minor upgrade for {ci} dependency", "TPL-MINOR-LIB-UPGRADE"),
    ("Patch upgrade — {ci}", "TPL-MINOR-LIB-UPGRADE"),
    ("Database schema migration on {ci}", None),
    ("Network reconfiguration for {ci}", None),
    ("Firewall rule update — {ci}", None),
]

_DESCRIPTIONS = [
    "Routine change as per the standard runbook.",
    "Engineer notes: scheduled outside business hours.",
    "Coordinated with downstream team in advance.",
    "Tested in staging earlier this week.",
]

# Synthetic IDs start far above the hand-crafted RFC-99XX range so there
# is no chance of accidental collision when both corpora coexist. The
# schema's id pattern is `^RFC-[0-9]+$` — digits only, no SYNTH suffix.
_SYNTH_ID_BASE = 1_000_000


def generate_rfcs(
    n: int,
    *,
    seed: int = 0,
    cmdb_path: Path = DEFAULT_CMDB_PATH,
    base_date: datetime | None = None,
) -> list[dict]:
    """
    Generate `n` schema-valid RFCs that reference real CIs from the given CMDB.

    The RNG is seeded so repeated calls with the same seed produce identical
    corpora — the same reproducibility discipline as the rest of the codebase.
    Every returned RFC has been validated against schemas/change.json; a
    validation failure raises jsonschema.ValidationError loudly here rather
    than later inside the agent.
    """
    rng = random.Random(seed)
    ci_ids = _load_ci_ids(cmdb_path)
    if not ci_ids:
        raise ValueError(f"CMDB at {cmdb_path} has no CIs")
    if base_date is None:
        base_date = datetime(2026, 5, 1, tzinfo=timezone.utc)

    rfcs = []
    for i in range(n):
        rfc = _generate_one_rfc(rng, ci_ids, base_date, seqnum=i)
        validation.validate_rfc(rfc)
        rfcs.append(rfc)
    return rfcs


def write_rfcs(rfcs: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"rfcs": rfcs}, f, indent=2)


def _load_ci_ids(cmdb_path: Path) -> list[str]:
    with open(cmdb_path) as f:
        cmdb = json.load(f)
    return [ci["id"] for ci in cmdb["cis"]]


def _generate_one_rfc(
    rng: random.Random,
    ci_ids: list[str],
    base_date: datetime,
    seqnum: int,
) -> dict:
    n_cis = rng.choices([1, 2, 3], weights=[80, 15, 5])[0]
    affected_cis = rng.sample(ci_ids, k=min(n_cis, len(ci_ids)))

    title_template, proposed_template_id = rng.choice(_TITLE_PATTERNS)
    ci_slug = affected_cis[0].removeprefix("ci-").removesuffix("-tls").replace("-", "_")
    title = title_template.format(ci=ci_slug)

    submitted_offset = timedelta(days=rng.uniform(-15, 15), hours=rng.uniform(0, 23))
    submitted_at = base_date + submitted_offset

    rfc: dict = {
        "id": f"RFC-{_SYNTH_ID_BASE + seqnum}",
        "title": title,
        "submitter": rng.choice(_SYNTH_SUBMITTERS),
        "submitted_at": _isoformat(submitted_at),
        "affected_cis": affected_cis,
        "change_type": _draw_change_type(rng),
    }

    if rng.random() < 0.60:
        planned_offset = timedelta(days=rng.uniform(0, 14), hours=rng.uniform(0, 23))
        rfc["planned_start_at"] = _isoformat(submitted_at + planned_offset)

    if proposed_template_id is not None:
        rfc["proposed_template_id"] = proposed_template_id

    if rng.random() < 0.30:
        rfc["description"] = rng.choice(_DESCRIPTIONS)

    return rfc


def _draw_change_type(rng: random.Random) -> str | None:
    return "emergency" if rng.random() < 0.05 else None


def _isoformat(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a synthetic RFC corpus that references the existing CMDB."
    )
    parser.add_argument("--rfcs", type=int, default=50, help="number of RFCs to generate")
    parser.add_argument("--seed", type=int, default=0, help="random seed")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_PATH,
        help="output path (default data/synthetic/rfcs.json)",
    )
    parser.add_argument(
        "--cmdb",
        type=Path,
        default=DEFAULT_CMDB_PATH,
        help="CMDB to draw CIs from (default data/cmdb.json)",
    )
    args = parser.parse_args(argv)

    rfcs = generate_rfcs(args.rfcs, seed=args.seed, cmdb_path=args.cmdb)
    write_rfcs(rfcs, args.out)
    print(f"Wrote {len(rfcs)} RFCs to {args.out} (seed {args.seed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
