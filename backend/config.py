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

# --- remediation (hybrid autonomy) ---
# Shared secret the Doctor sends to the agent's /actions/execute. Must match
# the agent's AGENT_TOKEN.
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "change-me-shared-secret")
# true  -> auto-fix safe incidents, then verify / escalate
# false -> only record a pending action for a human to approve
AUTO_REMEDIATE = os.environ.get("AUTO_REMEDIATE", "true").lower() == "true"
REMEDIATION_MAX_ATTEMPTS = int(os.environ.get("REMEDIATION_MAX_ATTEMPTS", "2"))
REMEDIATION_COOLDOWN = int(os.environ.get("REMEDIATION_COOLDOWN", "30"))

# Which incident types are safe to auto-remediate, and the action for each.
# site_down -> restart the app container. Everything else is left to escalate.
_TARGET = os.environ.get("REMEDIATION_TARGET", "shopsmart_app")
REMEDIATION_PLAN = {
    "site_down": {"action": "restart", "target": _TARGET},
}

# --- LLM / RAG diagnosis (Tier 2/3) ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_URL = os.environ.get("GROQ_URL", "https://api.groq.com/openai/v1/chat/completions")
CHROMA_PATH = os.environ.get("CHROMA_PATH", "./data/chroma")
RAG_ENABLED = os.environ.get("RAG_ENABLED", "true").lower() == "true"
LLM_ENABLED = bool(GROQ_API_KEY)
