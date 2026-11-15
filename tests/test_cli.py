"""The user-facing commands documented in the README (offline ones)."""

from __future__ import annotations

import json
import zipfile

import pytest

from customer_support_agent import cli
from customer_support_agent.config import find_project_root


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "")  # .env (if present) must not leak a real key into tests


def test_smoke_passes(capsys):
    assert cli.main(["smoke"]) == 0
    assert "All checks passed" in capsys.readouterr().out


def test_demo_list(capsys):
    assert cli.main(["demo", "--list"]) == 0
    out = capsys.readouterr().out
    assert "frustrated_customer" in out and "prompt_injection" in out


def test_demo_without_key_explains_what_to_do(capsys):
    assert cli.main(["demo", "--scenario", "frustrated_customer"]) == 2
    assert "OPENAI_API_KEY is not set" in capsys.readouterr().out


def test_invalid_scenario(capsys):
    assert cli.main(["demo", "--scenario", "nope"]) == 2
    assert "Valid scenarios" in capsys.readouterr().out


def test_tool_command(capsys):
    assert cli.main(["tool", "get_shopify_order", "--ticket", "5001", "--arg", "order_id=1042"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out[out.index("{") :])
    assert payload["order"]["order_number"] == "1042"


def test_tool_list(capsys):
    assert cli.main(["tool", "list"]) == 0
    assert "request_human_approval" in capsys.readouterr().out


def test_webhook_no_agent_all(capsys):
    assert cli.main(["webhook", "--all", "--no-agent", "--reset"]) == 0
    out = capsys.readouterr().out
    assert "REJECTED" in out and "IGNORED" in out and "VALIDATED" in out


def test_webhook_unknown_event(capsys):
    assert cli.main(["webhook", "--event", "nope", "--no-agent"]) == 2


def test_approvals_empty_list(capsys):
    assert cli.main(["approvals"]) == 0


def test_build_skill(capsys):
    assert cli.main(["build-skill"]) == 0
    zip_path = find_project_root() / "dist" / "customer-support-skill.zip"
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
    assert "customer-support/SKILL.md" in names
    assert "customer-support/references/knowledge-base/policies/return-policy.md" in names
    assert "customer-support/references/workflows/escalation.md" in names
    assert "customer-support/references/sample-data/shopify-orders.json" in names
    assert {n.split("/")[0] for n in names} == {"customer-support"}


def test_committed_skill_zip_is_up_to_date(tmp_path):
    """The downloadable claude-skill/customer-support-skill.zip must match the current sources.

    If this fails, rebuild it with:  python run.py build-skill --out claude-skill
    """
    from customer_support_agent.skill_builder import build_skill, zip_contents

    root = find_project_root()
    committed = root / "claude-skill" / "customer-support-skill.zip"
    assert committed.exists(), "Run: python run.py build-skill --out claude-skill"
    fresh = build_skill(root, tmp_path).zip_path
    assert zip_contents(committed) == zip_contents(fresh), "Run: python run.py build-skill --out claude-skill"
