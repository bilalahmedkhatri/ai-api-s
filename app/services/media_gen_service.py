"""app/services/media_gen_service.py — Dynamic Media Generation (Image/Video)."""

import asyncio
import base64
import json
import logging
import os
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

import httpx
from jsonpath_ng import parse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.db_models import MediaModel
from app.services.generation_telemetry import begin_attempt, observe_http, provider_attempt
from app.services.key_manager import get_next_available_key, mark_key_quota_exceeded, mark_key_used
from app.services.model_endpoints import legacy_views

logger = logging.getLogger(__name__)


async def get_media_models(db: AsyncSession, media_type: str | None = None) -> list[dict]:
    """Return all active media models, optionally filtered by type."""
    stmt = select(MediaModel)
    if media_type:
        stmt = stmt.where(MediaModel.media_type == media_type)

    result = await db.execute(stmt)
    rows = result.scalars().all()
    models = []
    for kind in ("image", "video"):
        models.extend(await legacy_views(db, [row for row in rows if row.media_type == kind], kind))

    return [
        {
            "name": m.name,
            "display_name": m.display_name,
            "media_type": m.media_type,
            "provider": m.provider,
            "default_width": m.default_width,
            "default_height": m.default_height,
        }
        for m in models
    ]


async def get_media_model(name: str, db: AsyncSession) -> MediaModel:
    """Retrieve a specific active MediaModel by name."""
    result = await db.execute(
        select(MediaModel)
        .where(MediaModel.name == name)
    )
    model = result.scalar_one_or_none()
    models = await legacy_views(db, [model] if model else [], model.media_type if model else "image")
    model = models[0] if models else None
    if not model:
        raise ValueError(f"Media model '{name}' not found or inactive.")
    return model


def _extract_results(data: dict | bytes, config: dict) -> list[str]:
    """Dynamically extract media URLs or Base64 from API responses via JSONPath."""
    if config.get("type") == "binary":
        # Save raw binary bytes to local static file
        media_bytes = data
        ext = "png" if config.get("mime", "").endswith("png") else "mp4"
        filename = f"{uuid.uuid4().hex}.{ext}"
        filepath = Path(settings.generated_media_dir) / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(media_bytes)
        return [f"/static/generated/{filename}"]

    if not isinstance(data, dict):
        # We expected JSON but got something else
        try:
            data = json.loads(data)
        except Exception as exc:
            raise ValueError("Response is not valid JSON but extraction config expects JSON") from exc

    expr = parse(config["path"])
    matches = [m.value for m in expr.find(data)]

    results = []
    for item in matches:
        if config.get("item_type") == "base64":
            # Save Base64 to local static file
            if "," in item:
                item = item.split(",", 1)[1]
            media_bytes = base64.b64decode(item)
            ext = "png" # Usually images come back as base64, video models usually return URLs
            filename = f"{uuid.uuid4().hex}.{ext}"
            filepath = Path(settings.generated_media_dir) / filename
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_bytes(media_bytes)
            results.append(f"/static/generated/{filename}")
        else:
            # It's a standard URL
            results.append(str(item))

    return results


def _build_auth_headers(model: MediaModel) -> dict:
    """Build Authorization headers dynamically from the DB config."""
    if not model.auth_env_var:
        return {}

    token = os.environ.get(model.auth_env_var)
    if not token:
        logger.error(f"Missing env var {model.auth_env_var} for model {model.name}")
        raise ValueError(f"Server is missing API key for {model.name}")

    if model.provider == "replicate":
        return {"Authorization": f"Bearer {token}"}
    elif model.provider == "openai":
        return {"Authorization": f"Bearer {token}"}
    elif model.provider == "stability":
        return {"Authorization": f"Bearer {token}", "Accept": "image/*"}
    elif model.provider == "fal":
        return {"Authorization": f"Key {token}"}
    else:
        return {"Authorization": f"Bearer {token}"}


async def _post_media(client, db, model, key_lock, **payload):
    key = None
    if getattr(model, "model_endpoint_id", None):
        async with key_lock:
            key = await get_next_available_key(db, model.provider, reserve=True)
        if not key:
            if not model.auth_env_var:
                raise ValueError("No eligible provider key for this model endpoint")
            headers = _build_auth_headers(model)  # Retain explicitly configured legacy env references.
        else:
            headers = {"Authorization": f"{'Key' if model.provider == 'fal' else 'Bearer'} {key[1]}"}
            if model.provider == "stability":
                headers["Accept"] = "image/*"
    else:
        headers = _build_auth_headers(model)
    url = getattr(model, "endpoint_url", None) or f"{model.api_base_url}{model.api_endpoint_path}"
    await begin_attempt()
    response = await client.post(url, headers=headers, **payload)
    observe_http(response)
    if key:
        async with key_lock:
            if response.status_code == 429:
                await mark_key_quota_exceeded(db, key[0])
            elif response.is_success:
                try:
                    await mark_key_used(db, key[0], reserved=True)
                except Exception as exc:
                    await db.rollback()
                    logger.warning("Media key bookkeeping deferred error_type=%s", type(exc).__name__)
    return response, headers


async def _poll_async_api(client: httpx.AsyncClient, model: MediaModel, headers: dict, request_id: str) -> list[str]:
    """Poll an async endpoint until success, failure, or timeout."""
    template = getattr(model, "poll_endpoint_url", None) or f"{model.api_base_url}{model.poll_endpoint_path}"
    poll_url = template.replace('{id}', request_id)
    start_time = datetime.now(UTC)

    while True:
        elapsed = (datetime.now(UTC) - start_time).total_seconds()
        if elapsed > model.poll_timeout_s:
            raise TimeoutError(f"Polling timed out after {model.poll_timeout_s}s")

        poll_resp = await client.get(poll_url, headers=headers)
        observe_http(poll_resp)
        poll_resp.raise_for_status()
        data = poll_resp.json()

        status_expr = parse(model.poll_status_field)
        status_matches = status_expr.find(data)

        if not status_matches:
            logger.warning("Media polling response has no configured status field")
            await asyncio.sleep(model.poll_interval_s)
            continue

        current_status = status_matches[0].value

        if current_status == model.poll_success_value:
            return _extract_results(data, model.response_extraction_config)
        elif current_status == model.poll_failed_value:
            raise RuntimeError("Provider reported generation failure")

        await asyncio.sleep(model.poll_interval_s)


async def generate_media(
    db: AsyncSession,
    model_name: str,
    prompt: str,
    negative_prompt: str | None = None,
    width: int | None = None,
    height: int | None = None,
    duration_s: float | None = None,
    num_outputs: int = 1,
    extra_params: dict | None = None,
    model_snapshot=None,
) -> tuple[list[str], str]:
    """Generate media (image or video) using the configured provider strategy."""

    # 1. Load model config
    model = model_snapshot or await get_media_model(model_name, db)
    key_lock = asyncio.Lock()  # Serialize this AsyncSession's DB work, never network I/O.

    # 2. Prepare payload
    num_outputs = min(num_outputs, model.max_outputs)
    payload = model.default_params.copy()
    if extra_params:
        payload.update(extra_params)

    width = width or model.default_width
    height = height or model.default_height


    # 3. Dispatch based on provider
    operation = nullcontext() if model.provider == "replicate" else provider_attempt(
        model.media_type, model=model.name, provider=model.provider, operation_id="outputs")
    async with operation, httpx.AsyncClient(timeout=60.0) as client:

        # --- REPLICATE ---
        if model.provider == "replicate":
            # Replicate generates 1 image per prediction. If num_outputs > 1, we fan-out.
            async def run_one(index):
                async with provider_attempt(model.media_type, model=model.name, provider=model.provider,
                                            operation_id=f"output:{index}"):
                    return await submit_one()

            async def submit_one():
                input_data = {"prompt": prompt}
                if negative_prompt:
                    input_data["negative_prompt"] = negative_prompt
                if width:
                    input_data["width"] = width
                if height:
                    input_data["height"] = height
                # Overrides
                input_data.update(payload)

                body = {"version": model.model_version, "input": input_data}
                resp, headers = await _post_media(client, db, model, key_lock, json=body)
                resp.raise_for_status()

                prediction = resp.json()
                req_id = prediction["id"]

                if model.is_async_poll:
                    return await _poll_async_api(client, model, headers, req_id)
                else:
                    return _extract_results(prediction, model.response_extraction_config)

            tasks = [run_one(index) for index in range(num_outputs)]
            # Finish every started sibling before persisting the parent failure.
            results = await asyncio.gather(*tasks, return_exceptions=True)
            failure = next((result for result in results if isinstance(result, BaseException)), None)
            if failure is not None:
                raise failure
            # Flatten lists
            flat_results = [url for sublist in results for url in sublist]
            return flat_results, model.media_type


        # --- OPENAI ---
        elif model.provider == "openai":
            body = {
                "model": model.model_version,
                "prompt": prompt,
                "n": num_outputs,
                "response_format": "url" if not model.returns_base64 else "b64_json",
            }
            if width and height:
                body["size"] = f"{width}x{height}"
            body.update(payload)

            resp, headers = await _post_media(client, db, model, key_lock, json=body)
            resp.raise_for_status()

            urls = _extract_results(resp.json(), model.response_extraction_config)
            return urls, model.media_type


        # --- STABILITY AI ---
        elif model.provider == "stability":
            # Uses form data
            data = {"prompt": prompt}
            if negative_prompt:
                data["negative_prompt"] = negative_prompt
            if model.returns_base64:
                data["output_format"] = "png"
            data.update(payload) # merge defaults

            resp, headers = await _post_media(client, db, model, key_lock, data=data)
            resp.raise_for_status()

            content = resp.content if model.response_extraction_config.get("type") == "binary" else resp.json()
            urls = _extract_results(content, model.response_extraction_config)
            return urls, model.media_type


        # --- FAL ---
        elif model.provider == "fal":
            body = {"prompt": prompt}
            if width and height:
                body["image_size"] = {"width": width, "height": height}
            body.update(payload)

            resp, headers = await _post_media(client, db, model, key_lock, json=body)
            resp.raise_for_status()

            # fal can be sync or async depending on the endpoint used (/v1/generation vs /v1/async)
            if model.is_async_poll:
                req_id = resp.json().get("request_id")
                return await _poll_async_api(client, model, headers, req_id), model.media_type
            else:
                urls = _extract_results(resp.json(), model.response_extraction_config)
                return urls, model.media_type


        # --- GENERIC HTTP ---
        elif model.provider == "generic_http":
            body = {"prompt": prompt}
            body.update(payload)

            resp, headers = await _post_media(client, db, model, key_lock, json=body)
            resp.raise_for_status()

            if model.is_async_poll:
                # Need to find the request ID in the initial response
                req_id_field = payload.get("_generic_req_id_field", "id") # hacky way to specify it
                req_id = resp.json().get(req_id_field)
                if not req_id:
                    raise ValueError(f"Could not extract request ID from generic provider response: {resp.json()}")
                return await _poll_async_api(client, model, headers, req_id), model.media_type
            else:
                urls = _extract_results(resp.json(), model.response_extraction_config)
                return urls, model.media_type

        else:
            raise ValueError(f"Unsupported provider: {model.provider}")
