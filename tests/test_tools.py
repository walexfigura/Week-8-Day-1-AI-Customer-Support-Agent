"""Deterministic tests for the Shopify, Zendesk, and registry tools."""

from __future__ import annotations

import json

import pytest


def run(services, ctx, name, **args):
    return services.registry.execute(name, args, ctx)


# --- Order lookups and delivery assessment ---------------------------------


@pytest.mark.parametrize(
    "ticket, order, fulfillment_status, state",
    [
        ("5001", "1042", "FULFILLED", "on_track"),
        ("5002", "1043", "FULFILLED", "presumed_lost"),
        ("5009", "1049", "FULFILLED", "late"),
        ("5011", "1047", "FULFILLED", "delivered"),
        ("5010", "1046", "PARTIALLY_FULFILLED", "delivered"),
    ],
)
def test_order_lookup_and_assessment(services, ctx_for, ticket, order, fulfillment_status, state):
    result = run(services, ctx_for(ticket), "get_shopify_order", order_id=order)
    assert result["ok"], result
    view = result["order"]
    assert view["order_number"] == order
    assert view["fulfillment_status"] == fulfillment_status
    assert view["shipments"][0]["delivery_assessment"]["state"] == state


def test_unfulfilled_order_has_no_shipments(services, ctx_for):
    # Priya's ticket 5003 has no order number, but the requester does own 1044.
    view = run(services, ctx_for("5003"), "get_shopify_order", order_id="1044")["order"]
    assert view["fulfillment_status"] == "UNFULFILLED"
    assert view["shipments"] == []
    assert view["shipping_method"] == "Express"


def test_presumed_lost_math(services, ctx_for):
    a = run(services, ctx_for("5002"), "get_shopify_order", order_id="#1043")["order"]["shipments"][0][
        "delivery_assessment"
    ]
    assert a["business_days_past_estimate"] == 10
    assert a["business_days_since_last_scan"] == 12
    assert a["last_scan_on"] == "2026-10-23"


def test_delivered_not_received_wait_satisfied(services, ctx_for):
    a = run(services, ctx_for("5011"), "get_shopify_order", order_id="1047")["order"]["shipments"][0][
        "delivery_assessment"
    ]
    assert a["hours_since_delivery"] > 48
    assert a["delivered_not_received_wait_satisfied"] is True


def test_refund_visible_in_order(services, ctx_for):
    view = run(services, ctx_for("5005"), "get_shopify_order", order_id="1045")["order"]
    assert view["financial_status"] == "REFUNDED"
    assert view["refunds"][0]["amount"] == "59.00"
    assert view["refunds"][0]["created_at"].startswith("2026-11-03")


def test_backorder_date_visible(services, ctx_for):
    view = run(services, ctx_for("5010"), "get_shopify_order", order_id="1046")["order"]
    blanket = next(li for li in view["line_items"] if li["sku"] == "NS-BLK-50")
    assert blanket["fulfillment_status"] == "UNFULFILLED"
    assert blanket["backorder"]["expected_ship_date"] == "2026-11-18"


# --- Authorization and privacy ----------------------------------------------


def test_other_customers_order_is_not_disclosed(services, ctx_for):
    ctx = ctx_for("5008")  # Rex asks about Jordan's order 1043
    result = run(services, ctx, "get_shopify_order", order_id="1043")
    assert not result["ok"]
    assert result["error"]["code"] == "order_not_found_for_requester"
    assert "Jordan" not in json.dumps(result)
    assert ctx.security_blocks  # recorded for escalation rule E8


def test_missing_and_foreign_orders_look_identical(services, ctx_for):
    missing = run(services, ctx_for("5013"), "get_shopify_order", order_id="1099")
    foreign = run(services, ctx_for("5013"), "get_shopify_order", order_id="1043")
    assert missing["error"]["code"] == foreign["error"]["code"] == "order_not_found_for_requester"
    # The message differs only by the order number itself (no hint of existence).
    assert missing["error"]["message"].replace("1099", "N") == foreign["error"]["message"].replace("1043", "N")


def test_order_view_is_data_minimized(services, ctx_for):
    text = json.dumps(run(services, ctx_for("5001"), "get_shopify_order", order_id="1042"))
    assert "maya.chen@example.com" not in text
    assert "418 Willow Bend" not in text
    assert "4242" not in text  # card last4
    assert "Madison, WI" in text  # city/state is enough for delivery questions


def test_invalid_order_number(services, ctx_for):
    result = run(services, ctx_for("5001"), "get_shopify_order", order_id="abc")
    assert result["error"]["code"] == "invalid_order_number"


def test_injected_order_note_is_marked_untrusted(services, ctx_for):
    view = run(services, ctx_for("5008"), "get_shopify_order", order_id="1051")["order"]
    note = view["customer_order_note"]
    assert note["trust"] == "untrusted_customer_content"
    assert "warning" in note


def test_ticket_access_restricted_to_requester(services, ctx_for):
    result = run(services, ctx_for("5008"), "get_zendesk_ticket", ticket_id="5002")
    assert result["error"]["code"] == "ticket_not_accessible"
    ok = run(services, ctx_for("5010"), "get_zendesk_ticket", ticket_id="4990")  # same requester
    assert ok["ok"]


# --- Zendesk ------------------------------------------------------------------


def test_ticket_metadata_masks_email(services, ctx_for):
    t = run(services, ctx_for("5001"), "get_zendesk_ticket", ticket_id="5001")["ticket"]
    assert t["requester_email_masked"] == "m***@example.com"
    assert t["latest_customer_message"]["body"]["trust"] == "untrusted_customer_content"


def test_history_signals_for_frustrated_customer(services, ctx_for):
    r = run(services, ctx_for("5002"), "get_zendesk_ticket_history", ticket_id="5002")
    signals = r["signals"]
    assert signals["customer_message_count"] == 3
    assert signals["repeat_contact"] is True
    assert "refund_request" in signals["detected"]
    assert any("ridiculous" in i for i in signals["frustration_indicators"])
    assert r["last_staff_reply_at"].startswith("2026-10-30")


def test_list_requester_tickets_finds_unresolved_earlier_ticket(services, ctx_for):
    r = run(services, ctx_for("5010"), "list_requester_tickets", ticket_id="5010")
    others = r["other_tickets"]
    assert [t["ticket_id"] for t in others] == ["4990"]
    assert others[0]["status"] == "pending"


def test_missing_ticket(services, ctx_for):
    r = run(services, ctx_for("5001"), "get_zendesk_ticket", ticket_id="9999")
    assert r["error"]["code"] == "ticket_not_found"


# --- Products and returns -------------------------------------------------------


def test_product_lookup_by_id_sku_and_handle(services, ctx_for):
    ctx = ctx_for("5004")
    for ident in ("7001", "NS-BTL-24", "trailhead-insulated-bottle-24oz"):
        p = run(services, ctx, "get_shopify_product", product_id=ident)["product"]
        assert p["title"] == "Trailhead Insulated Bottle 24 oz"
    assert run(services, ctx, "get_shopify_product", product_id="nope")["error"]["code"] == "product_not_found"


def test_product_search(services, ctx_for):
    r = run(services, ctx_for("5004"), "search_shopify_products", query="Trailhead bottle")
    assert r["results"][0]["product_id"] == "7001"


def test_lamp_has_troubleshooting(services, ctx_for):
    p = run(services, ctx_for("5012"), "get_shopify_product", product_id="NS-LMP-01")["product"]
    assert any("Flickering" in step for step in p["troubleshooting"])


def test_return_eligibility(services, ctx_for):
    items = {i["sku"]: i for i in run(services, ctx_for("5006"), "check_return_eligibility", order_id="1048")["items"]}
    assert items["NS-MUG-4C"]["eligibility"] == "not_eligible"
    assert {"final_sale", "outside_return_window"} <= set(items["NS-MUG-4C"]["reasons"])
    assert items["NS-EAR-02"]["days_since_delivery"] == 49
    assert items["NS-EAR-02"]["if_damaged_or_defective"] == "warranty_claim"

    lamp = run(services, ctx_for("5012"), "check_return_eligibility", order_id="1041")["items"][0]
    assert lamp["eligibility"] == "eligible" and lamp["return_window_ends"] == "2026-11-27"

    damaged = run(services, ctx_for("5007"), "check_return_eligibility", order_id="1050")["items"][0]
    assert damaged["if_damaged_or_defective"] == "damage_window_replacement_or_refund"

    earbuds = run(services, ctx_for("5003"), "check_return_eligibility", order_id="1044")["items"][0]
    assert earbuds["reasons"] == ["not_delivered_yet"]

    returned = run(services, ctx_for("5005"), "check_return_eligibility", order_id="1045")["items"][0]
    assert returned["reasons"] == ["already_returned_or_refunded"]


# --- Registry enforcement ---------------------------------------------------------


@pytest.mark.parametrize("name", ["issue_refund", "cancel_order", "send_customer_message", "close_ticket"])
def test_restricted_operations_are_blocked(services, ctx_for, name):
    result = services.registry.execute(name, {"order_id": "1043"}, ctx_for("5002"))
    assert result["error"]["code"] == "permission_denied"


def test_unknown_tool(services, ctx_for):
    assert services.registry.execute("do_magic", {}, ctx_for("5001"))["error"]["code"] == "unknown_tool"


def test_no_write_tools_are_offered_to_the_model(services):
    offered = {t["name"] for t in services.registry.openai_tools()}
    assert not offered & {"issue_refund", "cancel_order", "send_customer_message", "close_ticket"}
    assert {"get_shopify_order", "request_human_approval", "submit_case_resolution"} <= offered


def test_schemas_are_strict(services):
    for tool in services.registry.openai_tools():
        params = tool["parameters"]
        assert tool["strict"] is True
        assert params["additionalProperties"] is False
        assert set(params["required"]) == set(params["properties"])


def test_argument_validation(services, ctx_for):
    ctx = ctx_for("5001")
    assert services.registry.execute("get_shopify_order", "{bad json", ctx)["error"]["code"] == "invalid_arguments"
    assert services.registry.execute("get_shopify_order", {}, ctx)["error"]["code"] == "invalid_arguments"
    assert (
        services.registry.execute("get_shopify_order", {"order_id": "1042", "x": 1}, ctx)["error"]["code"]
        == "invalid_arguments"
    )


def test_fault_injection_produces_structured_failure(services, ctx_for):
    ctx = ctx_for("5001", get_shopify_order="backend_unavailable")
    result = services.registry.execute("get_shopify_order", {"order_id": "1042"}, ctx)
    assert result["error"]["code"] == "backend_unavailable"
    assert result["error"]["retryable"] is True
    assert ctx.tool_failures == ["get_shopify_order"]
