# Integrating Shopify

> **Status:** the mock backend is fully functional and tested. The live
> adapter (`LiveShopifyBackend`) is implementation-ready and its request
> construction and response normalization are unit-tested against documented
> response shapes, but it **has not been run against a real Shopify store** in
> this repository. Verify it with a development store before relying on it.

## The tool contract (what the model sees)

| Tool | Input | Returns |
|---|---|---|
| `get_shopify_order` | `order_id` (e.g. `"1042"` or `"#1042"`) | Data-minimized order: status, items, shipments with `delivery_assessment`, refunds, returns, totals, city/state only |
| `check_return_eligibility` | `order_id` | Per-item eligibility calculated from the return policy |
| `search_shopify_products` | `query` | Matching products (id, title, SKU, price) |
| `get_shopify_product` | `product_id` (id, SKU, or handle) | Specs, care, warranty, troubleshooting |

These never change between mock and live mode.

## Mock file lookup vs. a real API request

| Concern | Mock (`MockShopifyBackend`) | Live (`LiveShopifyBackend`) |
|---|---|---|
| Source | `data/shopify-orders.json`, `data/shopify-products.json` | `POST https://{store}/admin/api/{version}/graphql.json` |
| Order lookup | Dictionary lookup by order number | `orders(first: 1, query: "name:#1043")` |
| Authentication | None | `X-Shopify-Access-Token` header (custom app Admin API token) |
| Errors | Missing record → `None` | HTTP 401/403 → `BackendAuthError`; 429/5xx → retried, then `BackendUnavailableError`; GraphQL `THROTTLED` → `BackendUnavailableError` |
| Rate limits | None | GraphQL cost-based leaky bucket; inspect `extensions.cost.throttleStatus` |
| Shape | Already normalized | `normalize_shopify_order()` maps GraphQL nodes into the mock shape |

## Setting up a live connection

1. **Create a development store** (Shopify Partners) or use a staging store.
   Don't start with production data.
2. **Create a custom app** in the store admin (Settings → Apps and sales
   channels → Develop apps) and grant **read-only** Admin API scopes:
   - `read_orders` — orders, line items, refunds
   - `read_fulfillments` (or the merchant-managed fulfillment-order scopes your
     version requires) — tracking and delivery events
   - `read_products` — catalog
   - `read_customers` — needed for order email in some API versions
   Never grant write scopes to this app: the agent doesn't need them.
3. **Protected customer data.** Email and address fields are protected customer
   data in Shopify. Depending on your app type you may need to request access
   in the app configuration. Without it, `email` may come back empty and every
   lookup will fail the identity check (by design).
4. **Install the app** and copy the Admin API access token.
5. **Configure `.env`:**
   ```bash
   SUPPORT_BACKEND=live
   SHOPIFY_STORE_DOMAIN=your-dev-store.myshopify.com
   SHOPIFY_ADMIN_ACCESS_TOKEN=shpat_...      # never commit this
   SHOPIFY_API_VERSION=2026-10                 # a currently supported version
   ```
   `SUPPORT_BACKEND=live` switches *both* Shopify and Zendesk, so configure
   Zendesk too (see `integrating-zendesk.md`).
6. **Test one lookup** without the model:
   ```bash
   python run.py tool get_shopify_order --ticket <real-ticket-id> --arg order_id=<number>
   ```

## The GraphQL query

See `ORDER_QUERY` in `src/customer_support_agent/tools/shopify.py`. It
requests only the fields the tool needs: order name, statuses, shipping
line, city/province, line items, fulfillments (tracking info, estimated and
actual delivery, recent events), and refunds. Field names were written
against the current Admin GraphQL schema; Shopify versions its API
quarterly, so re-check fields when you change `SHOPIFY_API_VERSION`.

**Things to verify against your store:**

- Whether your carriers populate `estimatedDeliveryAt`, `deliveredAt`, and
  fulfillment `events`. Without them, `delivery_assessment` becomes
  `tracking_pending` or relies on `estimated_delivery` only.
- Whether product specs/care/warranty live in metafields. The adapter reads
  `custom.specifications` (JSON), `custom.care`, `custom.warranty`, and
  `custom.final_sale`. Rename these to match your store.
- Returns: the mock includes a `returns` list; the live adapter leaves it empty.
  Add the `Order.returns` connection if you use Shopify Returns.

## Normalization

`normalize_shopify_order()` converts:

| GraphQL | Normalized |
|---|---|
| `name: "#1043"` | `order_number: "1043"` |
| `displayFinancialStatus` | `financial_status` |
| `displayFulfillmentStatus` | `fulfillment_status` |
| `fulfillments[].trackingInfo[0]` | `carrier`, `tracking_number`, `tracking_url` |
| `fulfillments[].estimatedDeliveryAt` | `estimated_delivery` (date) |
| `fulfillments[].events.nodes` | `tracking_events` (oldest first) |
| `lineItems.nodes[].unfulfilledQuantity > 0` | `fulfillment_status: UNFULFILLED` |
| `*Set.shopMoney.amount` | plain decimal strings |

Because everything downstream (policy calculations, presentation, identity
check, tests) uses the normalized shape, this function is the only place
Shopify-specific structure lives.

## Identity verification

`_load_verified_order()` compares the order's email with the requester email
bound from the Zendesk ticket. Missing orders and other customers' orders
return the **same** error, so the tool can't be used to discover which order
numbers exist. In production you would strengthen this (see
`production-considerations.md`): verified email channels, logged-in sessions,
or a one-time code for web-form tickets.

## What the agent can never do

There are no write operations in `LiveShopifyBackend`. Refunds, cancellations,
and edits are represented only as *approval requests*; `approvals.py` shows
which mutation a human-triggered integration would call (`refundCreate`,
`orderCancel`, …) and only prints a simulation message.
