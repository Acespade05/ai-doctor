# Attack #1 (alternative) — HTTP load flood with Locust
# Tool: Locust (https://locust.io) — run from YOUR laptop.
#
# Install:  pip install locust
# Run:      locust -f attacks/locustfile.py --host https://shopsmart-aisre.duckdns.org
#           then open http://localhost:8089 and set users=300, spawn-rate=20
# Headless: locust -f attacks/locustfile.py --host $SITE --users 300 --spawn-rate 20 -t 3m --headless
#
# Watch: CPU/latency climb on the Engineer view; a site_slow/site_down incident
# opens; the Doctor responds. SAFETY: your own site only.

from locust import HttpUser, task, between


class Shopper(HttpUser):
    wait_time = between(0.1, 0.5)

    @task(3)
    def home(self):
        self.client.get("/")

    @task(2)
    def products(self):
        self.client.get("/api/products")

    @task(1)
    def health(self):
        self.client.get("/health")
