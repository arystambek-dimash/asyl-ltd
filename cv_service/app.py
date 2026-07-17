from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .contracts import ProcessorOptions
from .processor import ProcessorManager
from .security import valid_api_key
from .settings import parse_camera


def create_app(manager: ProcessorManager) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        manager.close()

    app = FastAPI(
        title="ASYL AI camera service",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def backend_only(request: Request, call_next):
        if not valid_api_key(request.headers.get("X-Api-Key"), manager.settings.api_key_sha256):
            return JSONResponse(status_code=401, content={"detail": "invalid API key"})
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def invalid_input(_request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(OverflowError)
    async def capacity(_request: Request, exc: OverflowError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(RuntimeError)
    async def dependency_unavailable(_request: Request, exc: RuntimeError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    def camera_id(value: str) -> str:
        try:
            return parse_camera(value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def with_startup(payload: dict) -> dict:
        return {
            **payload,
            "startup": {
                "model_reused": True,
                "model_instances": 1,
                "encoder": manager.encoder,
            },
        }

    @app.get("/health")
    def health():
        statuses = manager.statuses()
        try:
            inventory = manager.mediamtx.camera_inventory()
            mediamtx = {"available": True, "cameras": len(inventory)}
        except RuntimeError as exc:
            mediamtx = {"available": False, "error": str(exc)}
        processors_healthy = all(item["processor_alive"] for item in statuses)
        return with_startup({
            "status": "ok" if mediamtx["available"] and processors_healthy else "degraded",
            "model": manager.model.metadata(),
            "mediamtx": mediamtx,
            "processors": len(statuses),
            "counting": sum(bool(item["running"]) for item in statuses),
            "last_frames": {item["cam"]: item["last_frame_at"] for item in statuses},
        })

    @app.get("/cameras")
    def cameras():
        return with_startup(manager.cameras())

    @app.get("/processors")
    def processors():
        return {"processors": [with_startup(item) for item in manager.statuses()]}

    @app.get("/processors/{camera}")
    def processor(camera: str):
        try:
            return with_startup(manager.get(camera_id(camera)).status())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="processor not found") from exc

    @app.post("/processors/{camera}")
    def start(camera: str, options: ProcessorOptions):
        return with_startup(manager.start(camera_id(camera), options))

    @app.post("/processors/{camera}/prewarm")
    def prewarm(camera: str, options: ProcessorOptions):
        return with_startup(manager.prewarm(camera_id(camera), options))

    @app.post("/processors/{camera}/reset")
    def reset(camera: str):
        try:
            return with_startup(manager.reset(camera_id(camera)))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="processor not found") from exc

    @app.delete("/processors/{camera}")
    def stop(camera: str):
        try:
            return with_startup(manager.idle(camera_id(camera)))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="processor not found") from exc

    return app
