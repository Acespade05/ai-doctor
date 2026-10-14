"""Doctor configuration from environment (.env)."""
import json
import os

# Patients to poll. JSON list of {"name","url"}. url points at the agent's API.
_DEFAULT_PATIENTS = '[{"name": "shopsmart-prod", "url": "http://PATIENT_IP:8080"}]'
PATIENTS = json.loads(os.environ.get("PATIENTS", _DEFAULT_PATIENTS))

# Doctor probes the public site itself — the outage signal, no patient change.
PUBLIC_PROBE_URL = os.environ.get(
    "PUBLIC_PROBE_URL", "https://shopsmart-aisre.duckdns.org/health"
)

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "10"))
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "5"))
DB_PATH = os.environ.get("DB_PATH", "./data/doctor.db")

# --- rules-first thresholds (all overridable via env) ---
CPU_HIGH = float(os.environ.get("CPU_HIGH", "90"))
MEM_HIGH = float(os.environ.get("MEM_HIGH", "90"))
DISK_HIGH = float(os.environ.get("DISK_HIGH", "90"))
PROBE_LATENCY_HIGH_MS = float(os.environ.get("PROBE_LATENCY_HIGH_MS", "3000"))
AGENT_STALE_SECONDS = int(os.environ.get("AGENT_STALE_SECONDS", "60"))

# --- traffic / flood detection (from the agent's nginx-log telemetry) ---
# RPS above this, or a large fraction of requests being rate-limited (429) or
# erroring (5xx), signals a flood/attack. nginx already caps /api at 20r/s per
# IP, so a sustained 429 fraction is the clearest attack signature.
RPS_HIGH = float(os.environ.get("RPS_HIGH", "40"))
RATE_LIMITED_HIGH = float(os.environ.get("RATE_LIMITED_HIGH", "0.3"))
ERROR_RATE_HIGH = float(os.environ.get("ERROR_RATE_HIGH", "0.3"))
TRAFFIC_MIN_REQUESTS = int(os.environ.get("TRAFFIC_MIN_REQUESTS", "30"))

# --- remediation (hybrid autonomy) ---
# Shared secret the Doctor sends to the agent's /actions/execute. Must match
# the agent's AGENT_TOKEN.
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "change-me-shared-secret")
# true  -> auto-fix safe incidents, then verify / escalate
# false -> only record a pending action for a human to approve
AUTO_REMEDIATE = os.environ.get("AUTO_REMEDIATE", "true").lower() == "true"
REMEDIATION_MAX_ATTEMPTS = int(os.environ.get("REMEDIATION_MAX_ATTEMPTS", "2"))
REMEDIATION_COOLDOWN = int(os.environ.get("REMEDIATION_COOLDOWN", "30"))

# Baseline a human would take to recover the same incident manually (minutes).
# Used only for the Executive scorecard's "revenue protected" estimate, which is
# clearly labelled as an estimate in the UI.
MANUAL_MTTR_MINUTES = float(os.environ.get("MANUAL_MTTR_MINUTES", "25"))

# Which incident types are safe to auto-remediate, and the action for each.
# A restart clears a crashed/leaking process; Slowloris exhausts nginx workers,
# so a slow site restarts nginx. CPU/disk are left to ESCALATE on purpose: a
# restart rarely fixes a traffic-driven CPU spike or a full disk, and the honest
# response (rate-limit / scale / cleanup) is Phase 2 / a human decision.
_APP_TARGET = os.environ.get("REMEDIATION_TARGET", "shopsmart_app")
_NGINX_TARGET = os.environ.get("REMEDIATION_NGINX_TARGET", "shopsmart_nginx")
_DB_TARGET = os.environ.get("REMEDIATION_DB_TARGET", "shopsmart_postgres")
_REDIS_TARGET = os.environ.get("REMEDIATION_REDIS_TARGET", "shopsmart_redis")

# Which container maps to which incident type when the agent reports it down.
# This lets the Doctor raise a PRECISE incident (db_down) instead of a generic
# site_down, and target the right container for recovery.
CONTAINER_INCIDENT = {
    _APP_TARGET: "app_down",
    _NGINX_TARGET: "nginx_down",
    _DB_TARGET: "db_down",
    _REDIS_TARGET: "redis_down",
}

# Each plan is an ORDERED list of attempts. Precise incidents restart exactly the
# culprit; site_down (probe failed but we can't see containers) tries app then nginx.
REMEDIATION_PLAN = {
    "app_down": [{"action": "restart", "target": _APP_TARGET}],
    "nginx_down": [{"action": "restart", "target": _NGINX_TARGET}],
    "db_down": [{"action": "restart", "target": _DB_TARGET}],
    "redis_down": [{"action": "restart", "target": _REDIS_TARGET}],
    "site_down": [
        {"action": "restart", "target": _APP_TARGET},
        {"action": "restart", "target": _NGINX_TARGET},
    ],
    "high_memory": [{"action": "restart", "target": _APP_TARGET}],
    "site_slow": [{"action": "restart", "target": _NGINX_TARGET}],
    # Flood: block the attacking source at nginx. "__top_ip__" is resolved from
    # the incident's details at run time (the top source IP in the traffic sample).
    "traffic_spike": [{"action": "block_ip", "target": "__top_ip__"}],
}

# --- LLM / RAG diagnosis (Tier 2/3) ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_URL = os.environ.get("GROQ_URL", "https://api.groq.com/openai/v1/chat/completions")
CHROMA_PATH = os.environ.get("CHROMA_PATH", "./data/chroma")
RAG_ENABLED = os.environ.get("RAG_ENABLED", "true").lower() == "true"
LLM_ENABLED = bool(GROQ_API_KEY)
