"""Manual Jetson acceptance test with the real model and disposable records."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from enmotion_robot.web import create_app
from enmotion_robot.work import WorkSpace


if __name__ == "__main__":
    os.environ.setdefault("ENMOTION_DIALOGUE_URL", "http://127.0.0.1:8081/v1/chat/completions")
    os.environ.setdefault("ENMOTION_DIALOGUE_MODEL", "qwen3-4b-q4")
    with TemporaryDirectory(prefix="enmotion-capture-test-") as folder:
        token = Path(folder) / "token"
        token.write_text("disposable-test-key")
        os.environ["ENMOTION_SYNC_TOKEN_FILE"] = str(token)
        work = WorkSpace(Path(folder) / "work.db")
        task = work.save("tasks", {"title": "周五演示前确认场地"})
        client = TestClient(create_app(workspace=work, manage_supervisor=False))
        headers = {"Authorization": "Bearer disposable-test-key"}
        state = client.get("/api/sync/work", headers=headers).json()
        assert state["tasks"] == [task]
        assert client.patch(f'/api/sync/tasks/{task["id"]}', headers=headers, json={"done": True}).status_code == 200
        assert not client.get("/api/sync/work", headers=headers).json()["tasks"]
        work.save("tasks", {"done": False}, task["id"])
        for mode, text in (
            ("notes", "标题：演示会议。正文：已完成原型，决定周五演示，场地待确认。"),
            ("breakdown", "周五需要演示产品原型，帮我拆解准备步骤。"),
            ("review", "复盘目前的演示准备进展，未知的部分请标明。"),
        ):
            before = {k: work.snapshot()[k] for k in ("tasks", "notes")}
            payload = {"mode": mode, "text": text, "request_id": "acceptance-" + mode}
            response = client.post("/api/sync/capture", headers=headers, json=payload)
            assert response.status_code == 200, response.text
            result = response.json()
            print(mode, result["reply"], flush=True)
            assert not result["fallback"], result
            assert client.post("/api/sync/capture", headers=headers, json=payload).json() == result
            after = work.snapshot()
            if mode == "notes":
                assert after["notes"], "Agent did not save the note"
            else:
                assert before == {k: after[k] for k in ("tasks", "notes")}, "Advice changed records"
                if mode == "review":
                    assert any(s["ok"] and s["operation"] in {"list_tasks", "search_notes", "read_note"} for s in result["agent"]["steps"])
        print("Capture modes, sidebar completion and safe replay verified", flush=True)
