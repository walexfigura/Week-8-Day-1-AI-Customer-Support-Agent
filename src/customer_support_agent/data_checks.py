"""Consistency checks across mock data, knowledge base, webhook samples, and evals.

Fictional data drifts easily: someone changes a price, a SKU, or a policy
number in one file and forgets the others. `run_all_checks` catches that, and
both `python run.py smoke` and the unit tests call it.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

from .knowledge import KnowledgeBase
from .policy import PolicyConfig, parse_iso

EXPECTED_WARRANTY = {"NS-LMP-01": "12 months", "NS-EAR-02": "12 months", "NS-BTL-24": "24 months", "NS-GIFT-50": "n/a"}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def check_data(data_dir: Path) -> list[str]:
    problems: list[str] = []
    products = {p["sku"]: p for p in _load(data_dir / "shopify-products.json")["products"]}
    orders = _load(data_dir / "shopify-orders.json")["orders"]
    zendesk = _load(data_dir / "zendesk-tickets.json")
    users = {u["id"]: u for u in zendesk["users"]}
    customer_emails = {u["email"] for u in users.values() if u["role"] == "end-user"}

    for sku, product in products.items():
        expected = EXPECTED_WARRANTY.get(sku, "90 days")
        if product["warranty"] != expected:
            problems.append(f"product {sku}: warranty {product['warranty']!r} != policy {expected!r}")

    for order in orders:
        num = order["order_number"]
        if order["name"] != f"#{num}":
            problems.append(f"order {num}: name {order['name']!r} does not match number")
        if order["customer"]["email"] not in customer_emails:
            problems.append(f"order {num}: customer email has no matching Zendesk user")
        line_skus = set()
        subtotal = Decimal("0")
        for li in order["line_items"]:
            line_skus.add(li["sku"])
            product = products.get(li["sku"])
            if product is None:
                problems.append(f"order {num}: unknown SKU {li['sku']}")
                continue
            if li["product_id"] != product["product_id"] or li["title"] != product["title"]:
                problems.append(f"order {num}: line item {li['sku']} does not match product record")
            if Decimal(li["unit_price"]) != Decimal(product["price"]):
                problems.append(f"order {num}: {li['sku']} price {li['unit_price']} != catalog {product['price']}")
            subtotal += Decimal(li["unit_price"]) * li["quantity"]
        totals = order["totals"]
        if subtotal != Decimal(totals["subtotal"]):
            problems.append(f"order {num}: subtotal {totals['subtotal']} != line items {subtotal}")
        if Decimal(totals["subtotal"]) + Decimal(totals["shipping"]) != Decimal(totals["total"]):
            problems.append(f"order {num}: subtotal + shipping != total")
        created = parse_iso(order["created_at"])
        for f in order["fulfillments"]:
            if not set(f["line_item_skus"]) <= line_skus:
                problems.append(f"order {num}: fulfillment {f['id']} ships SKUs not on the order")
            shipped = parse_iso(f["shipped_at"])
            if shipped and created and shipped < created:
                problems.append(f"order {num}: shipped before it was placed")
            delivered = parse_iso(f.get("delivered_at"))
            if delivered and shipped and delivered < shipped:
                problems.append(f"order {num}: delivered before it shipped")
            if bool(delivered) != (f["display_status"] == "DELIVERED"):
                problems.append(f"order {num}: delivered_at and display_status disagree")
        for refund in order["refunds"]:
            if Decimal(refund["amount"]) > Decimal(totals["total"]):
                problems.append(f"order {num}: refund exceeds total")

    ticket_ids = set()
    for ticket in zendesk["tickets"]:
        ticket_ids.add(ticket["id"])
        if ticket["requester_id"] not in users:
            problems.append(f"ticket {ticket['id']}: unknown requester {ticket['requester_id']}")
        for comment in ticket["comments"]:
            if comment["author_id"] not in users:
                problems.append(f"ticket {ticket['id']}: comment {comment['id']} has unknown author")

    for event in _load(data_dir / "webhook-events.json")["events"]:
        subject = event["payload"].get("subject")
        if subject and subject.rsplit(":", 1)[-1] not in ticket_ids:
            problems.append(f"webhook event {event['name']}: references unknown ticket {subject}")
    return problems


def check_knowledge_base(kb: KnowledgeBase, products_path: Path) -> list[str]:
    problems: list[str] = []
    policy = PolicyConfig.from_knowledge_base(kb)
    products = {p["sku"]: p for p in _load(products_path)["products"]}
    for sku in policy.final_sale_skus | policy.non_returnable_skus:
        if sku not in products:
            problems.append(f"return policy lists unknown SKU {sku}")
        elif not products[sku]["final_sale"]:
            problems.append(f"return policy says {sku} is final sale/non-returnable but product record disagrees")
    for sku in policy.unopened_only_skus:
        if products.get(sku, {}).get("returnable_if_opened", True):
            problems.append(f"return policy says {sku} is unopened-only but product record disagrees")

    # FAQs must agree with the authoritative numbers.
    window = f"{policy.return_window_days} days"
    for doc_id in ("faqs/returns/exchange-process.md", "policies/return-policy.md"):
        doc = kb.get(doc_id)
        if doc and window not in doc.body:
            problems.append(f"{doc_id} does not mention the {window} return window")
    refund_faq = kb.get("faqs/returns/refund-timeline.md")
    if refund_faq:
        meta = kb.get("policies/return-policy.md").metadata  # type: ignore[union-attr]
        for value in (
            f"{meta['refund_processing_business_days']} business days",
            meta["bank_posting_business_days"].replace("-", "–"),
        ):
            if value not in refund_faq.body:
                problems.append(f"refund-timeline FAQ does not mention '{value}' from the return policy")

    # Every "Related documents" link must exist.
    for doc in kb.documents.values():
        for ref in re.findall(r"`((?:policies|faqs|procedures)/[\w\-/]+\.md)`", doc.body):
            if kb.get(ref) is None:
                problems.append(f"{doc.doc_id} links to missing document {ref}")
    return problems


def check_eval_cases(cases_path: Path, data_dir: Path, tool_names: set[str]) -> list[str]:
    problems: list[str] = []
    ticket_ids = {t["id"] for t in _load(data_dir / "zendesk-tickets.json")["tickets"]}
    seen = set()
    for case in _load(cases_path)["cases"]:
        cid = case["id"]
        if cid in seen:
            problems.append(f"duplicate eval case id {cid}")
        seen.add(cid)
        for key in ("input", "expected_behavior", "important_evidence", "prohibited_actions", "checks"):
            if key not in case:
                problems.append(f"eval case {cid} is missing '{key}'")
        if case.get("input", {}).get("ticket_id") not in ticket_ids:
            problems.append(f"eval case {cid} references unknown ticket")
        checks = case.get("checks", {})
        named = list(checks.get("must_call_tools", [])) + list(checks.get("prohibited_tools", []))
        named += [t for group in checks.get("must_call_any_of", []) for t in group]
        named += list(case.get("input", {}).get("fault_injection", {}))
        for tool in named:
            if tool not in tool_names:
                problems.append(f"eval case {cid} names unknown tool {tool}")
    return problems
