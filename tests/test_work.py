from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from enmotion_robot.work import WorkSpace
from enmotion_robot.web import create_app


def test_work_records_survive_restart_and_remain_separate(tmp_path):
    path = tmp_path / "work.db"
    work = WorkSpace(path)
    task = work.save("tasks", {"title": "跟进方案", "due": "2026-11-07T15:00:00+08:00"})
    assert datetime.fromisoformat(task["due"]).hour == 7
    work.save("notes", {"title": "会议", "body": "原始记录保留"})
    work.save("tasks", {"done": True}, task["id"])
    restored = WorkSpace(path).snapshot()
    assert restored["tasks"][0]["done"] == 1
    assert restored["notes"][0]["body"] == "原始记录保留"
    assert work.history.list_memories() == []
    assert work.delete("tasks", task["id"])
    assert not work.snapshot()["tasks"]


def test_work_api_and_validation(tmp_path):
    work = WorkSpace(tmp_path / "work.db")
    client = TestClient(create_app(workspace=work, manage_supervisor=False))
    assert client.get("/").status_code == 200
    assert "工作空间" in client.get("/").text
    assert "表情与动作" in client.get("/debug").text
    assert client.post("/api/work/tasks", json={"title": ""}).status_code == 422
    assert client.post("/api/work/tasks", json={"title": "开会", "due": "2026-11-07T15:00"}).status_code == 422
    task = client.post("/api/work/tasks", json={"title": "开会"}).json()
    assert client.patch(f"/api/work/tasks/{task['id']}", json={"done": True}).status_code == 200
    assert client.patch("/api/work/notes/999", json={"title": "x"}).status_code == 404
    assert client.post("/api/work/anything", json={"title": "x"}).status_code == 422


def test_work_chat_failure_is_explicit_and_creates_no_tasks(tmp_path, monkeypatch):
    monkeypatch.delenv("ENMOTION_DIALOGUE_URL", raising=False)
    work = WorkSpace(tmp_path / "work.db")
    result = work.chat("拆解项目目标", "breakdown")
    assert result["fallback"] is True
    assert not work.snapshot()["tasks"]
    assert work.snapshot()["messages"][-1]["content"] == result["reply"]
    with pytest.raises(ValueError):
        work.chat("x" * 2501)
