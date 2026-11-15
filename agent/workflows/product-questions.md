# Workflow: Product Questions and Troubleshooting

Use for: specifications, care instructions, compatibility, "does it…?",
and "it isn't working" questions.

Sources: product records (`search_shopify_products`, `get_shopify_product`),
`faqs/products/product-info.md`, `faqs/products/troubleshooting.md`,
`policies/warranty-policy.md`.

## Step 1 — Identify the product

- If the customer names the product, call `search_shopify_products` with the name.
- If they reference an order instead, look up the order and use the line items.
- If it's unclear which product they mean, ask — Northstar sells several
  similar items (e.g. two electronics products with USB-C charging).

## Step 2 — Answer from the record

- Use `get_shopify_product` for specifications. Product records include
  `specifications`, `care`, and `warranty_months`.
- Use the product FAQ for broader questions (materials, sizing, care).
- If neither source answers the question, **say so** and offer to pass the
  question to the team. Do not guess dimensions, materials, or compatibility.

## Step 3 — Troubleshooting

1. Search the knowledge base for the product + symptom
   (e.g. "lamp flickering").
2. Give the documented steps in order, numbered, briefly.
3. Tell the customer what to do if the steps don't help (warranty claim or return).
4. If the customer has **already tried** the documented steps, skip to warranty
   or return options (use `check_return_eligibility` if there's an order).

## Safety exceptions — escalate immediately (urgent)

Overheating, swelling battery, burning smell, smoke, sparks, liquid leaking
from an electronic device, or any injury. Draft: tell the customer to stop using
the product and unplug it, and that a specialist will contact them. Do not
troubleshoot further.

## Step 4 — Draft

Keep product answers short and factual. Quote numbers exactly as they appear
in the record (e.g. "keeps drinks cold for up to 24 hours").
