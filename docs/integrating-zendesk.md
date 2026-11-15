# Integrating Zendesk

> **Status:** the mock backend and the local webhook simulator are fully
> functional and tested. The live REST adapter (`LiveZendeskBackend`) is
> unit-tested against documented response shapes but **has not been run
> against a real Zendesk account** here. Nothing in this project posts to
> Zendesk or receives real webhooks.

## The tool contract

| Tool | Input | Returns |
|---|---|---|
| `get_zendesk_ticket` | `ticket_id` | Subject, status, priority, channel, tags, masked requester email, latest customer message |
| `get_zendesk_ticket_history` | `ticket_id` | All comments (customer text labeled untrusted) + heuristic signals |
| `list_requester_tickets` | `ticket_id` | The same requester's other tickets |

The agent can only read the current ticket and other tickets from the same
requester (`ticket_not_accessible` otherwise).

## Mock records vs. the REST API

| Concern | Mock (`MockZendeskBackend`) | Live (`LiveZendeskBackend`) |
|---|---|---|
| Source | `data/zendesk-tickets.json` | `https://{subdomain}.zendesk.com/api/v2` |
| Ticket | Dictionary lookup | `GET /tickets/{id}.json` |
| Comments | Embedded in the ticket | `GET /tickets/{id}/comments.json` |
| Authors | `users` list | `GET /users/show_many.json?ids=…` |
| Requester's tickets | Filter by requester | `GET /users/{id}/tickets/requested.json` |
| Auth | None | Basic auth `{email}/token` + API token (or OAuth bearer token) |
| Rate limits | None | HTTP 429 with `Retry-After`; retried by `http_utils.request_json` |
| Shape | Normalized on read | `normalize_zendesk_ticket()` |

## Setting up a live connection

1. Use a **sandbox** Zendesk account if your plan has one.
2. Admin Center → Apps and integrations → APIs → Zendesk API → enable **token
   access** and create an API token. Prefer a dedicated agent user with the
   narrowest role that can read tickets.
3. `.env`:
   ```bash
   SUPPORT_BACKEND=live
   ZENDESK_SUBDOMAIN=yourcompany
   ZENDESK_EMAIL=ai-agent@yourcompany.example
   ZENDESK_API_TOKEN=...              # never commit this
   ```
   (Configure Shopify too — `SUPPORT_BACKEND` switches both.)
4. Test without the model:
   ```bash
   python run.py tool get_zendesk_ticket_history --ticket 12345 --arg ticket_id=12345
   ```

For production, OAuth with a read-only scope (`tickets:read`, `users:read`)
is preferable to an API token, because API tokens act with the full
permissions of the user they belong to.

## Simulated webhooks

`data/webhook-events.json` holds sample deliveries modeled on Zendesk's
event-subscription webhook format (`type`, `subject`, `detail`, `event`,
`time`, `id`). `python run.py webhook` signs each one with a local secret and
passes the raw body and headers to `WebhookHandler.handle()`:

| Step | What happens | Why |
|---|---|---|
| 1 Verify signature | `base64(HMAC-SHA256(secret, timestamp + body))` compared with `X-Zendesk-Webhook-Signature` | Anyone can POST to a public URL |
| 2 Validate | Pydantic model; `subject` must be `zen:ticket:<id>` | Reject malformed or unexpected payloads early |
| 3 De-duplicate | Event id checked in `logs/processed-webhook-events.json` | Webhooks are delivered at least once; retries are normal |
| 4 Filter | Unknown event types and staff-authored comments ignored | Prevents the agent replying to itself or colleagues |
| 5 Load ticket | New comment stored in the mock history; full ticket loaded | The agent needs context, not just the latest message |
| 6 Run agent | Draft + decision | — |
| 7 Mark processed | Only after success | A failed run can be retried |

Run `python run.py webhook --list` to see the sample events, and
`python run.py webhook --all --no-agent` to see steps 1–5 without an API key.

## Deploying a real webhook endpoint (not included)

To receive real events you would:

1. **Host an HTTPS endpoint** — e.g. a small FastAPI/Flask route on a platform
   with TLS, or a serverless function. Zendesk cannot reach `localhost`.
2. **Create the webhook** in Admin Center → Apps and integrations → Webhooks,
   subscribed to ticket events (ticket created, comment added), and copy its
   **signing secret** into `ZENDESK_WEBHOOK_SECRET`.
3. **Verify every request** with `verify_signature()` and reject stale
   timestamps (e.g. older than 5 minutes) to prevent replay.
4. **Respond fast.** Return `200` within a few seconds and do the agent run in
   a background job queue. Zendesk retries slow or failed deliveries.
5. **Make processing idempotent** with a shared store (database or Redis) keyed
   by event id instead of the local JSON file.
6. **Write back carefully.** If you post the draft back to Zendesk, add it as an
   **internal note** for a human to review — never a public reply — and use a
   separate, narrowly scoped credential for that one action.

The handler in this repo is written so that only step 1 (the HTTP route) and
the idempotency store need to change.
