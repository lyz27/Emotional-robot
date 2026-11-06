import pytest

from enmotion_robot.work import WorkSpace
from enmotion_robot.work_agent import WorkTools, write_receipts


def test_tools_query_before_update_and_repeated_creation_is_deduplicated(tmp_path):
    work = WorkSpace(tmp_path / "work.db")
    tools = WorkTools(work)
    args = {"operation": "create_task", "title": "确认方案"}
    first = tools.execute(args)
    assert tools.execute(args) == first
    assert len(work.snapshot()["tasks"]) == 1
    update = {"operation": "update_task", "id": first["result"]["id"], "done": True}
    assert tools.execute(update)["ok"] is True
    assert tools.execute({"operation": "list_tasks"})["result"]["items"][0]["done"] == 1


def test_update_resolves_title_without_id_and_ignores_model_guessed_id(tmp_path):
    work = WorkSpace(tmp_path / "work.db")
    wanted = work.save("tasks", {"title": "确认供应商方案"})
    other = work.save("tasks", {"title": "整理会议记录"})
    tools = WorkTools(work, user_text="确认供应商方案改到明天下午三点")
    result = tools.execute({"operation": "update_task", "id": other["id"], "target": "确认供应商方案", "due": "2026-11-07T15:00:00+08:00"})
    assert result["ok"]
    assert result["result"]["id"] == wanted["id"]
    assert next(t for t in work.snapshot()["tasks"] if t["id"] == other["id"])["due"] is None


def test_content_and_time_create_task_without_command_verb(tmp_path):
    work = WorkSpace(tmp_path / "work.db")
    tools = WorkTools(work, user_text="明天下午三点确认供应商方案")
    result = tools.execute({"operation": "create_task", "title": "明天下午三点确认供应商方案", "due": "2026-11-07T15:00:00+08:00"})
    assert result["ok"]
    assert result["result"]["title"] == "确认供应商方案"


def test_ambiguous_title_asks_for_human_detail_not_id(tmp_path):
    work = WorkSpace(tmp_path / "work.db")
    work.save("tasks", {"title": "确认方案"})
    work.save("tasks", {"title": "确认方案"})
    result = WorkTools(work, user_text="确认方案标记完成").execute({"operation": "update_task", "target": "确认方案", "done": True})
    assert not result["ok"]
    assert "ID" not in result["error"]
    assert all(not t["done"] for t in work.snapshot()["tasks"])


def test_receipt_reports_actual_record_and_beijing_time_without_id():
    reply = write_receipts([{"ok": True, "operation": "update_task", "arguments": {"due": "x"},
                             "result": {"id": 123, "title": "确认方案", "due": "2026-11-07T07:00:00+00:00"}}])
    assert "15:00" in reply
    assert "北京时间" in reply
    assert "123" not in reply


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
