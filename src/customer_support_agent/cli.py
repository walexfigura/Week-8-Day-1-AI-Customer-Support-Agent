"""Command-line interface: `python run.py <command>`.

Commands
  demo         Run the agent on a sample scenario (needs OPENAI_API_KEY)
  webhook      Process simulated Zendesk webhook deliveries
  eval         Run agent-level behavior evaluations (needs OPENAI_API_KEY)
  smoke        Offline diagnostics: data, tools, webhooks, skill build (no API key)
  tool         Call one tool directly, exactly as the model would (no API key)
  approvals    List / approve / reject queued approval requests (simulation)
  build-skill  Build dist/customer-support-skill.zip
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

from .config import MissingCredentialsError, Settings
from .models import AgentResult

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

_COLOR = sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def bold(t: str) -> str:
    return _c(t, "1")


def green(t: str) -> str:
    return _c(t, "32")


def red(t: str) -> str:
    return _c(t, "31")


def yellow(t: str) -> str:
    return _c(t, "33")


def dim(t: str) -> str:
    return _c(t, "2")


def _wrap(text: str, indent: str = "    ") -> str:
    paragraphs = text.strip().split("\n")
    return "\n".join(
        textwrap.fill(p, 92, initial_indent=indent, subsequent_indent=indent) if p.strip() else "" for p in paragraphs
    )


def print_result(result: AgentResult, settings: Settings, title: str = "") -> None:
    line = "═" * 96
    print(
        f"\n{line}\n{bold('Northstar Supply Support Agent')}  {title}  ticket #{result.ticket_id}  trace {result.trace_id}"
    )
    print(
        dim(
            f"model {result.model} · backend {settings.backend} · reference date "
            f"{settings.reference_datetime.date()} · {result.usage.model_calls} model calls · "
            f"{result.usage.input_tokens + result.usage.output_tokens} tokens"
        )
    )
    print(line)

    print(bold("\nTool trace") + dim("  (chosen by the model, executed by Python)"))
    if not result.trace:
        print("  (no tools called)")
    for r in result.trace:
        args = ", ".join(f"{k}={json.dumps(v)}" for k, v in r.arguments.items())
        mark = green("✓") if r.ok else (red("⛔") if r.blocked else yellow("✗"))
        print(f"  {r.step:>2}. {mark} {r.name}({args})")
        print(dim(f"        → {r.summary}"))

    esc = result.escalation
    print(bold("\nDecision"))
    intents = result.intent + (f" (+ {', '.join(result.secondary_intents)})" if result.secondary_intents else "")
    print(f"  Intent:          {intents}")
    s = result.sentiment
    print(f"  Sentiment:       {s.label}{' · repeat contact' if s.repeat_contact else ''}")
    for ind in s.indicators[:5]:
        print(dim(f"                   - {ind}"))
    if esc.required:
        rules = ", ".join(esc.rule_ids)
        if esc.overridden_by_rules:
            note = yellow(f" [changed by code rules {rules}]")
        else:
            note = dim(f" [code rules agree: {rules}]") if rules else ""
        print(f"  Escalation:      {red('REQUIRED')} → {esc.team} ({esc.priority}){note}")
        print(_wrap(f"Reason: {esc.reason}", "                   "))
    else:
        print(f"  Escalation:      {green('not required')}")
    if result.needs_clarification:
        print(f"  Clarification:   {result.clarifying_question}")
    if result.approval_requests:
        for a in result.approval_requests:
            amount = f" ${a.amount_usd:.2f}" if a.amount_usd is not None else ""
            print(
                f"  Approval:        {a.id} {a.action_type}{amount} for #{a.order_number} — "
                f"{yellow(a.status)} (NOT executed)"
            )
    else:
        print("  Approval:        none requested")
    print(f"  Policy refs:     {', '.join(result.policy_references) or '—'}")
    for e in result.evidence[:6]:
        print(dim(f"  Evidence:        [{e.source}] {e.detail}"))
    for g in result.guardrail_events:
        print(yellow(f"  Guardrail:       {g.kind}: {g.detail}"))

    print(bold("\nInternal summary (for staff)"))
    print(_wrap(result.internal_summary or "—"))
    print(bold("\nDraft response") + red("  — NOT SENT. A human must review and send it."))
    print(_wrap(result.draft_response or "(no draft)"))
    if result.error:
        print(red(f"\nError: {result.error}"))
    print(dim(f"\nStructured log: {settings.logs_dir}/agent-*.jsonl  (search for {result.trace_id})"))


def _scenarios(settings: Settings) -> dict[str, dict]:
    from .evaluation import load_cases

    return {c["id"]: c for c in load_cases(settings.project_root / "evals" / "test-cases.json")}


def _services(settings: Settings):
    from .services import build_services

    return build_services(settings)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_demo(args: argparse.Namespace, settings: Settings) -> int:
    scenarios = _scenarios(settings)
    if args.list:
        print(bold("Available scenarios") + " (python run.py demo --scenario <id>)\n")
        for cid, case in scenarios.items():
            print(f"  {cid:<26} ticket {case['input']['ticket_id']:<5} {case['title']}")
        return 0
    if args.ticket:
        ticket_id, faults, title = args.ticket, {}, f"ticket {args.ticket}"
    else:
        if args.scenario not in scenarios:
            print(red(f"Unknown scenario '{args.scenario}'."))
            print("Valid scenarios: " + ", ".join(scenarios))
            return 2
        case = scenarios[args.scenario]
        ticket_id, faults, title = (
            case["input"]["ticket_id"],
            case["input"].get("fault_injection", {}),
            f"[{args.scenario}]",
        )
    settings.require_openai_key()
    from .agent import SupportAgent, TicketNotFoundError

    services = _services(settings)
    try:
        result = SupportAgent(services).run(ticket_id, fault_injection=faults)
    except TicketNotFoundError as exc:
        print(red(str(exc)))
        return 2
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print_result(result, settings, title)
    return 0 if result.status == "completed" else 1


def cmd_webhook(args: argparse.Namespace, settings: Settings) -> int:
    from .agent import SupportAgent
    from .webhook_simulator import (
        LOCAL_DEMO_SECRET,
        IdempotencyStore,
        WebhookHandler,
        load_sample_events,
        simulate_delivery,
    )

    events = load_sample_events(settings.data_dir)
    if args.list:
        print(bold("Sample webhook events") + " (python run.py webhook --event <name>)\n")
        for name, e in events.items():
            print(f"  {name:<20} {e['description']}")
        return 0
    store = IdempotencyStore(settings.logs_dir / "processed-webhook-events.json")
    if args.reset:
        store.reset()
        print(green("Cleared the processed-event store."))
        if not (args.event or args.all):
            return 0
    names = list(events) if args.all else [args.event]
    for name in names:
        if name not in events:
            print(red(f"Unknown event '{name}'. Valid events: {', '.join(events)}"))
            return 2
    agent = None
    if not args.no_agent:
        settings.require_openai_key()
    services = _services(settings)
    if not args.no_agent:
        agent = SupportAgent(services)
    secret = settings.zendesk_webhook_secret or LOCAL_DEMO_SECRET
    handler = WebhookHandler(services, agent, secret=secret, idempotency=store)

    print(
        dim(
            "Local simulation: events are read from data/webhook-events.json. Nothing is received from "
            "or sent to Zendesk."
        )
    )
    exit_code = 0
    for name in names:
        body, headers = simulate_delivery(events[name], secret)
        outcome = handler.handle(body, headers)
        color = {"processed": green, "validated": green, "duplicate": yellow, "ignored": yellow}.get(
            outcome.status, red
        )
        print(
            f"\n{bold('Event')} {name}  →  {color(outcome.status.upper())}  "
            f"(event {outcome.event_id or '?'}, ticket {outcome.ticket_id or '?'})"
        )
        for i, step in enumerate(outcome.steps, 1):
            print(dim(f"   {i}. {step}"))
        print(f"   {outcome.detail}")
        if outcome.result:
            print_result(outcome.result, settings, f"[webhook:{name}]")
        if outcome.status == "failed":
            exit_code = 1
    return exit_code


def cmd_eval(args: argparse.Namespace, settings: Settings) -> int:
    from .agent import SupportAgent
    from .evaluation import score_case, summarize

    settings.require_openai_key()
    cases = list(_scenarios(settings).values())
    if args.case:
        cases = [c for c in cases if c["id"] in args.case]
        if not cases:
            print(red("No matching cases. Use `python run.py demo --list` to see ids."))
            return 2
    services = _services(settings)
    agent = SupportAgent(services)
    scores = []
    print(bold(f"Running {len(cases)} case(s) × {args.repeat} with {settings.openai_model}\n"))
    for case in cases:
        for attempt in range(1, args.repeat + 1):
            result = agent.run(case["input"]["ticket_id"], fault_injection=case["input"].get("fault_injection", {}))
            score = score_case(case, result)
            scores.append(score)
            label = green("PASS") if score.passed else red("FAIL")
            suffix = f" (run {attempt})" if args.repeat > 1 else ""
            print(f"  {label}  {case['id']:<26}{suffix} trace {result.trace_id}")
            for check in score.failures:
                print(red(f"         ✗ {check.name}: {check.detail}"))
            if args.verbose:
                print(_wrap(result.draft_response, "         │ "))
    summary = summarize(scores)
    out_dir = settings.project_root / "evals" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"eval-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    summary["model"] = settings.openai_model
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(bold(f"\n{summary['passed']}/{summary['cases']} passed ({summary['pass_rate']:.0%})"))
    print(dim(f"Full report: {out_path.relative_to(settings.project_root)}"))
    return 0 if summary["failed"] == 0 else 1


def cmd_tool(args: argparse.Namespace, settings: Settings) -> int:
    from .tools.base import ToolContext

    services = _services(settings)
    if args.name == "list":
        for spec in services.registry.specs.values():
            params = ", ".join(spec.parameters["properties"])
            print(f"  {spec.name:<28} [{spec.permission}]  ({params})")
        return 0
    ticket = services.zendesk.get_ticket(args.ticket)
    if ticket is None:
        print(red(f"Ticket {args.ticket} not found"))
        return 2
    payload: dict = json.loads(args.json_args) if args.json_args else {}
    for pair in args.arg or []:
        key, _, value = pair.partition("=")
        payload[key] = value
    ctx = ToolContext(
        services=services,
        trace_id="tr_manual",
        ticket_id=ticket["id"],
        requester_id=ticket["requester"]["id"],
        requester_email=ticket["requester"]["email"],
        requester_name=ticket["requester"]["name"],
    )
    print(
        dim(
            f"Calling {args.name} as the agent would for ticket {ticket['id']} "
            f"(requester {ticket['requester']['name']})\n"
        )
    )
    result = services.registry.execute(args.name, payload, ctx)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


def cmd_approvals(args: argparse.Namespace, settings: Settings) -> int:
    from .approvals import ApprovalError, ApprovalQueue

    queue = ApprovalQueue(settings.logs_dir / "approvals.jsonl")
    try:
        if args.approve:
            req = queue.approve(args.approve, args.approver or "", args.note)
            print(green(f"{req.id} approved by {req.decided_by}."))
            print(queue.execute_approved(req.id))
            return 0
        if args.reject:
            req = queue.reject(args.reject, args.approver or "", args.note)
            print(yellow(f"{req.id} rejected by {req.decided_by}."))
            return 0
    except ApprovalError as exc:
        print(red(f"Not allowed: {exc}"))
        return 1
    if not queue.requests:
        print("No approval requests yet. Run a scenario such as `python run.py demo --scenario frustrated_customer`.")
        return 0
    for req in sorted(queue.requests.values(), key=lambda r: r.created_at):
        amount = f"${req.amount_usd:.2f}" if req.amount_usd is not None else "-"
        print(
            f"  {req.id}  {req.status:<22} {req.action_type:<18} order #{req.order_number:<5} {amount:>9}  "
            f"ticket {req.ticket_id}  trace {req.trace_id}"
        )
        print(dim(f"          reason: {req.reason}"))
    return 0


def cmd_build_skill(args: argparse.Namespace, settings: Settings) -> int:
    from .skill_builder import SkillValidationError, build_skill, verify_zip

    try:
        out_dir = Path(args.out).resolve() if args.out else None
        report = build_skill(settings.project_root, out_dir)
        names = verify_zip(report.zip_path)
    except SkillValidationError as exc:
        print(red(f"Skill validation failed: {exc}"))
        return 1
    for warning in report.warnings:
        print(yellow(f"warning: {warning}"))
    shown = (
        report.zip_path.relative_to(settings.project_root)
        if report.zip_path.is_relative_to(settings.project_root)
        else report.zip_path
    )
    print(green(f"Built {shown} ({len(names)} files)"))
    print(dim("Top level: customer-support/SKILL.md + references/ (guides, knowledge-base, workflows, sample-data)"))
    print("Upload it in Claude: Settings → Capabilities → Skills → Upload skill. See README 'Using the Claude Skill'.")
    return 0


def cmd_smoke(args: argparse.Namespace, settings: Settings) -> int:
    from .smoke import run_smoke

    return run_smoke(settings)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python run.py", description="Northstar Supply AI customer support agent (educational)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("demo", help="Run the agent on a sample scenario (needs OPENAI_API_KEY)")
    p.add_argument("--scenario", default="simple_order_status", help="Scenario id (see --list)")
    p.add_argument("--ticket", help="Run on any mock ticket id instead of a scenario")
    p.add_argument("--list", action="store_true", help="List scenarios")
    p.add_argument("--json", action="store_true", help="Print the full AgentResult as JSON")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("webhook", help="Process simulated Zendesk webhook events")
    p.add_argument("--event", default="new_ticket", help="Event name (see --list)")
    p.add_argument("--all", action="store_true", help="Process every sample event in order")
    p.add_argument("--list", action="store_true", help="List sample events")
    p.add_argument(
        "--no-agent", action="store_true", help="Run validation/dedup/history steps only (no API key needed)"
    )
    p.add_argument("--reset", action="store_true", help="Forget processed event ids first")
    p.set_defaults(func=cmd_webhook)

    p = sub.add_parser("eval", help="Run agent behavior evaluations (needs OPENAI_API_KEY)")
    p.add_argument("--case", action="append", help="Only run this case id (repeatable)")
    p.add_argument("--repeat", type=int, default=1, help="Run each case N times to measure consistency")
    p.add_argument("--verbose", action="store_true", help="Also print each draft")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("smoke", help="Offline diagnostics (no API key needed)")
    p.set_defaults(func=cmd_smoke)

    p = sub.add_parser("tool", help="Call one tool directly (no API key needed)")
    p.add_argument("name", help="Tool name, or 'list'")
    p.add_argument("--ticket", default="5001", help="Ticket whose requester the call runs as (default 5001)")
    p.add_argument("--arg", action="append", help="key=value argument (repeatable)")
    p.add_argument("--json-args", help="Arguments as a JSON object")
    p.set_defaults(func=cmd_tool)

    p = sub.add_parser("approvals", help="List or decide approval requests (simulation)")
    p.add_argument("--approve", metavar="ID")
    p.add_argument("--reject", metavar="ID")
    p.add_argument("--approver", help="Name of the human approver (required to approve/reject)")
    p.add_argument("--note")
    p.set_defaults(func=cmd_approvals)

    p = sub.add_parser("build-skill", help="Build dist/customer-support-skill.zip")
    p.add_argument("--out", help="Output folder (default: dist). Maintainers: --out claude-skill updates the download")
    p.set_defaults(func=cmd_build_skill)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings.from_env()
        return args.func(args, settings)
    except MissingCredentialsError as exc:
        print(red(str(exc)))
        return 2
    except FileNotFoundError as exc:
        print(red(f"Missing file: {exc}"))
        print("Are you running this from the repository folder? Try `cd Week-8-Day-1-AI-Customer-Support-Agent`.")
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
