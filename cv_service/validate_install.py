from __future__ import annotations

from .runtime import build_runtime
from .settings import Settings


def main() -> None:
    settings = Settings.from_env()
    model, mediamtx, encoder = build_runtime(settings)
    for camera in settings.prewarm_cameras:
        mediamtx.validate_source(
            camera,
            settings.source_stream(camera, settings.prewarm_source),
        )
    print({
        "model": model.metadata(),
        "encoder": encoder,
        "prewarm": settings.prewarm_cameras,
        "model_reused": True,
        "model_instances": 1,
    })


if __name__ == "__main__":
    main()
