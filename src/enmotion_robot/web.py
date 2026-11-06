"""Browser debug dashboard and runtime entry point."""

from __future__ import annotations

import argparse
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from .companion import CompanionCore, CompanionError
from .supervisor import SAFE_COMMANDS, DeviceSupervisor
from .simulator import SIMULATOR_COMMANDS, RobotSimulator
from .speech import SpeechCoordinator, SpeechError, WavBytesSource
from .work import WorkSpace
from .folder_sync import import_note, heartbeat


LOG = logging.getLogger("enmotion.web")
STATIC_DIR = Path(__file__).with_name("static")


def create_app(
    supervisor: DeviceSupervisor | None = None,
    simulator: RobotSimulator | None = None,
    speech: SpeechCoordinator | None = None,
    companion: CompanionCore | None = None,
    workspace: WorkSpace | None = None,
    *,
    manage_supervisor: bool = True,
) -> FastAPI:
    runtime = supervisor or DeviceSupervisor()
    robot = simulator or RobotSimulator()
    voice = speech or SpeechCoordinator()
    mind = companion or CompanionCore()
    work = workspace or WorkSpace()

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
    app.state.speech = voice
    app.state.companion = mind
    companion_control = {"active": False, "emotion": "calm"}

    def apply_companion_action(result: dict) -> None:
        action = result["action"]
        robot.command("set_emotion", emotion=action["emotion"])
        robot.command("set_gaze", **action["gaze"])
        robot.command("set_neck", **action["neck"])
        if action.get("blink"):
            robot.command("blink")
        companion_control.update(active=True, emotion=action["emotion"])

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "work.html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @app.get("/debug", include_in_schema=False)
    def debug_page():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/work")
    def work_state():
        return work.snapshot()

    def sync_authorize(authorization):
        token_path = Path(os.getenv("ENMOTION_SYNC_TOKEN_FILE", STATIC_DIR.parents[2] / "runtime/sync-token"))
        try:
            token = token_path.read_text().strip()
        except OSError:
            raise HTTPException(503, "同步尚未配置")
        if not token or not secrets.compare_digest(authorization or "", "Bearer " + token):
            raise HTTPException(401, "同步认证失败")

    @app.post("/api/sync/import")
    def sync_import(payload: dict = Body(...), authorization: str | None = Header(default=None)):
        sync_authorize(authorization)
        try:
            return import_note(work, payload)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/sync/heartbeat")
    def sync_heartbeat(payload: dict = Body(...), authorization: str | None = Header(default=None)):
        sync_authorize(authorization)
        try:
            return heartbeat(work, payload)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/work/chat")
    async def work_chat(payload: dict = Body(default_factory=dict)):
        try:
            return await run_in_threadpool(work.chat, payload.get("text", ""), payload.get("mode", "chat"), payload.get("request_id"))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/sync/capture")
    async def quick_capture(payload: dict = Body(...), authorization: str | None = Header(default=None)):
        sync_authorize(authorization)
        text = payload.get("text")
        request_id = payload.get("request_id")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2200:
            raise HTTPException(422, "请输入 1–2200 字的工作内容")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 80:
            raise HTTPException(422, "缺少有效的提交标识")
        mode = payload.get("mode", "chat")
        if mode not in ("chat", "notes", "breakdown", "review"):
            raise HTTPException(422, "未知工作模式")
        note = text.strip().startswith(("记一下", "笔记", "记录一下", "保存笔记", "保存工作笔记"))
        instruction = (
            "请保存以下工作笔记，保留原意，不设置提醒。调用 work_records 工具，operation 为 save_note，title 为简短标题，body 为正文：\n" if note or mode == "notes" else
            "请办理这条随手记：工作事项建立待办，普通资料保存笔记；修改或完成按原意执行。不要编造时间。用户内容：\n"
        )
        if mode == "breakdown":
            instruction = "请拆解以下工作目标，给出先后步骤与第一步；本轮只给建议，不创建或修改记录：\n"
        elif mode == "review":
            instruction = "请查询现有待办和笔记，结合以下补充做工作复盘：已完成、未完成、卡点和下一步；本轮不修改记录，不编造事实：\n"
        prompt = instruction + text.strip()
        try:
            result = await run_in_threadpool(work.chat, prompt, mode, request_id)
            if mode == "notes" or (note and mode == "chat"):
                steps = result.get("agent", {}).get("steps", [])
                if not any(s.get("ok") and s.get("operation") == "save_note" for s in steps):
                    result = {**result, "fallback": True, "reply": "笔记尚未保存，文字已保留，请重试或调整内容。"}
            return result
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/sync/work")
    def quick_work(authorization: str | None = Header(default=None)):
        sync_authorize(authorization)
        state = work.snapshot()
        return {"tasks": [t for t in state["tasks"] if not t["done"]], "notes": state["notes"][:10]}

    @app.patch("/api/sync/tasks/{item_id}")
    def quick_complete(item_id: int, payload: dict = Body(...), authorization: str | None = Header(default=None)):
        sync_authorize(authorization)
        if set(payload) != {"done"} or type(payload["done"]) is not bool:
            raise HTTPException(422, "这里只接受完成状态")
        try:
            return work.save("tasks", {"done": payload["done"]}, item_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/work/{kind}")
    def work_create(kind: str, payload: dict = Body(default_factory=dict)):
        try:
            return work.save(kind, payload)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.patch("/api/work/{kind}/{item_id}")
    def work_edit(kind: str, item_id: int, payload: dict = Body(default_factory=dict)):
        try:
            return work.save(kind, payload, item_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/work/{kind}/{item_id}")
    def work_delete(kind: str, item_id: int):
        try:
            if not work.delete(kind, item_id):
                raise HTTPException(404, "记录不存在")
            return {"ok": True}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/status")
    def status() -> dict:
        return runtime.snapshot()

    @app.get("/api/robot")
    def robot_status() -> dict:
        companion_state = mind.status("browser")
        simulation = robot.snapshot()
        if (
            companion_control["active"]
            and simulation["emotion"] == companion_control["emotion"]
            and simulation["emotion"] != companion_state["emotion"]["name"]
        ):
            apply_companion_action({"action": mind.current_action()})
            simulation = robot.snapshot()
        return {
            "device": runtime.snapshot(),
            "simulation": simulation,
            "speech": voice.status(),
            "companion": companion_state,
        }

    @app.get("/api/companion/status")
    def companion_status(session_id: str = "default") -> dict:
        return mind.status(session_id)

    @app.post("/api/companion/chat")
    async def companion_chat(payload: dict = Body(default_factory=dict)) -> dict:
        try:
            result = await run_in_threadpool(
                mind.chat,
                str(payload.get("text", "")),
                session_id=str(payload.get("session_id", "default")),
                remember=bool(payload.get("remember", True)),
            )
            if not payload.get("defer_expression", False):
                apply_companion_action(result)
            return result
        except CompanionError as exc:
            raise HTTPException(
                status_code=422, detail={"code": exc.code, "message": str(exc)}
            ) from exc

    @app.get("/api/companion/memories")
    def list_memories(kind: str | None = None) -> dict:
        try:
            items = mind.store.list_memories(kind)
            return {"items": items, "count": len(items)}
        except CompanionError as exc:
            raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc

    @app.delete("/api/companion/memories")
    def clear_memories() -> dict:
        return {"ok": True, "deleted": mind.store.clear_memories()}

    @app.put("/api/companion/memories/{memory_id}")
    def update_memory(memory_id: int, payload: dict = Body(default_factory=dict)) -> dict:
        try:
            return {
                "ok": True,
                "memory": mind.store.update_memory(
                    memory_id,
                    key=str(payload["key"]) if "key" in payload else None,
                    value=str(payload["value"]) if "value" in payload else None,
                ),
            }
        except CompanionError as exc:
            status = 404 if exc.code == "memory_not_found" else 422
            raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)}) from exc

    @app.delete("/api/companion/memories/{memory_id}")
    def delete_memory(memory_id: int) -> dict:
        if not mind.store.delete_memory(memory_id):
            raise HTTPException(status_code=404, detail="memory not found")
        return {"ok": True, "deleted": memory_id}

    @app.delete("/api/companion/sessions/{session_id}")
    def clear_session(session_id: str) -> dict:
        return {"ok": True, "deleted": mind.store.clear_session(mind._session_id(session_id))}

    @app.get("/api/speech/status")
    def speech_status() -> dict:
        return voice.status()

    @app.post("/api/speech/process")
    async def process_speech(
        audio: UploadFile = File(...),
        reply: str = Form(default=""),
        session_id: str = Form(default="voice"),
        remember: bool = Form(default=True),
    ) -> dict:
        if not (audio.filename or "").lower().endswith(".wav"):
            raise HTTPException(status_code=415, detail="请上传 .wav 格式的音频文件")
        data = await audio.read()
        if len(data) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="音频文件不能超过 20MB")
        dialogue: dict | None = None

        def build_reply(transcript: str) -> str:
            nonlocal dialogue
            dialogue = mind.chat(transcript, session_id=session_id, remember=remember)
            return dialogue["reply"]

        try:
            result = await run_in_threadpool(
                voice.process,
                WavBytesSource(data, audio.filename or "upload.wav"),
                reply_override=reply,
                reply_builder=None if reply.strip() else build_reply,
            )
            if dialogue:
                result["companion"] = dialogue
                apply_companion_action(dialogue)
            return result
        except SpeechError as exc:
            status_code = 504 if exc.code == "timeout" else 409 if exc.code == "interrupted" else 422
            raise HTTPException(status_code=status_code, detail={"code": exc.code, "message": str(exc)}) from exc
        except CompanionError as exc:
            raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc

    @app.post("/api/speech/tts")
    def synthesize_speech(payload: dict = Body(default_factory=dict)) -> dict:
        try:
            return voice.synthesize_text(str(payload.get("text", "")))
        except SpeechError as exc:
            raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc

    @app.post("/api/speech/interrupt")
    def interrupt_speech() -> dict:
        return voice.interrupt()

    @app.get("/audio/{filename}", include_in_schema=False)
    def generated_audio(filename: str) -> FileResponse:
        safe_name = Path(filename).name
        if safe_name != filename or not safe_name.endswith(".wav"):
            raise HTTPException(status_code=404, detail="audio not found")
        path = voice.output_dir / safe_name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="audio not found")
        return FileResponse(path, media_type="audio/wav", filename=safe_name)

    @app.post("/api/control/{command}")
    def control(command: str, payload: dict = Body(default_factory=dict)) -> dict:
        """Use one endpoint for simulator and safe physical-device commands."""
        target = payload.pop("target", "simulator")
        try:
            if target == "simulator":
                if command not in SIMULATOR_COMMANDS:
                    raise HTTPException(status_code=400, detail="unsupported simulator command")
                if command != "blink":
                    companion_control["active"] = False
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

