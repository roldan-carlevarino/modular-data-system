"""Careers opportunity agent cron.

Triggers the backend to (1) fetch new market openings from the configured
sources and (2) queue anything unscored for AI fit-scoring on the Mac worker.

The fetch/score logic lives in the API (single source of truth), so this cron
just authenticates and calls the endpoints over HTTP using stdlib only.

Env (reuses the worker's credentials):
  API_BASE      Backend base URL           (default http://localhost:8000)
  KN_USERNAME   Admin username for login   (required to run)
  KN_PASSWORD   Admin password for login   (required to run)
"""

import json
import os
import urllib.parse
import urllib.request

API_BASE = os.getenv("API_BASE", "http://localhost:8000").rstrip("/")
USERNAME = os.getenv("KN_USERNAME", "")
PASSWORD = os.getenv("KN_PASSWORD", "")


def _login() -> str:
    data = urllib.parse.urlencode({"username": USERNAME, "password": PASSWORD}).encode()
    req = urllib.request.Request(
        f"{API_BASE}/auth/login", data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode())
    token = payload.get("access_token") or payload.get("token")
    if not token:
        raise RuntimeError("login response missing token")
    return token


def _post(path: str, token: str, body: dict):
    req = urllib.request.Request(
        f"{API_BASE}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def main():
    if not USERNAME or not PASSWORD:
        print("[careers] KN_USERNAME/KN_PASSWORD not set; skipping")
        return
    token = _login()
    fetched = _post("/careers/opportunities/fetch", token, {})
    print(f"[careers] fetched: {fetched.get('inserted')} new from "
          f"{fetched.get('sources')} source(s)")
    scored = _post("/careers/opportunities/rescore", token, {"scope": "unscored"})
    print(f"[careers] queued for scoring: {scored.get('enqueued')}")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    main()
