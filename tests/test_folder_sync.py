import os
import time

from fastapi.testclient import TestClient

from enmotion_robot.folder_sync import collect_files, import_note, sync_once
from enmotion_robot.web import create_app
from enmotion_robot.work import WorkSpace


def test_import_update_version_and_web_edit_conflict(tmp_path):
    work = WorkSpace(tmp_path / "work.db")
    payload = {"source": "mac-inbox", "path": "项目/会议.md", "body": "第一版"}
    first = import_note(work, payload)
    assert not import_note(work, payload)["changed"]
    payload["body"] = "第二版"
    assert import_note(work, payload)["note_id"] == first["note_id"]
    work.save("notes", {"body": "网页人工修改"}, first["note_id"])
    payload["body"] = "Mac第三版"
    conflict = import_note(work, payload)
    assert conflict["conflict_copy"]
    assert conflict["note_id"] != first["note_id"]
    assert any(n["body"] == "网页人工修改" for n in work.snapshot()["notes"])
    with work.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM sync_versions").fetchone()[0] == 3


def test_folder_scope_cache_and_no_delete_propagation(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    note = inbox / "工作.md"
    note.write_text("会议决定先做A", encoding="utf8")
    os.utime(note, (time.time()-5, time.time()-5))
    (inbox / ".secret.md").write_text("ignored")
    (inbox / "link.md").symlink_to(note)
    (inbox / "data.bin").write_bytes(b"ignored")
    assert list(collect_files(inbox)) == [note]
    work = WorkSpace(tmp_path / "work.db")
    def post(path, payload):
        return import_note(work, payload) if path.endswith("import") else {"ok": True}
    config = {"folder": str(inbox)}
    state = tmp_path / "state.json"
    assert sync_once(config, state, post)["imported"] == 1
    assert sync_once(config, state, post)["imported"] == 0
    note.unlink()
    sync_once(config, state, post)
    assert len(work.snapshot()["notes"]) == 1


def test_sync_endpoint_requires_token_and_rejects_traversal(tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text("test-key")
    monkeypatch.setenv("ENMOTION_SYNC_TOKEN_FILE", str(token))
    work = WorkSpace(tmp_path / "work.db")
    client = TestClient(create_app(workspace=work, manage_supervisor=False))
    payload = {"path": "a.md", "body": "工作内容"}
    assert client.post("/api/sync/import", json=payload).status_code == 401
    headers = {"Authorization": "Bearer test-key"}
    assert client.post("/api/sync/import", headers=headers, json=payload).status_code == 200
    assert client.post("/api/sync/import", headers=headers, json={**payload, "path": "../a.md"}).status_code == 422
    assert client.post("/api/sync/heartbeat", headers=headers, json={"scanned":1}).status_code == 200
    assert client.get("/api/work").json()["sync"]["file_count"] == 1


def test_capture_auth_validation_and_retry_identity(tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text("test-key")
    monkeypatch.setenv("ENMOTION_SYNC_TOKEN_FILE", str(token))
    work = WorkSpace(tmp_path / "work.db")
    calls = []
    def chat(text, mode, request_id):
        calls.append((text, mode, request_id))
        return {"reply": "已保存"}
    monkeypatch.setattr(work, "chat", chat)
    client = TestClient(create_app(workspace=work, manage_supervisor=False))
    payload = {"text": "明天下午三点跟进报价", "request_id": "capture-retry"}
    headers = {"Authorization": "Bearer test-key"}
    assert client.post("/api/sync/capture", json=payload).status_code == 401
    for text in (None, {}, "", "x" * 2201):
        assert client.post("/api/sync/capture", headers=headers, json={**payload, "text": text}).status_code == 422
    assert client.post("/api/sync/capture", headers=headers, json={"text": "工作"}).status_code == 422
    for _ in range(2):
        assert client.post("/api/sync/capture", headers=headers, json=payload).json()["reply"] == "已保存"
    assert calls[0] == calls[1]
    assert calls[0][0].endswith(payload["text"])
    assert calls[0][1:] == ("chat", "capture-retry")
    for mode in ("notes", "breakdown", "review"):
        response = client.post("/api/sync/capture", headers=headers, json={**payload, "mode": mode})
        assert response.status_code == 200
        if mode == "notes":
            assert response.json()["fallback"]  # A model claim alone is not a saved note.
            assert "尚未保存" in response.json()["reply"]
        assert calls[-1][1] == mode
    assert client.post("/api/sync/capture", headers=headers, json={**payload, "mode": "shell"}).status_code == 422


def test_native_work_list_and_complete_share_browser_records(tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text("test-key")
    monkeypatch.setenv("ENMOTION_SYNC_TOKEN_FILE", str(token))
    work = WorkSpace(tmp_path / "work.db")
    task = work.save("tasks", {"title": "跟进方案"})
    completed = work.save("tasks", {"title": "已完成"})
    work.save("tasks", {"done": True}, completed["id"])
    for i in range(12):
        work.save("notes", {"title": f"笔记{i}", "body": "会议决定"})
    client = TestClient(create_app(workspace=work, manage_supervisor=False))
    headers = {"Authorization": "Bearer test-key"}
    path = f'/api/sync/tasks/{task["id"]}'
    assert client.get("/api/sync/work").status_code == 401
    assert client.patch(path, json={"done": True}).status_code == 401
    state = client.get("/api/sync/work", headers=headers).json()
    assert state["tasks"] == [task]
    assert len(state["notes"]) == 10
    for payload in ({"done": 1}, {"done": "true"}, {"done": True, "title": "更改标题"}, {}):
        assert client.patch(path, headers=headers, json=payload).status_code == 422
    for _ in range(2):
        assert client.patch(path, headers=headers, json={"done": True}).json()["done"] == 1
    assert client.get("/api/sync/work", headers=headers).json()["tasks"] == []
    assert client.get("/api/work").json()["tasks"][0]["done"] == 1
    assert client.patch("/api/sync/tasks/999", headers=headers, json={"done": True}).status_code == 404
