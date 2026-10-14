#!/usr/bin/env bash
# Attacks #4 & #5 — resource exhaustion, run ON the ShopSmart host (the patient).
# These are self-inflicted chaos (not network attacks): they push host CPU/memory
# up so the Doctor sees high_memory / high_cpu and reacts.
#
# How to run on the AWS box:  ssh in (or AWS SSM), then:
#     sudo apt-get install -y stress-ng     # once
#     bash stress_host.sh mem               # memory exhaustion  -> high_memory -> Doctor restarts app
#     bash stress_host.sh cpu               # CPU saturation     -> high_cpu    -> Doctor escalates
#
# Watch the Engineer view: the matching tile goes red, an incident opens, and
# (for memory) the Doctor auto-restarts shopsmart_app. Ctrl-C or `pkill stress-ng` to stop.
set -euo pipefail
MODE="${1:-mem}"

case "$MODE" in
  mem)
    echo "[*] eating memory for 90s (watch 'high_memory' open on the dashboard)…"
    stress-ng --vm 2 --vm-bytes 90% --vm-keep --timeout 90s
    ;;
  cpu)
    echo "[*] saturating all CPUs for 90s (watch 'high_cpu' open)…"
    stress-ng --cpu 0 --timeout 90s
    ;;
  disk)
    echo "[*] filling a scratch file for 90s (watch 'high_disk')…"
    stress-ng --hdd 2 --hdd-bytes 80% --timeout 90s
    ;;
  *)
    echo "usage: bash stress_host.sh [mem|cpu|disk]"; exit 1;;
esac
echo "[*] done — load released. The Doctor should resolve the incident on the next poll."
