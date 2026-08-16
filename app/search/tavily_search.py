"""Tavily search integration."""


def tavily_search(query: str) -> dict:
    """Return a stubbed Tavily search result payload."""
    return {"provider": "tavily", "query": query, "results": []}
