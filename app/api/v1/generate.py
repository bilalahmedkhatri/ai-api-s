"""Prompt-only generation contract; limits run before FastAPI buffers/parses JSON."""

import asyncio
import logging
import time
from typing import Literal

from anyio import CancelScope
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator
from slowapi.errors import RateLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_api_key
from app.core.config import settings
from app.core.middleware import limiter
from app.db.database import get_db
from app.models.db_models import AccessAPIKey, FinalResponse, Query
from app.services.generation import GenerationError, GenerationTrace, generate_text
from app.services.generation_telemetry import request_identity
from app.services.query_telemetry import cost_summary, provider_calls

logger = logging.getLogger(__name__)


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(strict=True, min_length=1, max_length=settings.generation_max_prompt_chars)

    @field_validator("prompt")
    @classmethod
    def meaningful_prompt(cls, value):
        if not value.strip():
            raise ValueError("Prompt must contain non-whitespace text")
        # Reject unpaired JSON surrogate escapes, preserving valid Unicode.
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Prompt must contain valid Unicode") from None
        return value


class GenerateResponse(BaseModel):
    answer: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=80)
    request_id: str
    finish_reason: Literal["stop", "unknown"]


def error_response(request, status, code, message):
    logger.info(
        "Generation outcome request_id=%s status=%d code=%s", request.state.request_id, status, code
    )
    return JSONResponse(
        status_code=status,
        content={
            "detail": {"code": code, "message": message},
            "request_id": request.state.request_id,
        },
        headers={"Cache-Control": "no-store"},
    )


class BoundedGenerationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            try:
                async with asyncio.timeout(settings.generation_timeout_seconds) as deadline:
                    request.state.generation_deadline = deadline
                    length = request.headers.get("content-length")
                    if length and (
                        len(length) > 20 or not length.isascii() or not length.isdigit()
                    ):
                        return error_response(
                            request, 422, "invalid_request", "Invalid Content-Length"
                        )
                    if length and int(length) > settings.generation_max_body_bytes:
                        return error_response(
                            request,
                            413,
                            "request_too_large",
                            "Request body exceeds the configured byte limit",
                        )
                    chunks, size = [], 0
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > settings.generation_max_body_bytes:
                            return error_response(
                                request,
                                413,
                                "request_too_large",
                                "Request body exceeds the configured byte limit",
                            )
                        chunks.append(chunk)
                    request._body = b"".join(chunks)
                    response = await handler(request)
                    response.headers["Cache-Control"] = "no-store"
                    return response
            except RequestValidationError:
                # The global legacy handler logs/echoes inputs. Do not send
                # personal prompts or unexpected secret fields to that handler.
                return error_response(
                    request,
                    422,
                    "invalid_request",
                    "Expected only a non-empty valid Unicode prompt within the configured character limit",
                )
            except RateLimitExceeded:
                return error_response(request, 429, "rate_limited", "Service request rate exceeded")
            except GenerationError as exc:
                return error_response(request, exc.status, exc.code, exc.message)
            except HTTPException as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail, "request_id": request.state.request_id},
                    headers={**(exc.headers or {}), "Cache-Control": "no-store"},
                )
            except TimeoutError:
                return error_response(
                    request,
                    504,
                    "request_timeout",
                    "Request deadline exceeded; completion is unknown",
                )
            except Exception:
                return error_response(
                    request, 503, "generation_unavailable", "Generation is temporarily unavailable"
                )

        return bounded


router = APIRouter(prefix="/generate", tags=["generation"], route_class=BoundedGenerationRoute)
_slots = asyncio.Semaphore(settings.generation_max_concurrency)


@router.post("/", response_model=GenerateResponse, summary="Generate text from a complete prompt")
@limiter.limit(lambda: settings.generation_rate_limit)
async def generate_endpoint(
    req: GenerateRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    api_key: AccessAPIKey = Depends(get_current_api_key),
):
    started = time.monotonic()
    row = Query(
        raw_query=req.prompt, input_type="text", authorized_app_id=api_key.authorized_app_id
    )
    row.meta = {
        **request_identity(request, api_key.authorized_app_id),
        "request_id": request.state.request_id,
        "api_key_id": api_key.id,
        "request_type": "generate",
        "endpoint": "/api/v1/generate/",
        "status": "processing",
        "usage_accounting_status": "not_applicable",
    }
    db.add(row)
    await db.commit()
    identifier = row.id
    request.state.query_record_id = identifier
    trace = GenerationTrace()
    calls = []
    token = provider_calls.set(calls)
    status, http_status, error_type, error_code = "completed", 200, None, None
    try:
        if _slots.locked():
            raise GenerationError(429, "concurrency_limited", "Service generation capacity is busy")
        async with _slots:
            answer, finish_reason = await generate_text(
                req.prompt, db=db, request_id=request.state.request_id, trace=trace
            )
    except asyncio.CancelledError:
        deadline = getattr(request.state, "generation_deadline", None)
        timed_out = deadline is not None and deadline.expired()
        status, http_status = ("failed", 504) if timed_out else ("cancelled", 499)
        error_type = "TimeoutError" if timed_out else "CancelledError"
        error_code = "request_timeout" if timed_out else "request_cancelled"
        raise
    except Exception as exc:
        status = "failed"
        http_status = exc.status if isinstance(exc, GenerationError) else 503
        error_type = type(exc).__name__
        error_code = exc.code if isinstance(exc, GenerationError) else "generation_unavailable"
        raise
    finally:
        provider_calls.reset(token)
        # Persist even when the outer request deadline or disconnect cancels us.
        # Failed DB writes leave the committed initial row for existing recovery.
        with CancelScope(shield=True):
            try:
                async with asyncio.timeout(10):
                    if status != "completed":
                        await db.rollback()
                    saved = await db.get(Query, identifier)
                    result = trace.result
                    metadata = {
                        "request_type": "generate",
                        "status": status,
                        "finish_reason": trace.finish_reason,
                        "title": trace.title if status == "completed" else None,
                        "cache_hit": False,
                        "requires_search": False,
                        "model_attempts": trace.attempts,
                        "error_type": error_type,
                        "error_code": error_code,
                    }
                    final = FinalResponse(
                        query_id=identifier,
                        generated_output=result.answer if result else "",
                        model_used=result.model_used if result else "failed",
                        prompt_tokens=result.prompt_tokens if result else None,
                        completion_tokens=result.completion_tokens if result else None,
                        cost_usd=result.cost_usd if result else None,
                    )
                    final.meta = metadata
                    db.add(final)
                    saved.meta = {
                        **saved.meta,
                        "status": status,
                        "http_status": http_status,
                        "error_type": error_type,
                        "error_code": error_code,
                        "duration_ms": round((time.monotonic() - started) * 1000, 1),
                        **cost_summary(
                            calls, interrupted=error_type in {"CancelledError", "TimeoutError"}
                        ),
                        "usage_accounting_status": "pending"
                        if status == "completed"
                        else "not_applicable",
                    }
                    await db.commit()
            except Exception as exc:
                await db.rollback()
                logger.warning(
                    "Generation record unavailable query_id=%s error_type=%s",
                    identifier,
                    type(exc).__name__,
                )
                if status == "completed":
                    raise GenerationError(
                        503, "record_persistence_failed", "Generation result could not be saved"
                    ) from None
    return GenerateResponse(
        answer=answer,
        title=trace.title,
        request_id=request.state.request_id,
        finish_reason=finish_reason,
    )
