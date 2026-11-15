"""Knowledge-base and workflow tools.

`search_knowledge_base` returns short ranked snippets; `read_knowledge_document`
returns a full document. The model is expected to search first, then read only
what it needs — the same "progressive disclosure" idea Claude Skills use.
Every document the model retrieves is recorded so the application can check
that cited policy references were actually read.
"""

from __future__ import annotations

from typing import Any

from ..knowledge import KB_CATEGORIES
from .base import ToolContext, fail, ok

MAX_DOCUMENT_CHARS = 9000


def search_knowledge_base(ctx: ToolContext, query: str, category: str = "all") -> dict[str, Any]:
    if category not in ("all", *KB_CATEGORIES):
        return fail("invalid_category", f"category must be one of: all, {', '.join(KB_CATEGORIES)}")
    if not query.strip():
        return fail("empty_query", "Provide a few keywords, e.g. 'return window opened earbuds'.")
    hits = ctx.services.kb.search(query, category=category, max_results=4)
    for hit in hits:
        ctx.retrieved_documents.append(hit.doc_id)
    return ok(
        query=query,
        results=[h.as_dict() for h in hits],
        guidance="Policies are authoritative. Read a document before relying on details beyond the snippet.",
    )


def read_knowledge_document(ctx: ToolContext, document_id: str) -> dict[str, Any]:
    doc = ctx.services.kb.get(document_id)
    if doc is None:
        return fail(
            "document_not_found",
            f"No document '{document_id}'. Use an id returned by search_knowledge_base, "
            "e.g. 'policies/return-policy.md'.",
        )
    ctx.retrieved_documents.append(doc.doc_id)
    content = doc.body
    truncated = len(content) > MAX_DOCUMENT_CHARS
    return ok(
        document_id=doc.doc_id,
        title=doc.title,
        doc_type=doc.doc_type,
        authoritative=doc.metadata.get("authority") == "authoritative",
        last_reviewed=doc.metadata.get("last_reviewed"),
        content=content[:MAX_DOCUMENT_CHARS],
        truncated=truncated,
    )


def read_workflow(ctx: ToolContext, workflow_name: str) -> dict[str, Any]:
    text = ctx.services.workflows.read(workflow_name)
    if text is None:
        return fail("workflow_not_found", f"Unknown workflow. Available: {', '.join(ctx.services.workflows.names)}")
    return ok(workflow=workflow_name, content=text)
