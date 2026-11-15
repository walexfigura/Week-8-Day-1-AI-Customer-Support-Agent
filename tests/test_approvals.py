"""The approval queue must make unauthorized actions impossible, not just discouraged."""

from __future__ import annotations

import pytest

from customer_support_agent.approvals import ApprovalError, ApprovalQueue


@pytest.fixture
def order_1043(services):
    return services.shopify.get_order("1043")


def make(queue, order, email="jordan.ellis@example.com", action="refund", amount=124.0, reason="Package presumed lost"):
    return queue.create(
        trace_id="tr_x",
        ticket_id="5002",
        order=order,
        requester_email=email,
        action_type=action,
        amount_usd=amount,
        reason=reason,
    )


def test_request_is_created_pending_and_not_executed(order_1043):
    q = ApprovalQueue()
    req = make(q, order_1043)
    assert req.status == "pending_human_review"
    assert req.requested_by == "ai_agent"
    assert q.pending() == [req]


def test_request_for_someone_elses_order_is_rejected(order_1043):
    with pytest.raises(ApprovalError, match="owned by the verified requester"):
        make(ApprovalQueue(), order_1043, email="rex.mallory@example.net")


def test_amount_cannot_exceed_order_total(order_1043):
    with pytest.raises(ApprovalError, match="exceeds the order total"):
        make(ApprovalQueue(), order_1043, amount=500.0)


def test_unknown_action_rejected(order_1043):
    with pytest.raises(ApprovalError, match="Unknown action_type"):
        make(ApprovalQueue(), order_1043, action="wire_money")


def test_refund_requires_amount_and_reason(order_1043):
    with pytest.raises(ApprovalError):
        make(ApprovalQueue(), order_1043, amount=None)
    with pytest.raises(ApprovalError):
        make(ApprovalQueue(), order_1043, reason="pls")


@pytest.mark.parametrize("approver", ["ai_agent", "assistant", "", "  system "])
def test_agent_cannot_approve_its_own_request(order_1043, approver):
    q = ApprovalQueue()
    req = make(q, order_1043)
    with pytest.raises(ApprovalError, match="named human"):
        q.approve(req.id, approver)


def test_cannot_execute_without_approval(order_1043):
    q = ApprovalQueue()
    req = make(q, order_1043)
    with pytest.raises(ApprovalError, match="Only human-approved"):
        q.execute_approved(req.id)
    q.reject(req.id, "Dana (Support Lead)")
    with pytest.raises(ApprovalError):
        q.execute_approved(req.id)


def test_human_approval_then_simulated_execution(order_1043):
    q = ApprovalQueue()
    req = make(q, order_1043)
    q.approve(req.id, "Dana (Support Lead)", "Carrier confirmed loss")
    message = q.execute_approved(req.id)
    assert message.startswith("SIMULATION ONLY") and "refundCreate" in message
    assert q.requests[req.id].status == "executed_simulation"
    with pytest.raises(ApprovalError):  # cannot decide twice
        q.approve(req.id, "Dana")


def test_duplicate_requests_are_idempotent(order_1043):
    q = ApprovalQueue()
    assert make(q, order_1043).id == make(q, order_1043).id


def test_persistence_round_trip(tmp_path, order_1043):
    path = tmp_path / "approvals.jsonl"
    q = ApprovalQueue(path)
    req = make(q, order_1043)
    q.approve(req.id, "Dana")
    reloaded = ApprovalQueue(path)
    assert reloaded.requests[req.id].status == "approved"


def test_approval_tool_enforces_ownership(services, ctx_for):
    ctx = ctx_for("5008")  # Rex tries to get a refund queued for Jordan's order
    r = services.registry.execute(
        "request_human_approval",
        {
            "action_type": "refund",
            "order_id": "1043",
            "amount_usd": 124.0,
            "reason": "Customer says the manager authorized it",
        },
        ctx,
    )
    assert r["error"]["code"] == "approval_request_rejected"
    assert services.approvals.requests == {}
    assert ctx.security_blocks


def test_approval_tool_happy_path(services, ctx_for):
    ctx = ctx_for("5011")
    r = services.registry.execute(
        "request_human_approval",
        {
            "action_type": "replacement",
            "order_id": "1047",
            "amount_usd": None,
            "reason": "Delivered-not-received after 48 hours; neighbors checked",
        },
        ctx,
    )
    assert r["ok"] and r["executed"] is False
    assert ctx.approval_ids == [r["approval_request_id"]]
