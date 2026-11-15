"""Configuration loaded from environment variables (and an optional `.env` file).

Every setting has a safe default so the offline commands (`smoke`, `tool`,
`webhook --no-agent`, `build-skill`, and the unit tests) work without any
credentials. Only model-backed commands need `OPENAI_API_KEY`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_REFERENCE_DATE = "2026-11-10"  # the "today" of the fictional datasets


def find_project_root() -> Path:
    """Locate the repository root (the folder containing `data/` and `agent/`).

    Works whether the package is run from a clone (`python run.py`) or installed
    in editable mode. Override with NORTHSTAR_PROJECT_ROOT if needed.
    """
    override = os.environ.get("NORTHSTAR_PROJECT_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "data").is_dir() and (candidate / "agent").is_dir():
            return candidate
    cwd = Path.cwd()
    if (cwd / "data").is_dir() and (cwd / "agent").is_dir():
        return cwd
    raise RuntimeError(
        "Could not find the project root (a folder containing 'data/' and 'agent/'). "
        "Run commands from the repository folder or set NORTHSTAR_PROJECT_ROOT."
    )


def _load_dotenv(root: Path) -> None:
    """Load `.env` if python-dotenv is installed. Existing env vars win."""
    env_file = root / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependency is declared, but stay friendly
        return
    load_dotenv(env_file, override=False)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


def _parse_reference(raw: str) -> datetime:
    """Turn NORTHSTAR_REFERENCE_DATE into an aware datetime.

    A fixed date keeps the mock scenarios deterministic (e.g. order 1043 is
    always "presumed lost"). Use `now` to use the real clock instead.
    A bare date means 17:00 UTC (noon US Eastern) on that day.
    """
    raw = raw.strip()
    if raw.lower() == "now":
        return datetime.now(timezone.utc)
    if "T" in raw:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(raw + "T17:00:00+00:00")


@dataclass
class Settings:
    project_root: Path
    openai_api_key: str | None = field(default=None, repr=False)
    openai_model: str = DEFAULT_MODEL
    openai_reasoning_effort: str | None = "low"
    max_iterations: int = 10
    max_tool_calls: int = 20
    backend: str = "mock"  # "mock" or "live"
    reference_datetime: datetime = field(default_factory=lambda: _parse_reference(DEFAULT_REFERENCE_DATE))
    log_dir: Path | None = None
    # Live integration settings (only used when backend == "live")
    shopify_store_domain: str | None = None
    shopify_access_token: str | None = field(default=None, repr=False)
    shopify_api_version: str = "2026-10"
    zendesk_subdomain: str | None = None
    zendesk_email: str | None = None
    zendesk_api_token: str | None = field(default=None, repr=False)
    zendesk_webhook_secret: str | None = field(default=None, repr=False)

    # Derived paths -------------------------------------------------------
    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    @property
    def knowledge_dir(self) -> Path:
        return self.project_root / "knowledge-base"

    @property
    def agent_dir(self) -> Path:
        return self.project_root / "agent"

    @property
    def logs_dir(self) -> Path:
        return self.log_dir or (self.project_root / "logs")

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> Settings:
        root = project_root or find_project_root()
        _load_dotenv(root)
        env = os.environ
        effort = env.get("OPENAI_REASONING_EFFORT", "low").strip().lower()
        backend = env.get("SUPPORT_BACKEND", "mock").strip().lower() or "mock"
        if backend not in {"mock", "live"}:
            raise ValueError("SUPPORT_BACKEND must be 'mock' or 'live'")
        log_dir = env.get("NORTHSTAR_LOG_DIR", "").strip()
        return cls(
            project_root=root,
            openai_api_key=env.get("OPENAI_API_KEY") or None,
            openai_model=env.get("OPENAI_MODEL", "").strip() or DEFAULT_MODEL,
            openai_reasoning_effort=None if effort in {"", "none", "off"} else effort,
            max_iterations=_int_env("AGENT_MAX_ITERATIONS", 10),
            max_tool_calls=_int_env("AGENT_MAX_TOOL_CALLS", 20),
            backend=backend,
            reference_datetime=_parse_reference(env.get("NORTHSTAR_REFERENCE_DATE", "") or DEFAULT_REFERENCE_DATE),
            log_dir=Path(log_dir).expanduser() if log_dir else None,
            shopify_store_domain=env.get("SHOPIFY_STORE_DOMAIN") or None,
            shopify_access_token=env.get("SHOPIFY_ADMIN_ACCESS_TOKEN") or None,
            shopify_api_version=env.get("SHOPIFY_API_VERSION", "").strip() or "2026-10",
            zendesk_subdomain=env.get("ZENDESK_SUBDOMAIN") or None,
            zendesk_email=env.get("ZENDESK_EMAIL") or None,
            zendesk_api_token=env.get("ZENDESK_API_TOKEN") or None,
            zendesk_webhook_secret=env.get("ZENDESK_WEBHOOK_SECRET") or None,
        )

    def require_openai_key(self) -> str:
        if not self.openai_api_key:
            raise MissingCredentialsError(
                "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key, "
                "or run the offline checks with `python run.py smoke`."
            )
        return self.openai_api_key


class MissingCredentialsError(RuntimeError):
    """Raised when a command needs credentials that are not configured."""
