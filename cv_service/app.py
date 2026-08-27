from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .contracts import (
    AlwaysOnOptions,
    CountingLineOptions,
    ProcessorOptions,
    RecordingDeleteOptions,
    WagonNumberOptions,
)
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
        journal = manager.event_journal_health()
        if not journal["available"]:
            return with_startup({
                "status": "degraded",
                "model": manager.model.metadata(),
                "mediamtx": {
                    "available": None,
                    "error": "not checked while event journal is unavailable",
                },
                "event_journal": journal,
                "processors": len(manager.processors),
                "counting": None,
                "last_frames": {},
                "capabilities": {
                    "wagon_plate": manager.wagon_plate_capability(),
                    "count_events": True,
                },
            })
        statuses = manager.statuses()
        try:
            inventory = manager.mediamtx.camera_inventory()
            mediamtx = {"available": True, "cameras": len(inventory)}
        except RuntimeError as exc:
            mediamtx = {"available": False, "error": str(exc)}
        processors_healthy = all(item["processor_alive"] for item in statuses)
        return with_startup({
            "status": (
                "ok"
                if mediamtx["available"]
                and processors_healthy
                and journal["available"]
                else "degraded"
            ),
            "model": manager.model.metadata(),
            "mediamtx": mediamtx,
            "event_journal": journal,
            "processors": len(statuses),
            "counting": sum(bool(item["running"]) for item in statuses),
            "last_frames": {item["cam"]: item["last_frame_at"] for item in statuses},
            "capabilities": {
                "wagon_plate": manager.wagon_plate_capability(),
                "count_events": True,
            },
        })

    @app.get("/events")
    def count_events(
        after_id: int = 0,
        limit: int = 500,
        cam: str | None = None,
    ):
        return manager.count_events(
            after_id=after_id,
            limit=limit,
            cam=camera_id(cam) if cam is not None else None,
        )

    @app.get("/cameras")
    def cameras():
        return with_startup(manager.cameras())

    @app.get("/cameras/{camera}/line")
    def counting_line(camera: str):
        return manager.counting_line(camera_id(camera))

    @app.put("/cameras/{camera}/line")
    def save_counting_line(camera: str, options: CountingLineOptions):
        status, payload = manager.save_counting_line(
            camera_id(camera), options.line, options.direction,
        )
        return JSONResponse(status_code=status, content=payload)

    @app.post("/wagon-number/detect")
    async def detect_wagon_number(request: Request):
        content_type = request.headers.get("content-type", "").partition(";")[0]
        if content_type.strip().lower() != "image/jpeg":
            raise HTTPException(status_code=415, detail="Content-Type must be image/jpeg")
        raw_length = request.headers.get("content-length")
        if raw_length is not None:
            try:
                content_length = int(raw_length)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="invalid Content-Length") from exc
            if content_length < 0:
                raise HTTPException(status_code=400, detail="invalid Content-Length")
            if content_length > manager.settings.wagon_frame_max_bytes:
                raise HTTPException(status_code=413, detail="JPEG frame is too large")

        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > manager.settings.wagon_frame_max_bytes:
                raise HTTPException(status_code=413, detail="JPEG frame is too large")
            body.extend(chunk)
        return manager.detect_wagon_plate(bytes(body))

    @app.get("/processors")
    def processors():
        return {"processors": [with_startup(item) for item in manager.statuses()]}

    @app.get("/always-on")
    def always_on():
        return manager.always_on_status()

    @app.put("/always-on")
    def configure_always_on(options: AlwaysOnOptions):
        return manager.configure_always_on(options.cameras, options.source)

    @app.get("/camera-roles/wagon-number")
    def wagon_number():
        return manager.wagon_number_status()

    @app.put("/camera-roles/wagon-number")
    def configure_wagon_number(options: WagonNumberOptions):
        return manager.configure_wagon_number(options.camera, options.source)

    @app.delete("/recordings")
    def delete_recordings(options: RecordingDeleteOptions):
        deleted = manager.mediamtx.delete_recording_segments(
            options.stream, options.starts)
        return {"deleted": deleted, "requested": len(options.starts)}

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
