"""Zendesk tools and backends.

Like the Shopify module: backends return one normalized ticket shape, and the
tool functions decide what the model may see.

Normalized ticket:
    {"id", "subject", "status", "priority", "channel", "tags", "created_at",
     "updated_at", "requester": {"id", "name", "email"},
     "comments": [{"id", "author_id", "author_name", "author_role", "public",
                   "created_at", "body"}]}

`LiveZendeskBackend` targets the Zendesk Support REST API (v2). It is
implementation-ready but has NOT been verified against a live account here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

import httpx

from ..guardrails import detect_signals
from ..logging_utils import mask_email
from .base import ToolContext, fail, ok, untrusted
from .http_utils import request_json


class ZendeskBackend(Protocol):
    name: str

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None: ...
    def list_requester_tickets(self, requester_id: str) -> list[dict[str, Any]]: ...
    def record_incoming_comment(self, ticket_id: str, comment: dict[str, Any]) -> bool: ...


# ---------------------------------------------------------------------------
# Mock backend
# ---------------------------------------------------------------------------


class MockZendeskBackend:
    name = "mock"

    def __init__(self, data_dir: Path):
        path = data_dir / "zendesk-tickets.json"
        if not path.exists():
            raise FileNotFoundError(f"Mock data file missing: {path}")
        raw = json.loads(path.read_text("utf-8"))
        self.users = {u["id"]: u for u in raw["users"]}
        self._tickets = {t["id"]: t for t in raw["tickets"]}

    def _normalize(self, ticket: dict[str, Any]) -> dict[str, Any]:
        requester = self.users[ticket["requester_id"]]
        comments = []
        for c in sorted(ticket["comments"], key=lambda c: c["created_at"]):
            author = self.users.get(c["author_id"], {"name": "Unknown", "role": "end-user"})
            comments.append(
                {
                    "id": c["id"],
                    "author_id": c["author_id"],
                    "author_name": author["name"],
                    "author_role": "staff" if author["role"] in {"agent", "admin"} else "customer",
                    "public": c.get("public", True),
                    "created_at": c["created_at"],
                    "body": c["body"],
                }
            )
        return {
            **{
                k: ticket[k]
                for k in ("id", "subject", "status", "priority", "channel", "tags", "created_at", "updated_at")
            },
            "requester": {"id": requester["id"], "name": requester["name"], "email": requester["email"]},
            "comments": comments,
        }

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        ticket = self._tickets.get(str(ticket_id).strip().lstrip("#"))
        return self._normalize(ticket) if ticket else None

    def list_requester_tickets(self, requester_id: str) -> list[dict[str, Any]]:
        return [self._normalize(t) for t in self._tickets.values() if t["requester_id"] == requester_id]

    def record_incoming_comment(self, ticket_id: str, comment: dict[str, Any]) -> bool:
        """Simulate Zendesk storing a comment that arrived via webhook (in memory only)."""
        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            raise KeyError(ticket_id)
        if any(c["id"] == comment["id"] for c in ticket["comments"]):
            return False
        ticket["comments"].append(
            {
                "id": comment["id"],
                "author_id": comment["author_id"],
                "public": comment.get("public", True),
                "created_at": comment["created_at"],
                "body": comment["body"],
            }
        )
        ticket["updated_at"] = comment["created_at"]
        if ticket["status"] in {"pending", "solved"}:
            ticket["status"] = "open"  # Zendesk reopens tickets when the customer replies
        return True


# ---------------------------------------------------------------------------
# Live backend (Zendesk Support REST API v2)
# ---------------------------------------------------------------------------


def normalize_zendesk_ticket(
    ticket: dict[str, Any], comments: list[dict[str, Any]], users: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Map `GET /api/v2/tickets/{id}` + comments + users into the normalized shape."""
    requester = users.get(str(ticket.get("requester_id")), {})
    normalized_comments = []
    for c in comments:
        author = users.get(str(c.get("author_id")), {})
        normalized_comments.append(
            {
                "id": str(c.get("id")),
                "author_id": str(c.get("author_id")),
                "author_name": author.get("name", "Unknown"),
                "author_role": "staff" if author.get("role") in {"agent", "admin"} else "customer",
                "public": c.get("public", True),
                "created_at": c.get("created_at"),
                "body": c.get("plain_body") or c.get("body", ""),
            }
        )
    return {
        "id": str(ticket.get("id")),
        "subject": ticket.get("subject") or "",
        "status": ticket.get("status"),
        "priority": ticket.get("priority"),
        "channel": (ticket.get("via") or {}).get("channel"),
        "tags": ticket.get("tags", []),
        "created_at": ticket.get("created_at"),
        "updated_at": ticket.get("updated_at"),
        "requester": {
            "id": str(ticket.get("requester_id")),
            "name": requester.get("name", ""),
            "email": requester.get("email", ""),
        },
        "comments": sorted(normalized_comments, key=lambda c: c["created_at"] or ""),
    }


class LiveZendeskBackend:
    """Read-only Zendesk adapter using API-token auth ({email}/token:{api_token}).
    Untested against a real account in this repository."""

    name = "live"

    def __init__(self, subdomain: str, email: str, api_token: str, client: httpx.Client | None = None):
        if not (subdomain and email and api_token):
            raise ValueError("ZENDESK_SUBDOMAIN, ZENDESK_EMAIL and ZENDESK_API_TOKEN are required for the live backend")
        self.base = f"https://{subdomain}.zendesk.com/api/v2"
        self.client = client or httpx.Client(timeout=15.0, auth=(f"{email}/token", api_token))

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        return request_json(self.client, "GET", f"{self.base}{path}", params=params or None)

    def _users(self, ids: set[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        data = self._get("/users/show_many.json", ids=",".join(sorted(ids)))
        return {str(u["id"]): u for u in data.get("users", [])}

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        data = self._get(f"/tickets/{ticket_id}.json")
        if data.get("_not_found"):
            return None
        ticket = data["ticket"]
        comments = self._get(f"/tickets/{ticket_id}/comments.json").get("comments", [])
        ids = {str(ticket["requester_id"])} | {str(c["author_id"]) for c in comments}
        return normalize_zendesk_ticket(ticket, comments, self._users(ids))

    def list_requester_tickets(self, requester_id: str) -> list[dict[str, Any]]:
        data = self._get(f"/users/{requester_id}/tickets/requested.json", sort_by="updated_at", sort_order="desc")
        users = self._users({requester_id})
        return [normalize_zendesk_ticket(t, [], users) for t in data.get("tickets", [])[:10]]

    def record_incoming_comment(self, ticket_id: str, comment: dict[str, Any]) -> bool:
        # In production the comment already exists in Zendesk when the webhook fires.
        return False


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------


def _load_accessible_ticket(ctx: ToolContext, ticket_id: str) -> tuple[dict | None, dict | None]:
    """The agent may read the current ticket and other tickets from the same requester only."""
    ticket = ctx.services.zendesk.get_ticket(str(ticket_id).strip().lstrip("#"))
    if ticket is None:
        return None, fail("ticket_not_found", f"Ticket {ticket_id} was not found.")
    if ticket["requester"]["id"] != ctx.requester_id:
        ctx.security_blocks.append(f"access to ticket {ticket_id} from another requester")
        ctx.services.logger.log(ctx.trace_id, "ticket_access_denied", ticket_id=str(ticket_id))
        return None, fail("ticket_not_accessible", "That ticket belongs to a different requester.")
    return ticket, None


def _present_comment(c: dict[str, Any]) -> dict[str, Any]:
    body = c["body"] if c["author_role"] == "staff" else untrusted(c["body"])
    return {
        "comment_id": c["id"],
        "author": c["author_role"],
        "author_first_name": c["author_name"].split(" ")[0],
        "public": c["public"],
        "created_at": c["created_at"],
        "body": body,
    }


def get_zendesk_ticket(ctx: ToolContext, ticket_id: str) -> dict[str, Any]:
    ticket, error = _load_accessible_ticket(ctx, ticket_id)
    if error:
        return error
    customer_comments = [c for c in ticket["comments"] if c["author_role"] == "customer"]
    return ok(
        ticket={
            "ticket_id": ticket["id"],
            "subject": untrusted(ticket["subject"]),
            "status": ticket["status"],
            "priority": ticket["priority"],
            "channel": ticket["channel"],
            "tags": ticket["tags"],
            "created_at": ticket["created_at"],
            "updated_at": ticket["updated_at"],
            "requester_first_name": ticket["requester"]["name"].split(" ")[0],
            "requester_email_masked": mask_email(ticket["requester"]["email"]),
            "requester_identity": "verified via ticket requester email (bound by the application)",
            "comment_count": len(ticket["comments"]),
            "latest_customer_message": _present_comment(customer_comments[-1]) if customer_comments else None,
        }
    )


def get_zendesk_ticket_history(ctx: ToolContext, ticket_id: str) -> dict[str, Any]:
    ticket, error = _load_accessible_ticket(ctx, ticket_id)
    if error:
        return error
    signals = detect_signals(ticket["comments"])
    last_staff = next((c for c in reversed(ticket["comments"]) if c["author_role"] == "staff"), None)
    return ok(
        ticket_id=ticket["id"],
        status=ticket["status"],
        comments=[_present_comment(c) for c in ticket["comments"]],
        last_staff_reply_at=last_staff["created_at"] if last_staff else None,
        signals=signals.as_dict(),
    )


def list_requester_tickets(ctx: ToolContext, ticket_id: str) -> dict[str, Any]:
    ticket, error = _load_accessible_ticket(ctx, ticket_id)
    if error:
        return error
    others = [t for t in ctx.services.zendesk.list_requester_tickets(ctx.requester_id) if t["id"] != ticket["id"]]
    summaries = []
    for t in sorted(others, key=lambda t: t["updated_at"] or "", reverse=True):
        last = t["comments"][-1] if t["comments"] else None
        summaries.append(
            {
                "ticket_id": t["id"],
                "subject": untrusted(t["subject"]),
                "status": t["status"],
                "created_at": t["created_at"],
                "updated_at": t["updated_at"],
                "last_comment_by": last["author_role"] if last else None,
                "last_comment_preview": (
                    last["body"][:160]
                    if last and last["author_role"] == "staff"
                    else untrusted(last["body"][:160])
                    if last
                    else None
                ),
            }
        )
    return ok(current_ticket_id=ticket["id"], other_tickets=summaries)
