"""Protect request resources and support private platform health probes."""
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.responses import JSONResponse


class PublicHTTPSRedirect(HTTPSRedirectMiddleware):
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] in {"/health", "/ready"}:
            return await self.app(scope, receive, send)
        return await super().__call__(scope, receive, send)


class RequestSizeLimit:
    """Bound multipart uploads and chunked bodies before framework parsing."""
    def __init__(self, app, maximum=6 * 1024 * 1024):
        self.app = app
        self.maximum = maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH", "DELETE"}:
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.maximum:
                return await JSONResponse({"detail": "Request is too large (maximum 6 MB)"}, status_code=413)(scope, receive, send)
            if not message.get("more_body", False):
                break
        consumed = False

        async def replay():
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)
