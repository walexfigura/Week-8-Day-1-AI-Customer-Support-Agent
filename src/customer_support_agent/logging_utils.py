"""Structured JSON-lines logging with trace IDs and redaction.

Every agent run gets a `trace_id`. Every event (model request, tool call,
guardrail decision, approval) is written as one JSON object per line to
`logs/agent-YYYY-MM-DD.jsonl`, so you can follow a run with:

    grep tr_1a2b3c logs/agent-*.jsonl

Credentials are never logged, and email addresses are masked.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_SECRET_KEYS = re.compile(r"(api[_-]?key|token|secret|password|authorization)", re.I)
_SECRET_VALUES = re.compile(r"\b(sk-[A-Za-z0-9_-]{8,}|shpat_[A-Za-z0-9]{8,})\b")


def new_trace_id() -> str:
    return "tr_" + secrets.token_hex(5)


def mask_email(text: str) -> str:
    """maya.chen@example.com -> m***@example.com"""
    return _EMAIL_RE.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)


def redact(value: Any) -> Any:
    """Recursively mask emails, and drop anything that looks like a credential."""
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _SECRET_KEYS.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _SECRET_VALUES.sub("[REDACTED]", mask_email(value))
    return value


class TraceLogger:
    """Append-only JSONL logger. Set `enabled=False` to keep tests from writing files."""

    def __init__(self, log_dir: Path, enabled: bool = True):
        self.log_dir = log_dir
        self.enabled = enabled
        self.events: list[dict[str, Any]] = []  # in-memory copy, handy for tests

    @property
    def path(self) -> Path:
        return self.log_dir / f"agent-{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"

    def log(self, trace_id: str, event: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "trace_id": trace_id,
            "event": event,
            **redact(fields),
        }
        self.events.append(record)
        if not self.enabled:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
