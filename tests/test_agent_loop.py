"""The agent loop, tested offline.

* A scripted fake client plays the role of the model, so we can test loop
  mechanics, guardrails, and limits deterministically.
* One test runs the *real* OpenAI SDK against an intercepted HTTP transport to
  verify the request we send and that we parse real-shaped responses.

These tests do not judge model quality — that's what `python run.py eval` is for.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from customer_support_agent.agent import SupportAgent, TicketNotFoundError


def call(name: str, call_id: str, **args) -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call",
        name=name,
        call_id=call_id,
        arguments=json.dumps(args),
        id=f"fc_{call_id}",
        status="completed",
    )


def resolution(**overrides) -> dict:
    base = {
        "intent": "delivery_problem",
        "secondary_intents": ["refund_request"],
        "internal_summary": "Order 1043 presumed lost; third contact; refund approval requested.",
        "sentiment": {"label": "angry", "indicators": ["third message about order 1043"], "repeat_contact": True},
        "evidence": [{"source": "shopify_order:1043", "detail": "No scan since 2026-10-23"}],
        "policy_references": ["policies/shipping-policy.md"],
        "needs_clarification": False,
        "clarifying_question": None,
        "escalation": {
            "required": True,
            "reason": "Presumed lost + repeat contact",
            "priority": "high",
            "team": "Support Lead",
        },
        "draft_response": "Hi Jordan, I'm sorry... I've passed a refund request for order 1043 to our support lead "
        "for review. Northstar Supply Support",
    }
    base.update(overrides)
    return base


class ScriptedClient:
    """Mimics `client.responses.create` by returning pre-written turns."""

    def __init__(self, turns: list[list[SimpleNamespace]]):
        self.turns = list(turns)
        self.requests: list[dict] = []
        self.responses = self

    def create(self, **kwargs):
        self.requests.append({**kwargs, "input": list(kwargs["input"])})  # snapshot; the agent keeps appending
        output = self.turns.pop(0) if self.turns else [call("search_knowledge_base", "loop", query="x", category="all")]
        usage = SimpleNamespace(input_tokens=100, output_tokens=10)
        return SimpleNamespace(output=output, usage=usage)


def test_full_frustrated_customer_flow(services):
    client = ScriptedClient(
        [
            [call("get_zendesk_ticket_history", "c1", ticket_id="5002")],
            [
                call("get_shopify_order", "c2", order_id="1043"),
                call("read_knowledge_document", "c3", document_id="policies/shipping-policy.md"),
            ],
            [
                call(
                    "request_human_approval",
                    "c4",
                    action_type="refund",
                    order_id="1043",
                    amount_usd=124.0,
                    reason="Presumed lost: 10 business days past estimate",
                )
            ],
            [call("submit_case_resolution", "c5", **resolution())],
        ]
    )
    result = SupportAgent(services, client=client).run("5002")

    assert result.status == "completed"
    assert result.tools_used == [
        "get_zendesk_ticket_history",
        "get_shopify_order",
        "read_knowledge_document",
        "request_human_approval",
    ]
    assert len(result.approval_requests) == 1 and result.approval_requests[0].status == "pending_human_review"
    assert result.escalation.required and result.escalation.priority == "high"
    assert {"E6", "E7"} <= set(result.escalation.rule_ids)
    assert result.policy_references == ["policies/shipping-policy.md"]
    assert result.usage.model_calls == 4

    # Every tool output was sent back to the model, matched by call_id.
    last_input = client.requests[-1]["input"]
    outputs = {i["call_id"] for i in last_input if isinstance(i, dict) and i.get("type") == "function_call_output"}
    assert outputs == {"c1", "c2", "c3", "c4"}
    # Request settings
    first = client.requests[0]
    assert first["store"] is False and first["tool_choice"] == "required"
    assert "<customer_message>" in first["input"][0]["content"]


def test_rules_override_a_model_that_forgets_to_escalate(services):
    client = ScriptedClient(
        [
            [
                call(
                    "submit_case_resolution",
                    "c1",
                    **resolution(escalation={"required": False, "reason": None, "priority": None, "team": None}),
                )
            ],
        ]
    )
    result = SupportAgent(services, client=client).run("5002")
    assert result.escalation.required and result.escalation.overridden_by_rules
    assert "E7" in result.escalation.rule_ids
    assert any(e.kind == "escalation_override" for e in result.guardrail_events)


def test_attempted_refund_is_blocked_and_flagged(services):
    client = ScriptedClient(
        [
            [call("issue_refund", "c1", order_id="1043", amount=124)],
            [call("submit_case_resolution", "c2", **resolution(policy_references=[]))],
        ]
    )
    result = SupportAgent(services, client=client).run("5002")
    assert result.trace[0].blocked and result.trace[0].error_code == "permission_denied"
    assert any(e.kind == "blocked_tool" for e in result.guardrail_events)
    assert "E8" in result.escalation.rule_ids


def test_prohibited_claim_in_draft_is_flagged(services):
    client = ScriptedClient(
        [
            [
                call(
                    "submit_case_resolution",
                    "c1",
                    **resolution(draft_response="Hi Jordan, I've issued a full refund of $124.00 to your card."),
                )
            ]
        ]
    )
    result = SupportAgent(services, client=client).run("5002")
    assert any(e.kind == "prohibited_claim" for e in result.guardrail_events)


def test_unretrieved_citations_are_removed(services):
    client = ScriptedClient(
        [[call("submit_case_resolution", "c1", **resolution(policy_references=["policies/return-policy.md"]))]]
    )
    result = SupportAgent(services, client=client).run("5002")
    assert result.policy_references == []
    assert any(e.kind == "unverified_reference" for e in result.guardrail_events)


def test_invalid_resolution_gets_a_second_chance(services):
    client = ScriptedClient(
        [
            [call("submit_case_resolution", "c1", intent="not_a_real_intent")],
            [call("submit_case_resolution", "c2", **resolution())],
        ]
    )
    result = SupportAgent(services, client=client).run("5002")
    assert result.status == "completed"
    error_output = next(
        i
        for i in client.requests[1]["input"]
        if isinstance(i, dict) and i.get("type") == "function_call_output" and i["call_id"] == "c1"
    )
    assert "invalid_resolution" in error_output["output"]


def test_iteration_limit_fails_safe(services):
    services.settings.max_iterations = 3
    result = SupportAgent(services, client=ScriptedClient([])).run("5001")
    assert result.status == "iteration_limit"
    assert result.escalation.required and result.draft_response == ""
    assert result.usage.model_calls == 3


def test_tool_budget(services):
    services.settings.max_tool_calls = 2
    turns = [[call("search_knowledge_base", f"s{i}", query="refund", category="all")] for i in range(3)]
    turns.append([call("submit_case_resolution", "done", **resolution(policy_references=[]))])
    result = SupportAgent(services, client=ScriptedClient(turns)).run("5002")
    assert len(result.trace) == 2 and result.status == "completed"


def test_api_error_returns_escalated_result(services):
    class Boom:
        responses = None

        def __init__(self):
            self.responses = self

        def create(self, **kwargs):
            raise RuntimeError("401 invalid api key")

    result = SupportAgent(services, client=Boom()).run("5001")
    assert result.status == "error" and "invalid api key" in result.error
    assert result.escalation.required


def test_tool_failure_triggers_escalation(services):
    client = ScriptedClient(
        [
            [call("get_shopify_order", "c1", order_id="1042")],
            [
                call(
                    "submit_case_resolution",
                    "c2",
                    **resolution(
                        intent="order_status",
                        secondary_intents=[],
                        policy_references=[],
                        escalation={"required": False, "reason": None, "priority": None, "team": None},
                        draft_response="Hi Maya, our team is checking on order 1042 and will follow up shortly.",
                    ),
                )
            ],
        ]
    )
    result = SupportAgent(services, client=client).run(
        "5001", fault_injection={"get_shopify_order": "backend_unavailable"}
    )
    assert result.trace[0].error_code == "backend_unavailable"
    assert result.escalation.required and "E9" in result.escalation.rule_ids


def test_unknown_ticket(services):
    with pytest.raises(TicketNotFoundError):
        SupportAgent(services, client=ScriptedClient([])).run("0000")


def test_logs_have_trace_id_and_no_emails(services):
    client = ScriptedClient(
        [
            [call("get_shopify_order", "c1", order_id="1042")],
            [call("submit_case_resolution", "c2", **resolution(policy_references=[]))],
        ]
    )
    result = SupportAgent(services, client=client).run("5001")
    events = services.logger.events
    assert {e["trace_id"] for e in events} == {result.trace_id}
    assert [e["event"] for e in events][0] == "run_started" and events[-1]["event"] == "run_completed"
    assert "maya.chen@example.com" not in json.dumps(events)


# ---------------------------------------------------------------------------
# Real OpenAI SDK, intercepted HTTP — verifies request format & response parsing
# ---------------------------------------------------------------------------


def _response_json(output: list[dict], n: int) -> dict:
    return {
        "id": f"resp_{n}",
        "object": "response",
        "created_at": 1789000000,
        "status": "completed",
        "model": "gpt-5.4-mini",
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "required",
        "tools": [],
        "text": {"format": {"type": "text"}},
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "metadata": {},
        "temperature": 1.0,
        "top_p": 1.0,
        "truncation": "disabled",
        "usage": {
            "input_tokens": 50,
            "output_tokens": 5,
            "total_tokens": 55,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def test_real_sdk_round_trip(services):
    from openai import OpenAI

    turns = [
        [
            {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "gAAAA-opaque"},
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "get_shopify_order",
                "arguments": json.dumps({"order_id": "1042"}),
                "status": "completed",
            },
        ],
        [
            {
                "type": "function_call",
                "id": "fc_2",
                "call_id": "call_2",
                "name": "submit_case_resolution",
                "arguments": json.dumps(
                    resolution(
                        intent="order_status",
                        secondary_intents=[],
                        policy_references=[],
                        escalation={"required": False, "reason": None, "priority": None, "team": None},
                    )
                ),
                "status": "completed",
            }
        ],
    ]
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/responses")
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=_response_json(turns[len(sent) - 1], len(sent)))

    client = OpenAI(api_key="sk-test-not-real", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = SupportAgent(services, client=client).run("5001")

    assert result.status == "completed" and result.tools_used == ["get_shopify_order"]
    first, second = sent
    assert first["model"] == services.settings.openai_model
    assert first["store"] is False and first["include"] == ["reasoning.encrypted_content"]
    assert {t["name"] for t in first["tools"]} >= {"get_shopify_order", "submit_case_resolution"}
    # Turn 2 must replay the reasoning item, the function call, and our function_call_output.
    types = [item.get("type") for item in second["input"]]
    assert types[1:] == ["reasoning", "function_call", "function_call_output"]
    replayed = second["input"][2]
    assert replayed["call_id"] == "call_1" and replayed["name"] == "get_shopify_order"
    assert json.loads(second["input"][3]["output"])["ok"] is True
