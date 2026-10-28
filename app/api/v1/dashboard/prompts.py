"Session-authenticated CRUD adapter for admin-managed AI prompts."

from app.db.database import get_db
from app.models.db_models import Prompt
from app.services.dashboard import audit, check_origin, member, require_admin
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

router = APIRouter(prefix=/dashboard/prompts, tags=[dashboard])

VALID_CATEGORIES = {
    Audio Generation,
    Video Generation,
    Blog Generation,
    Image Generation,
    Cover Letter,
    Social Media Copy,
}


class PromptCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra=forbid)
    title: str = Field(min_length=2, max_length=255)
    category: str = Field(min_length=2, max_length=64)
    content: str = Field(min_length=1)
    author_name: str = Field(min_length=1, max_length=128, default=Admin)


def _serialize(p: Prompt) -> dict:
    return {
        id: p.id,
        title: p.title,
        category: p.category,
        content: p.content,
        author_name: p.author_name,
        created_at: p.created_at.isoformat() if p.created_at else None,
        updated_at: p.updated_at.isoformat() if p.updated_at else None,
    }


@router.get(")
async def list_prompts(
 response: Response,
 page: int = 1,
 page_size: int = 10,
 m=Depends(member),
 db=Depends(get_db),
):
 require_admin(m)
 response.headers[Cache-Control] = no-store
 page = max(1, page)
 page_size = min(max(1, page_size), 50)
 offset = (page - 1) * page_size

 total: int = (await db.execute(select(func.count()).select_from(Prompt))).scalar_one()
 rows = (
 await db.execute(
 select(Prompt).order_by(Prompt.created_at.desc()).offset(offset).limit(page_size)
 )
 ).scalars().all()

 return {
 items: [_serialize(p) for p in rows],
 total: total,
 page: page,
 page_size: page_size,
 has_next: offset + page_size < total,
 has_prev: page > 1,
 }


@router.post(, status_code=201)
async def create_prompt(
 body: PromptCreate,
 request: Request,
 response: Response,
 m=Depends(member),
 db=Depends(get_db),
):
 check_origin(request)
 require_admin(m)
 response.headers[Cache-Control] = no-store

 if body.category not in VALID_CATEGORIES:
 raise HTTPException(422, fInvalid category. Allowed: {sorted(VALID_CATEGORIES)})

 prompt = Prompt(
 title=body.title,
 category=body.category,
 content=body.content,
 author_name=body.author_name,
 )
 db.add(prompt)
 await audit(db, m, CREATE_PROMPT, body.title, 201)
 await db.commit()
 await db.refresh(prompt)
 return _serialize(prompt)


@router.get(/{prompt_id})
async def get_prompt(
 prompt_id: int,
 response: Response,
 m=Depends(member),
 db=Depends(get_db),
):
 require_admin(m)
 response.headers[Cache-Control] = no-store
 prompt = await db.get(Prompt, prompt_id)
 if not prompt:
 raise HTTPException(404, Prompt not found)
 return _serialize(prompt)


@router.patch(/{prompt_id})
async def update_prompt(
 prompt_id: int,
 body: PromptCreate,
 request: Request,
 response: Response,
 m=Depends(member),
 db=Depends(get_db),
):
 check_origin(request)
 require_admin(m)
 response.headers[Cache-Control] = no-store

 if body.category not in VALID_CATEGORIES:
 raise HTTPException(422, fInvalid category. Allowed: {sorted(VALID_CATEGORIES)})

 prompt = await db.get(Prompt, prompt_id)
 if not prompt:
 raise HTTPException(404, Prompt not found)

 prompt.title = body.title
 prompt.category = body.category
 prompt.content = body.content
 prompt.author_name = body.author_name
 await audit(db, m, UPDATE_PROMPT, str(prompt_id))
 await db.commit()
 await db.refresh(prompt)
 return _serialize(prompt)


@router.delete(/{prompt_id}, status_code=204)
async def delete_prompt(
 prompt_id: int,
 request: Request,
 response: Response,
 m=Depends(member),
 db=Depends(get_db),
):
 check_origin(request)
 require_admin(m)
 response.headers[Cache-Control] = no-store

 prompt = await db.get(Prompt, prompt_id)
 if not prompt:
 raise HTTPException(404, Prompt not found)

 await db.delete(prompt)
 await audit(db, m, DELETE_PROMPT, str(prompt_id))
 await db.commit()
