"""The only "action" tool the agent has: asking a human for approval.

It validates the request through `ApprovalQueue.create` (which re-checks order
ownership and amounts in code) and returns a pending request ID. It never
executes anything.
"""

from __future__ import annotations

from typing import Any

from ..approvals import ApprovalError
from .base import ToolContext, fail, ok
from .shopify import normalize_order_number


def request_human_approval(
    ctx: ToolContext, action_type: str, order_id: str, amount_usd: float | None, reason: str
) -> dict[str, Any]:
    number = normalize_order_number(order_id)
    order = ctx.services.shopify.get_order(number) if number else None
    try:
        request = ctx.services.approvals.create(
            trace_id=ctx.trace_id,
            ticket_id=ctx.ticket_id,
            order=order,
            requester_email=ctx.requester_email,
            action_type=action_type,
            amount_usd=amount_usd,
            reason=reason,
        )
    except ApprovalError as exc:
        ctx.services.logger.log(ctx.trace_id, "approval_rejected", action_type=action_type, reason=str(exc))
        if "owned by the verified requester" in str(exc) and order is not None:
            ctx.security_blocks.append(f"approval requested for order #{number} not owned by requester")
        return fail("approval_request_rejected", str(exc))
    if request.id not in ctx.approval_ids:
        ctx.approval_ids.append(request.id)
    ctx.services.logger.log(
        ctx.trace_id,
        "approval_requested",
        approval_id=request.id,
        action_type=request.action_type,
        order_number=request.order_number,
        amount_usd=request.amount_usd,
    )
    return ok(
        approval_request_id=request.id,
        status=request.status,
        executed=False,
        instructions=(
            "Nothing has been executed. Tell the customer the request is being reviewed by the team; "
            "do not say it is approved."
        ),
    )
