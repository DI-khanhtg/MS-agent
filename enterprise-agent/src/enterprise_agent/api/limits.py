"""Enforce body size before multipart parsing, including chunked uploads."""

from starlette.responses import JSONResponse


class BodyLimitMiddleware:
    def __init__(self, app, limit: int = 2_500_000) -> None:
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            return await self.app(scope, receive, send)
        # Buffer only bounded payloads, so untrusted chunked input cannot bypass the limit.
        messages, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.limit:
                return await JSONResponse({"detail": "Request body too large"}, 413)(
                    scope, receive, send)
            messages.append(message)
            if not message.get("more_body", False):
                break
        index = 0

        async def bounded_receive():
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return await receive()

        await self.app(scope, bounded_receive, send)
