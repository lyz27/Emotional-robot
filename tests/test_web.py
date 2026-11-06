from fastapi.testclient import TestClient

from enmotion_robot.companion import CompanionCore
from enmotion_robot.web import create_app


class StubSupervisor:
    def snapshot(self) -> dict:
        return {"connected": True, "state": "safe", "port": "/dev/ttyUSB0"}

    def command(self, command: str) -> dict:
        return {"v": 1, "id": 1, "type": command, "ok": True, "state": "safe"}


class StubSpeech:
    output_dir = __import__("pathlib").Path("/tmp/enmotion-test-audio")

    def status(self) -> dict:
        return {"ready": True, "offline": True, "models": {"asr": True, "vad": True, "tts": True}}

    def process(self, source, *, reply_override=None, reply_builder=None) -> dict:
        assert source.name == "hello.wav"
        reply = reply_override or (reply_builder("你好") if reply_builder else "你好呀")
        return {"ok": True, "transcript": "你好", "reply": reply, "audio_url": "/audio/reply.wav"}

    def synthesize_text(self, text: str) -> dict:
        return {"ok": True, "text": text, "audio_url": "/audio/tts.wav"}

    def interrupt(self) -> dict:
        return {"ok": True, "generation": 1}


def test_status_endpoint() -> None:
    client = TestClient(create_app(StubSupervisor(), manage_supervisor=False))
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["state"] == "safe"


def test_dashboard_is_not_cached_between_deployments() -> None:
    client = TestClient(create_app(StubSupervisor(), manage_supervisor=False))
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    assert "Enmotion Studio" in response.text


def test_safe_command_and_arm_rejection() -> None:
    client = TestClient(create_app(StubSupervisor(), manage_supervisor=False))
    assert client.post("/api/command/ping").status_code == 200
    assert client.post("/api/command/arm").status_code == 400


def test_combined_robot_state_and_simulator_commands() -> None:
    client = TestClient(create_app(StubSupervisor(), manage_supervisor=False))
    response = client.post(
        "/api/control/set_emotion",
        json={"target": "simulator", "emotion": "happy"},
    )
    assert response.status_code == 200
    assert response.json()["result"]["emotion"] == "happy"

    state = client.get("/api/robot").json()
    assert state["device"]["connected"] is True
    assert state["simulation"]["emotion"] == "happy"


def test_unified_interface_routes_safe_commands_to_esp32() -> None:
    client = TestClient(create_app(StubSupervisor(), manage_supervisor=False))
    response = client.post("/api/control/ping", json={"target": "esp32"})
    assert response.status_code == 200
    assert response.json()["result"]["type"] == "ping"
    assert client.post("/api/control/arm", json={"target": "esp32"}).status_code == 400


def test_invalid_simulator_values_return_validation_error() -> None:
    client = TestClient(create_app(StubSupervisor(), manage_supervisor=False))
    response = client.post(
        "/api/control/set_emotion",
        json={"target": "simulator", "emotion": "angry"},
    )
    assert response.status_code == 422


def test_speech_file_tts_and_interrupt_endpoints() -> None:
    client = TestClient(
        create_app(StubSupervisor(), speech=StubSpeech(), manage_supervisor=False)
    )
    state = client.get("/api/robot").json()
    assert state["speech"]["ready"] is True

    response = client.post(
        "/api/speech/process",
        files={"audio": ("hello.wav", b"fake wav", "audio/wav")},
        data={"reply": "自定义回复"},
    )
    assert response.status_code == 200
    assert response.json()["reply"] == "自定义回复"

    response = client.post("/api/speech/tts", json={"text": "测试语音"})
    assert response.status_code == 200
    assert response.json()["text"] == "测试语音"
    assert client.post("/api/speech/interrupt").json()["generation"] == 1


def test_companion_chat_memory_edit_delete_and_emotion(tmp_path) -> None:
    companion = CompanionCore(database_path=tmp_path / "companion.db")
    client = TestClient(
        create_app(StubSupervisor(), companion=companion, manage_supervisor=False)
    )
    response = client.post(
        "/api/companion/chat",
        json={"text": "我叫小明，我喜欢蓝色，今天很开心", "session_id": "browser"},
    )
    assert response.status_code == 200
    assert response.json()["emotion"]["name"] == "happy"
    assert client.get("/api/robot").json()["simulation"]["emotion"] == "happy"

    memories = client.get("/api/companion/memories").json()["items"]
    name = next(item for item in memories if item["key"] == "name")
    assert client.put(
        f"/api/companion/memories/{name['id']}", json={"value": "小蓝"}
    ).json()["memory"]["value"] == "小蓝"
    assert client.delete(f"/api/companion/memories/{name['id']}").status_code == 200
    assert client.delete("/api/companion/memories").json()["ok"] is True


def test_voice_uses_companion_when_reply_override_is_empty(tmp_path) -> None:
    companion = CompanionCore(database_path=tmp_path / "companion.db")
    client = TestClient(
        create_app(
            StubSupervisor(), speech=StubSpeech(), companion=companion, manage_supervisor=False
        )
    )
    response = client.post(
        "/api/speech/process",
        files={"audio": ("hello.wav", b"fake wav", "audio/wav")},
    )
    assert response.status_code == 200
    assert response.json()["companion"]["provider"] == "offline-local"


def test_deferred_expression_does_not_move_before_audio_and_blink_preserves_decay(tmp_path):
    from enmotion_robot.companion import EmotionStateMachine
    now = [0.0]
    mind = CompanionCore(
        database_path=tmp_path / "memory.db",
        emotion=EmotionStateMachine(decay_seconds=10, clock=lambda: now[0]),
    )
    client = TestClient(create_app(StubSupervisor(), companion=mind, manage_supervisor=False))
    result = client.post("/api/companion/chat", json={"text":"我好开心","defer_expression":True})
    assert result.json()["emotion"]["name"] == "happy"
    assert client.get("/api/robot").json()["simulation"]["emotion"] == "calm"
    client.post("/api/companion/chat", json={"text":"我好开心"})
    client.post("/api/control/blink", json={"target":"simulator"})
    now[0] = 20
    assert client.get("/api/robot").json()["simulation"]["emotion"] == "calm"

