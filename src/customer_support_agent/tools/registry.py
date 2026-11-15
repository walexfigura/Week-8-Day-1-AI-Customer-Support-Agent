"""Tool registry: JSON schemas for the model + permission-enforced execution.

This is where "the prompt says don't" becomes "the code won't":

* Only tools listed in `TOOL_SPECS` are offered to the model, and each has a
  permission level. There is no refund, cancel, send, or close tool at all.
* `execute()` refuses any call that is not registered. Calls to known
  sensitive operations (e.g. `issue_refund`) are recorded as blocked
  guardrail events, so a model that *tries* is visible in the trace.
* Arguments are parsed and validated before a handler runs; failures become
  structured errors the model can recover from.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from ..knowledge import KB_CATEGORIES
from ..models import APPROVAL_ACTIONS, INTENTS, TEAMS
from . import actions, knowledge_tools, shopify, zendesk
from .base import BackendAuthError, BackendUnavailableError, ToolContext, fail

Permission = Literal["read", "approval_request", "final"]

# Operations the agent must never perform. They are not implemented anywhere in
# this codebase; listing them lets us label attempted calls clearly.
RESTRICTED_OPERATIONS = {
    "issue_refund",
    "create_refund",
    "refund_order",
    "cancel_order",
    "modify_order",
    "update_order",
    "edit_order",
    "create_payment",
    "make_payment",
    "send_customer_message",
    "send_reply",
    "send_email",
    "close_ticket",
    "solve_ticket",
    "update_ticket",
    "approve_request",
    "approve_refund",
    "update_customer",
}
SUBMIT_TOOL = "submit_case_resolution"
MAX_OUTPUT_CHARS = 12000


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    permission: Permission
    handler: Callable[..., dict[str, Any]] | None


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    """Strict-mode JSON schema: every property required, no extras."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_NULLABLE_STR = {"type": ["string", "null"]}


def _resolution_schema(workflows: list[str]) -> dict[str, Any]:
    return _obj(
        {
            "intent": {"type": "string", "enum": list(INTENTS), "description": "Primary customer need."},
            "secondary_intents": {"type": "array", "items": {"type": "string", "enum": list(INTENTS)}},
            "internal_summary": {**_STR, "description": "1-3 sentences for staff: key facts and reasoning."},
            "sentiment": _obj(
                {
                    "label": {"type": "string", "enum": ["calm", "neutral", "concerned", "frustrated", "angry"]},
                    "indicators": {
                        "type": "array",
                        "items": _STR,
                        "description": "Concrete evidence, e.g. 'third message about the same order'.",
                    },
                    "repeat_contact": {"type": "boolean"},
                }
            ),
            "evidence": {
                "type": "array",
                "items": _obj(
                    {
                        "source": {
                            **_STR,
                            "description": "e.g. 'shopify_order:1043', 'zendesk_ticket:5002', 'kb:policies/shipping-policy.md'",
                        },
                        "detail": _STR,
                    }
                ),
            },
            "policy_references": {
                "type": "array",
                "items": _STR,
                "description": "Knowledge document ids you retrieved and relied on.",
            },
            "needs_clarification": {"type": "boolean"},
            "clarifying_question": _NULLABLE_STR,
            "escalation": _obj(
                {
                    "required": {"type": "boolean"},
                    "reason": _NULLABLE_STR,
                    "priority": {"type": ["string", "null"], "enum": ["low", "normal", "high", "urgent", None]},
                    "team": {"type": ["string", "null"], "enum": [*TEAMS, None]},
                }
            ),
            "draft_response": {
                **_STR,
                "description": "Customer-facing draft for human review. Not sent automatically.",
            },
        }
    )


def build_tool_specs(workflow_names: list[str]) -> list[ToolSpec]:
    order_arg = {"order_id": {**_STR, "description": "Order number the customer provided, e.g. '1042'."}}
    ticket_arg = {"ticket_id": {**_STR, "description": "Zendesk ticket id, e.g. '5002'."}}
    return [
        ToolSpec(
            "get_zendesk_ticket",
            "Read a Zendesk ticket's metadata and latest customer message. Only the current "
            "requester's tickets are accessible.",
            _obj(ticket_arg),
            "read",
            zendesk.get_zendesk_ticket,
        ),
        ToolSpec(
            "get_zendesk_ticket_history",
            "Read all comments on a ticket (oldest first) plus heuristic conversation signals "
            "(repeat contact, frustration indicators, explicit human request, injection attempts).",
            _obj(ticket_arg),
            "read",
            zendesk.get_zendesk_ticket_history,
        ),
        ToolSpec(
            "list_requester_tickets",
            "List the requester's other tickets (to spot repeat contacts or unresolved earlier conversations).",
            _obj(ticket_arg),
            "read",
            zendesk.list_requester_tickets,
        ),
        ToolSpec(
            "get_shopify_order",
            "Look up an order by number. Returns data only if the order belongs to the verified "
            "requester; includes a code-calculated delivery_assessment per shipment.",
            _obj(order_arg),
            "read",
            shopify.get_shopify_order,
        ),
        ToolSpec(
            "check_return_eligibility",
            "Calculate return eligibility for each item in an order (return window, final sale, "
            "damage window, warranty) using the official return policy.",
            _obj(order_arg),
            "read",
            shopify.check_return_eligibility,
        ),
        ToolSpec(
            "search_shopify_products",
            "Search the product catalog by name or keywords.",
            _obj({"query": _STR}),
            "read",
            shopify.search_shopify_products,
        ),
        ToolSpec(
            "get_shopify_product",
            "Get a product's specifications, care, warranty and troubleshooting by product id, SKU or handle.",
            _obj({"product_id": _STR}),
            "read",
            shopify.get_shopify_product,
        ),
        ToolSpec(
            "search_knowledge_base",
            "Keyword search over Northstar's policies, FAQs and procedures. Returns ranked snippets with document ids.",
            _obj({"query": _STR, "category": {"type": "string", "enum": ["all", *KB_CATEGORIES]}}),
            "read",
            knowledge_tools.search_knowledge_base,
        ),
        ToolSpec(
            "read_knowledge_document",
            "Read a full knowledge document by id, e.g. 'policies/return-policy.md'.",
            _obj({"document_id": _STR}),
            "read",
            knowledge_tools.read_knowledge_document,
        ),
        ToolSpec(
            "read_workflow",
            "Load the step-by-step support procedure for a request type.",
            _obj({"workflow_name": {"type": "string", "enum": workflow_names}}),
            "read",
            knowledge_tools.read_workflow,
        ),
        ToolSpec(
            "request_human_approval",
            "Queue a refund, replacement, cancellation, order change, store credit, or return "
            "exception for a human to decide. Never executes anything.",
            _obj(
                {
                    "action_type": {"type": "string", "enum": list(APPROVAL_ACTIONS)},
                    "order_id": _STR,
                    "amount_usd": {
                        "type": ["number", "null"],
                        "description": "From the order record; null if not monetary.",
                    },
                    "reason": _STR,
                }
            ),
            "approval_request",
            actions.request_human_approval,
        ),
        ToolSpec(
            SUBMIT_TOOL,
            "Submit the final structured decision and draft reply. Call exactly once, last.",
            _resolution_schema(workflow_names),
            "final",
            None,
        ),
    ]


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec]):
        self.specs = {s.name: s for s in specs}

    def openai_tools(self) -> list[dict[str, Any]]:
        """Function-tool definitions in the OpenAI Responses API format."""
        return [
            {
                "type": "function",
                "name": s.name,
                "description": s.description,
                "parameters": s.parameters,
                "strict": True,
            }
            for s in self.specs.values()
        ]

    def execute(self, name: str, raw_arguments: str | dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        """Run a tool by name with permission checks. Always returns a result dict."""
        spec = self.specs.get(name)
        if spec is None or spec.handler is None:
            code = "permission_denied" if name in RESTRICTED_OPERATIONS else "unknown_tool"
            return fail(
                code,
                f"'{name}' is not an available tool. The agent cannot perform this action; "
                "use request_human_approval for refunds, cancellations, or changes.",
            )
        try:
            args = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments)
        except json.JSONDecodeError:
            return fail("invalid_arguments", "Arguments were not valid JSON.")
        expected = set(spec.parameters["properties"])
        missing = expected - set(args)
        if missing - {"category"}:
            return fail("invalid_arguments", f"Missing required arguments: {', '.join(sorted(missing))}")
        unexpected = set(args) - expected
        if unexpected:
            return fail("invalid_arguments", f"Unexpected arguments: {', '.join(sorted(unexpected))}")

        injected = ctx.fault_injection.get(name)
        try:
            if injected:
                raise BackendUnavailableError(f"Injected fault for testing: {injected}")
            result = spec.handler(ctx, **args)
        except BackendUnavailableError as exc:
            ctx.tool_failures.append(name)
            return fail(
                "backend_unavailable",
                f"{exc}. Do not guess; tell the customer the team will follow up.",
                retryable=True,
            )
        except BackendAuthError as exc:
            ctx.tool_failures.append(name)
            return fail("backend_auth_error", str(exc))
        except Exception as exc:  # noqa: BLE001 - a tool bug must not crash the run
            ctx.tool_failures.append(name)
            return fail("tool_error", f"{exc.__class__.__name__}: {exc}")
        return result

    def timed_execute(
        self, name: str, raw_arguments: str | dict[str, Any], ctx: ToolContext
    ) -> tuple[dict[str, Any], int]:
        start = time.perf_counter()
        result = self.execute(name, raw_arguments, ctx)
        return result, int((time.perf_counter() - start) * 1000)


def serialize_result(result: dict[str, Any]) -> str:
    text = json.dumps(result, default=str)
    if len(text) > MAX_OUTPUT_CHARS:
        text = json.dumps({"ok": result.get("ok", True), "truncated": True, "partial": text[:MAX_OUTPUT_CHARS]})
    return text


def summarize_result(name: str, result: dict[str, Any]) -> str:
    """One-line, human-readable summary of a tool result for the CLI trace."""
    if not result.get("ok"):
        err = result.get("error", {})
        return f"{err.get('code')}: {err.get('message', '')[:90]}"
    if name == "get_shopify_order":
        o = result["order"]
        states = [s["delivery_assessment"]["state"] for s in o["shipments"]] or ["no shipments"]
        return (
            f"#{o['order_number']} {o['fulfillment_status']} / {o['financial_status']}; shipments: {', '.join(states)}"
        )
    if name == "check_return_eligibility":
        return "; ".join(f"{i['sku']}={i['eligibility']}" for i in result["items"])
    if name == "get_zendesk_ticket":
        t = result["ticket"]
        return f"ticket {t['ticket_id']} ({t['status']}), {t['comment_count']} comments"
    if name == "get_zendesk_ticket_history":
        s = result["signals"]
        detected = ", ".join(s["detected"]) or "none"
        return f"{len(result['comments'])} comments; {s['customer_message_count']} from customer; signals: {detected}"
    if name == "list_requester_tickets":
        others = result["other_tickets"]
        return f"{len(others)} other ticket(s)" + (
            ": " + ", ".join(f"{t['ticket_id']} ({t['status']})" for t in others) if others else ""
        )
    if name == "search_knowledge_base":
        return (
            "top: " + ", ".join(r["document_id"] for r in result["results"][:3]) if result["results"] else "no results"
        )
    if name == "read_knowledge_document":
        return f"read {result['document_id']}"
    if name == "read_workflow":
        return f"loaded workflow '{result['workflow']}'"
    if name in {"search_shopify_products"}:
        return ", ".join(f"{r['title']} ({r['product_id']})" for r in result["results"]) or "no matches"
    if name == "get_shopify_product":
        return f"{result['product']['title']} ({result['product']['sku']})"
    if name == "request_human_approval":
        return f"{result['approval_request_id']} {result['status']} (not executed)"
    return "ok"
