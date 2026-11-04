from fastapi.testclient import TestClient

from enmotion_robot.web import create_app


class StubSupervisor:
    def snapshot(self) -> dict:
        return {"connected": True, "state": "safe", "port": "/dev/ttyUSB0"}

    def command(self, command: str) -> dict:
        return {"v": 1, "id": 1, "type": command, "ok": True, "state": "safe"}


def test_status_endpoint() -> None:
    client = TestClient(create_app(StubSupervisor(), manage_supervisor=False))
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["state"] == "safe"


def test_safe_command_and_arm_rejection() -> None:
    client = TestClient(create_app(StubSupervisor(), manage_supervisor=False))
    assert client.post("/api/command/ping").status_code == 200
    assert client.post("/api/command/arm").status_code == 400

