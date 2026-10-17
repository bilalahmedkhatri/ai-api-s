"""Admin-only registration in the shared AI model catalog."""
from app.db.database import get_db
from app.models.db_models import AIModel, ProviderAPIKey
from app.services.dashboard import audit, check_origin, member, require_admin
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
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
