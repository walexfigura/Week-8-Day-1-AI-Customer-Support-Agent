# Workflow: Ambiguous Requests, Multiple Questions, and Follow-ups

Real messages rarely arrive as one clean question. Use this workflow when a
message is vague, contains several requests, changes topic, or continues an
earlier conversation.

## Missing order number

- Don't search for orders without a number and don't guess.
- Ask one clear question: "Could you reply with your order number? It's the
  four-digit number in your confirmation email (for example, #1042). If possible,
  reply from the email address you used at checkout."
- If the question can be partly answered without the order (e.g. general
  shipping times), answer that part too — give the customer something useful now.
- Set `needs_clarification: true` and put the question in `clarifying_question`.

## Multiple questions in one message

1. List the questions in your internal summary.
2. Investigate each one (they may need different tools/workflows).
3. Answer them in the order the customer asked them, one short paragraph each.
4. Set `intent` to the main need and list the others in `secondary_intents`.

## Topic changes

If the latest message is about something different from the earlier history
(e.g. the old ticket was about tracking, the new message asks about a return),
answer the **new** question, and only mention the old issue if it is still open.

## Follow-up messages

A follow-up often supplies information we asked for (an order number, a photo,
a confirmation).

1. Read the ticket history to see what was asked and promised.
2. Do not re-ask for information the customer has already given.
3. Continue the workflow that was in progress — e.g. after the customer supplies
   an order number, run `order-inquiries.md` Step 2.
4. If a previous ticket from the same customer is still open on the same topic,
   mention it in the internal summary so staff can merge them.

## Explicit requests for a person

Always escalate (rule E1 in `escalation.md`). You may still gather facts so the
human picks up a ready-to-act case.

## When information conflicts

- Customer statement vs. order record → trust the record, explain it kindly,
  and note the conflict internally.
- Two documents disagree → follow `policies/`, note the conflict, and escalate
  if the difference affects the answer.
- Tool error → never fill the gap with assumptions.
