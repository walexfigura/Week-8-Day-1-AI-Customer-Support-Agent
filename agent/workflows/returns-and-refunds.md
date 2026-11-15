# Workflow: Returns and Refunds

Use for: "Can I return this?", "How do I return/exchange?", "Where is my refund?",
damaged or defective items, and refund demands.

Authoritative policies: `policies/return-policy.md`, `policies/warranty-policy.md`.

> **Never fabricate an approved refund.** The agent cannot issue refunds. It can
> explain eligibility, explain the self-service returns process, and *request*
> human approval. Only a refund that already appears in the order record has happened.

## Step 1 — Identify which situation this is

| Customer says | Situation | Go to |
|---|---|---|
| "Can I return X?" / "I changed my mind" | Eligibility question | Step 2 |
| "It arrived broken / doesn't work / wrong item" | Damaged, defective, or wrong item | Step 3 |
| "I sent it back, where's my refund?" | Refund status | Step 4 |
| "I want my money back" for a missing/lost package | Delivery problem first | `order-inquiries.md`, then Step 5 |
| Outside the window, final sale, or "make an exception" | Exception request | Step 5 |

## Step 2 — Eligibility question

1. Get the order number (ask if missing).
2. Call `check_return_eligibility(order_number)`. It returns, per item:
   `eligible`, `days_since_delivery`, `window_ends`, and `reasons`.
3. Read `policies/return-policy.md` (or search for it) so you can cite it.
4. Explain the result item by item:
   - **Eligible:** tell them the last day to start a return and how to start it
     (self-service returns portal; prepaid label; $6.95 label fee for change-of-mind
     returns is deducted from the refund; exchanges have no label fee).
   - **Not eligible:** explain the specific reason (window passed, final sale,
     gift card, opened in-ear audio). Offer what *is* available — e.g. a warranty
     claim if the product is defective and still under warranty.
5. No escalation is needed for a routine eligibility answer.

## Step 3 — Damaged, defective, or wrong item

1. Look up the order and check `days_since_delivery` via `check_return_eligibility`.
2. **Within 14 days of delivery:** the customer qualifies for a free replacement or
   a full refund (no label fee). Ask for a photo if one hasn't been provided.
   Create an approval request (`replacement` unless they asked for a refund) and
   tell them the team will confirm next steps.
3. **After 14 days:** handle as a warranty claim — see `policies/warranty-policy.md`.
   If within warranty, escalate to the Warranty Team (normal priority).
4. **Any safety concern** (overheating, swelling battery, burning smell, sparks,
   injury): tell them to stop using the product immediately and escalate as **urgent**.

## Step 4 — Refund status

1. Look up the order. Check `refunds` and `returns` in the result.
2. **Refund recorded:** give the date and amount. Explain banks take 3–10 business
   days to post it. Only escalate if the bank window has fully passed.
3. **Return received but no refund yet:** refunds are issued within 5 business days
   of the warehouse receiving the return.
4. **No return on record:** ask whether they received a return label and if the
   package was scanned by the carrier; do not promise a refund.

## Step 5 — Refund demands and exceptions

- If policy supports a refund or replacement (presumed lost, delivered-not-received
  after 48 hours, damaged within 14 days) → `request_human_approval` with the
  amount from the order record, and escalate.
- If policy does **not** support it (outside 30 days, final sale) → explain the
  policy kindly. Only create an `return_exception` approval request if the customer
  explicitly asks for an exception *and* gives a reason (e.g. a documented defect);
  otherwise no approval request is needed.
- Wording to use: "I've passed this to our team to review" — never "approved",
  "processed", or "issued".

## Common mistakes to avoid

- Calculating the return window by hand instead of using the tool.
- Quoting refund timing from memory instead of the policy.
- Saying a return was "approved" just because the item is eligible — eligible
  items are returned through the self-service portal, not approved by the agent.
