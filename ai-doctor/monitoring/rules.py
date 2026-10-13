"""Rules-first detection: turn one sample into zero or more findings.

A finding is a dict {type, severity, summary, details}. Pure function — no I/O —
so it is easy to test and later sit alongside the Isolation Forest verdict.
"""
from backend.config import (
    CPU_HIGH, MEM_HIGH, DISK_HIGH, PROBE_LATENCY_HIGH_MS, AGENT_STALE_SECONDS,
)


def detect(sample):
    findings = []

    # --- site reachability (from the Doctor's own public probe) ---
    if sample.get("site_up") == 0:
        findings.append({
            "type": "site_down",
            "severity": "critical",
            "summary": f"Public site health check is failing (status={sample.get('site_status')}).",
            "details": {"site_status": sample.get("site_status"),
                        "site_latency_ms": sample.get("site_latency_ms")},
        })
    elif (sample.get("site_latency_ms") or 0) > PROBE_LATENCY_HIGH_MS:
        findings.append({
            "type": "site_slow",
            "severity": "medium",
            "summary": f"Site health check slow: {sample.get('site_latency_ms')} ms.",
            "details": {"site_latency_ms": sample.get("site_latency_ms")},
        })

    # --- monitoring coverage ---
    if not sample.get("agent_reachable"):
        findings.append({
            "type": "monitoring_blind",
            "severity": "high",
            "summary": "Patient agent unreachable — no system metrics (AI-SRE is blind).",
            "details": {},
        })
        return findings  # no system metrics to evaluate

    age = sample.get("agent_age_s")
    if age is not None and age > AGENT_STALE_SECONDS:
        findings.append({
            "type": "agent_stale",
            "severity": "medium",
            "summary": f"Agent last reported {round(age)}s ago (stale).",
            "details": {"agent_age_s": age},
        })

    # --- resource exhaustion ---
    for key, thr, typ, label in (
        ("cpu_percent", CPU_HIGH, "high_cpu", "CPU"),
        ("memory_percent", MEM_HIGH, "high_memory", "Memory"),
        ("disk_percent", DISK_HIGH, "high_disk", "Disk"),
    ):
        val = sample.get(key)
        if val is not None and val > thr:
            findings.append({
                "type": typ,
                "severity": "high",
                "summary": f"{label} usage {val}% (> {thr}%).",
                "details": {key: val, "threshold": thr},
            })

    return findings
