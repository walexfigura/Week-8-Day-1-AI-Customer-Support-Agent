"""Shared fixtures. Every test runs offline against the mock datasets."""

from __future__ import annotations

from pathlib import Path

import pytest

from customer_support_agent.config import Settings, find_project_root
from customer_support_agent.services import Services, build_services
from customer_support_agent.tools.base import ToolContext

ROOT = find_project_root()


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    for var in ("OPENAI_API_KEY", "SUPPORT_BACKEND", "NORTHSTAR_REFERENCE_DATE", "OPENAI_MODEL"):
        monkeypatch.delenv(var, raising=False)
    s = Settings.from_env(ROOT)
    s.openai_api_key = None
    s.log_dir = tmp_path / "logs"
    return s


@pytest.fixture
def services(settings: Settings) -> Services:
    return build_services(settings, persist=False)


@pytest.fixture
def ctx_for(services: Services):
    def make(ticket_id: str, **faults: str) -> ToolContext:
        t = services.zendesk.get_ticket(ticket_id)
        assert t is not None, ticket_id
        return ToolContext(
            services=services,
            trace_id="tr_test",
            ticket_id=t["id"],
            requester_id=t["requester"]["id"],
            requester_email=t["requester"]["email"],
            requester_name=t["requester"]["name"],
            fault_injection=dict(faults),
        )

    return make
