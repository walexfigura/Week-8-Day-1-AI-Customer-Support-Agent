"""Live adapter tests with intercepted HTTP (no credentials, no network).

These verify request construction, normalization, retries, and error mapping
against *documented* response shapes. They do NOT prove the adapters work
against a real Shopify store or Zendesk account — see docs/integrating-*.md.
"""

from __future__ import annotations

import json

import httpx
import pytest

from customer_support_agent.tools.base import BackendAuthError, BackendUnavailableError
from customer_support_agent.tools.http_utils import request_json
from customer_support_agent.tools.shopify import LiveShopifyBackend
from customer_support_agent.tools.zendesk import LiveZendeskBackend

SHOPIFY_ORDER_NODE = {
    "id": "gid://shopify/Order/1",
    "name": "#1043",
    "createdAt": "2026-10-19T20:41:00Z",
    "email": "jordan.ellis@example.com",
    "note": None,
    "displayFinancialStatus": "PAID",
    "displayFulfillmentStatus": "FULFILLED",
    "shippingLine": {"title": "Standard"},
    "shippingAddress": {
        "address1": "77 Cedar Hollow Ln",
        "city": "Austin",
        "provinceCode": "TX",
        "zip": "78704",
        "countryCodeV2": "US",
    },
    "customer": {"id": "gid://shopify/Customer/8002", "displayName": "Jordan Ellis"},
    "subtotalPriceSet": {"shopMoney": {"amount": "124.00", "currencyCode": "USD"}},
    "totalShippingPriceSet": {"shopMoney": {"amount": "0.00"}},
    "totalPriceSet": {"shopMoney": {"amount": "124.00", "currencyCode": "USD"}},
    "lineItems": {
        "nodes": [
            {
                "sku": "NS-BLK-50",
                "title": "Cloudrest Cotton Throw Blanket",
                "quantity": 1,
                "unfulfilledQuantity": 0,
                "originalUnitPriceSet": {"shopMoney": {"amount": "79.00"}},
                "product": {"id": "gid://shopify/Product/7003"},
            }
        ]
    },
    "fulfillments": [
        {
            "id": "gid://shopify/Fulfillment/9",
            "status": "SUCCESS",
            "displayStatus": "IN_TRANSIT",
            "createdAt": "2026-10-20T17:15:00Z",
            "deliveredAt": None,
            "estimatedDeliveryAt": "2026-10-27T00:00:00Z",
            "trackingInfo": [{"company": "USPS", "number": "9400100000000000104301", "url": "https://t.example/1"}],
            "fulfillmentLineItems": {"nodes": [{"lineItem": {"sku": "NS-BLK-50"}}]},
            "events": {
                "nodes": [
                    {
                        "happenedAt": "2026-10-23T03:50:00Z",
                        "status": "IN_TRANSIT",
                        "message": "Arrived",
                        "city": "Memphis",
                        "province": "TN",
                    }
                ]
            },
        }
    ],
    "refunds": [],
}


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_shopify_order_query_and_normalization():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["token"] = request.headers["X-Shopify-Access-Token"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"orders": {"nodes": [SHOPIFY_ORDER_NODE]}}})

    backend = LiveShopifyBackend("demo-store.myshopify.com", "shpat_test", "2026-10", client=mock_client(handler))
    order = backend.get_order("1043")
    assert seen["url"] == "https://demo-store.myshopify.com/admin/api/2026-10/graphql.json"
    assert seen["token"] == "shpat_test"
    assert seen["body"]["variables"] == {"query": "name:#1043"}
    assert order["order_number"] == "1043"
    assert order["customer"]["email"] == "jordan.ellis@example.com"
    f = order["fulfillments"][0]
    assert f["estimated_delivery"] == "2026-10-27" and f["tracking_number"].startswith("9400")
    assert f["tracking_events"][0]["location"] == "Memphis, TN"
    assert order["line_items"][0]["product_id"] == "7003"


def test_normalized_live_order_works_with_policy_assessment(services):
    from customer_support_agent.policy import assess_shipment

    backend = LiveShopifyBackend(
        "s.myshopify.com",
        "t",
        "2026-10",
        client=mock_client(lambda r: httpx.Response(200, json={"data": {"orders": {"nodes": [SHOPIFY_ORDER_NODE]}}})),
    )
    order = backend.get_order("1043")
    state = assess_shipment(order["fulfillments"][0], services.policy, services.settings.reference_datetime)["state"]
    assert state == "presumed_lost"  # same answer as the mock backend


def test_shopify_not_found_and_throttling():
    empty = LiveShopifyBackend(
        "s.myshopify.com",
        "t",
        "2026-10",
        client=mock_client(lambda r: httpx.Response(200, json={"data": {"orders": {"nodes": []}}})),
    )
    assert empty.get_order("9999") is None
    throttled = LiveShopifyBackend(
        "s.myshopify.com",
        "t",
        "2026-10",
        client=mock_client(
            lambda r: httpx.Response(
                200, json={"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}]}
            )
        ),
    )
    with pytest.raises(BackendUnavailableError, match="throttled"):
        throttled.get_order("1043")


def test_zendesk_ticket_normalization():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert request.headers["Authorization"].startswith("Basic ")
        if path == "/api/v2/tickets/5002.json":
            return httpx.Response(
                200,
                json={
                    "ticket": {
                        "id": 5002,
                        "subject": "Package never arrived",
                        "status": "open",
                        "priority": "normal",
                        "requester_id": 9002,
                        "tags": [],
                        "via": {"channel": "email"},
                        "created_at": "2026-10-29T15:10:00Z",
                        "updated_at": "2026-11-10T13:55:00Z",
                    }
                },
            )
        if path == "/api/v2/tickets/5002/comments.json":
            return httpx.Response(
                200,
                json={
                    "comments": [
                        {
                            "id": 2,
                            "author_id": 100,
                            "body": "We'll check",
                            "public": True,
                            "created_at": "2026-10-30T13:45:00Z",
                        },
                        {
                            "id": 1,
                            "author_id": 9002,
                            "body": "Where is it?",
                            "public": True,
                            "created_at": "2026-10-29T15:10:00Z",
                        },
                    ]
                },
            )
        if path == "/api/v2/users/show_many.json":
            return httpx.Response(
                200,
                json={
                    "users": [
                        {"id": 9002, "name": "Jordan Ellis", "email": "jordan.ellis@example.com", "role": "end-user"},
                        {"id": 100, "name": "Chris Doyle", "email": "chris@x.example", "role": "agent"},
                    ]
                },
            )
        return httpx.Response(404)

    backend = LiveZendeskBackend(
        "northstar",
        "agent@x.example",
        "token",
        client=httpx.Client(transport=httpx.MockTransport(handler), auth=("agent@x.example/token", "token")),
    )
    ticket = backend.get_ticket("5002")
    assert ticket["requester"]["email"] == "jordan.ellis@example.com"
    assert [c["author_role"] for c in ticket["comments"]] == ["customer", "staff"]  # sorted oldest first
    assert backend.get_ticket("404404") is None


def test_rate_limit_retry_honors_retry_after():
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"ok": True})

    assert request_json(mock_client(handler), "GET", "https://x.zendesk.com/api/v2/a", sleep=sleeps.append) == {
        "ok": True
    }
    assert sleeps == [2.0]


def test_persistent_server_errors_raise_backend_unavailable():
    sleeps = []
    with pytest.raises(BackendUnavailableError):
        request_json(mock_client(lambda r: httpx.Response(503)), "GET", "https://x.example/a", sleep=sleeps.append)
    assert len(sleeps) == 2  # 3 attempts, 2 waits


def test_auth_errors_are_not_retried():
    with pytest.raises(BackendAuthError):
        request_json(mock_client(lambda r: httpx.Response(401)), "GET", "https://x.example/a", sleep=lambda s: None)


def test_live_backends_require_credentials():
    with pytest.raises(ValueError):
        LiveShopifyBackend("", "", "2026-10")
    with pytest.raises(ValueError):
        LiveZendeskBackend("sub", "", "")
