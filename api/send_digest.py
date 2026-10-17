"""Короткий вызов QStash в 09:00 МСК: отправка готового запаса."""

from __future__ import annotations

import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finderjob import daily  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        secret = os.environ.get("DIGEST_SEND_SECRET", "")
        supplied = self.headers.get("Authorization", "")
        if not secret:
            self._reply(503, {"error": "Digest endpoint is not configured"})
            return
        if not hmac.compare_digest(supplied, f"Bearer {secret}"):
            self._reply(401, {"error": "Unauthorized"})
            return
        try:
            users, vacancies = daily.send_prepared()
        except Exception as exc:
            print(f"Ошибка отправки дайджеста: {exc}")
            self._reply(503, {"error": "Digest is not ready"})
            return
        self._reply(200, {"users": users, "vacancies": vacancies})
