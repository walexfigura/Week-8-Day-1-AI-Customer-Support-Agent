# Workflow: Frustrated Customers

Use when a message shows strong emotion (anger, all-caps, "ridiculous",
"unacceptable", repeated exclamation points), mentions previous contacts
("third time", "again", "still"), or when the history tool reports
frustration indicators.

Example:

> "This is ridiculous. I've contacted you THREE times and my package still
> hasn't arrived. I want my money back!"

A good agent hears **four** things in that message: a missing order, a refund
request, strong frustration, and repeated contact — which suggests earlier
promises weren't kept.

## Principle: investigate before you empathize

An apology without facts sounds like a script. Gather facts first, so the
reply can say something specific ("Your package hasn't moved since October 23,
and that's not okay").

## Step 1 — Read the whole history

1. `get_zendesk_ticket_history` — count the customer's messages and read what
   our team said previously. Did we promise a follow-up that never happened?
2. `list_requester_tickets` — are there other open or recent tickets about the
   same problem?
3. Note the heuristic `signals` the history tool returns. They are keyword-based
   hints, **not** a sentiment score. Confirm them against what was actually said.

## Step 2 — Investigate the underlying problem

Run the normal workflow for the issue (usually `order-inquiries.md`). The
emotion does not change the facts, but it does change urgency and escalation.

## Step 3 — Decide on escalation

Escalate (high priority) when **any** of these is true:

- 3 or more customer messages about the same unresolved issue;
- an earlier promise from our team was not kept;
- the customer requests a refund or replacement that needs approval;
- the customer asks for a manager or person;
- the message includes legal threats or chargeback threats (→ out of scope, urgent).

Frustration *alone*, about an issue you can fully resolve right now (e.g. a
package that is simply arriving tomorrow), does not require escalation.

## Step 4 — Write the reply

Structure:

1. **Acknowledge specifically** — name the actual problem and the repeated contact.
   "You've had to write to us three times about order 1043, and you still don't
   have it. I'm sorry — that's not the experience we want for you."
2. **State the facts** — what the record shows, in plain words.
3. **Say what happens next and who owns it** — "I've passed your refund request to
   our support lead, who will review it and reply by [time frame from the
   escalation guide]."
4. **No defensiveness, no over-promising.** Do not say the refund is approved.

Avoid: "I understand your frustration" as an opener, blaming the carrier
dismissively, asking the customer to repeat information already in the history,
or multiple apologies.

## Step 5 — Internal summary for staff

Include: number of contacts, dates, what was promised before, current order
status, what you requested approval for, and why you escalated.
