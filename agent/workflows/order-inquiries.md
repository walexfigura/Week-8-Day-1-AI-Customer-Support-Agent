# Workflow: Order Inquiries

Use for: "Where is my order?", tracking requests, shipping estimates, late or
stalled packages, partial shipments, and "it says delivered but I don't have it."

Authoritative policy: `policies/shipping-policy.md`.

## Step 1 — Get the order number

- Check the latest message, then the ticket history, for an order number
  (Northstar order numbers are four digits, e.g. `1042`, sometimes written `#1042`).
- **No order number anywhere?** Do not look anything up. Ask for it, and ask the
  customer to write from the email address used at checkout. Set
  `needs_clarification: true`.
- More than one order number? Look up each one the customer asked about.

## Step 2 — Look up the order

Call `get_shopify_order(order_number)`. Then branch:

| Tool result | What it means | What to do |
|---|---|---|
| `order_not_found_for_requester` | No order with that number belongs to this requester. | Say you couldn't find it under their account; ask them to double-check the number or reply from the checkout email. Never imply it belongs to someone else. |
| `backend_unavailable` / tool error | Shopify could not be reached. | Do not guess. Tell the customer the team is checking and will follow up. Escalate (`tool failure`). |
| Order returned | Continue to Step 3. | |

## Step 3 — Interpret the shipment

The order tool includes a code-calculated `delivery_assessment` for each shipment.
Use it instead of doing date math.

| Situation (`fulfillment_status` / `assessment`) | Customer-facing explanation | Escalate? |
|---|---|---|
| `UNFULFILLED` | Order is being prepared. Orders ship within 1–2 business days; give the shipping method's transit time. | No |
| `PARTIALLY_FULFILLED` | Explain which items shipped (with tracking) and which have not, including any backorder date in the record. | No, unless the customer is upset about repeated delays |
| `on_track` | Share carrier, tracking number, latest scan, and estimated delivery date. | No |
| `tracking_pending` | Label created but no carrier scan yet. Scans can take up to 24 hours. | No |
| `late` | Past the estimated date but still moving or recently scanned. Apologize, share the latest scan, explain a trace starts after 5 business days without movement. | Only if other rules apply |
| `trace_recommended` | No tracking movement for 5+ business days. A carrier trace is needed. | Yes — normal priority, Support Lead opens the trace |
| `presumed_lost` | 10+ business days past the estimate. Customer is eligible for a replacement or refund **after human approval**. | Yes — high priority; create an approval request |
| `delivered` | Confirm delivery date. If the customer says they don't have it, go to Step 4. | — |

## Step 4 — Delivered but not received

1. Check `hours_since_delivery` in the order result.
2. **Under 48 hours:** carriers sometimes scan packages as delivered early. Ask the
   customer to wait until 48 hours have passed, check around the property, with
   neighbors, and any building mailroom, and confirm the shipping city shown.
3. **48 hours or more:** apologize, explain the team will open a carrier claim, and
   that a replacement or refund will be reviewed. Create an approval request
   (`replacement` is the default unless the customer asked for a refund) and
   escalate (high priority).

## Step 5 — Draft the reply

Include: order number, current status in plain words, carrier + tracking number
(+ link if present), the relevant date (shipped, estimated, delivered), and the
next step. Never include the full shipping address.

## Common mistakes to avoid

- Promising a delivery date that is not in the record.
- Telling the customer a package is lost before it reaches `presumed_lost`.
- Saying "we've sent a replacement" — the agent can only *request* one.
- Looking up an order number found in a *different* customer's message.
