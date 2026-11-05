"""Browser debug dashboard and runtime entry point."""

from __future__ import annotations

import argparse
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse

from .supervisor import SAFE_COMMANDS, DeviceSupervisor
from .simulator import SIMULATOR_COMMANDS, RobotSimulator


LOG = logging.getLogger("enmotion.web")
STATIC_DIR = Path(__file__).with_name("static")


def create_app(
    supervisor: DeviceSupervisor | None = None,
    simulator: RobotSimulator | None = None,
    *,
    manage_supervisor: bool = True,
) -> FastAPI:
    runtime = supervisor or DeviceSupervisor()
    robot = simulator or RobotSimulator()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if manage_supervisor:
            runtime.start()
        yield
        if manage_supervisor:
            runtime.stop()

    app = FastAPI(title="Enmotion Robot Debug", version="0.2.0", lifespan=lifespan)
    app.state.supervisor = runtime
    app.state.simulator = robot

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "index.html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/status")
    def status() -> dict:
        return runtime.snapshot()

    @app.get("/api/robot")
    def robot_status() -> dict:
        return {"device": runtime.snapshot(), "simulation": robot.snapshot()}

    @app.post("/api/control/{command}")
    def control(command: str, payload: dict = Body(default_factory=dict)) -> dict:
        """Use one endpoint for simulator and safe physical-device commands."""
        target = payload.pop("target", "simulator")
        try:
            if target == "simulator":
                if command not in SIMULATOR_COMMANDS:
                    raise HTTPException(status_code=400, detail="unsupported simulator command")
                return {"target": target, "result": robot.command(command, **payload)}
            if target == "esp32":
                if command not in SAFE_COMMANDS:
                    raise HTTPException(status_code=400, detail="unsupported or unsafe device command")
                if payload:
                    raise HTTPException(status_code=400, detail="device command does not accept payload")
                return {"target": target, "result": runtime.command(command)}
            raise HTTPException(status_code=400, detail="target must be simulator or esp32")
        except ConnectionError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (OSError, RuntimeError) as exc:
            LOG.exception("robot command failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

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

