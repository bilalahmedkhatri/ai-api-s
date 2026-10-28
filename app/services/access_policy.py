"""One operation policy for dashboard sessions and app keys. Unknown routes deny."""

from sqlalchemy import select

from app.models.dashboard import Membership, Permission

# Explicit templates: adding a router does not silently grant a new capability.
APP_OPERATIONS = {
    "GET /api/v1/audio/models": "read",
    "GET /api/v1/audio/voices": "read",
    # GET can synthesize provider audio on a cache miss; it is a create capability.
    "GET /api/v1/audio/sample": "create",
    "POST /api/v1/audio/tts": "create",
    "GET /api/v1/audio/tts/status/{job_id}": "read",
    "GET /api/v1/audio/tts/stream/{job_id}": "read",
    "POST /api/v1/audio/tts/renew-url": "update",
    "POST /api/v1/audio/tts/retry/{job_id}": "update",
    "POST /api/v1/query/": "create",
    "GET /api/v1/query/requests": "read",
    "GET /api/v1/query/requests/{query_id}": "read",
    "POST /api/v1/generate/": "create",
    "GET /api/v1/image/models": "read",
    "POST /api/v1/image/generate": "create",
    "GET /api/v1/video/models": "read",
    "POST /api/v1/video/generate": "create",
    "POST /api/v1/ingest/image/": "create",
    "POST /api/v1/ingest/video/": "create",
    "POST /api/v1/facebook/store-token": "create",
    "POST /api/v1/facebook/post-pages": "create",
    "GET /api/v1/facebook/capabilities": "read",
    "DELETE /api/v1/facebook/posts/{post_id}": "delete",
    "PATCH /api/v1/facebook/posts/{post_id}": "update",
    "DELETE /api/v1/facebook/pages/{page_id}": "delete",
    "POST /api/v1/media/process-urls": "create",
    "GET /api/v1/media/{item_id}": "read",
    "DELETE /api/v1/media/": "delete",
}
ADMIN_OPERATIONS = {
    "GET /api/v1/apps/",
    "POST /api/v1/apps/",
    "GET /api/v1/apps/endpoints",
    "PATCH /api/v1/apps/{app_id}",
    "PATCH /api/v1/apps/owners/{user_id}/permission",
    "POST /api/v1/apps/{app_id}/link-key/{key_id}",
    "GET /api/v1/keys/",
    "POST /api/v1/keys/",
    "DELETE /api/v1/keys/{key_id}",
    "POST /api/v1/keys/provider",
    "GET /api/v1/keys/provider/{provider}",
    "PATCH /api/v1/keys/provider/{key_id}",
    "PATCH /api/v1/keys/provider/{key_id}/toggle",
    "DELETE /api/v1/keys/provider/{key_id}",
    "POST /api/v1/keys/provider/reset-daily",
    "POST /api/v1/cron/reset-daily-keys",
    *{
        f"{method} /api/v1/cron/{name}"
        for method in ("GET", "POST")
        for name in ("sync-models", "sync-provider-models", "sync-other-models")
    },
}
PUBLIC_READS = {"GET /health", "GET /api/v1/health/"}


def operation_kind(method, path):
    key = f"{method.upper()} {path}"
    if key in APP_OPERATIONS:
        return "app"
    if key in ADMIN_OPERATIONS:
        return "admin"
    if key in PUBLIC_READS:
        return "public"
    return None


async def member_operation_allowed(db, membership, method, path, *, grants=None):
    from app.services.dashboard import aware, now

    if membership.status != "approved" or (
        membership.access_expires_at and aware(membership.access_expires_at) <= now()
    ):
        return False
    kind = operation_kind(method, path)
    if kind is None:
        return False
    if membership.role == "admin":
        return True
    key = f"{method.upper()} {path}"
    action = APP_OPERATIONS.get(key)
    if kind != "app" or membership.role not in {"viewer", "operator"}:
        return False
    if membership.role == "viewer" and action != "read":
        return False
    if grants is None:
        rules = (
            (await db.execute(select(Permission).where(Permission.membership_id == membership.id)))
            .scalars()
            .all()
        )
        grants = {(r.resource_type, r.resource_key, r.action) for r in rules}
    if ("endpoint", key, "execute") not in grants:
        return False
    if action != "read":
        return all(
            ("component", component, "view") in grants
            for component in ("endpoint.execute", f"endpoint.{action}")
        )
    return True


async def effective_app_grants(db, membership):
    rules = (
        (await db.execute(select(Permission).where(Permission.membership_id == membership.id)))
        .scalars()
        .all()
    )
    grants = {(r.resource_type, r.resource_key, r.action) for r in rules}
    return [
        key
        for key in APP_OPERATIONS
        if await member_operation_allowed(db, membership, *key.split(" ", 1), grants=grants)
    ]


async def app_membership(db, app):
    """Check every app owned by a member, including pre-customer/legacy apps."""
    rows = (
        (
            await db.execute(
                select(Membership)
                .where(
                    (Membership.user_id == app.owner_user_id)
                    | (Membership.api_project_id == app.id)
                )
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    if len(rows) > 1 or (rows and rows[0].user_id != app.owner_user_id):
        # Broken links must not become service accounts that bypass member checks.
        from fastapi import HTTPException

        raise HTTPException(403, "Project ownership is unavailable")
    return rows[0] if rows else None
