"""Shared types for tools: the per-run context, errors, and result helpers.

Every tool returns a JSON-serializable dict shaped like either

    {"ok": true, ...data...}
    {"ok": false, "error": {"code": "...", "message": "...", "retryable": bool}}

so the model always gets a predictable structure — including when something
goes wrong — instead of a Python traceback or a silent empty string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # avoid import cycles at runtime
    from ..services import Services


class BackendUnavailableError(Exception):
    """A backend (mock or live API) could not be reached or returned a server error."""


class BackendAuthError(Exception):
    """Credentials are missing, invalid, or lack the required scopes."""


@dataclass
class ToolContext:
    """Facts the *application* binds to a run. The model cannot change these.

    `requester_email` comes from the Zendesk ticket, not from anything the model
    or the customer typed, so a message saying "I'm the owner of order 1043"
    cannot widen access.
    """

    services: Services
    trace_id: str
    ticket_id: str
    requester_id: str
    requester_email: str
    requester_name: str
    fault_injection: dict[str, str] = field(default_factory=dict)
    # Collected during the run, read by the agent afterwards:
    approval_ids: list[str] = field(default_factory=list)
    tool_failures: list[str] = field(default_factory=list)
    security_blocks: list[str] = field(default_factory=list)
    retrieved_documents: list[str] = field(default_factory=list)

    @property
    def requester_first_name(self) -> str:
        return (self.requester_name or "").split(" ")[0]


def ok(**data: Any) -> dict[str, Any]:
    return {"ok": True, **data}


def fail(code: str, message: str, retryable: bool = False, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "retryable": retryable, **extra}}


def untrusted(text: str | None) -> dict[str, Any] | None:
    """Wrap customer-controlled text so the model can see it is data, not instructions."""
    if text is None:
        return None
    return {"trust": "untrusted_customer_content", "text": text}
