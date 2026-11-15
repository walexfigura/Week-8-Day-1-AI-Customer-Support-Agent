"""`python run.py smoke` — offline diagnostics that need no API key.

Checks that the environment, mock data, knowledge base, tools, guardrails,
webhook pipeline, eval definitions, and Skill packaging all work, and prints
a ✓/✗ checklist. Nothing is written to logs/ and no network calls are made.
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from .config import Settings


def run_smoke(settings: Settings) -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, fn: Callable[[], str]) -> None:
        try:
            results.append((name, True, fn()))
        except Exception as exc:  # noqa: BLE001 - report every failure, keep going
            results.append((name, False, f"{exc.__class__.__name__}: {exc}"))

    def py_version() -> str:
        if sys.version_info < (3, 10):  # noqa: UP036 - users may run run.py without pip install
            raise RuntimeError(f"Python 3.10+ required, found {sys.version.split()[0]}")
        return sys.version.split()[0]

    check("Python version", py_version)

    def deps() -> str:
        import httpx  # noqa: F401
        import openai
        import pydantic  # noqa: F401

        return f"openai {openai.__version__}"

    check("Dependencies installed", deps)

    from .data_checks import check_data, check_eval_cases, check_knowledge_base
    from .services import build_services

    services_box: dict = {}

    def load_services() -> str:
        services_box["s"] = build_services(settings, persist=False)
        s = services_box["s"]
        return (
            (
                f"{len(s.shopify.orders)} orders, {len(s.shopify.products)} products, "  # type: ignore[attr-defined]
                f"{len(s.zendesk._tickets)} tickets, {len(s.kb.documents)} KB docs, "  # type: ignore[attr-defined]
                f"{len(s.workflows.names)} workflows"
            )
            if settings.backend == "mock"
            else "live backend configured"
        )

    check("Mock data + knowledge base load", load_services)
    if "s" not in services_box:
        return _report(results)
    services = services_box["s"]

    def data_consistency() -> str:
        problems = check_data(settings.data_dir) + check_knowledge_base(
            services.kb, settings.data_dir / "shopify-products.json"
        )
        if problems:
            raise AssertionError("; ".join(problems[:5]))
        p = services.policy
        return f"consistent (return window {p.return_window_days} days, lost after {p.presumed_lost_business_days_past_estimate} business days)"

    check("Data & policy consistency", data_consistency)

    from .approvals import ApprovalError
    from .tools.base import ToolContext

    def ctx_for(ticket_id: str) -> ToolContext:
        t = services.zendesk.get_ticket(ticket_id)
        return ToolContext(
            services=services,
            trace_id="tr_smoke",
            ticket_id=t["id"],
            requester_id=t["requester"]["id"],
            requester_email=t["requester"]["email"],
            requester_name=t["requester"]["name"],
        )

    run = services.registry.execute

    def tool_order() -> str:
        r = run("get_shopify_order", {"order_id": "1042"}, ctx_for("5001"))
        state = r["order"]["shipments"][0]["delivery_assessment"]["state"]
        assert state == "on_track", state
        r = run("get_shopify_order", {"order_id": "#1043"}, ctx_for("5002"))
        state2 = r["order"]["shipments"][0]["delivery_assessment"]["state"]
        assert state2 == "presumed_lost", state2
        return "1042 on_track, 1043 presumed_lost"

    check("Shopify order tool", tool_order)

    def tool_privacy() -> str:
        r = run("get_shopify_order", {"order_id": "1043"}, ctx_for("5008"))
        assert not r["ok"] and r["error"]["code"] == "order_not_found_for_requester", r
        r = run("get_zendesk_ticket", {"ticket_id": "5002"}, ctx_for("5008"))
        assert not r["ok"] and r["error"]["code"] == "ticket_not_accessible", r
        return "other customers' orders and tickets are hidden"

    check("Identity / privacy checks", tool_privacy)

    def tool_returns() -> str:
        items = run("check_return_eligibility", {"order_id": "1048"}, ctx_for("5006"))["items"]
        assert all(i["eligibility"] == "not_eligible" for i in items), items
        items = run("check_return_eligibility", {"order_id": "1041"}, ctx_for("5012"))["items"]
        assert items[0]["return_window_ends"] == "2026-11-27", items[0]
        return "1048 not eligible; 1041 window ends 2026-11-27"

    check("Return eligibility tool", tool_returns)

    def tool_kb() -> str:
        r = run(
            "search_knowledge_base",
            {"query": "how long do I have to return something", "category": "all"},
            ctx_for("5001"),
        )
        top = [h["document_id"] for h in r["results"]]
        assert "policies/return-policy.md" in top[:2], top
        return f"top hit {top[0]}"

    check("Knowledge search", tool_kb)

    def tool_ticket() -> str:
        r = run("get_zendesk_ticket_history", {"ticket_id": "5002"}, ctx_for("5002"))
        s = r["signals"]
        assert s["repeat_contact"] and s["customer_message_count"] == 3, s
        return f"5002: {s['customer_message_count']} customer messages, signals {list(s['detected'])}"

    check("Zendesk history + signals", tool_ticket)

    def guardrails() -> str:
        r = run("issue_refund", {"order_id": "1043"}, ctx_for("5002"))
        assert r["error"]["code"] == "permission_denied", r
        ctx = ctx_for("5002")
        r = run(
            "request_human_approval",
            {
                "action_type": "refund",
                "order_id": "1043",
                "amount_usd": 124.0,
                "reason": "Package presumed lost per shipping policy",
            },
            ctx,
        )
        assert r["ok"] and r["executed"] is False, r
        try:
            services.approvals.approve(r["approval_request_id"], "ai_agent")
            raise AssertionError("agent self-approval was allowed")
        except ApprovalError:
            pass
        return "issue_refund blocked; approval created as pending; agent self-approval refused"

    check("Guardrails & approvals", guardrails)

    def webhooks() -> str:
        from .webhook_simulator import (
            LOCAL_DEMO_SECRET,
            IdempotencyStore,
            WebhookHandler,
            load_sample_events,
            simulate_delivery,
        )

        fresh = build_services(settings, persist=False)
        handler = WebhookHandler(fresh, None, secret=LOCAL_DEMO_SECRET, idempotency=IdempotencyStore(None))
        statuses = {}
        for name, event in load_sample_events(settings.data_dir).items():
            outcome = handler.handle(*simulate_delivery(event, LOCAL_DEMO_SECRET))
            statuses[name] = outcome.status
            if outcome.status == "validated":
                handler.idempotency.mark(outcome.event_id)  # emulate a successful run for the duplicate check
        assert statuses.get("duplicate_delivery") == "duplicate", statuses
        assert statuses.get("malformed") == "rejected", statuses
        assert statuses.get("staff_comment") == "ignored", statuses
        return ", ".join(f"{k}={v}" for k, v in statuses.items())

    check("Webhook pipeline (no agent)", webhooks)

    def evals() -> str:
        problems = check_eval_cases(
            settings.project_root / "evals" / "test-cases.json", settings.data_dir, set(services.registry.specs)
        )
        if problems:
            raise AssertionError("; ".join(problems[:5]))
        from .evaluation import load_cases

        return f"{len(load_cases(settings.project_root / 'evals' / 'test-cases.json'))} cases well-formed"

    check("Evaluation cases", evals)

    def skill() -> str:
        from .skill_builder import build_skill, verify_zip

        with tempfile.TemporaryDirectory() as tmp:
            report = build_skill(settings.project_root, Path(tmp))
            names = verify_zip(report.zip_path)
        return f"valid ZIP with {len(names)} files (temporary build)"

    check("Claude Skill packaging", skill)

    def api_key() -> str:
        if settings.openai_api_key:
            return f"OPENAI_API_KEY is set; model {settings.openai_model}"
        return "OPENAI_API_KEY not set — offline commands work; demo/eval/webhook need a key"

    check("OpenAI configuration (info)", api_key)
    return _report(results)


def _report(results: list[tuple[str, bool, str]]) -> int:
    from .cli import bold, green, red

    print(bold("Northstar support agent — offline smoke test\n"))
    for name, passed, detail in results:
        print(f"  {green('✓') if passed else red('✗')} {name:<34} {detail}")
    failed = sum(not p for _, p, _ in results)
    print()
    print(green("All checks passed.") if not failed else red(f"{failed} check(s) failed."))
    return 0 if not failed else 1
