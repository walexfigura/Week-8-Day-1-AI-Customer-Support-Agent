"""Builds the objects every command needs (one place to swap mock vs. live)."""

from __future__ import annotations

from dataclasses import dataclass

from .approvals import ApprovalQueue
from .config import Settings
from .knowledge import KnowledgeBase, WorkflowLibrary
from .logging_utils import TraceLogger
from .policy import PolicyConfig
from .tools.registry import ToolRegistry, build_tool_specs
from .tools.shopify import LiveShopifyBackend, MockShopifyBackend, ShopifyBackend
from .tools.zendesk import LiveZendeskBackend, MockZendeskBackend, ZendeskBackend


@dataclass
class Services:
    settings: Settings
    kb: KnowledgeBase
    workflows: WorkflowLibrary
    policy: PolicyConfig
    shopify: ShopifyBackend
    zendesk: ZendeskBackend
    approvals: ApprovalQueue
    logger: TraceLogger
    registry: ToolRegistry


def build_services(settings: Settings, *, persist: bool = True) -> Services:
    """`persist=False` keeps logs and approvals in memory (used by tests)."""
    kb = KnowledgeBase(settings.knowledge_dir)
    workflows = WorkflowLibrary(settings.agent_dir / "workflows")
    if settings.backend == "live":
        shopify: ShopifyBackend = LiveShopifyBackend(
            settings.shopify_store_domain or "",
            settings.shopify_access_token or "",
            settings.shopify_api_version,
        )
        zendesk: ZendeskBackend = LiveZendeskBackend(
            settings.zendesk_subdomain or "",
            settings.zendesk_email or "",
            settings.zendesk_api_token or "",
        )
    else:
        shopify = MockShopifyBackend(settings.data_dir)
        zendesk = MockZendeskBackend(settings.data_dir)
    return Services(
        settings=settings,
        kb=kb,
        workflows=workflows,
        policy=PolicyConfig.from_knowledge_base(kb),
        shopify=shopify,
        zendesk=zendesk,
        approvals=ApprovalQueue(settings.logs_dir / "approvals.jsonl" if persist else None),
        logger=TraceLogger(settings.logs_dir, enabled=persist),
        registry=ToolRegistry(build_tool_specs(workflows.names)),
    )
