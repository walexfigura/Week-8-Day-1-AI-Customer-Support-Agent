"""Local Zendesk webhook simulator.

IMPORTANT: this does not open a port or receive anything from the internet. It
reads sample deliveries from `data/webhook-events.json` and runs them through
the same handler a real HTTPS endpoint would call:

    raw body + headers
      1. verify the HMAC signature
      2. validate the payload structure
      3. de-duplicate by event id (idempotency)
      4. ignore events we don't act on (staff comments, other event types)
      5. identify the ticket and load its Zendesk history
      6. invoke the agent
      7. return the decision (draft + escalation) — never post anything back

To host this for real you would wrap `WebhookHandler.handle()` in a small web
framework route behind HTTPS (see docs/integrating-zendesk.md).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from .agent import SupportAgent
from .models import AgentResult
from .services import Services

SIGNATURE_HEADER = "X-Zendesk-Webhook-Signature"
TIMESTAMP_HEADER = "X-Zendesk-Webhook-Signature-Timestamp"
# Used only when ZENDESK_WEBHOOK_SECRET is not set, so the verification step still runs locally.
LOCAL_DEMO_SECRET = "local-simulator-secret-not-for-production"
HANDLED_TYPES = {"zen:event-type:ticket.created", "zen:event-type:ticket.comment_added"}


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------


class CommentAuthor(BaseModel):
    id: str
    name: str = ""
    is_staff: bool = False


class EventComment(BaseModel):
    id: str
    body: str
    is_public: bool = True
    created_at: str
    author: CommentAuthor


class WebhookPayload(BaseModel):
    id: str = Field(min_length=1)
    type: str
    subject: str
    time: str
    detail: dict[str, Any] = Field(default_factory=dict)
    event: dict[str, Any] = Field(default_factory=dict)

    @field_validator("subject")
    @classmethod
    def _ticket_subject(cls, value: str) -> str:
        if not re.fullmatch(r"zen:ticket:\d+", value):
            raise ValueError("subject must look like 'zen:ticket:<id>'")
        return value

    @property
    def ticket_id(self) -> str:
        return self.subject.rsplit(":", 1)[1]

    @property
    def comment(self) -> EventComment | None:
        raw = self.event.get("comment")
        return EventComment.model_validate(raw) if raw else None


# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------


def sign(body: bytes, timestamp: str, secret: str) -> str:
    """Zendesk-style signature: base64(HMAC-SHA256(secret, timestamp + body))."""
    digest = hmac.new(secret.encode(), timestamp.encode() + body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def verify_signature(body: bytes, headers: dict[str, str], secret: str) -> bool:
    signature = headers.get(SIGNATURE_HEADER)
    timestamp = headers.get(TIMESTAMP_HEADER)
    if not signature or not timestamp:
        return False
    return hmac.compare_digest(sign(body, timestamp, secret), signature)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class IdempotencyStore:
    """Remembers processed event ids. Webhooks are delivered *at least once*, so
    duplicates are normal and must be ignored. Persisted to a JSON file so the
    duplicate check works across CLI runs; pass `path=None` for in-memory."""

    def __init__(self, path: Path | None = None):
        self.path = path
        self.seen: dict[str, str] = {}
        if path and path.exists():
            self.seen = json.loads(path.read_text(encoding="utf-8"))

    def contains(self, event_id: str) -> bool:
        return event_id in self.seen

    def mark(self, event_id: str) -> None:
        self.seen[event_id] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._save()

    def forget(self, event_id: str) -> None:
        self.seen.pop(event_id, None)
        self._save()

    def reset(self) -> None:
        self.seen = {}
        self._save()

    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.seen, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


@dataclass
class WebhookOutcome:
    status: Literal["processed", "validated", "duplicate", "ignored", "rejected", "failed"]
    event_id: str | None
    ticket_id: str | None
    steps: list[str] = field(default_factory=list)
    detail: str = ""
    result: AgentResult | None = None


class WebhookHandler:
    def __init__(self, services: Services, agent: SupportAgent | None, *, secret: str, idempotency: IdempotencyStore):
        self.services = services
        self.agent = agent  # None = run steps 1-5 only (offline)
        self.secret = secret
        self.idempotency = idempotency

    def handle(self, body: bytes, headers: dict[str, str]) -> WebhookOutcome:
        steps: list[str] = []

        # 1. Authenticate the sender.
        if not verify_signature(body, headers, self.secret):
            return WebhookOutcome("rejected", None, None, ["signature check failed"], "Invalid or missing signature")
        steps.append("signature verified")

        # 2. Validate structure.
        try:
            payload = WebhookPayload.model_validate_json(body)
            comment = payload.comment
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"]) or "payload"
            return WebhookOutcome(
                "rejected",
                None,
                None,
                steps + ["payload validation failed"],
                f"Invalid payload: {where}: {first['msg']}",
            )
        steps.append(f"payload valid ({payload.type})")

        # 3. Duplicate check.
        if self.idempotency.contains(payload.id):
            return WebhookOutcome(
                "duplicate",
                payload.id,
                payload.ticket_id,
                steps + ["duplicate event id"],
                f"Event {payload.id} was already processed at {self.idempotency.seen[payload.id]}; skipped.",
            )
        steps.append("event id not seen before")

        # 4. Filter events we don't act on.
        if payload.type not in HANDLED_TYPES:
            self.idempotency.mark(payload.id)
            return WebhookOutcome(
                "ignored", payload.id, payload.ticket_id, steps, f"Event type {payload.type} not handled"
            )
        if comment and comment.author.is_staff:
            self.idempotency.mark(payload.id)
            return WebhookOutcome(
                "ignored",
                payload.id,
                payload.ticket_id,
                steps + ["staff-authored comment"],
                "Comment written by staff; the agent only responds to customers (prevents reply loops).",
            )

        # 5. Identify the ticket and load history.
        ticket = self.services.zendesk.get_ticket(payload.ticket_id)
        if ticket is None:
            return WebhookOutcome(
                "rejected", payload.id, payload.ticket_id, steps, f"Ticket {payload.ticket_id} not found"
            )
        if comment:
            added = self.services.zendesk.record_incoming_comment(
                ticket["id"],
                {
                    "id": comment.id,
                    "author_id": comment.author.id,
                    "public": comment.is_public,
                    "created_at": comment.created_at,
                    "body": comment.body,
                },
            )
            steps.append("new comment added to mock ticket history" if added else "comment already in history")
            ticket = self.services.zendesk.get_ticket(payload.ticket_id) or ticket
        customer_msgs = sum(1 for c in ticket["comments"] if c["author_role"] == "customer")
        steps.append(
            f"ticket {ticket['id']} loaded: {len(ticket['comments'])} comments ({customer_msgs} from customer)"
        )

        if self.agent is None:
            return WebhookOutcome(
                "validated",
                payload.id,
                payload.ticket_id,
                steps + ["agent skipped (--no-agent)"],
                "Steps 1-5 completed without calling the model. Event NOT marked as processed.",
            )

        # 6. Invoke the agent. Mark processed only after success so a failure can be retried.
        event_kind = payload.type.rsplit(":", 1)[-1]
        result = self.agent.run(ticket["id"], trigger_comment_id=comment.id if comment else None, event_type=event_kind)
        steps.append(f"agent run {result.trace_id} finished: {result.status}")
        if result.status == "error":
            return WebhookOutcome("failed", payload.id, payload.ticket_id, steps, result.error or "agent error", result)
        self.idempotency.mark(payload.id)
        steps.append("event marked as processed")
        return WebhookOutcome(
            "processed", payload.id, payload.ticket_id, steps, "Draft and decision ready for human review.", result
        )


# ---------------------------------------------------------------------------
# Sample events
# ---------------------------------------------------------------------------


def load_sample_events(data_dir: Path) -> dict[str, dict[str, Any]]:
    path = data_dir / "webhook-events.json"
    if not path.exists():
        raise FileNotFoundError(f"Webhook sample file missing: {path}")
    return {e["name"]: e for e in json.loads(path.read_text(encoding="utf-8"))["events"]}


def simulate_delivery(event: dict[str, Any], secret: str) -> tuple[bytes, dict[str, str]]:
    """Produce the raw body and signed headers Zendesk would send for a sample event."""
    body = json.dumps(event["payload"]).encode()
    timestamp = event["payload"].get("time", "2026-11-10T00:00:00Z")
    return body, {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: sign(body, timestamp, secret),
        TIMESTAMP_HEADER: timestamp,
    }
