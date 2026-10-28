import asyncio
from contextlib import asynccontextmanager, suppress
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .rpc import RpcError
from .service import Harness

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"


class VoiceStart(BaseModel):
    client_id: str = Field(min_length=1, max_length=100)
    sdp: str = Field(min_length=20, max_length=100_000)


class Owner(BaseModel):
    client_id: str = Field(min_length=1, max_length=100)


class Activity(Owner):
    kind: str = Field(pattern="^(connected|interrupted|input_started|input_stopped)$")


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=30_000)
    deep: bool = False


def local_origin(origin, host):
    parsed = urlsplit(origin)
    return parsed.scheme == "http" and parsed.netloc == host and parsed.hostname in ("localhost", "127.0.0.1", "::1")


def create_app(harness=None):
    service = harness or Harness(ROOT, os.environ.get("VOICE_STATE_DIR", str(ROOT / ".state")))
    connections = {}

    @asynccontextmanager
    async def lifespan(app):
        await service.start()
        yield
        await service.close()

    app = FastAPI(title="Lipa · Slovene Voice Harness", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.harness = service
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])

    @app.middleware("http")
    async def origin_check(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and not local_origin(origin, request.headers.get("host", "")):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Only this local application's origin is allowed."}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(RpcError)
    async def rpc_error(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    async def status():
        return service.snapshot()

    @app.post("/api/voice/start")
    async def voice_start(body: VoiceStart):
        return await service.start_voice(body.sdp, body.client_id)

    @app.post("/api/voice/stop")
    async def voice_stop(body: Owner):
        await service.stop_voice(body.client_id)
        return {"stopped": True, "task_preserved": True}

    @app.post("/api/voice/activity")
    async def voice_activity(body: Activity):
        await service.browser_activity(body.kind, body.client_id)
        return {"accepted": True}

    @app.post("/api/message")
    async def message(body: Message):
        if not body.text.strip():
            raise HTTPException(422, "A message cannot be empty.")
        return await service.message(body.text.strip(), body.deep)

    @app.post("/api/task/cancel")
    async def cancel():
        await service.cancel_task()
        return {"cancellation_requested": True}

    @app.post("/api/desktop/reconnect")
    async def reconnect_desktop():
        await service.reconnect_desktop()
        return {"connected": True, "task_replayed": False}

    @app.websocket("/api/events")
    async def events(websocket: WebSocket):
        if not local_origin(websocket.headers.get("origin", ""), websocket.headers.get("host", "")):
            await websocket.close(code=1008)
            return
        client_id = websocket.query_params.get("client_id", "")[:100]
        if not client_id:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        queue = service.subscribe()
        connections[client_id] = connections.get(client_id, 0) + 1

        async def send():
            while True:
                await websocket.send_json(await queue.get())

        async def receive():
            while True:
                await websocket.receive_text()

        tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                with suppress(WebSocketDisconnect, RuntimeError):
                    task.result()
        finally:
            service.listeners.discard(queue)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            connections[client_id] -= 1
            if connections[client_id] == 0:
                del connections[client_id]
                # Closing a tab ends its metered voice session, but leaves reasoning running.
                await asyncio.sleep(2)
                if client_id not in connections and service.owner == client_id:
                    with suppress(Exception):
                        await service.stop_voice(client_id)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
