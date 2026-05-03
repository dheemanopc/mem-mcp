"""Load test for mem-mcp (T-9.9).

Run:
    poetry run locust -f tests/load/locustfile.py --host https://memsys.dheemantech.in --users 100 --spawn-rate 10

10 simulated tenants x 10 concurrent users each. Each user holds a Bearer JWT
fetched once from a fixture. Tasks weighted toward search (5x) over write (1x)
to mirror real read/write ratio.

NOTE: requires real JWTs from a staging Cognito pool. The fixture file path
defaults to tests/load/jwt_fixture.json — see docs/load_test_results.md for
how to generate it.
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

from locust import HttpUser, between, task

_FIXTURE = Path(os.environ.get("MEM_LOAD_JWT_FIXTURE", "tests/load/jwt_fixture.json"))


class MemMcpUser(HttpUser):
    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        if not _FIXTURE.exists():
            raise RuntimeError(
                f"JWT fixture not found at {_FIXTURE}; see docs/load_test_results.md"
            )
        fixtures = json.loads(_FIXTURE.read_text())
        # Pick a random tenant's JWT for this user
        creds = random.choice(fixtures["tenants"])
        self.client.headers["Authorization"] = f"Bearer {creds['jwt']}"
        self._tenant_label = creds.get("label", "unknown")

    @task(5)
    def search_memory(self) -> None:
        body = {
            "jsonrpc": "2.0",
            "id": "load-search",
            "method": "tools/call",
            "params": {
                "name": "memory.search",
                "arguments": {"query": random.choice(_QUERIES), "limit": 10},
            },
        }
        with self.client.post("/mcp", json=body, name="memory.search", catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"status={resp.status_code}")

    @task(1)
    def write_memory(self) -> None:
        body = {
            "jsonrpc": "2.0",
            "id": "load-write",
            "method": "tools/call",
            "params": {
                "name": "memory.write",
                "arguments": {
                    "content": f"load-test note {random.randint(0, 1_000_000)}",
                    "type": "note",
                    "tags": ["load-test"],
                },
            },
        }
        with self.client.post("/mcp", json=body, name="memory.write", catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"status={resp.status_code}")


_QUERIES = [
    "what did we decide about the database",
    "the deployment plan for next week",
    "our auth strategy",
    "the meeting notes from last sprint",
    "remind me about the API rate limits",
    "tags about backend project",
    "configuration choices",
    "test failures we investigated",
]
