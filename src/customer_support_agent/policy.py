"""Deterministic policy calculations.

Language models are unreliable at date arithmetic, so anything numeric — return
windows, business days past an estimate, hours since delivery — is calculated
here, in code, and handed to the model as facts.

The *numbers* come from the front matter of the authoritative policy documents
(`knowledge-base/policies/*.md`). That keeps one source of truth: change
`return_window_days` in `return-policy.md` and both the agent's answers and
these calculations change together.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .knowledge import KnowledgeBase

# US federal holidays that fall inside the fictional dataset's time range.
# A production system would use a holiday calendar library or carrier data.
US_FEDERAL_HOLIDAYS = {
    date(2026, 9, 7),  # Labor Day
    date(2026, 10, 12),  # Columbus Day / Indigenous Peoples' Day
    date(2026, 11, 11),  # Veterans Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas Day
    date(2027, 1, 1),  # New Year's Day
}


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_business_day(day: date) -> bool:
    return day.weekday() < 5 and day not in US_FEDERAL_HOLIDAYS


def business_days_between(start: date, end: date) -> int:
    """Count business days d with start < d <= end (0 if end <= start)."""
    if end <= start:
        return 0
    count, day = 0, start
    while day < end:
        day += timedelta(days=1)
        if is_business_day(day):
            count += 1
    return count


def _csv(value: str | None) -> set[str]:
    return {item.strip() for item in (value or "").split(",") if item.strip()}


def _num(meta: dict[str, str], key: str) -> float:
    if key not in meta:
        raise KeyError(f"Policy front matter is missing required key '{key}'")
    return float(meta[key])


@dataclass(frozen=True)
class PolicyConfig:
    return_window_days: int
    damage_report_window_days: int
    return_label_fee_usd: float
    final_sale_skus: frozenset[str]
    non_returnable_skus: frozenset[str]
    unopened_only_skus: frozenset[str]
    trace_after_business_days_without_scan: int
    presumed_lost_business_days_past_estimate: int
    delivered_not_received_wait_hours: int

    @classmethod
    def from_knowledge_base(cls, kb: KnowledgeBase) -> PolicyConfig:
        returns = kb.get("policies/return-policy.md")
        shipping = kb.get("policies/shipping-policy.md")
        if returns is None or shipping is None:
            raise FileNotFoundError("policies/return-policy.md and policies/shipping-policy.md are required")
        r, s = returns.metadata, shipping.metadata
        return cls(
            return_window_days=int(_num(r, "return_window_days")),
            damage_report_window_days=int(_num(r, "damage_report_window_days")),
            return_label_fee_usd=_num(r, "return_label_fee_usd"),
            final_sale_skus=frozenset(_csv(r.get("final_sale_skus"))),
            non_returnable_skus=frozenset(_csv(r.get("non_returnable_skus"))),
            unopened_only_skus=frozenset(_csv(r.get("unopened_only_skus"))),
            trace_after_business_days_without_scan=int(_num(s, "trace_after_business_days_without_scan")),
            presumed_lost_business_days_past_estimate=int(_num(s, "presumed_lost_business_days_past_estimate")),
            delivered_not_received_wait_hours=int(_num(s, "delivered_not_received_wait_hours")),
        )


def warranty_end(delivered: date, warranty: str | None) -> date | None:
    """Convert a product's warranty string ("12 months", "90 days") into an end date."""
    if not warranty:
        return None
    match = re.match(r"(\d+)\s*(month|day)", warranty.strip().lower())
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2)
    if unit == "day":
        return delivered + timedelta(days=amount)
    month_index = delivered.month - 1 + amount
    year, month = delivered.year + month_index // 12, month_index % 12 + 1
    day = min(delivered.day, 28)  # avoid invalid dates like Feb 30
    return date(year, month, day)


def assess_shipment(fulfillment: dict[str, Any], policy: PolicyConfig, now: datetime) -> dict[str, Any]:
    """Classify one shipment as on_track / late / trace_recommended / presumed_lost / delivered."""
    today = now.date()
    delivered_at = parse_iso(fulfillment.get("delivered_at"))
    if delivered_at:
        hours = (now - delivered_at).total_seconds() / 3600
        return {
            "state": "delivered",
            "delivered_on": delivered_at.date().isoformat(),
            "hours_since_delivery": round(hours, 1),
            "days_since_delivery": (today - delivered_at.date()).days,
            "delivered_not_received_wait_satisfied": hours >= policy.delivered_not_received_wait_hours,
        }

    events = fulfillment.get("tracking_events") or []
    estimate = fulfillment.get("estimated_delivery")
    estimate_date = date.fromisoformat(estimate) if estimate else None
    if not events:
        return {"state": "tracking_pending", "estimated_delivery": estimate}

    last_scan = max(parse_iso(e["timestamp"]) for e in events if e.get("timestamp"))
    since_scan = business_days_between(last_scan.date(), today)
    past_estimate = business_days_between(estimate_date, today) if estimate_date else 0

    if past_estimate >= policy.presumed_lost_business_days_past_estimate:
        state = "presumed_lost"
    elif since_scan >= policy.trace_after_business_days_without_scan:
        state = "trace_recommended"
    elif estimate_date and today > estimate_date:
        state = "late"
    else:
        state = "on_track"
    return {
        "state": state,
        "estimated_delivery": estimate,
        "last_scan_on": last_scan.date().isoformat(),
        "business_days_since_last_scan": since_scan,
        "business_days_past_estimate": past_estimate,
    }


def return_eligibility(
    order: dict[str, Any],
    products_by_sku: dict[str, dict[str, Any]],
    policy: PolicyConfig,
    now: datetime,
) -> list[dict[str, Any]]:
    """Per-line-item return eligibility, calculated from the order and the policy."""
    today = now.date()
    delivered_by_sku: dict[str, date] = {}
    for fulfillment in order.get("fulfillments", []):
        delivered_at = parse_iso(fulfillment.get("delivered_at"))
        if delivered_at:
            for sku in fulfillment.get("line_item_skus", []):
                delivered_by_sku[sku] = delivered_at.date()
    refunded_skus = {s for r in order.get("refunds", []) for s in r.get("line_item_skus", [])}
    returned_skus = {s for r in order.get("returns", []) for s in r.get("line_item_skus", [])}

    results = []
    for item in order.get("line_items", []):
        sku = item["sku"]
        product = products_by_sku.get(sku, {})
        reasons: list[str] = []
        delivered = delivered_by_sku.get(sku)
        entry: dict[str, Any] = {"sku": sku, "title": item["title"], "unit_price": item["unit_price"]}

        if sku in refunded_skus or sku in returned_skus:
            entry.update(eligibility="not_eligible", reasons=["already_returned_or_refunded"])
            results.append(entry)
            continue
        if delivered is None:
            entry.update(
                eligibility="not_eligible",
                reasons=["not_delivered_yet"],
                note="Items can be returned after delivery. For late or lost packages, follow the shipping policy.",
            )
            results.append(entry)
            continue

        days = (today - delivered).days
        window_ends = delivered + timedelta(days=policy.return_window_days)
        damage_ends = delivered + timedelta(days=policy.damage_report_window_days)
        w_end = warranty_end(delivered, product.get("warranty"))

        if sku in policy.non_returnable_skus:
            reasons.append("non_returnable_item")
        if sku in policy.final_sale_skus or product.get("final_sale"):
            reasons.append("final_sale")
        if days > policy.return_window_days:
            reasons.append("outside_return_window")

        if reasons:
            eligibility = "not_eligible"
        elif sku in policy.unopened_only_skus:
            eligibility = "eligible_if_unopened"
            reasons.append("returnable_only_if_unopened")
        else:
            eligibility = "eligible"

        if days <= policy.damage_report_window_days:
            defect_path = "damage_window_replacement_or_refund"
        elif w_end and today <= w_end:
            defect_path = "warranty_claim"
        else:
            defect_path = "not_covered"

        entry.update(
            eligibility=eligibility,
            reasons=reasons,
            delivered_on=delivered.isoformat(),
            days_since_delivery=days,
            return_window_ends=window_ends.isoformat(),
            damage_report_window_ends=damage_ends.isoformat(),
            warranty=product.get("warranty"),
            warranty_ends=w_end.isoformat() if w_end else None,
            if_damaged_or_defective=defect_path,
            change_of_mind_label_fee_usd=policy.return_label_fee_usd,
        )
        results.append(entry)
    return results
