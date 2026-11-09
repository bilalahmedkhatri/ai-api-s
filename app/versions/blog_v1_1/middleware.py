"""V1.1 cache protection and response-start timing without extra database work."""

from time import perf_counter


class BlogV11Middleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not (
            path == "/api/v1.1/dashboard/blog" or path.startswith("/api/v1.1/dashboard/blog/")
        ):
            return await self.app(scope, receive, send)
        started = perf_counter()

        async def timed_send(message):
            if message["type"] == "http.response.start":
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"cache-control"
                ]
                headers.extend(
                    [
                        (b"cache-control", b"no-store"),
                        (
                            b"server-timing",
                            f"backend;dur={(perf_counter() - started) * 1000:.2f}".encode(),
                        ),
                    ]
                )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, timed_send)
