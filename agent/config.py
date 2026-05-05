"""
Central configuration for the change-management context layer.

All thresholds and policy knobs live here so the design is visible in one place
and there is a single source of truth for the harness, relationships, rules,
and history modules.

Production note: in a real system most of these would be owned by the CAB and
sourced from a policy service (e.g. OPA bundles), not hard-coded in Python.
"""
from pathlib import Path

# Where the layer modules read their data from. Tests redirect this to a
# synthetic corpus by monkeypatching `config.DATA_DIR`; relationships.py
# also exposes `invalidate_graph()` so the cached CMDB graph rebuilds
# against the new location.
DATA_DIR = Path(__file__).parent.parent / "data"

# Relationships layer.
MIN_EDGE_CONFIDENCE = 0.80
MAX_EDGE_AGE_DAYS = 30

# Rules layer.
TEMPLATE_MATCH_THRESHOLD = 0.20
PRECEDENT_INCIDENT_RATE_THRESHOLD = 0.20
PRECEDENT_MIN_SAMPLE = 3

# History layer.
HISTORY_LOOKBACK_K = 10

# Operational kill-switch. When True the agent refuses every classification
# and routes to CAB. Real systems back this with a centrally controlled
# feature flag so a single human can stop all auto-approvals instantly.
KILL_SWITCH = False

# Audit log. Every decision (including refusals) is appended to this JSONL
# file so the agent's behaviour is reviewable and so future history-layer
# queries can include the agent's own prior decisions. Set to None to
# disable emission.
AUDIT_LOG_PATH = "data/audit_log.jsonl"
