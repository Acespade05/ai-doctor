// Attack #1 — HTTP load flood (traffic spike)
// Tool: k6  (https://k6.io)  — run from YOUR laptop, not the server.
// Goal: drive request volume up so the Doctor sees latency/CPU climb.
//
// Install k6:  winget install k6  (Windows)  |  brew install k6 (mac)  |  apt install k6 (Kali/Debian)
// Run:         set SITE=https://shopsmart-aisre.duckdns.org   (Windows: set ; bash: export)
//              k6 run attacks/k6_load_flood.js
//
// Watch on the dashboard (Engineer view): CPU/latency rise, a site_slow or
// site_down incident opens, the Doctor diagnoses + (Phase 2) rate-limits.
// SAFETY: your own site only. Start small; raise VUs gradually.

import http from 'k6/http';
import { sleep } from 'k6';

const SITE = __ENV.SITE || 'http://localhost';

export const options = {
  stages: [
    { duration: '30s', target: 50 },   // warm up to 50 virtual users
    { duration: '1m',  target: 300 },  // ramp to 300 — the flood
    { duration: '1m',  target: 300 },  // hold the pressure
    { duration: '30s', target: 0 },    // back off (let the Doctor show recovery)
  ],
};

export default function () {
  http.get(`${SITE}/`);
  http.get(`${SITE}/api/products`);   // hit a dynamic route too
  sleep(0.2);
}
