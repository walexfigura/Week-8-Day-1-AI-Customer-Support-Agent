"""Typed data models shared by the agent, tools, webhook handler, and evals.

Pydantic validates anything that crosses a trust boundary: the model's final
`submit_case_resolution` arguments, incoming webhook payloads, and eval cases.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Intent = Literal[
    "order_status",
    "shipping_estimate",
    "tracking",
    "delivery_problem",
    "return_request",
    "refund_request",
    "refund_status",
    "exchange_request",
    "product_question",
    "troubleshooting",
    "damaged_item",
    "human_request",
    "out_of_scope",
    "other",
]
INTENTS: tuple[str, ...] = Intent.__args__  # type: ignore[attr-defined]

SentimentLabel = Literal["calm", "neutral", "concerned", "frustrated", "angry"]
Priority = Literal["low", "normal", "high", "urgent"]
Team = Literal["Support Lead", "Billing", "Trust & Safety", "Warranty Team"]
TEAMS: tuple[str, ...] = Team.__args__  # type: ignore[attr-defined]
PRIORITY_ORDER = {"low": 0, "normal": 1, "high": 2, "urgent": 3}

ApprovalAction = Literal[
    "refund",
    "replacement",
    "return_exception",
    "order_cancellation",
    "order_modification",
    "store_credit",
]
APPROVAL_ACTIONS: tuple[str, ...] = ApprovalAction.__args__  # type: ignore[attr-defined]


class Sentiment(BaseModel):
    label: SentimentLabel = "neutral"
    indicators: list[str] = Field(default_factory=list)
    repeat_contact: bool = False


class EvidenceItem(BaseModel):
    source: str  # e.g. "shopify_order:1043", "zendesk_ticket:5002", "kb:policies/return-policy.md"
    detail: str


class Escalation(BaseModel):
    required: bool = False
    reason: str | None = None
    priority: Priority | None = None
    team: Team | None = None
    # Filled in by the application, never by the model:
    overridden_by_rules: bool = False
    rule_ids: list[str] = Field(default_factory=list)


class CaseResolution(BaseModel):
    """What the model submits through the `submit_case_resolution` tool."""

    model_config = ConfigDict(extra="ignore")

    intent: Intent
    secondary_intents: list[Intent] = Field(default_factory=list)
    internal_summary: str
    sentiment: Sentiment
    evidence: list[EvidenceItem] = Field(default_factory=list)
    policy_references: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarifying_question: str | None = None
    escalation: Escalation
    draft_response: str


class ToolCallRecord(BaseModel):
    step: int
    name: str
    arguments: dict[str, Any]
    ok: bool
    summary: str
    error_code: str | None = None
    duration_ms: int = 0
    blocked: bool = False


class GuardrailEvent(BaseModel):
    kind: str  # e.g. "blocked_tool", "escalation_override", "unverified_reference"
    detail: str


class ApprovalRequest(BaseModel):
    id: str
    trace_id: str
    ticket_id: str
    order_number: str
    action_type: ApprovalAction
    amount_usd: float | None = None
    reason: str
    status: Literal["pending_human_review", "approved", "rejected", "executed_simulation"] = "pending_human_review"
    requested_by: str = "ai_agent"
    created_at: str
    decided_by: str | None = None
    decided_at: str | None = None
    decision_note: str | None = None


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0


class AgentResult(BaseModel):
    """The complete, reviewable output of one agent run."""

    trace_id: str
    ticket_id: str
    status: Literal["completed", "iteration_limit", "error"]
    model: str
    intent: Intent = "other"
    secondary_intents: list[Intent] = Field(default_factory=list)
    internal_summary: str = ""
    sentiment: Sentiment = Field(default_factory=Sentiment)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    policy_references: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarifying_question: str | None = None
    escalation: Escalation = Field(default_factory=Escalation)
    draft_response: str = ""
    approval_requests: list[ApprovalRequest] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    trace: list[ToolCallRecord] = Field(default_factory=list)
    guardrail_events: list[GuardrailEvent] = Field(default_factory=list)
    retrieved_documents: list[str] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    error: str | None = None
    draft_sent: Literal[False] = False  # the agent never sends anything
