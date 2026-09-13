"""Independent authenticated upload/download API and Streamable HTTP MCP."""
from __future__ import annotations

import hmac
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from mcp.server.transport_security import TransportSecuritySettings

from .jobs import MAX_UPLOAD, Conflict
from .mcp_server import configured_store, create_mcp


def create_app(store=None, token=None):
    store = store or configured_store()
    token = token or os.getenv("CANVASLAB3D_TOKEN")
    if not token or len(token) < 32:
        raise RuntimeError("Set CANVASLAB3D_TOKEN to at least 32 random characters")
    transport = create_mcp(store).streamable_http_app(streamable_http_path="/mcp-3d", stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=os.getenv("CANVASLAB3D_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*").split(","),
            allowed_origins=os.getenv("CANVASLAB3D_ALLOWED_ORIGINS", "http://localhost:*,http://127.0.0.1:*").split(",")))

    @asynccontextmanager
    async def lifespan(app):
        async with transport.router.lifespan_context(transport):
            yield

    app = FastAPI(title="CanvasLab 3D", lifespan=lifespan)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.url.path != "/health" and not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer "+token):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409 if isinstance(exc, Conflict) else 400)

    @app.get("/health")
    def health():
        return {"service": "canvaslab-3d", "status": "up", "release_grade": False}

    @app.post("/assets")
    async def upload(request: Request):
        payload = bytearray()
        async for part in request.stream():
            if len(payload)+len(part) > MAX_UPLOAD:
                raise HTTPException(413, "Upload exceeds 20 MiB")
            payload.extend(part)
        import asyncio
        return await asyncio.to_thread(store.upload, bytes(payload))

    @app.get("/assets/{asset_id}")
    def source(asset_id: str):
        if not re.fullmatch(r"[a-f0-9]{64}", asset_id):
            raise HTTPException(404)
        with store.transaction() as db:
            store.get(db, "asset", asset_id)
        return FileResponse(store.root / "assets" / asset_id, media_type="application/octet-stream", filename="source-image")

    @app.get("/builds/{job_id}/{build_id}/project.zip")
    def download(job_id: str, build_id: str):
        directory = store.build_path(job_id, build_id)
        return FileResponse(directory / "project.zip", filename=f"canvaslab-3d-{build_id[:12]}.zip")

    @app.get("/components/{asset_id}/{format}")
    def component(asset_id: str, format: str):
        if format not in {"glb","blend"}:
            raise HTTPException(404)
        store.components.get(asset_id)
        filename = f"component.{format}"
        return FileResponse(store.components.root/asset_id/filename, filename=f"component-{asset_id[:12]}.{format}",
                            media_type="model/gltf-binary" if format == "glb" else "application/octet-stream")

    @app.get("/captures/{capture_id}/{filename}")
    def evidence(capture_id: str, filename: str):
        if not re.fullmatch(r"[a-f0-9]{32}", capture_id):
            raise HTTPException(404)
        with store.transaction() as db:
            record = store.get(db, "capture", capture_id)
        if filename not in record["files"]:
            raise HTTPException(404)
        return FileResponse(store.root / "captures" / capture_id / filename)

    app.mount("/", transport)
    return app
