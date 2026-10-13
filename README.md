# AI-SRE — Doctor

Self-healing infrastructure monitor for ShopSmart (the "patient"). The Doctor
polls the patient's metrics agent, probes the public site, detects incidents,
and — under guardrails — remediates or escalates. Served with a live dashboard.

Runs on the account-2 (Doctor) VM. ShopSmart is a separate repo on account 1.

## Project structure
```
ai-doctor/
  backend/        FastAPI service: API routes (main.py), storage, config
  monitoring/     poll loop + site/agent probes (poller.py), detection rules
  remediation/    hybrid-autonomy engine: decide -> act -> verify -> escalate
  llm/            RAG + Groq LLM diagnosis (Tier 2/3) — to build
  frontend/       the operations dashboard (index.html)
  requirements.txt  Dockerfile  docker-compose.yml  .env  README.md
```
One service runs it all: the backend starts the monitoring loop in a thread and
serves both the API and the dashboard.

## Run
```bash
cp .env.example .env     # already filled with the DuckDNS values
docker compose up -d --build
```
Open **http://localhost:8000/** for the dashboard. API: `/health`, `/status`,
`/incidents`, `/actions`, `/baseline`, `/samples/recent`.

Run without Docker (dev):
```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000     # from the repo root
```

## Layers (maps to the three-layer design)
- **Watcher** → `monitoring/` (metrics in, rules now, Isolation Forest later)
- **Doctor** → `remediation/` + `llm/` (decide/act + RAG/LLM diagnosis)
- **Narrator** → `frontend/` (role-based dashboard)

## Status
- ✅ Monitoring: poll + public probe + rules-first detection + incident lifecycle
- ✅ Remediation: auto-restart (guard-railed) vs escalate — recommend-only when
  `AUTO_REMEDIATE=false`
- ✅ Dashboard: live operations view
- ⏳ `llm/`: RAG + Groq diagnosis (Tier 2/3)
- ⏳ Watcher anomaly scoring (Isolation Forest) once ~28-day baseline is ready
- ⏳ Semgrep security scan, evaluation benchmark
