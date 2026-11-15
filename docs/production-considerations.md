# Production Considerations

This project is a teaching example. It shows the shape of a real support
agent, but it is **not production-ready**. This is what a real business would
need to add before letting customers' messages flow through it.

## 1. Customer identity verification

- The mock treats the Zendesk requester email as verified. Email-channel
  tickets are reasonably trustworthy; **web-form and chat tickets are not**
  (anyone can type any email).
- Production options: require a logged-in session, send a one-time code to
  the checkout email, or match on several facts (email + ZIP + order total).
- Return identical responses for "doesn't exist" and "not yours" (already done
  here) and rate-limit lookups per requester to prevent order-number guessing.

## 2. Privacy and least privilege

- Read-only API scopes for the agent. Write actions use a separate service,
  separate credentials, and a human approval step.
- Minimize what reaches the model: the tool layer here already drops emails,
  street addresses, and payment data. Review every new field you add.
- Check your model provider's data-processing terms. This project sends
  requests with `store=False`; confirm retention settings for your account.
- Inform customers that AI assists your support team, per local law.

## 3. Secrets management

- Use a secrets manager (cloud KMS/Secrets Manager, Vault, Doppler) instead of
  `.env` files on servers. Rotate tokens; scope them per environment.
- Never log credentials. `logging_utils.redact` masks known patterns, but don't
  rely on regex alone — avoid passing secrets into loggable structures.

## 4. Reliable webhook processing

- HTTPS endpoint, signature verification, timestamp freshness check.
- Acknowledge quickly; process in a durable queue with retries and a dead-letter queue.
- Shared idempotency store keyed by event id; handle out-of-order events
  (a follow-up can arrive before the ticket-created run finishes).
- Back-pressure: cap concurrent agent runs to protect API rate limits and budget.

## 5. Monitoring and alerting

- Dashboards for: run volume, latency, tool error rates, escalation rate,
  guardrail overrides (how often code had to correct the model), approval
  backlog, and cost per ticket.
- Alerts on spikes in `order_access_denied`, injection signals, or tool failures.
- Keep trace ids end-to-end (webhook → agent run → Zendesk note).

## 6. Human review

- Start with **draft-only** for every ticket. Measure how often humans send
  drafts unchanged before considering any automation.
- Only automate low-risk, high-confidence categories (e.g. "where is my
  order" with an on-track shipment), behind a feature flag, with sampling for QA.
- Approvals for money and order changes stay human, with an audit trail of
  who approved what and why.

## 7. Data retention

- Decide how long to keep logs, traces, and drafts; they contain customer
  context even with masking. Set automatic deletion.
- Support data-deletion requests (remove a customer's traces on request).

## 8. Prompt injection

- Assume any customer-controlled text (messages, order notes, attachment text,
  product reviews) can contain instructions.
- The strongest defense is architectural: **no tool the model can call should
  be able to cause harm**. Here the agent can only read data the requester
  owns and queue requests for humans.
- Keep labeling untrusted content, keep the injection heuristics as a signal
  (not a guarantee), and red-team regularly with new attack phrasings.

## 9. Tool errors and retries

- Retries with backoff for transient errors (implemented in `http_utils`),
  circuit breakers for prolonged outages, clear "we'll follow up" drafts.
- Distinguish "not found" from "unavailable" (implemented) so the agent never
  tells a customer their order doesn't exist because an API timed out.

## 10. Quality and evaluation

- Grow `evals/test-cases.json` from real (anonymized) tickets, especially
  failures. Run evals on every prompt, policy, or model change.
- Use `--repeat` to measure consistency; track pass rates over time.
- Consider an LLM-as-judge for tone, with human calibration — not included here.

## 11. Deployment and operating costs

- Cost per ticket ≈ (model calls × tokens) + API calls. The CLI prints token
  counts per run; multiply by your model's pricing and expected volume.
- Each round of tool calls is one model call, and every call resends the
  growing conversation, so investigation-heavy tickets cost more. Measure with
  `python run.py eval` (the CLI prints model calls and tokens per run) before
  estimating. Smaller models are often enough for triage.
- Budget for Shopify/Zendesk plan limits, hosting, queue/database, monitoring,
  and the human time spent reviewing drafts — that last item dominates early on.
