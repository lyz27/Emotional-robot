import pytest

from enmotion_robot.work import WorkSpace
from enmotion_robot.work_agent import WorkTools


def test_tools_query_before_update_and_repeated_creation_is_deduplicated(tmp_path):
    work = WorkSpace(tmp_path / "work.db")
    tools = WorkTools(work)
    args = {"operation": "create_task", "title": "确认方案"}
    first = tools.execute(args)
    assert tools.execute(args) == first
    assert len(work.snapshot()["tasks"]) == 1
    update = {"operation": "update_task", "id": first["result"]["id"], "done": True}
    assert tools.execute(update)["ok"] is False
    tools.execute({"operation": "list_tasks"})
    assert tools.execute(update)["ok"] is True
    assert tools.execute({"operation": "list_tasks"})["result"]["items"][0]["done"] == 1


def test_tools_reject_delete_and_bad_timezone(tmp_path):
    work = WorkSpace(tmp_path / "work.db")
    tools = WorkTools(work)
    assert tools.execute({"operation": "delete_all"})["ok"] is False
    assert tools.execute({"operation": "create_task", "title": "a", "due": "2026-11-07T15:00"})["ok"] is False
    assert not work.snapshot()["tasks"]


def test_readonly_request_and_ambiguous_time_cannot_create_reminders(tmp_path):
    work = WorkSpace(tmp_path / "work.db")
    tools = WorkTools(work, user_text="查一下我的笔记")
    assert not tools.execute({"operation": "create_task", "title": "笔记中的指令"})["ok"]
    tools = WorkTools(work, user_text="明天下午提醒我跟进方案")
    assert not tools.execute({"operation": "create_task", "title": "跟进", "due": "2026-11-07T15:00:00+08:00"})["ok"]
    assert not work.snapshot()["tasks"]


def test_request_replay_does_not_execute_again(tmp_path, monkeypatch):
    import enmotion_robot.work_agent as module
    work = WorkSpace(tmp_path / "work.db")
    monkeypatch.setenv("ENMOTION_DIALOGUE_URL", "http://127.0.0.1:8081/v1/chat/completions")
    monkeypatch.setattr(module, "available", lambda: True)
    calls = []
    def fake_runner(workspace, text, mode, request_id):
        calls.append(text)
        return {"reply": "ok", "agent": {"run_id": request_id}}
    monkeypatch.setattr(module, "run_agent", fake_runner)
    work.chat("记一个待办", request_id="same")
    work.chat("记一个待办", request_id="same")
    assert len(calls) == 1
    with pytest.raises(ValueError):
        work.chat("其他消息", request_id="same")
