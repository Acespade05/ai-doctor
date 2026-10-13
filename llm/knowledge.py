"""Seed knowledge base of known incidents — the initial corpus for RAG and the
deterministic runbook for Tier 1.

Each entry: a known failure, its typical root cause, and the safe fix. This is
what ChromaDB is seeded with (so retrieval has real prior incidents to match)
and what the rules tier looks up directly.
"""

KNOWN_INCIDENTS = [
    {
        "id": "site_down",
        "title": "Site down / 502",
        "symptom": "Public health check failing, HTTP 5xx, site unreachable.",
        "root_cause": "The app container has crashed or is not responding; nginx has no healthy upstream.",
        "recommended_action": "restart the app container, then verify the health probe recovers.",
    },
    {
        "id": "redis_down",
        "title": "Redis unavailable",
        "symptom": "Cache/session errors, Redis ping failing, elevated latency.",
        "root_cause": "The Redis container is down or unreachable.",
        "recommended_action": "restart the redis container; confirm the app reconnects.",
    },
    {
        "id": "db_slow",
        "title": "Database latency",
        "symptom": "Slow queries, rising DB connection count, checkout timeouts.",
        "root_cause": "PostgreSQL lock contention or an exhausted connection pool.",
        "recommended_action": "recycle the connection pool (restart the app); investigate long-running queries.",
    },
    {
        "id": "memory_leak",
        "title": "Memory leak",
        "symptom": "Memory usage climbing steadily toward exhaustion.",
        "root_cause": "A process is leaking memory (heap growth over time).",
        "recommended_action": "restart the affected container to reclaim memory; flag for code investigation.",
    },
    {
        "id": "traffic_spike",
        "title": "Traffic spike",
        "symptom": "Request rate well above baseline, CPU saturation, 429/503s.",
        "root_cause": "Request saturation — load exceeds current capacity (surge or abuse).",
        "recommended_action": "apply rate limiting; if a single source, block it; consider scaling.",
    },
    {
        "id": "bad_deployment",
        "title": "Bad deployment",
        "symptom": "Elevated 5xx errors immediately after a deploy.",
        "root_cause": "A recent deployment introduced a regression.",
        "recommended_action": "roll back to the previous known-good deployment; escalate for review.",
    },
    {
        "id": "high_cpu",
        "title": "High CPU",
        "symptom": "CPU utilisation sustained near 100%.",
        "root_cause": "Runaway process or load beyond capacity.",
        "recommended_action": "identify the hot process; restart if runaway, otherwise scale.",
    },
    {
        "id": "high_memory",
        "title": "High memory",
        "symptom": "Memory utilisation sustained near 100%.",
        "root_cause": "Memory pressure — leak or undersized instance.",
        "recommended_action": "restart to reclaim memory; investigate for a leak.",
    },
]

# Fast deterministic lookup for the rules tier.
RULEBOOK = {k["id"]: k for k in KNOWN_INCIDENTS}


def as_documents():
    """Return (ids, texts, metadatas) for seeding a vector store."""
    ids, texts, metas = [], [], []
    for k in KNOWN_INCIDENTS:
        ids.append(k["id"])
        texts.append(f"{k['title']}. Symptom: {k['symptom']} Root cause: {k['root_cause']}")
        metas.append({"title": k["title"], "root_cause": k["root_cause"],
                      "recommended_action": k["recommended_action"]})
    return ids, texts, metas
