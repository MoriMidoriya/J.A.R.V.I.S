"""FastAPI application: `/ws` for the HUD, a small REST surface for scripts, and the built HUD as static files."""

from __future__ import annotations

from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel

from jarvis.config import ROOT, Settings
from jarvis.server.hub import Hub

HUD_DIST = ROOT / "hud" / "dist"


class ChatIn(BaseModel):
    text: str
    speak: bool = True  # False = answer silently via the agent (for scripts)


class ConfirmIn(BaseModel):
    id: str
    accepted: bool


def build_app(settings: Settings, hub: Hub, agent) -> FastAPI:
    app = FastAPI(title="J.A.R.V.I.S.", docs_url=None, redoc_url=None)

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await hub.serve(ws)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "state": hub.state, "model": settings.llm.primary, "clients": len(hub.clients)}

    @app.post("/chat")
    async def chat(body: ChatIn) -> JSONResponse:
        """Text in, text out. With speak=true the reply is also spoken and shown on the HUD."""
        if body.speak and hub.pipeline is not None:
            await hub.pipeline.submit_text(body.text)
            return JSONResponse({"accepted": True})
        reply = await agent.respond(body.text)
        return JSONResponse({"reply": reply})

    @app.post("/confirm")
    async def confirm(body: ConfirmIn) -> dict[str, bool]:
        if hub.pipeline is not None:
            hub.pipeline.resolve_confirm_external(body.accepted)
        return {"ok": True}

    @app.post("/interrupt")
    async def interrupt() -> dict[str, bool]:
        if hub.pipeline is not None:
            await hub.pipeline.interrupt()
        return {"ok": True}

    @app.post("/shutdown")
    async def shutdown() -> dict[str, bool]:
        """Graceful stop, used by the HUD launcher when the window quits (server is loopback-only)."""
        if hub.on_shutdown is None:
            return {"ok": False}
        logger.info("shutdown requested over HTTP")
        hub.on_shutdown()
        return {"ok": True}

    if (HUD_DIST / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=HUD_DIST / "assets"), name="assets")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(HUD_DIST / "index.html")

        @app.get("/{path:path}")
        async def static_file(path: str) -> FileResponse:
            f = HUD_DIST / path
            if f.is_file():
                return FileResponse(f)
            return FileResponse(HUD_DIST / "index.html")
    else:

        @app.get("/")
        async def index_missing() -> HTMLResponse:
            return HTMLResponse("<h2 style='font-family:sans-serif'>HUD not built.</h2><p>Run <code>cd hud && npm install && npm run build</code>.</p>", status_code=200)

    return app


async def serve(app: FastAPI, host: str, port: int) -> None:
    """Run uvicorn inside the existing asyncio loop (shares it with the voice pipeline).

    `log_config=None`: uvicorn's default logging config builds a stderr formatter, which raises under pythonw.exe
    (no console → `sys.stderr is None`) and silently killed the server on Windows autostart. Its loggers are quiet
    at WARNING anyway; anything important surfaces through our own loguru log.
    """
    config = uvicorn.Config(
        app, host=host, port=port, log_level="warning", log_config=None, access_log=False, loop="none", lifespan="off", ws_ping_interval=20, ws_ping_timeout=20
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # the main app handles Ctrl+C
    logger.info("HUD server on http://{}:{}", host, port)
    try:
        await server.serve()
    except Exception:
        logger.exception("HUD server failed to start on {}:{} (port in use?)", host, port)
        raise
