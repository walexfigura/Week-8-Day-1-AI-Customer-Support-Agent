"""Small HTTP helper shared by the live Shopify and Zendesk adapters.

Handles the boring-but-essential parts of calling real APIs: timeouts,
retrying rate limits (HTTP 429, honoring `Retry-After`) and transient server
errors with exponential backoff, and turning failures into the typed errors
the tools understand.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from .base import BackendAuthError, BackendUnavailableError

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    sleep=time.sleep,
    **kwargs: Any,
) -> dict[str, Any]:
    last_error: str = "unknown error"
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TimeoutException:
            last_error = "request timed out"
        except httpx.TransportError as exc:
            last_error = f"network error: {exc.__class__.__name__}"
        else:
            if response.status_code in (401, 403):
                raise BackendAuthError(
                    f"{response.status_code} from {httpx.URL(url).host}: check credentials and scopes"
                )
            if response.status_code == 404:
                return {"_not_found": True}
            if response.status_code in RETRYABLE_STATUS:
                last_error = f"HTTP {response.status_code}"
                retry_after = response.headers.get("Retry-After")
                if attempt < max_attempts:
                    delay = (
                        float(retry_after) if retry_after and retry_after.isdigit() else base_delay * 2 ** (attempt - 1)
                    )
                    sleep(min(delay, 30.0))
                    continue
                break
            response.raise_for_status()
            return response.json()
        if attempt < max_attempts:
            sleep(base_delay * 2 ** (attempt - 1))
    raise BackendUnavailableError(f"{method} {httpx.URL(url).host} failed after {max_attempts} attempts: {last_error}")
