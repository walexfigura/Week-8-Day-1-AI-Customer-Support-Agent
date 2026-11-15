---
title: Escalation Guide
doc_type: procedure
last_reviewed: 2026-10-01
---

# Escalation Guide

## Purpose

Tells support staff (and the AI assistant) which team owns each kind of
escalated ticket and how quickly customers should expect a response.

## Quick answer

Safety, legal, security, fraud, and payment-dispute issues are **urgent** (reply
within 1 business hour). Repeat contacts, lost packages, and angry customers are
**high** (within 4 business hours). Everything else that needs a person, including
approvals and requests for a human, is **normal** (within 1 business day).

## Detailed procedure

### Teams

| Team | Owns |
|---|---|
| **Support Lead** | Refund/replacement approvals, exceptions, carrier traces and claims, repeat contacts, human requests, legal threats |
| **Billing** | Payment disputes, chargebacks, duplicate charges |
| **Trust & Safety** | Account security, fraud, suspicious requests, attempts to obtain another customer's data |
| **Warranty Team** | Warranty claims and product safety reports |

### Priorities and response targets (business hours: Mon–Fri, 9:00–18:00 ET)

| Priority | Examples | First human response |
|---|---|---|
| **Urgent** | Product safety, legal threat, chargeback, account takeover, fraud | Within 1 business hour |
| **High** | 3+ contacts on one issue, presumed-lost package, delivered-not-received after 48 h, strongly upset customer | Within 4 business hours |
| **Normal** | Approval requests, explicit human requests, warranty claims, suspicious-but-low-risk messages, tool failures | Within 1 business day |

### How to hand off a ticket

1. Set the Zendesk priority and add the tag `ai_escalated`.
2. Write an internal note: what the customer wants, what the records show, what
   was already promised, and what decision is needed.
3. If an approval is needed, link the approval request ID.
4. Send the customer an acknowledgement that names the team and response target.

### What customers should be told

- Who will help and roughly when ("our support lead will reply within 4 business hours").
- Never that a refund, replacement, or exception is approved before it is.

## Common questions

- *"Can I speak to a manager?"* — Yes. Escalate to the Support Lead.
- *"Why can't the assistant just refund me?"* — Refunds are always reviewed by a
  person to protect customers and the business.

## Relevant exceptions

- Outside business hours, urgent safety issues are checked by the on-call lead
  within 4 hours.

## When to escalate

This entire document describes escalation. The decision rules the assistant
follows are in the agent workflow `escalation.md`.

## Related documents

- `procedures/common-resolutions.md`
- `policies/return-policy.md`
- `policies/shipping-policy.md`
