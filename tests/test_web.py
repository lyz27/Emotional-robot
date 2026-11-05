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

