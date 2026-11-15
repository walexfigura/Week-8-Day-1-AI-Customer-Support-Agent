# Tool Configuration (edit this for Mode B)

This file tells Claude **which real tools exist** in your environment and when
to use them. It does not create tools. If the tools below are not actually
available in the conversation, Claude must fall back to Mode A and say so.

## Current configuration

```
mode: A            # change to B after you connect real tools
shopify_tool: none
zendesk_tool: none
```

## How to fill this in

Replace the example rows with the exact tool names your connector exposes
(Claude can list its available tools if you ask). Keep every tool **read-only**.

| Workflow step | Example tool name (replace) | Inputs | Use when | Never use for |
|---|---|---|---|---|
| Read ticket + history | `zendesk_get_ticket`, `zendesk_list_ticket_comments` | ticket id | Every ticket that references earlier messages | Posting replies, changing status |
| Other tickets from requester | `zendesk_search_tickets` with `requester:<email>` | requester email | Repeat-contact checks | Reading other customers' tickets |
| Order lookup | `shopify_get_order` / GraphQL `orders(query: "name:#1043")` | order number | Customer gave an order number | Guessing order numbers |
| Product details | `shopify_get_product` / `products(query: ...)` | title/SKU | Product questions | — |

## Rules for using connectors

1. **Identity first.** Only share order details if the order's email matches the
   ticket requester's email. If they don't match or you can't tell, say you
   couldn't find the order under their account and ask them to write from the
   checkout email.
2. **Least data.** Quote order status, items, dates, carrier, and tracking
   number. Don't quote street addresses, emails, or payment details.
3. **No write actions.** Even if a connector offers refunds, cancellations,
   replies, or ticket updates, do not call them. Put the action under
   "Approval needed" for a human.
4. **Tool failures.** If a tool errors or times out, report it, don't guess, and
   recommend escalation (rule E9).
5. **Cite sources.** In "What I checked", name each tool you called and what it returned.

## Mapping to the Python agent

The Python project in the source repository defines the same contract in code
(`src/customer_support_agent/tools/registry.py`). Its tools are:
`get_zendesk_ticket`, `get_zendesk_ticket_history`, `list_requester_tickets`,
`get_shopify_order`, `check_return_eligibility`, `search_shopify_products`,
`get_shopify_product`, `search_knowledge_base`, `read_knowledge_document`,
`read_workflow`, `request_human_approval`, `submit_case_resolution`.
If you expose those through an MCP server, list them here.
