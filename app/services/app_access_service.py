"""Exact origin and endpoint permissions for gateway-owned applications."""

import json
from urllib.parse import urlsplit


def normalize_origin(value: str, *, allow_path: bool = False) -> str:
    """Canonical HTTP origin; no wildcards, credentials, or implicit subdomains."""
    value = value.strip()
    if any(char.isspace() for char in value) or "\\" in value:
        raise ValueError("Provide a valid HTTP(S) origin")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("An explicit http:// or https:// origin is required")
    if parsed.username is not None or parsed.password is not None or "*" in parsed.netloc:
        raise ValueError("Credentials and wildcards are not allowed in origins")
    if not allow_path and (parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("Register only the origin, without a path, query, or fragment")
    hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    host = f"[{hostname}]" if ":" in hostname else hostname
    port = parsed.port
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        host = f"{host}:{port}"
    return f"{parsed.scheme}://{host}"


def endpoint_allowed(permissions_json: str, method: str, route_path: str) -> bool:
    try:
        permissions = json.loads(permissions_json)
    except (TypeError, ValueError):
        return False
    return isinstance(permissions, list) and f"{method.upper()} {route_path}" in permissions
