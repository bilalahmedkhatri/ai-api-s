"""Blog-only bounded body handling and sanitized errors before JSON model parsing."""

import logging

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError

from app.services.dashboard import check_origin

logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 2 * 1024 * 1024


class BlogRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                if request.method in {"POST", "PATCH", "PUT"}:
                    check_origin(request)
                    if self.path.endswith(("/assets", "/profile/avatar")):
                        return await original(request)
                    content_type = (
                        request.headers.get("content-type", "").split(";")[0].strip().lower()
                    )
                    if content_type != "application/json":
                        raise HTTPException(415, "Send blog data as application/json.")
                    declared = request.headers.get("content-length")
                    if declared is not None and (
                        len(declared) > 12 or not declared.isascii() or not declared.isdecimal()
                    ):
                        raise HTTPException(400, "Invalid content length.")
                    if declared is not None and int(declared) > MAX_BODY_BYTES:
                        raise HTTPException(413, "Blog request is too large.")
                    chunks, size = [], 0
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > MAX_BODY_BYTES:
                            raise HTTPException(413, "Blog request is too large.")
                        chunks.append(chunk)
                    request._body = b"".join(chunks)
                return await original(request)
            except (RequestValidationError, RecursionError, UnicodeError):
                raise HTTPException(422, "Invalid blog data or document structure.") from None
            except TimeoutError:
                raise HTTPException(
                    503, "Blog storage timed out. Reload before retrying a write."
                ) from None
            except (BotoCoreError, ClientError) as error:
                logger.warning("Blog storage unavailable error_type=%s", type(error).__name__)
                raise HTTPException(
                    503, "Blog media storage is unavailable. Retry with the same upload token."
                ) from None
            except SQLAlchemyError as error:
                logger.warning("Blog database operation failed error_type=%s", type(error).__name__)
                raise HTTPException(503, "Blog storage could not complete this request.") from None

        return handler
