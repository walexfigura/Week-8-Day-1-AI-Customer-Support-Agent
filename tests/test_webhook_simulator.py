"""Webhook parsing, signature verification, filtering, and duplicate handling."""

from __future__ import annotations

import json

import pytest

from customer_support_agent.models import AgentResult
from customer_support_agent.webhook_simulator import (
    SIGNATURE_HEADER,
    IdempotencyStore,
    WebhookHandler,
    load_sample_events,
    simulate_delivery,
    verify_signature,
)

SECRET = "test-secret"


@pytest.fixture
def events(settings):
    return load_sample_events(settings.data_dir)


class FakeAgent:
    """Stands in for SupportAgent so the handler can be tested without a model."""

    def __init__(self, status: str = "completed"):
        self.calls: list[tuple[str, str | None]] = []
        self.status = status

    def run(self, ticket_id, trigger_comment_id=None, event_type="ticket.created"):
        self.calls.append((ticket_id, trigger_comment_id))
        return AgentResult(
            trace_id="tr_fake",
            ticket_id=ticket_id,
            status=self.status,
            model="fake",
            error="boom" if self.status == "error" else None,
        )


def handler_for(services, agent=None, store=None):
    return WebhookHandler(services, agent, secret=SECRET, idempotency=store or IdempotencyStore(None))


def test_signature_round_trip_and_tampering(events):
    body, headers = simulate_delivery(events["new_ticket"], SECRET)
    assert verify_signature(body, headers, SECRET)
    assert not verify_signature(body + b" ", headers, SECRET)
    assert not verify_signature(body, headers, "wrong-secret")
    assert not verify_signature(body, {}, SECRET)


def test_unsigned_request_rejected(services, events):
    body, headers = simulate_delivery(events["new_ticket"], SECRET)
    headers[SIGNATURE_HEADER] = "forged"
    outcome = handler_for(services).handle(body, headers)
    assert outcome.status == "rejected" and "signature" in outcome.detail.lower()


def test_malformed_event_rejected(services, events):
    outcome = handler_for(services).handle(*simulate_delivery(events["malformed"], SECRET))
    assert outcome.status == "rejected"
    assert "subject" in outcome.detail


def test_bad_subject_format_rejected(services, events):
    event = json.loads(json.dumps(events["new_ticket"]))
    event["payload"]["subject"] = "zen:user:9001"
    outcome = handler_for(services).handle(*simulate_delivery(event, SECRET))
    assert outcome.status == "rejected"


def test_new_ticket_processed_then_duplicate_skipped(services, events):
    agent = FakeAgent()
    handler = handler_for(services, agent)
    first = handler.handle(*simulate_delivery(events["new_ticket"], SECRET))
    assert first.status == "processed" and first.ticket_id == "5001"
    dup = handler.handle(*simulate_delivery(events["duplicate_delivery"], SECRET))
    assert dup.status == "duplicate"
    assert agent.calls == [("5001", None)]  # agent invoked exactly once


def test_failed_run_is_not_marked_processed(services, events):
    handler = handler_for(services, FakeAgent(status="error"))
    assert handler.handle(*simulate_delivery(events["new_ticket"], SECRET)).status == "failed"
    assert not handler.idempotency.contains("evt_01JCN5001A")  # retry will be processed


def test_follow_up_comment_is_added_to_history(services, events):
    agent = FakeAgent()
    before = len(services.zendesk.get_ticket("5003")["comments"])
    outcome = handler_for(services, agent).handle(*simulate_delivery(events["follow_up"], SECRET))
    assert outcome.status == "processed"
    comments = services.zendesk.get_ticket("5003")["comments"]
    assert len(comments) == before + 1 and "#1044" in comments[-1]["body"]
    assert agent.calls == [("5003", "50032")]


def test_existing_comment_not_duplicated(services, events):
    handler_for(services, FakeAgent()).handle(*simulate_delivery(events["escalation_worthy"], SECRET))
    assert len(services.zendesk.get_ticket("5002")["comments"]) == 4


def test_staff_comment_ignored(services, events):
    agent = FakeAgent()
    outcome = handler_for(services, agent).handle(*simulate_delivery(events["staff_comment"], SECRET))
    assert outcome.status == "ignored" and agent.calls == []


def test_no_agent_mode_does_not_mark_processed(services, events):
    handler = handler_for(services, None)
    outcome = handler.handle(*simulate_delivery(events["escalation_worthy"], SECRET))
    assert outcome.status == "validated"
    assert any("3 from customer" in s for s in outcome.steps)
    assert not handler.idempotency.contains(outcome.event_id)


def test_idempotency_store_persists(tmp_path):
    path = tmp_path / "seen.json"
    store = IdempotencyStore(path)
    store.mark("evt_1")
    assert IdempotencyStore(path).contains("evt_1")
    store.reset()
    assert not IdempotencyStore(path).contains("evt_1")
