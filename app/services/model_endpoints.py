"""Shared endpoint settings with legacy-ID compatibility during manual validation."""

from types import SimpleNamespace
from urllib.parse import urlsplit

from sqlalchemy import select

from app.models.db_models import MediaModel, ModelEndpoint, TTSModel

TTS_FIELDS = {"is_local", "default_voice", "default_lang", "default_speed", "provider_config"}
MEDIA_FIELDS = {"display_name", "model_version", "auth_env_var", "is_async_poll", "poll_endpoint_url",
                "poll_endpoint_path", "poll_status_field", "poll_success_value", "poll_failed_value",
                "poll_timeout_s", "poll_interval_s", "response_extraction_config", "returns_base64",
                "default_width", "default_height", "max_outputs", "default_params"}
SECRET_FIELDS = {"api_key", "api_token", "token", "secret", "password", "authorization",
                 "access_token", "refresh_token", "encrypted_key", "replicate_api_token"}


def validate_url(value):
    if value is None:
        return value
    parsed = urlsplit(value)
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Use an absolute HTTP(S) URL without credentials, query or fragment")
    return value


def validate_config(config):
    if isinstance(config, dict):
        for key, value in config.items():
            if key.lower() in SECRET_FIELDS:
                raise ValueError("Credentials belong in provider_api_keys, not endpoint settings")
            validate_config(value)
    elif isinstance(config, list):
        for value in config:
            validate_config(value)


async def legacy_views(db, rows, model_type):
    """Overlay settings without mutating old ORM rows, voice IDs or generation FKs."""
    if not rows:
        return []
    link = ModelEndpoint.legacy_tts_model_id if model_type == "tts" else ModelEndpoint.legacy_media_model_id
    endpoints = (await db.execute(select(ModelEndpoint).where(link.in_([row.id for row in rows])))).scalars().all()
    by_id = {getattr(endpoint, link.key): endpoint for endpoint in endpoints}
    result = []
    for row in rows:
        endpoint = by_id.get(row.id)
        if endpoint is None:
            if row.is_active:
                result.append(row)
            continue
        model = endpoint.ai_model
        if not endpoint.is_active or not model.is_active:
            continue
        fields = TTS_FIELDS if model_type == "tts" else MEDIA_FIELDS
        values = {column.key: getattr(row, column.key) for column in row.__table__.columns}
        values.update({key: getattr(row, key) for key in fields if hasattr(row, key)})
        values.update({key: value for key, value in endpoint.config.items() if key in fields})
        values.update(name=model.name, provider=model.provider, is_active=True,
                      model_endpoint_id=endpoint.id, credential_provider=model.provider)
        if model_type == "tts":
            values.update(api_endpoint_url=endpoint.endpoint_url,
                          is_local=endpoint.adapter == "local" or values.get("is_local", False))
        else:
            values.update(endpoint_url=endpoint.endpoint_url, api_base_url=endpoint.api_base_url,
                          api_endpoint_path="")
        result.append(SimpleNamespace(**values))
    return result


async def chat_options(db, model):
    endpoint = (await db.execute(select(ModelEndpoint).where(ModelEndpoint.ai_model_id == model.id,
                                ModelEndpoint.model_type == "chat"))).scalar_one_or_none()
    if endpoint is None:
        return {}
    if not endpoint.is_active or endpoint.adapter != "litellm":
        raise ValueError("Chat endpoint is disabled or incompatible")
    return {"api_base": endpoint.api_base_url} if endpoint.api_base_url else {}


async def ensure_legacy_link(db, endpoint, model):
    """New TTS/media registrations retain a legacy shell for existing FK consumers."""
    kind = endpoint.model_type
    if kind not in {"tts", "image", "video"}:
        return
    table = TTSModel if kind == "tts" else MediaModel
    link = "legacy_tts_model_id" if kind == "tts" else "legacy_media_model_id"
    identifier = getattr(endpoint, link)
    row = await db.get(table, identifier) if identifier else (await db.execute(
        select(table).where(table.name == model.name))).scalar_one_or_none()
    if row is not None and row.provider.strip().lower() != model.provider.strip().lower():
        raise ValueError("Legacy model name belongs to a different provider")
    if row is not None and kind != "tts" and row.media_type != kind:
        raise ValueError("Legacy media type does not match")
    if row is None:
        row = table(name=model.name, provider=model.provider, is_active=model.is_active)
        if kind != "tts":
            row.media_type = kind
            row.is_async_poll = endpoint.config.get("is_async_poll", False)
        db.add(row)
        await db.flush()
    setattr(endpoint, link, row.id)
