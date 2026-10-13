"""AI-SRE Doctor v0 — polling + rules-first incident detection.

Runs the poller in a background thread and exposes a read API for the (future)
Narrator dashboards. No remediation yet: every incident is detected and
recorded; hybrid autonomy (auto-fix vs escalate) is the next milestone.
"""
import json
import os
import threading
from datetime import datetime

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend import storage
from monitoring import poller
from remediation import engine as remediation
from backend.config import (PATIENTS, POLL_INTERVAL, AUTO_REMEDIATE, HTTP_TIMEOUT,
                            LLM_ENABLED, RAG_ENABLED, GROQ_MODEL)

_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")


def _patient_url(name):
    for p in PATIENTS:
        if p["name"] == name:
            return p["url"]
    return None

app = FastAPI(title="AI-SRE Doctor", version="0.1.0")


@app.on_event("startup")
def _startup():
    storage.init_db()
    t = threading.Thread(target=poller.loop, daemon=True)
    t.start()


@app.get("/")
def dashboard():
    index = os.path.join(_FRONTEND_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="dashboard not built")


@app.get("/health")
def health():
    return {"status": "ok", "patients": [p["name"] for p in PATIENTS],
            "poll_interval": POLL_INTERVAL, "auto_remediate": AUTO_REMEDIATE,
            "rag_enabled": RAG_ENABLED, "llm_enabled": LLM_ENABLED,
            "llm_model": GROQ_MODEL if LLM_ENABLED else None}


@app.post("/incidents/{incident_id}/diagnose")
def rediagnose(incident_id: int):
    """Re-run the diagnosis pipeline for one incident (manual trigger)."""
    incs = [i for i in storage.list_incidents(500) if i["id"] == incident_id]
    if not incs:
        raise HTTPException(status_code=404, detail="incident not found")
    from llm import diagnosis
    d = diagnosis.diagnose(incs[0])
    storage.set_diagnosis(incident_id, d)
    return {"incident_id": incident_id, "diagnosis": d}


@app.get("/baseline")
def baseline():
    """Real baseline progress, read from each patient agent's /metrics/summary."""
    out = []
    for p in PATIENTS:
        d = {}
        try:
            r = requests.get(f"{p['url']}/metrics/summary", timeout=HTTP_TIMEOUT)
            if r.status_code == 200:
                d = r.json()
        except requests.RequestException:
            pass
        days = None
        if d.get("earliest") and d.get("latest"):
            try:
                e = datetime.fromisoformat(d["earliest"])
                l = datetime.fromisoformat(d["latest"])
                days = round((l - e).total_seconds() / 86400, 2)
            except (ValueError, TypeError):
                pass
        out.append({"patient": p["name"], "sample_count": d.get("sample_count"),
                    "earliest": d.get("earliest"), "latest": d.get("latest"), "days": days})
    return {"baseline": out, "target_days": 28}


@app.get("/status")
def status():
    """Current snapshot: latest sample + open incidents per patient."""
    open_incidents = [i for i in storage.list_incidents(200) if i["status"] == "open"]
    return {
        "patients": [
            {"name": p["name"], "latest": storage.latest_sample(p["name"])}
            for p in PATIENTS
        ],
        "open_incidents": open_incidents,
    }


@app.get("/incidents")
def incidents(limit: int = 50):
    return {"incidents": storage.list_incidents(limit)}


@app.get("/incidents/{incident_id}")
def incident_detail(incident_id: int):
    inc = storage.get_incident(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="incident not found")
    try:
        inc["evidence"] = json.loads(inc.get("diag_evidence") or "[]")
    except (ValueError, TypeError):
        inc["evidence"] = []
    return {"incident": inc, "actions": storage.actions_for_incident(incident_id)}


@app.get("/samples/recent")
def samples_recent(minutes: int = 10):
    return {"samples": storage.recent_samples(minutes)}


@app.get("/actions")
def actions(limit: int = 50):
    return {"actions": storage.list_actions(limit)}


_CHAOS_ACTIONS = {"stop", "start", "restart"}
_CHAOS_TARGETS = {"shopsmart_app", "shopsmart_nginx"}


@app.post("/chaos")
def chaos(action: str, target: str = "shopsmart_app"):
    """Chaos Lab control: push a stop/start/restart to the agent to inject or
    clear a real outage from the dashboard. Whitelisted actions/targets only."""
    if action not in _CHAOS_ACTIONS or target not in _CHAOS_TARGETS:
        raise HTTPException(status_code=400, detail="action/target not allowed")
    name = PATIENTS[0]["name"] if PATIENTS else None
    url = _patient_url(name)
    if not url:
        raise HTTPException(status_code=400, detail="no patient configured")
    ok, out = remediation.execute_on_agent(url, action, target)
    return {"action": action, "target": target, "success": ok, "output": out}


@app.post("/actions/{action_id}/approve")
def approve_action(action_id: int):
    """Human approves a pending action (used when AUTO_REMEDIATE is off).
    On approval the Doctor pushes it to the agent and records the result."""
    a = storage.get_action(action_id)
    if not a:
        raise HTTPException(status_code=404, detail="action not found")
    if a["status"] != "pending_approval":
        raise HTTPException(status_code=409, detail=f"action is '{a['status']}', not pending_approval")
    url = _patient_url(a["patient"])
    if not url:
        raise HTTPException(status_code=400, detail="unknown patient")
    ok, out = remediation.execute_on_agent(url, a["type"], a["target"])
    storage.set_action_result(action_id, ok, out)
    return {"action_id": action_id, "success": ok, "output": out}
