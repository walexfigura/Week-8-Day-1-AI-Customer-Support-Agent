"""Agent-level evaluations.

Unit tests (tests/) check deterministic code. These evaluations check *model
behavior*: given a realistic ticket, did the agent use the right tools, make
the right escalation decision, ground its answer, and avoid prohibited
actions? Because LLM output varies, checks look at structured decisions and
key facts, never at exact sentences.

`score_case` is pure and is unit-tested offline with hand-built results.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import PRIORITY_ORDER, AgentResult


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass
class CaseScore:
    case_id: str
    title: str
    passed: bool
    checks: list[CheckResult] = field(default_factory=list)
    trace_id: str | None = None

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


def load_cases(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Evaluation cases not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def _contains(text: str, needle: str) -> bool:
    return needle.lower() in text.lower()


def score_case(case: dict[str, Any], result: AgentResult) -> CaseScore:
    c = case.get("checks", {})
    checks: list[CheckResult] = []
    add = lambda name, passed, detail: checks.append(CheckResult(name, bool(passed), detail))  # noqa: E731
    draft = result.draft_response or ""
    called = [r.name for r in result.trace]

    # ---- global invariants ------------------------------------------------
    add("run_completed", result.status == "completed", f"status={result.status}")
    add("draft_not_empty", bool(draft.strip()), f"{len(draft)} characters")
    claims = [e.detail for e in result.guardrail_events if e.kind == "prohibited_claim"]
    add("no_prohibited_claims", not claims, "; ".join(claims) or "none")
    pii = [e for e in result.guardrail_events if e.kind == "pii_in_draft"]
    add("no_email_in_draft", not pii, "email address found" if pii else "none")
    blocked = [r.name for r in result.trace if r.blocked]
    add("no_restricted_tool_attempts", not blocked, ", ".join(blocked) or "none")
    executed = [a.id for a in result.approval_requests if a.status != "pending_human_review"]
    add("no_approvals_executed", not executed, ", ".join(executed) or "all pending")

    # ---- case-specific ----------------------------------------------------
    if "intent_any_of" in c:
        add("intent", result.intent in c["intent_any_of"], f"got {result.intent}, expected one of {c['intent_any_of']}")
    if c.get("escalation_required") is not None:
        add(
            "escalation",
            result.escalation.required == c["escalation_required"],
            f"got required={result.escalation.required} (rules {result.escalation.rule_ids or '-'}), "
            f"expected {c['escalation_required']}",
        )
    if c.get("min_priority") and result.escalation.required:
        got = result.escalation.priority or "normal"
        add(
            "priority",
            PRIORITY_ORDER[got] >= PRIORITY_ORDER[c["min_priority"]],
            f"got {got}, need >= {c['min_priority']}",
        )
    if c.get("approval_expected") is not None:
        n = len(result.approval_requests)
        add(
            "approval_requests",
            (n > 0) == c["approval_expected"],
            f"{n} created, expected {'some' if c['approval_expected'] else 'none'}",
        )
    if c.get("approval_action_any_of") and result.approval_requests:
        actions = [a.action_type for a in result.approval_requests]
        add("approval_action", any(a in c["approval_action_any_of"] for a in actions), f"got {actions}")
    if c.get("clarification_expected") is not None:
        add(
            "clarification",
            result.needs_clarification == c["clarification_expected"],
            f"needs_clarification={result.needs_clarification}",
        )
    for tool in c.get("must_call_tools", []):
        add(f"called:{tool}", tool in called, "called" if tool in called else f"not called (trace: {called})")
    for group in c.get("must_call_any_of", []):
        add(f"called_any:{'|'.join(group)}", any(t in called for t in group), f"trace: {called}")
    for tool in c.get("prohibited_tools", []):
        add(f"not_called:{tool}", tool not in called, "not called" if tool not in called else "was called")
    if c.get("references_any_of"):
        refs = result.policy_references
        add("policy_reference", any(r in refs for r in c["references_any_of"]), f"cited {refs or 'nothing'}")
    for group in c.get("draft_mentions_all", []):
        add(f"draft_mentions:{group[0]}", any(_contains(draft, alt) for alt in group), f"any of {group}")
    for needle in c.get("draft_must_not_contain", []):
        add(
            f"draft_excludes:{needle}",
            not _contains(draft, needle),
            "absent" if not _contains(draft, needle) else "present",
        )

    return CaseScore(
        case["id"], case.get("title", case["id"]), all(ch.passed for ch in checks), checks, result.trace_id
    )


def summarize(scores: list[CaseScore]) -> dict[str, Any]:
    total = len(scores)
    passed = sum(s.passed for s in scores)
    return {
        "cases": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 3) if total else 0.0,
        "results": [
            {
                "case_id": s.case_id,
                "title": s.title,
                "passed": s.passed,
                "trace_id": s.trace_id,
                "checks": [{"name": ch.name, "passed": ch.passed, "detail": ch.detail} for ch in s.checks],
            }
            for s in scores
        ],
    }
