"""Knowledge base loading, search, policy parameters, and cross-file consistency."""

from __future__ import annotations

from datetime import date

import pytest

from customer_support_agent.data_checks import check_data, check_eval_cases, check_knowledge_base
from customer_support_agent.knowledge import parse_front_matter
from customer_support_agent.policy import business_days_between, warranty_end


def test_front_matter_parsing():
    meta, body = parse_front_matter("---\ntitle: X\nreturn_window_days: 30\n---\n\n# Body\n")
    assert meta == {"title": "X", "return_window_days": "30"}
    assert body.startswith("# Body")
    assert parse_front_matter("# no header") == ({}, "# no header")


@pytest.mark.parametrize(
    "query, expected",
    [
        ("return window", "policies/return-policy.md"),
        ("package no tracking update delayed", "policies/shipping-policy.md"),
        ("delivered but not received", "policies/shipping-policy.md"),
        ("when will my refund show up bank", "faqs/returns/refund-timeline.md"),
        ("lamp flickering", "faqs/products/troubleshooting.md"),
        ("talk to a manager escalate", "procedures/escalation-guide.md"),
        ("is the bottle dishwasher safe", "faqs/products/product-info.md"),
        ("exchange for a different color", "faqs/returns/exchange-process.md"),
    ],
)
def test_search_finds_relevant_document(services, query, expected):
    top = [h.doc_id for h in services.kb.search(query, max_results=3)]
    assert expected in top, top


def test_search_category_filter(services):
    hits = services.kb.search("refund", category="faqs")
    assert hits and all(h.doc_id.startswith("faqs/") for h in hits)


def test_search_tool_records_retrieval(services, ctx_for):
    ctx = ctx_for("5001")
    r = services.registry.execute("search_knowledge_base", {"query": "return window", "category": "all"}, ctx)
    assert r["ok"] and r["results"][0]["document_id"] == "policies/return-policy.md"
    assert "policies/return-policy.md" in ctx.retrieved_documents


def test_read_document_tool(services, ctx_for):
    ctx = ctx_for("5001")
    r = services.registry.execute("read_knowledge_document", {"document_id": "policies/shipping-policy.md"}, ctx)
    assert r["ok"] and r["authoritative"] is True and "Delivered but not received" in r["content"]
    # Lenient ids are accepted
    assert services.registry.execute(
        "read_knowledge_document", {"document_id": "knowledge-base/policies/return-policy"}, ctx
    )["ok"]
    missing = services.registry.execute("read_knowledge_document", {"document_id": "policies/nope.md"}, ctx)
    assert missing["error"]["code"] == "document_not_found"


def test_invalid_category(services, ctx_for):
    r = services.registry.execute("search_knowledge_base", {"query": "x", "category": "blog"}, ctx_for("5001"))
    assert r["error"]["code"] == "invalid_category"


def test_workflows(services, ctx_for):
    assert set(services.workflows.names) >= {
        "order-inquiries",
        "returns-and-refunds",
        "frustrated-customers",
        "product-questions",
        "escalation",
        "ambiguous-and-follow-ups",
    }
    r = services.registry.execute("read_workflow", {"workflow_name": "frustrated-customers"}, ctx_for("5002"))
    assert "investigate before you empathize" in r["content"].lower()


def test_policy_parameters_come_from_front_matter(services):
    p = services.policy
    assert p.return_window_days == 30
    assert p.damage_report_window_days == 14
    assert p.final_sale_skus == {"NS-MUG-4C"}
    assert p.unopened_only_skus == {"NS-EAR-02"}
    assert p.trace_after_business_days_without_scan == 5
    assert p.presumed_lost_business_days_past_estimate == 10
    assert p.delivered_not_received_wait_hours == 48


def test_business_days():
    assert business_days_between(date(2026, 11, 6), date(2026, 11, 9)) == 1  # Fri -> Mon
    assert business_days_between(date(2026, 11, 10), date(2026, 11, 12)) == 1  # Veterans Day skipped
    assert business_days_between(date(2026, 11, 9), date(2026, 11, 9)) == 0


def test_warranty_end():
    assert warranty_end(date(2026, 9, 22), "12 months") == date(2027, 9, 22)
    assert warranty_end(date(2026, 9, 22), "90 days") == date(2026, 12, 21)
    assert warranty_end(date(2026, 9, 22), "n/a") is None


def test_no_conflicting_sources_of_truth(services, settings):
    assert check_knowledge_base(services.kb, settings.data_dir / "shopify-products.json") == []


def test_mock_data_is_internally_consistent(settings):
    assert check_data(settings.data_dir) == []


def test_eval_cases_reference_real_tickets_and_tools(services, settings):
    assert (
        check_eval_cases(
            settings.project_root / "evals" / "test-cases.json", settings.data_dir, set(services.registry.specs)
        )
        == []
    )
