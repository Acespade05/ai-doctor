# AI-SRE — live demo script (site breaks → heals itself)

The goal: an examiner watches the **real website go down and come back on its own**,
with the dashboard narrating what the AI did.

## 0. Pre-flight (do this BEFORE the examiner is watching)

**Agent (ShopSmart side) — the one switch that matters:**
In `docker-compose.yml` under `patient-metrics-agent` → `environment`:
```yaml
    AGENT_DRY_RUN: "false"     # MUST be false, or nothing actually stops/restarts
```
Then on the VM: `docker compose up -d --build`.
> If this is "true", the buttons only *log* ("DRY_RUN: would…") and the site never
> actually breaks. This is the #1 reason a demo looks dead.

**Doctor side — make detection snappy for the stage.** In the Doctor's `.env`:
```
AUTO_REMEDIATE=true
POLL_INTERVAL=5            # detect within ~5s (default 10)
REMEDIATION_COOLDOWN=8     # gap between the app-try and the nginx-try
```
Then: `docker compose up -d --build`.

**Checklist**
- [ ] Port 8080 open from the Doctor to the agent; `AGENT_TOKEN` identical on both sides.
- [ ] Dashboard loads, header shows **Doctor online**, Service map all green.
- [ ] The real site loads in a browser.
- [ ] Open TWO things side by side: (1) the **real site** (a 2nd tab or your phone),
      (2) the **dashboard** on the Engineer view.

## 1. The main demo — take the WHOLE site down (most dramatic)

1. **Show healthy state.** Dashboard Engineer view (green), and the site loading fine.
2. **Break it.** Chaos Lab → **Stop whole site (nginx)**.
3. **Show it's down.** Switch to the site tab, refresh → **"This site can't be reached"**.
   This is the moment the examiner sees the outage is real.
4. **Narrate the AI (back on the dashboard):**
   - a `site_down` incident opens (red), within ~5s
   - AI diagnosis card fills in: root cause → strategy → confidence
   - Live timeline: *Detected → Diagnosed → Executed restart app → (no effect) → Executed restart nginx*
5. **Show it healed.** ~15–25s after the break, refresh the site tab → **it's back**.
   Nobody touched it — the Doctor did it.
6. **Show the record.** Incident flips to **resolved · auto-remediated** with an MTTR.
   Switch to **Founder** (revenue-at-risk cleared) and **Executive** (scorecard updated).

> Why two restarts: the Doctor can't tell from a failed probe whether app or nginx
> died, so it restarts the app first, and when the site is still down, restarts nginx.
> That's the honest, real recovery path — point it out, it's a feature not a bug.

## 2. Backup demo — crash just the backend (faster, single step)

Chaos Lab → **Stop backend (app)** → site errors → Doctor restarts the app →
back in ~7–10s. Use this if you want a quick, clean single-action recovery.

## 3. If something hangs on stage
- Site didn't break → `AGENT_DRY_RUN` is still "true". (Can't fix live; use a backup slide.)
- Site down and NOT recovering after ~40s → click **Restart nginx** (or **Restart app**)
  in Chaos Lab yourself; it's the same guarded action the Doctor uses.
- Reset everything healthy: make sure both app and nginx are running
  (`docker start shopsmart_app shopsmart_nginx` on the VM).

## 4. What to say (talking points)
- "The site just went down for real — here's a customer's view."
- "No human has touched anything. Watch the dashboard."
- "The AI detected it in seconds, diagnosed the cause, and is restoring it."
- "It's back. Mean time to recovery: N seconds. A human on-call would take minutes."
- "And here's the business view — this is what the outage was costing per minute."

## 5. After the demo — restore safe defaults
Put `AGENT_DRY_RUN` back to `"true"` (or leave false if you keep demoing), and
raise `POLL_INTERVAL` back to 10 if you want fewer requests.
