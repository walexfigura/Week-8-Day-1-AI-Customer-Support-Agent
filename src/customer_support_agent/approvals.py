"""Human-approval queue for sensitive actions.

The agent may *request* a refund, replacement, cancellation, order change, or
policy exception. It can never approve or execute one. This module enforces
that in code:

* `create()` validates every request (known action, verified order owner,
  sane amount) and stores it as `pending_human_review`.
* `approve()` / `reject()` refuse any approver that is the agent itself.
* `execute_approved()` refuses anything that is not approved, and even then
  only *simulates* the action — this teaching project never moves money.

Requests are kept in memory and, optionally, appended to a JSONL file so the
`python run.py approvals` command can show them after a run.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import APPROVAL_ACTIONS, ApprovalRequest

AGENT_IDENTITIES = {"ai_agent", "agent", "assistant", "system", "model"}

# What a real backend call would be, shown when a human-approved request is "executed".
SIMULATED_OPERATIONS = {
    "refund": "Shopify Admin GraphQL `refundCreate` mutation",
    "replacement": "Shopify `draftOrderCreate` (zero-priced replacement order)",
    "return_exception": "Shopify `returnCreate` with an exception note",
    "order_cancellation": "Shopify `orderCancel` mutation",
    "order_modification": "Shopify order editing (`orderEditBegin` / `orderEditCommit`)",
    "store_credit": "Shopify `storeCreditAccountCredit` mutation",
}


class ApprovalError(Exception):
    """Raised when an approval operation is not permitted."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ApprovalQueue:
    def __init__(self, store_path: Path | None = None):
        self.store_path = store_path
        self.requests: dict[str, ApprovalRequest] = {}
        if store_path and store_path.exists():
            self._load()

    # ------------------------------------------------------------------ storage
    def _load(self) -> None:
        assert self.store_path is not None
        for line in self.store_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                request = ApprovalRequest.model_validate(json.loads(line))
                self.requests[request.id] = request  # later lines supersede earlier ones

    def _persist(self, request: ApprovalRequest) -> None:
        if not self.store_path:
            return
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with self.store_path.open("a", encoding="utf-8") as handle:
            handle.write(request.model_dump_json() + "\n")

    # ----------------------------------------------------------------- creation
    def create(
        self,
        *,
        trace_id: str,
        ticket_id: str,
        order: dict[str, Any] | None,
        requester_email: str,
        action_type: str,
        amount_usd: float | None,
        reason: str,
    ) -> ApprovalRequest:
        """Validate and queue a request. Raises ApprovalError if it is not allowed."""
        if action_type not in APPROVAL_ACTIONS:
            raise ApprovalError(f"Unknown action_type '{action_type}'. Allowed: {', '.join(APPROVAL_ACTIONS)}")
        if order is None or order.get("customer", {}).get("email", "").lower() != requester_email.lower():
            raise ApprovalError("Approval requests can only be created for orders owned by the verified requester.")
        if not reason or len(reason.strip()) < 10:
            raise ApprovalError("A specific reason (at least 10 characters) is required.")
        total = float(order.get("totals", {}).get("total", 0) or 0)
        if amount_usd is not None:
            if amount_usd < 0:
                raise ApprovalError("amount_usd cannot be negative.")
            if amount_usd > total + 0.001:
                raise ApprovalError(f"amount_usd {amount_usd:.2f} exceeds the order total {total:.2f}.")
        if action_type in {"refund", "store_credit"} and amount_usd is None:
            raise ApprovalError(f"amount_usd is required for {action_type} requests.")

        order_number = str(order["order_number"])
        for existing in self.requests.values():  # idempotent within a ticket
            if (
                existing.ticket_id == ticket_id
                and existing.order_number == order_number
                and existing.action_type == action_type
                and existing.status == "pending_human_review"
            ):
                return existing

        request = ApprovalRequest(
            id="APR-" + secrets.token_hex(3).upper(),
            trace_id=trace_id,
            ticket_id=ticket_id,
            order_number=order_number,
            action_type=action_type,  # type: ignore[arg-type]
            amount_usd=round(amount_usd, 2) if amount_usd is not None else None,
            reason=reason.strip(),
            created_at=_now(),
        )
        self.requests[request.id] = request
        self._persist(request)
        return request

    # ---------------------------------------------------------------- decisions
    def _get(self, request_id: str) -> ApprovalRequest:
        if request_id not in self.requests:
            raise ApprovalError(f"No approval request with id {request_id}")
        return self.requests[request_id]

    def _decide(self, request_id: str, approver: str, status: str, note: str | None) -> ApprovalRequest:
        if not approver or approver.strip().lower() in AGENT_IDENTITIES:
            raise ApprovalError("Approvals must be decided by a named human, not by the agent.")
        request = self._get(request_id)
        if request.status != "pending_human_review":
            raise ApprovalError(f"{request_id} is already {request.status}.")
        updated = request.model_copy(
            update={
                "status": status,
                "decided_by": approver.strip(),
                "decided_at": _now(),
                "decision_note": note,
            }
        )
        self.requests[request_id] = updated
        self._persist(updated)
        return updated

    def approve(self, request_id: str, approver: str, note: str | None = None) -> ApprovalRequest:
        return self._decide(request_id, approver, "approved", note)

    def reject(self, request_id: str, approver: str, note: str | None = None) -> ApprovalRequest:
        return self._decide(request_id, approver, "rejected", note)

    def execute_approved(self, request_id: str) -> str:
        """Simulate executing an approved action. Nothing real happens."""
        request = self._get(request_id)
        if request.status != "approved":
            raise ApprovalError(f"{request_id} is '{request.status}'. Only human-approved requests can be executed.")
        executed = request.model_copy(update={"status": "executed_simulation"})
        self.requests[request_id] = executed
        self._persist(executed)
        operation = SIMULATED_OPERATIONS[request.action_type]
        amount = f" for ${request.amount_usd:.2f}" if request.amount_usd is not None else ""
        return (
            f"SIMULATION ONLY: would call {operation}{amount} on order #{request.order_number}. No API call was made."
        )

    def pending(self) -> list[ApprovalRequest]:
        return [r for r in self.requests.values() if r.status == "pending_human_review"]

    def for_trace(self, trace_id: str) -> list[ApprovalRequest]:
        return [r for r in self.requests.values() if r.trace_id == trace_id]
