"""The record of everything the engine did, and for whom.

One line of JSON per action, appended, never rewritten. Each line carries the hash of the one before it, so
a line removed or edited afterwards breaks the chain at that place and `verify()` says where.

Two things never enter it:
  · a secret — every value is scrubbed against the service's own credentials and against key names that
    usually hold one;
  · a row of data — callers pass counts and column names. Should a list of rows arrive anyway, under a key
    that names it for what it is, it is replaced by its length.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any, Optional

from .models import utcnow

_SECRET_KEY = re.compile(r"pass(word|wd|phrase)|pwd|secret|token|dsn|authorization|api[_-]?key|credential", re.I)
# « pass » is a password when it holds text, and a count of checks that passed when it holds a number. Read
# as a secret either way, it wrote `"pass": "[redacted]"` — and `"passed": "[redacted]"` — into every
# validation summary of the trail: the one field that says whether the validation passed.
_PASS_KEY = re.compile(r"^pass$", re.I)
_ROW_KEYS = {"rows", "data", "sample", "records", "result_rows"}
_GENESIS = "0" * 64


class AuditLog:
    def __init__(self, directory: Path, secrets: Optional[list[str]] = None):
        self.path = directory / "audit.jsonl"
        directory.mkdir(parents=True, exist_ok=True)
        self._secrets = sorted((s for s in (secrets or []) if s and len(s) >= 4), key=len, reverse=True)
        self._lock = threading.Lock()
        self._seq, self._last = self._tail()

    def _tail(self) -> tuple[int, str]:
        if not self.path.exists():
            return 0, _GENESIS
        last = None
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    last = line
        if last is None:
            return 0, _GENESIS
        record = json.loads(last)
        return record["seq"], record["hash"]

    # ── scrubbing ───────────────────────────────────────────────────────────────────────────────────────────
    def _scrub(self, value: Any, key: str = "") -> Any:
        if key and (_SECRET_KEY.search(key) or (_PASS_KEY.match(key) and isinstance(value, str))):
            return "[redacted]"
        if isinstance(value, dict):
            return {k: self._scrub(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            if key in _ROW_KEYS:
                return f"[{len(value)} rows withheld]"
            return [self._scrub(v) for v in value]
        if isinstance(value, str):
            for secret in self._secrets:
                if secret in value:
                    value = value.replace(secret, "[redacted]")
            return value
        return value

    # ── writing ─────────────────────────────────────────────────────────────────────────────────────────────
    def record(self, *, action: str, user: str, request_id: str, actor: str = "ai", ai_model: str = "none",
               project_id: Optional[str] = None, datasets: Optional[list[str]] = None,
               sql: Optional[str] = None, dbt_files: Optional[list[dict[str, str]]] = None,
               superset_assets: Optional[list[dict[str, Any]]] = None,
               validation: Optional[dict[str, Any]] = None, deployment: Optional[dict[str, Any]] = None,
               outcome: str = "ok", detail: Optional[dict[str, Any]] = None,
               duration_ms: Optional[int] = None, metrics: Optional[list[str]] = None,
               visual_qa: Optional[dict[str, Any]] = None, git_commit: Optional[str] = None) -> dict[str, Any]:
        body = {
            "timestamp": utcnow(),
            "action": action,
            "actor": actor,                 # ai · human · system
            "user": user,                   # on whose behalf
            "ai_model": ai_model,
            "request_id": request_id,
            "project_id": project_id,
            "datasets": datasets or [],
            "metrics": metrics or [],       # the governed metrics the action read or used
            "sql": sql,
            "dbt_files": dbt_files or [],
            "superset_assets": superset_assets or [],
            "validation": validation,
            "visual_qa": visual_qa,         # what the render showed and what the visual checks found
            "git_commit": git_commit,       # the commit the action made, or acted on
            "deployment": deployment,
            "outcome": outcome,
            "duration_ms": duration_ms,
            "detail": detail or {},
        }
        body = self._scrub(body)
        with self._lock:
            self._seq += 1
            body = {"seq": self._seq, "prev_hash": self._last, **body}
            digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            body["hash"] = digest
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(body, ensure_ascii=False) + "\n")
            self._last = digest
        return body

    # ── reading ─────────────────────────────────────────────────────────────────────────────────────────────
    def read(self, request_id: Optional[str] = None, project_id: Optional[str] = None,
             action: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        found: list[dict[str, Any]] = []
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                if request_id and record.get("request_id") != request_id:
                    continue
                if project_id and record.get("project_id") != project_id:
                    continue
                if action and record.get("action") != action:
                    continue
                found.append(record)
        return found[-limit:]

    def verify(self) -> dict[str, Any]:
        """Walk the chain. A broken link names the first record that no longer follows from the one before."""
        previous, count = _GENESIS, 0
        if not self.path.exists():
            return {"ok": True, "records": 0}
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                claimed = record.pop("hash", None)
                digest = hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                if record.get("prev_hash") != previous or digest != claimed:
                    return {"ok": False, "records": count, "broken_at_seq": record.get("seq")}
                previous, count = claimed, count + 1
        return {"ok": True, "records": count}
