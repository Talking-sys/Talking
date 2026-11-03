"""HTTP routes for unfurl + proxied preview images. Mounted from server.py."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from ..security import TokenBucket, client_ip
from .service import UnfurlService
from .ssrf import SSRFError


def register_unfurl_routes(
    app: FastAPI,
    *,
    unfurl: UnfurlService,
    limiter: TokenBucket,
) -> None:
    @app.get("/api/unfurl")
    async def get_unfurl(request: Request, url: str = ""):
        if not limiter.allow(client_ip(request)):
            raise HTTPException(429, "rate limit")
        if not url or len(url) > 2048:
            raise HTTPException(400, "invalid url")
        try:
            return unfurl.preview(url)
        except SSRFError:
            raise HTTPException(400, "invalid url")

    @app.get("/api/unfurl/image/{image_id}")
    def get_unfurl_image(image_id: str):
        data = unfurl.image_bytes(image_id)
        if not data:
            raise HTTPException(404, "not found")
        return Response(
            content=data,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "public, max-age=86400",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": "inline",
            },
        )
