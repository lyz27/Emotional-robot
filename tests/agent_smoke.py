"""Manual live-model smoke test; uses a disposable database, never user records."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from enmotion_robot.work import WorkSpace
from enmotion_robot.work_agent import run_agent


if __name__ == "__main__":
    os.environ.setdefault("ENMOTION_DIALOGUE_URL", "http://127.0.0.1:8081/v1/chat/completions")
    os.environ.setdefault("ENMOTION_DIALOGUE_MODEL", "qwen3-4b-q4")
    with TemporaryDirectory(prefix="enmotion-agent-test-") as folder:
        work = WorkSpace(Path(folder) / "work.db")
        result = run_agent(work, "帮我创建一个待办，标题是Agent验收临时任务，不设置提醒。", "chat")
        print(json.dumps(result, ensure_ascii=False), flush=True)
        assert any(t["title"] == "Agent验收临时任务" for t in work.snapshot()["tasks"]), "Agent未创建任务"
        result = run_agent(work, "请查询我的待办，把标题为Agent验收临时任务的那条标记完成。", "chat")
        print(json.dumps(result, ensure_ascii=False), flush=True)
        assert work.snapshot()["tasks"][0]["done"] == 1, "Agent未完成任务"
        result = run_agent(work, "保存一篇工作笔记，标题为Agent验收会议，正文是：决定周五演示，待确认场地。", "notes")
        print(json.dumps(result, ensure_ascii=False), flush=True)
        assert work.snapshot()["notes"], "Agent未保存笔记"
        result = run_agent(work, "查询工作笔记，找一下Agent验收会议里决定了什么？", "chat")
        print(json.dumps(result, ensure_ascii=False), flush=True)
        assert any(s["operation"] in ("search_notes", "read_note") and s["ok"] for s in result["agent"]["steps"])
