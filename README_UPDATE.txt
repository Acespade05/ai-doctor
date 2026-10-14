AI-SRE — flood incident resolves cleanly (no false "escalated")
===============================================================
One file changed:
  remediation/engine.py -> a flood (block_ip) is never escalated. The Doctor keeps
                           the block refreshed while the attack continues, and the
                           incident resolves as "auto-remediated" once the harm
                           (429/5xx) clears. Container restarts still escalate after
                           failed attempts, unchanged.

  cd ~/Desktop/ai-doctor
  unzip -o ~/Downloads/ai-doctor-flood-noescalate.zip -d .
  docker compose up -d --build
