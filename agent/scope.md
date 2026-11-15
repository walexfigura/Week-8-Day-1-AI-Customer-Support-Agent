# Agent Scope Definition

This document defines what the Northstar Supply support agent is for, what it
is explicitly *not* for, and what it is permitted to do. Defining scope before
writing prompts or code is the single most useful design step for a support
agent: it decides which tools exist, which knowledge must be written, and which
requests must go straight to a human.

Northstar Supply is fictional. Nothing in this repository describes a real business.

## Who the agent serves

- **Customers** who write in through Zendesk (email or web form).
- **Support staff**, who receive the agent's investigation, evidence, and draft
  reply, and decide what to send.

The agent is an *assistant to the support team*, not a replacement for it.

## In-scope requests

| Request type | What the agent does | Workflow |
|---|---|---|
| Order status | Looks up the verified order and explains where it is. | `order-inquiries.md` |
| Shipping estimates | Explains processing and transit times from the shipping policy. | `order-inquiries.md` |
| Tracking information | Shares carrier, tracking number, and latest scan. | `order-inquiries.md` |
| Delivery problems | Late, stalled, partial, or "delivered but missing" shipments. | `order-inquiries.md` |
| Returns & refunds | Explains eligibility (calculated in code), process, and refund timing. | `returns-and-refunds.md` |
| Product questions | Answers from product records and FAQs. | `product-questions.md` |
| Basic troubleshooting | Walks through documented steps; escalates safety issues. | `product-questions.md` |
| Frustrated customers | Investigates history before replying; escalates repeat contacts. | `frustrated-customers.md` |
| Human requests | Always routes to a person. | `escalation.md` |

## Out of scope (always escalate)

- Payment disputes, chargebacks, billing errors → **Billing**
- Legal threats or regulatory complaints → **Support Lead**
- Account security (hacked account, unrecognized orders) → **Trust & Safety**
- Fraud allegations or suspicious requests → **Trust & Safety**
- Changing account details (email, password, saved addresses)
- Any financial action beyond reading records

## Permission model

The agent's permissions are enforced by the Python tool registry
(`src/customer_support_agent/tools/registry.py`), not only by the prompt.

| Capability | Permission | Enforced by |
|---|---|---|
| Read the current ticket and the same requester's tickets | Allowed | `ToolContext` ticket access check |
| Read orders owned by the verified requester | Allowed | Identity check in `tools/shopify.py` |
| Read products and knowledge documents | Allowed | — |
| Draft customer replies | Allowed (draft only) | Output is returned, never sent |
| Create human approval requests | Allowed | `approvals.py` validates every request |
| Send customer messages | **Blocked** | Not exposed; registry rejects the call |
| Issue refunds / make payments | **Blocked** | Not exposed; registry rejects the call |
| Cancel or modify orders | **Blocked** | Not exposed; registry rejects the call |
| Close or solve tickets | **Blocked** | Not exposed; registry rejects the call |
| Approve its own approval requests | **Blocked** | `ApprovalQueue.approve` rejects agent approvers |

## Success criteria

A run is successful when the agent:

1. Uses only information it retrieved (orders, tickets, documents).
2. Answers every question the customer asked, or asks for what is missing.
3. Cites the policy documents it relied on.
4. Escalates every case the escalation rules require.
5. Never claims a refund, cancellation, or exception was approved.
6. Leaks no data belonging to anyone other than the verified requester.

These criteria are what `evals/test-cases.json` measures.
