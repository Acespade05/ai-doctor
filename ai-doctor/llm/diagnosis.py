"""Tiered diagnosis: Tier 1 rules -> Tier 2 ChromaDB RAG -> Tier 3 Groq LLM.

diagnose(incident, samples) returns:
    {tier, root_cause, recommended_action, confidence, evidence, status, llm_error?}

Graceful degradation:
  - No GROQ_API_KEY  -> Tier 3 (LLM) skipped.
  - chromadb missing -> Tier 2 (RAG) skipped; retrieval returns [].
  - Nothing matches  -> status "unknown" (never fabricates a diagnosis).

When the LLM is enabled but fails, the reason is logged and returned as
'llm_error' so failures are visible instead of silently falling back.
"""
import json

import requests

from backend.config import (
    GROQ_API_KEY, GROQ_MODEL, GROQ_URL, CHROMA_PATH, RAG_ENABLED, HTTP_TIMEOUT,
)
from llm.knowledge import RULEBOOK, as_documents

_collection = None
_collection_tried = False


# ---------------- Tier 2: ChromaDB RAG ----------------
def _get_collection():
    global _collection, _collection_tried
    if _collection is not None or _collection_tried:
        return _collection
    _collection_tried = True
    if not RAG_ENABLED:
        return None
    try:
        import chromadb
        try:
            client = chromadb.PersistentClient(
                path=CHROMA_PATH,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )
        except Exception:
            client = chromadb.PersistentClient(path=CHROMA_PATH)
        col = client.get_or_create_collection("incidents")
        if col.count() == 0:
            ids, texts, metas = as_documents()
            col.add(ids=ids, documents=texts, metadatas=metas)
        _collection = col
    except Exception as e:
        print(f"[doctor] RAG init failed: {e}")
        _collection = None
    return _collection


def retrieve(query_text, k=3):
    col = _get_collection()
    if col is None:
        return []
    try:
        res = col.query(query_texts=[query_text], n_results=k)
        out = []
        metas = (res.get("metadatas") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, m in enumerate(metas):
            out.append({
                "title": m.get("title"),
                "root_cause": m.get("root_cause"),
                "recommended_action": m.get("recommended_action"),
                "document": docs[i] if i < len(docs) else None,
                "distance": dists[i] if i < len(dists) else None,
            })
        return out
    except Exception as e:
        print(f"[doctor] RAG query failed: {e}")
        return []


# ---------------- Tier 3: Groq LLM ----------------
def _llm_diagnose(incident, evidence):
    """Returns (result_dict | None, error_str | None)."""
    if not GROQ_API_KEY:
        return None, "no_api_key"
    ev = "\n".join(f"- {e['title']}: {e['root_cause']} -> {e['recommended_action']}"
                   for e in evidence) or "none"
    system = ("You are an SRE diagnosis assistant for the ShopSmart e-commerce site. "
              "The stack runs as Docker Compose containers: shopsmart_app "
              "(Node/Express), shopsmart_postgres, shopsmart_redis, shopsmart_nginx. "
              "Remediation means restarting one of these containers (e.g. "
              "'restart the shopsmart_app container'). Recommend only safe, reversible "
              "Docker-level actions — never Kubernetes/kubectl. "
              "Return ONLY a JSON object with keys: root_cause (string), "
              "recommended_action (string), confidence (number 0-1).")
    user = (f"Incident type: {incident.get('type')}\n"
            f"Severity: {incident.get('severity')}\n"
            f"Summary: {incident.get('summary')}\n"
            f"Details: {incident.get('details')}\n\n"
            f"Similar past incidents:\n{ev}")
    try:
        r = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={"model": GROQ_MODEL, "temperature": 0.2, "max_tokens": 400,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
            timeout=max(HTTP_TIMEOUT, 20),
        )
        if r.status_code != 200:
            return None, f"groq_http_{r.status_code}: {r.text[:200]}"
        content = r.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.strip("`").split("\n", 1)[-1]
        data = json.loads(content)
        return {
            "root_cause": data.get("root_cause"),
            "recommended_action": data.get("recommended_action"),
            "confidence": data.get("confidence"),
        }, None
    except Exception as e:
        return None, f"exception: {type(e).__name__}: {e}"


# ---------------- Orchestrator ----------------
def diagnose(incident, samples=None):
    itype = incident.get("type")
    query = f"{itype}. {incident.get('summary', '')}"

    evidence = retrieve(query)                       # Tier 2
    llm, llm_err = _llm_diagnose(incident, evidence) # Tier 3 (uses Tier 2 context)
    rule = RULEBOOK.get(itype)                       # Tier 1
    ev_titles = [e["title"] for e in evidence if e.get("title")]

    if llm_err and llm_err != "no_api_key":
        print(f"[doctor] LLM diagnosis failed: {llm_err}")

    if llm and llm.get("root_cause"):
        return {"tier": "llm+rag" if evidence else "llm",
                "root_cause": llm["root_cause"],
                "recommended_action": llm.get("recommended_action"),
                "confidence": llm.get("confidence"),
                "evidence": ev_titles, "status": "ok"}

    out = None
    if rule:
        out = {"tier": "rules", "root_cause": rule["root_cause"],
               "recommended_action": rule["recommended_action"],
               "confidence": 0.75, "evidence": ev_titles, "status": "ok"}
    elif evidence:
        top = evidence[0]
        out = {"tier": "rag", "root_cause": top["root_cause"],
               "recommended_action": top["recommended_action"],
               "confidence": 0.5, "evidence": ev_titles, "status": "ok"}
    else:
        out = {"tier": None, "root_cause": None, "recommended_action": None,
               "confidence": None, "evidence": [], "status": "unknown"}

    # Surface why the LLM didn't produce the answer (visible in re-diagnose).
    if llm_err and llm_err != "no_api_key":
        out["llm_error"] = llm_err
    return out
