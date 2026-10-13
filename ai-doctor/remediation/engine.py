"""Hybrid-autonomy remediation driver.

For each open, auto-remediable incident the Doctor decides whether to act:
- AUTO_REMEDIATE on  -> push the whitelisted action to the agent, record result.
- AUTO_REMEDIATE off -> record a pending_approval action for a human.

Safety: at most REMEDIATION_MAX_ATTEMPTS tries, spaced by REMEDIATION_COOLDOWN;
if the site still isn't back, the incident is escalated (no more auto actions).
Verification is implicit — if the restart works, the next poll sees the site up
and resolves the incident (tagged auto-remediated); if not, we retry/escalate.
"""
import time

import requests

from backend import storage
from backend.config import (
    AUTO_REMEDIATE, REMEDIATION_MAX_ATTEMPTS, REMEDIATION_COOLDOWN,
    REMEDIATION_PLAN, AGENT_TOKEN, HTTP_TIMEOUT,
)


def execute_on_agent(patient_url, action_type, target):
    """Push one action to the agent's /actions/execute. Returns (success, output)."""
    try:
        r = requests.post(
            f"{patient_url}/actions/execute",
            json={"type": action_type, "target": target},
            headers={"X-Agent-Token": AGENT_TOKEN},
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code == 200:
            d = r.json()
            return bool(d.get("success")), str(d.get("output"))
        return False, f"agent HTTP {r.status_code}: {r.text[:200]}"
    except requests.RequestException as e:
        return False, f"agent unreachable: {e}"


def _run_action(patient, patient_url, incident_id, plan):
    action_id = storage.create_action(patient, incident_id, plan["action"], plan["target"], "pending")
    ok, out = execute_on_agent(patient_url, plan["action"], plan["target"])
    storage.set_action_result(action_id, ok, out)
    return action_id, ok, out


def process(patient, patient_url):
    for inc in storage.open_incidents(patient):
        plan = REMEDIATION_PLAN.get(inc["type"])
        if not plan or inc["escalated"]:
            continue  # not auto-remediable, or already handed to a human

        actions = storage.actions_for_incident(inc["id"])
        if any(a["status"] == "pending_approval" for a in actions):
            continue  # waiting on human approval

        # Cooldown: give the last attempt time to take effect before retrying.
        if actions and actions[-1]["executed_epoch"]:
            if time.time() - actions[-1]["executed_epoch"] < REMEDIATION_COOLDOWN:
                continue

        if len(actions) >= REMEDIATION_MAX_ATTEMPTS:
            storage.escalate_incident(
                inc["id"],
                f"Auto-remediation failed after {len(actions)} attempt(s): "
                f"'{plan['action']} {plan['target']}' did not restore the site. "
                f"Human action needed.",
            )
            continue

        if AUTO_REMEDIATE:
            _run_action(patient, patient_url, inc["id"], plan)
        else:
            storage.create_action(patient, inc["id"], plan["action"], plan["target"], "pending_approval")
