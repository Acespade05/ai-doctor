"""Rules-first detection: turn one sample into zero or more findings.

A finding is a dict {type, severity, summary, details}. Pure function — no I/O —
so it is easy to test and later sit alongside the Isolation Forest verdict.
"""
from backend.config import (
    CPU_HIGH, MEM_HIGH, DISK_HIGH, PROBE_LATENCY_HIGH_MS, AGENT_STALE_SECONDS,
    CONTAINER_INCIDENT, RPS_HIGH, RATE_LIMITED_HIGH, ERROR_RATE_HIGH,
    TRAFFIC_MIN_REQUESTS,
)

_SEVERITY = {"nginx_down": "critical", "app_down": "critical",
             "db_down": "critical", "redis_down": "high"}


def detect(sample, containers=None, traffic=None):
    findings = []

    # --- traffic flood / attack (from nginx-log telemetry) ---
    if traffic and traffic.get("available") and traffic.get("requests", 0) >= TRAFFIC_MIN_REQUESTS:
        rps = traffic.get("requests_per_sec", 0)
        rl = traffic.get("rate_limited_rate", 0)
        er = traffic.get("error_rate", 0)
        # Trigger on HARM (requests being rate-limited or erroring), not raw volume,
        # so that once the Doctor blocks the source (those requests become 403, not
        # 429/5xx) the harm clears and the incident self-resolves.
        if rl > RATE_LIMITED_HIGH or er > ERROR_RATE_HIGH:
            top = traffic.get("top_ip")
            findings.append({
                "type": "traffic_spike",
                "severity": "high",
                "summary": (f"Traffic spike: {rps} req/s, {int(rl*100)}% rate-limited, "
                            f"{int(er*100)}% errors" + (f" (top source {top})" if top else "")),
                "details": {"requests_per_sec": rps, "rate_limited_rate": rl,
                            "error_rate": er, "top_ip": top,
                            "top_ip_count": traffic.get("top_ip_count")},
            })

    # --- container-level failures (precise cause, from the agent's docker view) ---
    # When we can see a specific container is down, raise that precise incident and
    # suppress the generic site_down below (we have a better explanation + target).
    container_down = False
    if containers:
        for name, status in containers.items():
            itype = CONTAINER_INCIDENT.get(name)
            if itype and status not in ("running", "healthy"):
                container_down = True
                findings.append({
                    "type": itype,
                    "severity": _SEVERITY.get(itype, "high"),
                    "summary": f"Container {name} is {status}.",
                    "details": {"container": name, "status": status},
                })

    # --- site reachability (from the Doctor's own public probe) ---
    if sample.get("site_up") == 0 and not container_down:
        findings.append({
            "type": "site_down",
            "severity": "critical",
            "summary": f"Public site health check is failing (status={sample.get('site_status')}).",
            "details": {"site_status": sample.get("site_status"),
                        "site_latency_ms": sample.get("site_latency_ms")},
        })
    elif sample.get("site_up") != 0 and (sample.get("site_latency_ms") or 0) > PROBE_LATENCY_HIGH_MS:
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
