"""Optional web search for research roles. Off by default, with its own key."""

from __future__ import annotations

import os

import httpx

from .clients import ClientError, redact

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
MAX_RESULTS = 8
MAX_FIELD = 300


class BraveSearch:
    def __init__(
        self, *, api_key_env: str = "TRYPRO_SEARCH_API_KEY", transport: httpx.BaseTransport | None = None
    ) -> None:
        key = os.environ.get(api_key_env)
        if not key:
            raise ClientError(f"web search is turned on, but {api_key_env} is not set", retryable=False)
        self._secret = key
        self._http = httpx.Client(headers={"X-Subscription-Token": key}, timeout=30.0, transport=transport)

    def search(self, query: str) -> list[dict[str, str]]:
        try:
            response = self._http.get(BRAVE_URL, params={"q": query[:400], "count": MAX_RESULTS})
        except httpx.HTTPError as error:
            raise ClientError(redact(f"search request failed: {error}", self._secret), retryable=True) from error
        if response.status_code >= 400:
            raise ClientError(f"search returned HTTP {response.status_code}", retryable=response.status_code >= 500)
        results = (response.json().get("web") or {}).get("results") or []
        return [
            {
                "title": str(item.get("title", ""))[:MAX_FIELD],
                "url": str(item.get("url", "")),
                "description": str(item.get("description", ""))[:MAX_FIELD],
                "date": str(item.get("page_age") or item.get("age") or "")[:40],
            }
            for item in results[:MAX_RESULTS]
        ]
