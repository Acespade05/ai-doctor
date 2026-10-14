# AI-SRE — Real attack playbook

These are the **real** failure scenarios AI-SRE is meant to detect and recover.
Each one maps to a signal the Doctor sees and an action it takes. Run them against
**your own** ShopSmart instance only — this is resilience / chaos testing of your
own infrastructure.

Set your target once:
```bash
export SITE=https://shopsmart-aisre.duckdns.org    # your public site
```

## Phase 1 — works with the current agent (restart-based recovery)

| # | Attack | Run from | Command | Doctor sees | Doctor does |
|---|--------|----------|---------|-------------|-------------|
| 4 | Memory exhaustion | **patient host** (ssh/SSM) | `bash attacks/stress_host.sh mem` | `high_memory` | **auto-restart app** |
| 5 | CPU saturation | **patient host** | `bash attacks/stress_host.sh cpu` | `high_cpu` | **escalate** (restart won't fix load) |
| 2 | Slowloris (slow-HTTP) | **laptop / Kali** | `python3 attacks/slowloris.py $HOST -p 443 --https -s 400`  ·  or Kali `slowhttptest` | `site_slow` → `site_down` | **auto-restart nginx** |
| — | Backend crash (baseline) | dashboard **Chaos Lab** | Stop backend (app) | `site_down` | **auto-restart app** |

### Typical demo flow (memory)
1. Open the dashboard, Engineer view.
2. On the AWS box: `bash attacks/stress_host.sh mem`.
3. Watch: Memory tile → red, a `high_memory` incident opens, the Doctor
   diagnoses it, pushes **restart shopsmart_app**, the next poll sees it healthy
   → incident flips to **resolved · auto-remediated** with an MTTR.
4. Switch to Founder/Executive to see the business/scorecard side update.

> CPU (#5) escalates on purpose: restarting rarely fixes a traffic-driven CPU
> spike, so the honest response is to flag a human (or rate-limit — see Phase 2).

## Phase 2 — needs the agent upgrade (coming next)

| # | Attack | Run from | Command | Needs |
|---|--------|----------|---------|-------|
| 1 | HTTP load flood | laptop | `k6 run attacks/k6_load_flood.js` (or `locust -f attacks/locustfile.py`) | agent reports **requests/sec + 5xx-rate**; new **rate_limit** action |
| 3 | HTTP flood / high-RPS | Kali | `hping3 --flood -S -p 443 $HOST`  ·  or `wrk -t8 -c400 -d60s $SITE` | **block_ip** / rate_limit action |

Phase 2 adds nginx access-log tailing to the agent (→ real RPS + error-rate, which
also fills the "requests/sec + error rate" chart) and two guarded actions,
`rate_limit` (nginx `limit_req`) and `block_ip` (nginx `deny`). All of that is
**agent + nginx config — not ShopSmart app code.**

## Safety
- Only ever target your own instance. Keep floods modest — large-scale flooding
  can trip AWS's acceptable-use policy even on your own box.
- Make sure port 8080 is firewalled to the Doctor only, and `AGENT_TOKEN` matches.
- To pause auto-healing while experimenting: set the Doctor's `AUTO_REMEDIATE=false`
  (it then only recommends + escalates) or set the agent to `AGENT_DRY_RUN=true`.
