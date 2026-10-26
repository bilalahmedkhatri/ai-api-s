"""Admin-only registration in the shared AI model catalog."""
import json
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

from app.db.database import get_db
from app.models.db_models import AIModel, ModelEndpoint, ProviderAPIKey
from app.services.dashboard import audit, check_origin, member, require_admin
from app.services.model_endpoints import (
    MEDIA_FIELDS,
    TTS_FIELDS,
    ensure_legacy_link,
    validate_config,
    validate_url,
)
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
FIELDS = ("id", "name", "provider", "is_active", "is_free",
          "prompt_price_per_1k", "completion_price_per_1k", "created_at")


class CreateModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid", allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=128, pattern=r"^\S+$")
    provider: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    is_active: bool = True
    is_free: bool = True
    prompt_price_per_1k: float = Field(default=0, ge=0)
    completion_price_per_1k: float = Field(default=0, ge=0)

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value):
        return value.lower()


class EndpointInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid", allow_inf_nan=False)
    ai_model_id: int = Field(gt=0)
    model_type: Literal["chat", "tts", "image", "video", "embedding"]
    adapter: Literal["litellm", "http", "local"]
    endpoint_url: str | None = Field(default=None, max_length=2048)
    api_base_url: str | None = Field(default=None, max_length=2048)
    is_active: bool = True
    config: dict = Field(default_factory=dict)

    @field_validator("endpoint_url", "api_base_url")
    @classmethod
    def valid_url(cls, value):
        return validate_url(value)

    @model_validator(mode="after")
    def valid_settings(self):
        validate_config(self.config)
        fields = TTS_FIELDS if self.model_type == "tts" else MEDIA_FIELDS if self.model_type in {"image", "video"} else set()
        if self.config.keys() - fields:
            raise ValueError("Unsupported settings for this model type")
        for key, value in self.config.items():
            if value is None:
                if key in {"provider_config", "default_params", "response_extraction_config", "max_outputs",
                           "poll_timeout_s", "poll_interval_s", "is_local", "is_async_poll", "returns_base64"}:
                    raise ValueError("Operational settings cannot be null")
                continue
            if key in {"provider_config", "default_params", "response_extraction_config"} and not isinstance(value, dict):
                raise ValueError("Provider parameters and extraction settings must be objects")
            if key in {"is_local", "is_async_poll", "returns_base64"} and type(value) is not bool:
                raise ValueError("Flags must be boolean")
            if key in {"max_outputs", "default_width", "default_height"} and type(value) is not int:
                raise ValueError("Output counts and dimensions must be integers")
            if key in {"default_speed", "poll_timeout_s", "poll_interval_s", "max_outputs", "default_width", "default_height"}:
                if type(value) not in {int, float} or not 0 < value <= 8192:
                    raise ValueError("Numeric settings must be positive and bounded")
            if key in {"display_name", "model_version", "auth_env_var", "poll_endpoint_path", "poll_status_field",
                       "poll_success_value", "poll_failed_value", "default_voice", "default_lang"}:
                if not isinstance(value, str) or len(value) > 2048:
                    raise ValueError("Text settings must be strings up to 2048 characters")
            if key == "poll_endpoint_url":
                validate_url(value)
        if len(json.dumps(self.config, allow_nan=False)) > 32768:
            raise ValueError("Endpoint settings exceed 32KB")
        if self.model_type in {"chat", "embedding"} and self.adapter != "litellm":
            raise ValueError("Chat/embedding use the shared LiteLLM adapter")
        if self.model_type in {"image", "video"} and self.adapter != "http":
            raise ValueError("Media endpoints use the existing HTTP adapter")
        if self.model_type in {"image", "video"}:
            extraction = self.config.get("response_extraction_config") or {}
            if extraction.get("type") not in {"json_path", "binary"}:
                raise ValueError("Media requires response extraction settings")
            if extraction.get("type") == "json_path" and not isinstance(extraction.get("path"), str):
                raise ValueError("JSON extraction requires a path")
            if self.config.get("is_async_poll") and (not (self.config.get("poll_endpoint_url") or self.config.get("poll_endpoint_path"))
                    or not all(self.config.get(field) for field in ("poll_status_field", "poll_success_value", "poll_failed_value"))):
                raise ValueError("Async media requires polling URL, status field and terminal statuses")
        if self.model_type == "tts" and self.adapter == "litellm":
            raise ValueError("TTS uses the existing HTTP/local configuration")
        if self.adapter == "litellm" and self.endpoint_url and not self.api_base_url:
            raise ValueError("Provide api_base_url separately; a full endpoint is not a LiteLLM base")
        if self.endpoint_url and self.api_base_url and urlsplit(self.endpoint_url).netloc != urlsplit(self.api_base_url).netloc:
            raise ValueError("Endpoint and base URL must use the same host")
        if self.adapter == "http" and not self.endpoint_url:
            raise ValueError("HTTP endpoints require a complete endpoint_url")
        if self.adapter == "local" and (self.model_type != "tts" or self.endpoint_url or self.api_base_url):
            raise ValueError("Local configuration has no remote URL and is TTS-only")
        json.dumps(self.config, allow_nan=False)
        return self


def endpoint_record(endpoint, model):
    return {"id": endpoint.id, "ai_model_id": model.id, "model_type": endpoint.model_type,
            "adapter": endpoint.adapter, "endpoint_url": endpoint.endpoint_url, "api_base_url": endpoint.api_base_url,
            "is_active": endpoint.is_active, "config": endpoint.config,
            "legacy_tts_model_id": endpoint.legacy_tts_model_id, "legacy_media_model_id": endpoint.legacy_media_model_id,
            "created_at": endpoint.created_at, "updated_at": endpoint.updated_at}


@router.get("/model-endpoints")
async def list_model_endpoints(response: Response, ai_model_id: int | None = None,
                               m=Depends(member), db=Depends(get_db)):
    require_admin(m)
    response.headers["Cache-Control"] = "no-store"
    statement = select(ModelEndpoint, AIModel).join(AIModel, AIModel.id == ModelEndpoint.ai_model_id)
    if ai_model_id is not None:
        statement = statement.where(ModelEndpoint.ai_model_id == ai_model_id)
    rows = (await db.execute(statement.order_by(ModelEndpoint.id))).all()
    return {"endpoints": [endpoint_record(endpoint, model) for endpoint, model in rows]}


@router.post("/model-endpoints", status_code=201)
async def create_model_endpoint(body: EndpointInput, request: Request, response: Response,
                                m=Depends(member), db=Depends(get_db)):
    return await save_model_endpoint(body, request, response, None, m, db)


@router.put("/model-endpoints/{endpoint_id}")
async def update_model_endpoint(endpoint_id: int, body: EndpointInput, request: Request, response: Response,
                                m=Depends(member), db=Depends(get_db)):
    return await save_model_endpoint(body, request, response, endpoint_id, m, db)


async def save_model_endpoint(body, request, response, endpoint_id, m, db):
    check_origin(request)
    require_admin(m)
    response.headers["Cache-Control"] = "no-store"
    model = await db.get(AIModel, body.ai_model_id)
    if model is None:
        raise HTTPException(404, "AI model not found")
    endpoint = await db.get(ModelEndpoint, endpoint_id) if endpoint_id is not None else ModelEndpoint()
    if endpoint is None:
        raise HTTPException(404, "Model endpoint not found")
    if endpoint_id is not None and (endpoint.ai_model_id != model.id or endpoint.model_type != body.model_type):
        raise HTTPException(409, "Endpoint model and type cannot be reassigned")
    try:
        for field in ("ai_model_id", "model_type", "adapter", "endpoint_url", "api_base_url", "is_active"):
            setattr(endpoint, field, getattr(body, field))
        endpoint.config = body.config
        endpoint.updated_at = datetime.now(UTC)
        await ensure_legacy_link(db, endpoint, model)
        db.add(endpoint)
        await db.flush()
        await audit(db, m, "UPDATE_MODEL_ENDPOINT" if endpoint_id else "CREATE_MODEL_ENDPOINT", str(endpoint.id))
        await db.commit()
        await db.refresh(endpoint)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "This model/type or legacy link already has an endpoint") from None
    except ValueError:
        await db.rollback()
        raise HTTPException(400, "Endpoint configuration conflicts with the legacy catalog") from None
    return endpoint_record(endpoint, model)


@router.get("/provider-models")
async def list_models(response: Response, m=Depends(member), db=Depends(get_db)):
    require_admin(m)
    response.headers["Cache-Control"] = "no-store"
    # Explicit columns avoid the provider-key relationship and encrypted credentials.
    rows = await db.execute(select(*(getattr(AIModel, key) for key in FIELDS))
                            .order_by(AIModel.created_at.desc(), AIModel.id.desc()))
    provider_name = func.lower(func.trim(ProviderAPIKey.provider))
    providers = (await db.execute(select(provider_name).where(provider_name != "")
                                 .distinct().order_by(provider_name))).scalars().all()
    return {"models": [dict(row._mapping) for row in rows], "providers": providers}


@router.post("/provider-models", status_code=201)
async def create_model(body: CreateModel, request: Request, response: Response,
                       m=Depends(member), db=Depends(get_db)):
    check_origin(request)
    require_admin(m)
    response.headers["Cache-Control"] = "no-store"
    registered = (await db.execute(select(ProviderAPIKey.id).where(
        func.lower(func.trim(ProviderAPIKey.provider)) == body.provider).limit(1))).scalar_one_or_none()
    if registered is None:
        raise HTTPException(400, "Select a provider with a saved provider API key")
    values = body.model_dump()
    if body.is_free:
        values.update(prompt_price_per_1k=0, completion_price_per_1k=0)
    model = AIModel(**values)
    try:
        db.add(model)
        await db.flush()
        await audit(db, m, "CREATE_PROVIDER_MODEL", str(model.id))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "A model with this ID already exists") from None
    return {"success": True}
