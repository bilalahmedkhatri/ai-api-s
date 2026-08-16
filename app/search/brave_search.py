"""Brave search integration."""


def brave_search(query: str) -> dict:
    """Return a stubbed Brave search result payload."""
    return {"provider": "brave", "query": query, "results": []}
