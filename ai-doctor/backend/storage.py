"""SQLite storage: metric samples, incident lifecycle, and remediation actions.

- samples: the Doctor's own copy pulled from each agent + the public probe.
- incidents: detect -> resolve, with duration (MTTR), an escalated flag, and
  how it resolved (auto-remediated / recovered).
- actions: remediation attempts the Doctor drove (what, when, result), linked to
  the incident that triggered them.
"""
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

from backend.config import DB_PATH

_lock = threading.Lock()


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db():
    with _lock, _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL, ts_epoch REAL NOT NULL, patient TEXT NOT NULL,
                agent_reachable INTEGER, agent_age_s REAL,
                cpu_percent REAL, memory_percent REAL, disk_percent REAL,
                process_count INTEGER, net_sent INTEGER, net_recv INTEGER,
                site_up INTEGER, site_status INTEGER, site_latency_ms REAL
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_samples_epoch ON samples(ts_epoch);")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient TEXT NOT NULL, type TEXT NOT NULL, severity TEXT NOT NULL,
                status TEXT NOT NULL,                       -- open | resolved
                escalated INTEGER NOT NULL DEFAULT 0,
                resolution TEXT,                            -- auto-remediated | recovered
                summary TEXT, details TEXT,
                started_at TEXT NOT NULL, started_epoch REAL NOT NULL,
                last_seen_at TEXT, resolved_at TEXT, duration_seconds REAL
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_incident_open ON incidents(patient, type, status);"
        )
        # Diagnosis columns (added incrementally; safe on existing DBs).
        for col, decl in (
            ("diag_tier", "TEXT"), ("diag_root_cause", "TEXT"),
            ("diag_action", "TEXT"), ("diag_confidence", "REAL"),
            ("diag_evidence", "TEXT"),
        ):
            try:
                conn.execute(f"ALTER TABLE incidents ADD COLUMN {col} {decl};")
            except Exception:
                pass  # column already exists
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient TEXT NOT NULL, incident_id INTEGER NOT NULL,
                type TEXT NOT NULL, target TEXT NOT NULL,
                status TEXT NOT NULL,                       -- pending | pending_approval | done | failed
                created_at TEXT NOT NULL, created_epoch REAL NOT NULL,
                executed_epoch REAL, success INTEGER, output TEXT
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_actions_incident ON actions(incident_id);")


init_db()


def _now():
    t = time.time()
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat(), t


# ---------------- samples ----------------
def insert_sample(s):
    ts, epoch = _now()
    with _lock, _conn() as conn:
        conn.execute(
            """INSERT INTO samples
               (ts, ts_epoch, patient, agent_reachable, agent_age_s, cpu_percent,
                memory_percent, disk_percent, process_count, net_sent, net_recv,
                site_up, site_status, site_latency_ms)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?);""",
            (ts, epoch, s["patient"], s["agent_reachable"], s.get("agent_age_s"),
             s.get("cpu_percent"), s.get("memory_percent"), s.get("disk_percent"),
             s.get("process_count"), s.get("net_sent"), s.get("net_recv"),
             s.get("site_up"), s.get("site_status"), s.get("site_latency_ms")),
        )


def latest_sample(patient):
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT * FROM samples WHERE patient=? ORDER BY id DESC LIMIT 1;", (patient,)
        ).fetchone()
        return dict(row) if row else None


def recent_samples(minutes=10):
    cutoff = time.time() - minutes * 60
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM samples WHERE ts_epoch >= ? ORDER BY ts_epoch DESC LIMIT 5000;",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------- incidents ----------------
def get_open_incident(patient, type_):
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT * FROM incidents WHERE patient=? AND type=? AND status='open' "
            "ORDER BY id DESC LIMIT 1;", (patient, type_),
        ).fetchone()
        return dict(row) if row else None


def open_incidents(patient=None):
    with _lock, _conn() as conn:
        if patient:
            rows = conn.execute(
                "SELECT * FROM incidents WHERE status='open' AND patient=? ORDER BY id;",
                (patient,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM incidents WHERE status='open' ORDER BY id;"
            ).fetchall()
        return [dict(r) for r in rows]


def open_incident(patient, finding):
    ts, epoch = _now()
    with _lock, _conn() as conn:
        cur = conn.execute(
            """INSERT INTO incidents
               (patient, type, severity, status, summary, details, started_at,
                started_epoch, last_seen_at)
               VALUES (?,?,?,'open',?,?,?,?,?);""",
            (patient, finding["type"], finding["severity"], finding["summary"],
             json.dumps(finding.get("details", {})), ts, epoch, ts),
        )
        return cur.lastrowid


def touch_incident(incident_id):
    ts, _ = _now()
    with _lock, _conn() as conn:
        conn.execute("UPDATE incidents SET last_seen_at=? WHERE id=?;", (ts, incident_id))


def escalate_incident(incident_id, summary):
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE incidents SET escalated=1, severity='critical', summary=? WHERE id=?;",
            (summary, incident_id),
        )


def resolve_incident(incident_id):
    ts, epoch = _now()
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT started_epoch FROM incidents WHERE id=?;", (incident_id,)
        ).fetchone()
        dur = round(epoch - row["started_epoch"], 1) if row else None
        # resolution type: auto-remediated if any action on this incident succeeded
        succeeded = conn.execute(
            "SELECT COUNT(*) FROM actions WHERE incident_id=? AND success=1;", (incident_id,)
        ).fetchone()[0]
        resolution = "auto-remediated" if succeeded else "recovered"
        conn.execute(
            "UPDATE incidents SET status='resolved', resolved_at=?, duration_seconds=?, "
            "resolution=? WHERE id=?;",
            (ts, dur, resolution, incident_id),
        )
        return dur


def list_incidents(limit=50):
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM incidents ORDER BY id DESC LIMIT ?;", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def set_diagnosis(incident_id, d):
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE incidents SET diag_tier=?, diag_root_cause=?, diag_action=?, "
            "diag_confidence=?, diag_evidence=? WHERE id=?;",
            (d.get("tier"), d.get("root_cause"), d.get("recommended_action"),
             d.get("confidence"), json.dumps(d.get("evidence") or []), incident_id),
        )


def get_incident(incident_id):
    with _lock, _conn() as conn:
        row = conn.execute("SELECT * FROM incidents WHERE id=?;", (incident_id,)).fetchone()
        return dict(row) if row else None


# ---------------- actions ----------------
def create_action(patient, incident_id, type_, target, status):
    ts, epoch = _now()
    with _lock, _conn() as conn:
        cur = conn.execute(
            """INSERT INTO actions
               (patient, incident_id, type, target, status, created_at, created_epoch)
               VALUES (?,?,?,?,?,?,?);""",
            (patient, incident_id, type_, target, status, ts, epoch),
        )
        return cur.lastrowid


def set_action_result(action_id, success, output):
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE actions SET status=?, success=?, output=?, executed_epoch=? WHERE id=?;",
            ("done" if success else "failed", 1 if success else 0, output, time.time(), action_id),
        )


def get_action(action_id):
    with _lock, _conn() as conn:
        row = conn.execute("SELECT * FROM actions WHERE id=?;", (action_id,)).fetchone()
        return dict(row) if row else None


def actions_for_incident(incident_id):
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM actions WHERE incident_id=? ORDER BY id;", (incident_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def list_actions(limit=50):
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM actions ORDER BY id DESC LIMIT ?;", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
