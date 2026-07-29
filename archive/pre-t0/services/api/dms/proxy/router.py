"""Proxy unmatched /dms/* and /api/* to Cortex."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, Response

from settings import get_settings

router = APIRouter()


async def _forward(base: str, path: str, request: Request) -> Response:
    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
    body = await request.body()
    async with httpx.AsyncClient(timeout=120.0) as client:
        upstream = await client.request(request.method, url, content=body, headers=headers)
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


@router.api_route("/dms/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_dms(path: str, request: Request) -> Response:
    settings = get_settings()
    return await _forward(f"{settings.cortex_url}/dms", path, request)


@router.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_api(path: str, request: Request) -> Response:
    settings = get_settings()
    return await _forward(f"{settings.cortex_url}/api", path, request)
