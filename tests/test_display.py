from fastapi.testclient import TestClient

from enmotion_robot.web import create_app
from enmotion_robot.work import WorkSpace


def test_display_page_and_local_metrics(tmp_path):
    app = create_app(workspace=WorkSpace(tmp_path / "work.db"), manage_supervisor=False)
    client = TestClient(app, client=("127.0.0.1", 1234))
    page = client.get("/face")
    assert page.status_code == 200
    assert "no-store" in page.headers["cache-control"]
    assert "requestAnimationFrame" in page.text
    payload = {"fps": 59.7, "width": 1024, "height": 600}
    assert client.post("/api/display", json=payload).status_code == 200
    assert client.get("/api/display").json()["fps"] == 59.7
    assert client.post("/api/display", json={**payload, "fps": -1}).status_code == 422
    external = TestClient(app, client=("192.168.0.2", 1234))
    assert external.post("/api/display", json=payload).status_code == 403
