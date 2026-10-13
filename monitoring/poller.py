"""Polling loop: pull each patient agent + probe the public site, store a
sample, run detection, reconcile incidents.

Incident reconciliation: for each patient, the set of findings this tick is
compared with the currently-open incidents. New finding types open an incident;
types that have stopped firing are resolved (giving a duration). This is the
detect -> (later remediate) -> resolve lifecycle.
"""
import time
from datetime import datetime, timezone

import requests

from backend import storage
from remediation import engine as remediation
from monitoring.rules import detect
from backend.config import PATIENTS, PUBLIC_PROBE_URL, POLL_INTERVAL, HTTP_TIMEOUT


def _parse_iso(ts):
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def fetch_patient(url):
    """GET <url>/metrics/latest. Returns (metrics_dict | None, agent_age_seconds | None)."""
    try:
        r = requests.get(f"{url}/metrics/latest", timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            return None, None
        m = r.json()
        age = None
        dt = _parse_iso(m.get("timestamp"))
        if dt is not None:
            age = (datetime.now(timezone.utc) - dt).total_seconds()
        return m, age
    except (requests.RequestException, ValueError):
        return None, None


def probe_public():
    """Probe the public site. Returns (up, status, latency_ms)."""
    t0 = time.perf_counter()
    try:
        r = requests.get(PUBLIC_PROBE_URL, timeout=HTTP_TIMEOUT)
        latency = round((time.perf_counter() - t0) * 1000, 1)
        return (1 if r.status_code < 500 else 0), r.status_code, latency
    except requests.RequestException:
        return 0, None, round((time.perf_counter() - t0) * 1000, 1)


def build_sample(patient, metrics, age, probe):
    up, status, latency = probe
    s = {
        "patient": patient,
        "agent_reachable": 1 if metrics else 0,
        "agent_age_s": age,
        "site_up": up,
        "site_status": status,
        "site_latency_ms": latency,
    }
    if metrics:
        s.update({
            "cpu_percent": metrics.get("cpu_percent"),
            "memory_percent": metrics.get("memory_percent"),
            "disk_percent": metrics.get("disk_percent"),
            "process_count": metrics.get("process_count"),
            "net_sent": metrics.get("network_bytes_sent"),
            "net_recv": metrics.get("network_bytes_recv"),
        })
    return s


def reconcile(patient, findings):
    """Open new incidents, touch continuing ones, resolve cleared ones."""
    firing = {f["type"]: f for f in findings}
    # Resolve incidents whose condition cleared.
    for inc in storage.list_incidents(limit=200):
        if inc["patient"] == patient and inc["status"] == "open" and inc["type"] not in firing:
            storage.resolve_incident(inc["id"])
    # Open or touch current findings.
    for typ, finding in firing.items():
        existing = storage.get_open_incident(patient, typ)
        if existing:
            storage.touch_incident(existing["id"])
        else:
            iid = storage.open_incident(patient, finding)
            # Diagnose the new incident (Tier 1 rules -> 2 RAG -> 3 LLM).
            try:
                from llm import diagnosis
                storage.set_diagnosis(iid, diagnosis.diagnose(finding))
            except Exception as e:
                print(f"[doctor] diagnosis error: {e}")


def poll_once():
    probe = probe_public()
    for p in PATIENTS:
        metrics, age = fetch_patient(p["url"])
        sample = build_sample(p["name"], metrics, age, probe)
        storage.insert_sample(sample)
        reconcile(p["name"], detect(sample))
        # Drive remediation for any open auto-remediable incidents.
        remediation.process(p["name"], p["url"])


def loop():
    print(f"[doctor] poller started: {len(PATIENTS)} patient(s), every {POLL_INTERVAL}s")
    while True:
        start = time.time()
        try:
            poll_once()
        except Exception as e:  # never let the loop die
            print(f"[doctor] poll error: {e}")
        time.sleep(max(0, POLL_INTERVAL - (time.time() - start)))
