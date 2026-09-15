"""app/services/media_gen_service.py — Dynamic Media Generation (Image/Video)."""

import asyncio
import base64
import json
import logging
import os
import uuid
import httpx
from datetime import UTC, datetime
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from jsonpath_ng import parse

from app.models.db_models import MediaModel
from app.core.config import settings

logger = logging.getLogger(__name__)


async def get_media_models(db: AsyncSession, media_type: str | None = None) -> list[dict]:
    """Return all active media models, optionally filtered by type."""
    stmt = select(MediaModel).where(MediaModel.is_active == True)
    if media_type:
        stmt = stmt.where(MediaModel.media_type == media_type)
    
    result = await db.execute(stmt)
    models = result.scalars().all()
    
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
        .where(MediaModel.is_active == True)
    )
    model = result.scalar_one_or_none()
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
        except Exception:
            raise ValueError("Response is not valid JSON but extraction config expects JSON")

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


async def _poll_async_api(client: httpx.AsyncClient, model: MediaModel, headers: dict, request_id: str) -> list[str]:
    """Poll an async endpoint until success, failure, or timeout."""
    poll_url = f"{model.api_base_url}{model.poll_endpoint_path.replace('{id}', request_id)}"
    start_time = datetime.now(UTC)
    
    while True:
        elapsed = (datetime.now(UTC) - start_time).total_seconds()
        if elapsed > model.poll_timeout_s:
            raise TimeoutError(f"Polling timed out after {model.poll_timeout_s}s")
            
        poll_resp = await client.get(poll_url, headers=headers)
        poll_resp.raise_for_status()
        data = poll_resp.json()
        
        status_expr = parse(model.poll_status_field)
        status_matches = status_expr.find(data)
        
        if not status_matches:
            logger.warning(f"Could not find status field '{model.poll_status_field}' in {data}")
            await asyncio.sleep(model.poll_interval_s)
            continue
            
        current_status = status_matches[0].value
        
        if current_status == model.poll_success_value:
            return _extract_results(data, model.response_extraction_config)
        elif current_status == model.poll_failed_value:
            raise RuntimeError(f"Generation failed: {data}")
            
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
) -> tuple[list[str], str]:
    """Generate media (image or video) using the configured provider strategy."""
    
    # 1. Load model config
    model = await get_media_model(model_name, db)
    
    # 2. Prepare payload
    num_outputs = min(num_outputs, model.max_outputs)
    payload = model.default_params.copy()
    if extra_params:
        payload.update(extra_params)
        
    width = width or model.default_width
    height = height or model.default_height

    headers = _build_auth_headers(model)
    
    # 3. Dispatch based on provider
    async with httpx.AsyncClient(timeout=60.0) as client:
        
        # --- REPLICATE ---
        if model.provider == "replicate":
            # Replicate generates 1 image per prediction. If num_outputs > 1, we fan-out.
            async def run_one():
                input_data = {"prompt": prompt}
                if negative_prompt: input_data["negative_prompt"] = negative_prompt
                if width: input_data["width"] = width
                if height: input_data["height"] = height
                # Overrides
                input_data.update(payload)
                
                body = {"version": model.model_version, "input": input_data}
                resp = await client.post(f"{model.api_base_url}{model.api_endpoint_path}", json=body, headers=headers)
                resp.raise_for_status()
                
                prediction = resp.json()
                req_id = prediction["id"]
                
                if model.is_async_poll:
                    return await _poll_async_api(client, model, headers, req_id)
                else:
                    return _extract_results(prediction, model.response_extraction_config)

            tasks = [run_one() for _ in range(num_outputs)]
            results = await asyncio.gather(*tasks)
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
            
            resp = await client.post(f"{model.api_base_url}{model.api_endpoint_path}", json=body, headers=headers)
            resp.raise_for_status()
            
            urls = _extract_results(resp.json(), model.response_extraction_config)
            return urls, model.media_type


        # --- STABILITY AI ---
        elif model.provider == "stability":
            # Uses form data
            data = {"prompt": prompt}
            if negative_prompt: data["negative_prompt"] = negative_prompt
            if model.returns_base64:
                data["output_format"] = "png"
            data.update(payload) # merge defaults
            
            resp = await client.post(
                f"{model.api_base_url}{model.api_endpoint_path}", 
                data=data, 
                headers=headers
            )
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
            
            resp = await client.post(f"{model.api_base_url}{model.api_endpoint_path}", json=body, headers=headers)
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
            
            resp = await client.post(f"{model.api_base_url}{model.api_endpoint_path}", json=body, headers=headers)
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
