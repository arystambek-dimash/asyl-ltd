from __future__ import annotations

import logging
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cv_service.app import create_app
from cv_service.contracts import ProcessorOptions
from cv_service.processor import ProcessorManager
from cv_service.runtime import build_runtime
from cv_service.settings import Settings


def build_service(settings: Settings) -> tuple[ProcessorManager, object]:
    model, mediamtx, encoder = build_runtime(settings)
    manager = ProcessorManager(settings, model, mediamtx, encoder)
    try:
        for camera in settings.prewarm_cameras:
            manager.prewarm(camera, ProcessorOptions(source=settings.prewarm_source))
    except Exception:
        manager.close()
        raise
    return manager, create_app(manager)


def main() -> None:
    # All heavy imports, checkpoint validation, warm-up, MediaMTX prewarm and
    # encoder selection happen before uvicorn is asked to bind the HTTP port.
    settings = Settings.from_env()
    manager, app = build_service(settings)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger(__name__).info(
        "AI service ready model=%s device=%s classes=%s encoder=%s model_reused=true model_instances=1",
        settings.model_path.name,
        settings.model_device,
        manager.model.metadata().get("classes"),
        manager.encoder,
    )
    import uvicorn
    uvicorn.run(app, host=settings.bind_host, port=settings.bind_port, access_log=False)


if __name__ == "__main__":
    main()
