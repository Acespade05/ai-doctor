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
                            LLM_ENABLED, RAG_ENABLED, GROQ_MODEL, PUBLIC_PROBE_URL,
                            MANUAL_MTTR_MINUTES)

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


def _site_base():
    """ShopSmart's public base URL, derived from the health-probe URL."""
    base = PUBLIC_PROBE_URL or ""
    for suffix in ("/health", "/api/health", "/healthz"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base.rstrip("/")


def _get_json(url):
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except (requests.RequestException, ValueError):
        pass
    return None


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _fetch_business():
    """Live business metrics from ShopSmart's public /api/metrics/*."""
    base = _site_base()
    rev = _get_json(f"{base}/api/metrics/revenue-today") or {}
    aov = _get_json(f"{base}/api/metrics/aov") or {}
    conv = _get_json(f"{base}/api/metrics/conversion-rate") or {}
    sess = _get_json(f"{base}/api/metrics/active-sessions") or {}
    checkouts = _get_json(f"{base}/api/metrics/active-checkouts") or {}
    reachable = bool(rev or aov or conv or sess)

    revenue_today = _f(rev.get("revenue"))
    order_count = int(_f(rev.get("orderCount")))
    now = datetime.now()
    minutes_today = max(now.hour * 60 + now.minute, 1)
    revenue_per_min = round(revenue_today / minutes_today, 2)

    return {
        "reachable": reachable,
        "site_base": base,
        "revenue_today": round(revenue_today, 2),
        "order_count": order_count,
        "revenue_per_min": revenue_per_min,
        "average_order_value": round(_f(aov.get("averageOrderValue")), 2),
        "conversion_rate": round(_f(conv.get("conversionRate")), 2),
        "active_sessions": int(_f(sess.get("activeSessions"))),
        "active_checkouts": int(_f(checkouts.get("activeCheckouts"))),
    }


@app.get("/business")
def business():
    """Real business metrics pulled live from ShopSmart (Founder/Executive views).
    Read straight from the site — no editable inputs. 'reachable' false == site down."""
    return _fetch_business()


def _component_status(sample, open_types, business_ok):
    """Best-effort component health from the signals we actually have.
    up / down / unknown — never fabricated. down wins when an open incident
    names the component; otherwise a positive signal makes it up."""
    site_up = bool(sample and sample.get("site_up"))
    agent_ok = bool(sample and sample.get("agent_reachable"))

    def has(*keys):
        return any(any(k in t for k in keys) for t in open_types)

    nginx = "down" if has("nginx", "site_down", "gateway") else ("up" if site_up else "down")
    app = "down" if has("app", "deployment", "crash", "memory", "cpu", "site_down") else \
          ("up" if (business_ok or site_up) else "unknown")
    db = "down" if has("db", "postgres", "database", "sql") else \
         ("up" if business_ok else "unknown")
    cache = "down" if has("redis", "cache") else ("up" if (business_ok and site_up) else "unknown")
    return [
        {"name": "Nginx", "role": "gateway", "status": nginx},
        {"name": "App", "role": "backend", "status": app},
        {"name": "PostgreSQL", "role": "database", "status": db},
        {"name": "Redis", "role": "cache", "status": cache},
        {"name": "Agent", "role": "metrics", "status": "up" if agent_ok else "down"},
    ]


_ROLE_BY_CONTAINER = {"shopsmart_nginx": ("Nginx", "gateway"),
                      "shopsmart_app": ("App", "backend"),
                      "shopsmart_postgres": ("PostgreSQL", "database"),
                      "shopsmart_redis": ("Redis", "cache")}


@app.get("/servicemap")
def servicemap():
    """Per-component health for the service map. Uses the agent's REAL docker view
    when available (exact container up/down); otherwise falls back to inference
    from the probe, agent reachability, open incidents and the business API."""
    name = PATIENTS[0]["name"] if PATIENTS else None
    containers = poller.LATEST_CONTAINERS.get(name) if name else None
    sample = storage.latest_sample(name) if name else None
    agent_ok = bool(sample and sample.get("agent_reachable"))

    if containers:  # real docker status
        comps = []
        for cname, status in containers.items():
            label, role = _ROLE_BY_CONTAINER.get(cname, (cname, "service"))
            comps.append({"name": label, "role": role,
                          "status": "up" if status in ("running", "healthy") else "down"})
        comps.append({"name": "Agent", "role": "metrics",
                      "status": "up" if agent_ok else "down"})
        return {"patient": name, "components": comps, "source": "docker"}

    # fallback: inference
    open_types = [i.get("type", "").lower() for i in storage.open_incidents()]
    business_ok = _fetch_business().get("reachable", False)
    comps = _component_status(sample, open_types, business_ok)
    return {"patient": name, "components": comps, "source": "inferred"}


def _grade(uptime_pct, success_rate):
    score = uptime_pct * 0.6 + success_rate * 40
    if score >= 98: return "A+"
    if score >= 95: return "A"
    if score >= 90: return "B"
    if score >= 80: return "C"
    return "D"


@app.get("/scorecard")
def scorecard(days: int = 30):
    """Executive scorecard: cumulative reliability + ROI over a window.
    Uptime, auto-resolved ratio, avg recovery, and an ESTIMATED revenue-protected
    figure (clearly an estimate — compares AI recovery time to a manual baseline)."""
    window_s = max(days, 1) * 86400
    cutoff = datetime.now().timestamp() - window_s
    incs = [i for i in storage.list_incidents(2000)
            if (i.get("started_epoch") or 0) >= cutoff]

    total = len(incs)
    resolved = [i for i in incs if i.get("status") == "resolved"]
    auto = [i for i in resolved if i.get("resolution") == "auto-remediated"]
    escalated = [i for i in incs if i.get("escalated")]

    durations = [i["duration_seconds"] for i in resolved
                 if i.get("duration_seconds") is not None]
    avg_recovery = round(sum(durations) / len(durations), 1) if durations else None

    # downtime = time site-down incidents were open (resolved ones counted fully)
    downtime_s = sum(i.get("duration_seconds") or 0 for i in resolved
                     if "site_down" in (i.get("type") or "").lower())
    uptime_pct = round(max(0.0, 1 - downtime_s / window_s) * 100, 3)
    success_rate = round(len(resolved) / total, 3) if total else 1.0

    # revenue protected (ESTIMATE): per resolved incident, minutes of downtime
    # avoided vs the manual baseline, times current revenue/min.
    rev_per_min = _fetch_business().get("revenue_per_min", 0.0)
    minutes_saved = 0.0
    for i in resolved:
        actual_min = (i.get("duration_seconds") or 0) / 60.0
        minutes_saved += max(0.0, MANUAL_MTTR_MINUTES - actual_min)
    revenue_protected = round(rev_per_min * minutes_saved, 2)

    return {
        "window_days": days,
        "total_incidents": total,
        "auto_resolved": len(auto),
        "resolved": len(resolved),
        "escalated": len(escalated),
        "auto_resolved_pct": round(len(auto) / total * 100, 1) if total else 0.0,
        "success_rate": success_rate,
        "avg_recovery_seconds": avg_recovery,
        "uptime_pct": uptime_pct,
        "manual_mttr_minutes": MANUAL_MTTR_MINUTES,
        "revenue_protected_estimate": revenue_protected,
        "revenue_per_min": rev_per_min,
        "grade": _grade(uptime_pct, success_rate),
    }


@app.get("/timeline")
def timeline(limit: int = 20):
    """Chronological event feed built from real incident + action records."""
    events = []
    for i in storage.list_incidents(200):
        t = i.get("type", "incident")
        events.append({"at": i.get("started_at"), "epoch": i.get("started_epoch") or 0,
                       "kind": "detect", "text": f"Detected {t}",
                       "incident_id": i["id"], "severity": i.get("severity")})
        if i.get("diag_root_cause"):
            conf = i.get("diag_confidence")
            cstr = f" ({int(conf*100)}%)" if conf is not None else ""
            events.append({"at": i.get("last_seen_at") or i.get("started_at"),
                           "epoch": (i.get("started_epoch") or 0) + 1, "kind": "diagnose",
                           "text": f"Diagnosed: {i['diag_root_cause']} → "
                                   f"{i.get('diag_action') or 'review'}{cstr}",
                           "incident_id": i["id"]})
        if i.get("status") == "resolved":
            dur = i.get("duration_seconds")
            dstr = f" — MTTR {dur}s" if dur is not None else ""
            kind = "recover" if i.get("resolution") == "auto-remediated" else "resolve"
            events.append({"at": i.get("resolved_at"),
                           "epoch": (i.get("started_epoch") or 0) + (dur or 2),
                           "kind": kind,
                           "text": f"{'Auto-recovered' if kind=='recover' else 'Recovered'}"
                                   f" {t}{dstr}", "incident_id": i["id"]})
        if i.get("escalated"):
            events.append({"at": i.get("last_seen_at") or i.get("started_at"),
                           "epoch": (i.get("started_epoch") or 0) + 1.5, "kind": "escalate",
                           "text": f"Escalated {t} for human approval", "incident_id": i["id"]})
    for a in storage.list_actions(100):
        ok = a.get("success")
        verb = "Executed" if ok else ("Attempted" if a.get("status") != "failed" else "Failed")
        events.append({"at": a.get("created_at"), "epoch": a.get("created_epoch") or 0,
                       "kind": "action",
                       "text": f"{verb} {a.get('type')} → {a.get('target')}",
                       "incident_id": a.get("incident_id")})
    events.sort(key=lambda e: e["epoch"], reverse=True)
    return {"events": events[:limit]}


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


@app.get("/traffic")
def traffic():
    """Latest traffic telemetry (requests/sec, 5xx-rate, 429-rate) for the patient,
    as last polled from the agent's nginx-log reader. Powers the live traffic chart."""
    name = PATIENTS[0]["name"] if PATIENTS else None
    t = poller.LATEST_TRAFFIC.get(name) if name else None
    return {"patient": name, "traffic": t or {"available": False}}


@app.get("/blocks")
def blocks():
    """IP blocks the Doctor currently has active at the agent (ip -> seconds left)."""
    name = PATIENTS[0]["name"] if PATIENTS else None
    url = _patient_url(name) if name else None
    d = _get_json(f"{url}/blocks") if url else None
    return {"patient": name, "blocks": (d or {}).get("blocks", {})}


@app.get("/actions")
def actions(limit: int = 50):
    return {"actions": storage.list_actions(limit)}


_CHAOS_ACTIONS = {"stop", "start", "restart"}
_CHAOS_TARGETS = {"shopsmart_app", "shopsmart_nginx", "shopsmart_postgres", "shopsmart_redis"}
# Resource-exhaustion injection: the agent runs a bounded stress-ng load so the
# Doctor sees real high_memory / high_cpu / high_disk and reacts. Self-expires.
_INJECT_TARGETS = {"memory", "cpu", "disk"}


@app.post("/chaos")
def chaos(action: str, target: str = "shopsmart_app"):
    """Chaos Lab control. Two kinds, both pushed to the agent:
    - container faults: stop/start/restart on shopsmart_app | shopsmart_nginx
    - resource injection: inject on memory | cpu | disk (bounded stress, self-expires)
    Whitelisted combinations only."""
    container_fault = action in _CHAOS_ACTIONS and target in _CHAOS_TARGETS
    injection = action == "inject" and target in _INJECT_TARGETS
    unblock = action == "unblock_all"
    if not (container_fault or injection or unblock):
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
