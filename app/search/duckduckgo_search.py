"""DuckDuckGo search integration."""


def duckduckgo_search(query: str) -> dict:
    """Return a stubbed DuckDuckGo search result payload."""
    return {"provider": "duckduckgo", "query": query, "results": []}
