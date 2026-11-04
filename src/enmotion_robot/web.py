"""Browser debug dashboard and runtime entry point."""

from __future__ import annotations

import argparse
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .supervisor import SAFE_COMMANDS, DeviceSupervisor


LOG = logging.getLogger("enmotion.web")
STATIC_DIR = Path(__file__).with_name("static")


def create_app(supervisor: DeviceSupervisor | None = None, *, manage_supervisor: bool = True) -> FastAPI:
    runtime = supervisor or DeviceSupervisor()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if manage_supervisor:
            runtime.start()
        yield
        if manage_supervisor:
            runtime.stop()

    app = FastAPI(title="Enmotion Robot Debug", version="0.1.0", lifespan=lifespan)
    app.state.supervisor = runtime

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/status")
    def status() -> dict:
        return runtime.snapshot()

    @app.post("/api/command/{command}")
    def command(command: str) -> dict:
        if command not in SAFE_COMMANDS:
            raise HTTPException(status_code=400, detail="unsupported or unsafe command")
        try:
            return runtime.command(command)
        except ConnectionError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        except (OSError, RuntimeError) as exc:
            LOG.exception("device command failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Enmotion robot debug service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--log-level", default="info", choices=("debug", "info", "warning", "error"))
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()

