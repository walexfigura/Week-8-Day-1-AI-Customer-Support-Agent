"""Deterministic guardrails: conversation signals, escalation rules, draft checks.

These run in plain Python, independent of the model:

1. `detect_signals` scans customer messages for transparent keyword-based
   signals (explicit human request, legal threat, safety issue, injection
   attempt, frustration indicators, repeat contact). They are *hints*, given to
   the model as context — not a sentiment score to be trusted blindly.
2. `enforce_escalation_rules` applies the non-negotiable rules from
   `agent/workflows/escalation.md` (marked ⚙ there) after the model finishes,
   and records every override so reviewers can see where the model fell short.
3. `check_draft` flags drafts that claim something the agent cannot have done
   (e.g. "I've issued your refund") or that contain email addresses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import PRIORITY_ORDER, Escalation, GuardrailEvent

_PATTERNS: dict[str, list[str]] = {
    "explicit_human_request": [
        r"\b(speak|talk|chat)\s+(to|with)\s+(a|an|some(one|body)|real|actual|your)?\s*"
        r"(human|person|manager|supervisor|representative|someone|somebody|agent)",
        r"\b(real|actual|live)\s+(person|human)\b",
        r"\bnot\s+a\s+bot\b",
        r"\b(your|a)\s+(manager|supervisor)\b",
        r"\bhuman\s+(agent|being|support)\b",
    ],
    "legal_threat": [
        r"\b(lawyer|attorney|lawsuit|sue\b|suing|legal action|small claims|attorney general)",
    ],
    "payment_dispute": [
        r"\bcharge\s*back\b|\bchargeback",
        r"\b(dispute|disputing)\s+(the|this|a)?\s*(charge|payment|transaction)",
        r"\b(charged|billed)\s+(twice|two times|double)",
    ],
    "security_or_fraud": [
        r"\bhacked\b",
        r"\baccount\s+(was\s+|has been\s+)?(compromised|taken over)",
        r"\bunauthori[sz]ed\s+(order|charge|access|purchase)",
        r"\b(fraud|fraudulent|stolen card|identity theft)\b",
    ],
    "safety_issue": [
        r"\boverheat",
        r"\btoo hot\b",
        r"\bhot to the touch\b",
        r"\bsmok(e|ing)\b",
        r"\bburn(ing|t|ed)?\b",
        r"\bsparks?\b",
        r"\bswell(ing|ed)?\b",
        r"\bmelt",
        r"\bcaught fire\b|\bon fire\b",
        r"\binjur",
        r"\belectric(al)? shock",
    ],
    "prompt_injection": [
        r"ignore\s+(all\s+|your\s+|any\s+|the\s+)?(previous\s+|prior\s+|above\s+)?(rules|instructions|policies)",
        r"system\s+override",
        r"\badmin(istrator)?\s+mode\b",
        r"\bdeveloper\s+mode\b",
        r"\byou are now\b",
        r"disregard\s+(the\s+|all\s+|your\s+)?(above|previous|prior|rules)",
        r"\bpre-?approved\b",
        r"\bjailbreak\b",
        r"reveal\s+(your\s+)?(system\s+)?prompt",
        r"note to (the )?ai",
        r"\bas an ai (assistant|model)\b.*\bmust\b",
    ],
    "refund_request": [r"\bmoney back\b", r"\brefund\b", r"\breimburse"],
    "claims_repeat_contact": [
        r"\b(two|three|four|five|several|multiple|\d+)\s+times\b",
        r"\b(second|third|fourth|fifth)\s+time\b",
        r"\bagain\b",
        r"\bstill\s+(has ?n[o']t|have ?n[o']t|not|no)\b",
        r"\bnobody\s+(has\s+)?(followed|replied|responded|got back)",
    ],
}
_FRUSTRATION_WORDS = re.compile(
    r"\b(ridiculous|unacceptable|terrible|awful|worst|furious|angry|fed up|"
    r"a joke|waste of|never again|disappointed|frustrat\w*|absurd|outrageous)\b",
    re.I,
)
_CAPS_ALLOWLIST = {"UPS", "USPS", "USB", "LED", "FAQ", "USA", "SKU", "ETA", "OK", "DHL", "ET", "AM", "PM"}

SIGNAL_DISCLAIMER = (
    "Heuristic keyword signals computed in code. They can be wrong; confirm them "
    "against what the customer actually wrote."
)


@dataclass
class ConversationSignals:
    flags: dict[str, list[str]] = field(default_factory=dict)  # signal -> matched snippets
    frustration_indicators: list[str] = field(default_factory=list)
    customer_message_count: int = 0
    unanswered_customer_messages: int = 0

    def has(self, name: str) -> bool:
        return bool(self.flags.get(name))

    @property
    def repeat_contact(self) -> bool:
        return self.customer_message_count >= 3

    def as_dict(self) -> dict:
        return {
            "note": SIGNAL_DISCLAIMER,
            "customer_message_count": self.customer_message_count,
            "unanswered_customer_messages_since_last_staff_reply": self.unanswered_customer_messages,
            "repeat_contact": self.repeat_contact,
            "detected": {k: v for k, v in self.flags.items() if v},
            "frustration_indicators": self.frustration_indicators,
        }


def _snippet(text: str, match: re.Match[str], width: int = 40) -> str:
    start, end = max(0, match.start() - width), min(len(text), match.end() + width)
    return ("…" if start else "") + text[start:end].replace("\n", " ").strip() + ("…" if end < len(text) else "")


def detect_signals(messages: list[dict]) -> ConversationSignals:
    """`messages` are dicts with `author_role` ('customer'|'staff') and `body`."""
    signals = ConversationSignals()
    for message in messages:
        if message.get("author_role") == "staff":
            signals.unanswered_customer_messages = 0
            continue
        signals.customer_message_count += 1
        signals.unanswered_customer_messages += 1
        body = message.get("body", "")
        for name, patterns in _PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, body, re.I)
                if match:
                    signals.flags.setdefault(name, []).append(_snippet(body, match))
                    break
        for match in _FRUSTRATION_WORDS.finditer(body):
            signals.frustration_indicators.append(f"strong wording: '{match.group(0)}'")
        caps = [w for w in re.findall(r"\b[A-Z]{3,}\b", body) if w not in _CAPS_ALLOWLIST and not w.startswith("NS")]
        if caps:
            signals.frustration_indicators.append(f"all-caps emphasis: {', '.join(sorted(set(caps)))}")
        if body.count("!") >= 2 or "!!" in body:
            signals.frustration_indicators.append("multiple exclamation marks")
    if signals.has("claims_repeat_contact"):
        signals.frustration_indicators.append("customer says they have contacted us before")
    if signals.repeat_contact:
        signals.frustration_indicators.append(f"{signals.customer_message_count} customer messages on this ticket")
    return signals


# ----------------------------------------------------------------------------
# Escalation rule enforcement
# ----------------------------------------------------------------------------


@dataclass
class RuleHit:
    rule_id: str
    reason: str
    priority: str
    team: str


def evaluate_rules(
    signals: ConversationSignals,
    *,
    approvals_created: int,
    tool_failures: list[str],
    security_blocks: list[str],
    ticket_status: str | None,
) -> list[RuleHit]:
    """Return every mandatory escalation rule (E1–E9) that applies to this case."""
    hits: list[RuleHit] = []
    if signals.has("explicit_human_request"):
        priority = "high" if signals.frustration_indicators else "normal"
        hits.append(RuleHit("E1", "Customer explicitly asked for a person.", priority, "Support Lead"))
    if signals.has("legal_threat"):
        hits.append(RuleHit("E2", "Legal threat or legal action mentioned.", "urgent", "Support Lead"))
    if signals.has("payment_dispute"):
        hits.append(RuleHit("E3", "Payment dispute or chargeback mentioned.", "urgent", "Billing"))
    if signals.has("security_or_fraud"):
        hits.append(RuleHit("E4", "Possible account security or fraud issue.", "urgent", "Trust & Safety"))
    if signals.has("safety_issue"):
        hits.append(RuleHit("E5", "Possible product safety issue.", "urgent", "Warranty Team"))
    if approvals_created:
        hits.append(RuleHit("E6", "An action is waiting for human approval.", "normal", "Support Lead"))
    if signals.repeat_contact and ticket_status not in {"solved", "closed"}:
        hits.append(
            RuleHit(
                "E7",
                f"Repeat contact: {signals.customer_message_count} customer messages on an unresolved ticket.",
                "high",
                "Support Lead",
            )
        )
    if signals.has("prompt_injection") or security_blocks:
        detail = "; ".join(security_blocks) if security_blocks else "instructions aimed at the AI"
        hits.append(RuleHit("E8", f"Suspected manipulation attempt ({detail}).", "normal", "Trust & Safety"))
    if tool_failures:
        hits.append(
            RuleHit(
                "E9",
                f"Tool failure prevented verification: {', '.join(sorted(set(tool_failures)))}.",
                "normal",
                "Support Lead",
            )
        )
    return hits


def enforce_escalation_rules(escalation: Escalation, hits: list[RuleHit]) -> tuple[Escalation, list[GuardrailEvent]]:
    """Merge the model's escalation decision with mandatory rules. Never downgrades."""
    if not hits:
        return escalation, []
    events: list[GuardrailEvent] = []
    result = escalation.model_copy(deep=True)
    result.rule_ids = sorted({h.rule_id for h in hits}, key=lambda r: int(r[1:]))
    top = max(hits, key=lambda h: PRIORITY_ORDER[h.priority])

    if not result.required:
        result.required = True
        result.overridden_by_rules = True
        result.reason = "; ".join(h.reason for h in hits)
        result.priority = top.priority  # type: ignore[assignment]
        result.team = top.team  # type: ignore[assignment]
        events.append(
            GuardrailEvent(
                kind="escalation_override",
                detail=f"Model did not escalate; rules {', '.join(result.rule_ids)} require it.",
            )
        )
        return result, events

    current = result.priority or "normal"
    if PRIORITY_ORDER[top.priority] > PRIORITY_ORDER[current]:
        result.overridden_by_rules = True
        events.append(
            GuardrailEvent(
                kind="priority_raised",
                detail=f"Priority raised from {current} to {top.priority} by rule {top.rule_id}.",
            )
        )
        result.priority = top.priority  # type: ignore[assignment]
        if top.priority == "urgent":
            result.team = top.team  # type: ignore[assignment]
    if result.priority is None:
        result.priority = "normal"
    if result.team is None:
        result.team = top.team  # type: ignore[assignment]
    if not result.reason:
        result.reason = "; ".join(h.reason for h in hits)
    return result, events


# ----------------------------------------------------------------------------
# Draft checks
# ----------------------------------------------------------------------------

PROHIBITED_CLAIMS = [
    r"\bI(?:'ve| have)?\s+(?:just\s+)?(?:issued|processed|approved|refunded|credited)\b",
    r"\b(?:we|I)(?:'ve| have)\s+(?:issued|processed|approved)\s+(?:a|an|your|the)\s+(?:full\s+|partial\s+)?refund",
    r"\b(?:your|the|a)\s+(?:full\s+|partial\s+)?refund\s+(?:has been|is|was)\s+approved\b",
    r"\b(?:I|we)(?:'ve| have)\s+(?:cancell?ed|modified|changed)\s+(?:your|the)\s+order\b",
    r"\b(?:I|we)(?:'ve| have)\s+(?:sent|shipped)\s+(?:you\s+)?a\s+replacement\b",
    r"\breplacement\s+(?:has been|is)\s+approved\b",
    r"\b(?:I|we)(?:'ve| have)\s+closed\s+(?:your|this)\s+ticket\b",
]
_ALLOWED_EMAIL_DOMAINS = ("northstar-supply.example",)
_ANY_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def check_draft(draft: str) -> list[GuardrailEvent]:
    events = []
    for pattern in PROHIBITED_CLAIMS:
        match = re.search(pattern, draft, re.I)
        if match:
            events.append(
                GuardrailEvent(
                    kind="prohibited_claim",
                    detail=f"Draft claims an action the agent cannot perform: '{match.group(0)}'",
                )
            )
    for match in _ANY_EMAIL.finditer(draft):
        if not match.group(1).lower().endswith(_ALLOWED_EMAIL_DOMAINS):
            events.append(GuardrailEvent(kind="pii_in_draft", detail="Draft contains a customer email address."))
            break
    return events
