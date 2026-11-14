from enmotion_robot.vision import Orchestrator
from enmotion_robot.simulator import RobotSimulator


def target(x=0.5, area=.1, kind="face"):
    return {"kind": kind, "x": x, "y": -.2, "area": area, "box": [.4, .2, .2, .2]}


def test_enter_approach_leave_empty_and_camera_failure():
    flow = Orchestrator(RobotSimulator(), clock=lambda: 0)
    flow.observe([target()], now=0)
    flow.observe([target()], now=.6)
    assert flow.tick(now=.6)["present"]
    flow.observe([target(area=.17)], now=1)
    flow.observe([target(area=.18)], now=1.2)
    assert [e["event"] for e in flow.snapshot()["events"]] == ["approaching", "arrived"]
    flow.observe([], now=2)
    assert flow.tick(now=2)["present"]  # tolerate brief occlusion
    flow.observe([], now=3)
    assert not flow.tick(now=3)["present"]
    flow.observe([], now=34)
    flow.observe([], now=40)
    assert [e["event"] for e in flow.snapshot()["events"]].count("empty") == 1
    before = flow.snapshot()["events"]
    flow.observe([], online=False, now=80)
    assert flow.snapshot()["events"] == before


def test_priority_smooth_bounds_and_no_motor_commands():
    clock = [0.]
    robot = RobotSimulator()
    flow = Orchestrator(robot, clock=lambda: clock[0])
    flow.observe([target(x=1)], now=0)
    flow.observe([target(x=1)], now=.6)
    flow.tick(now=.6)
    assert 0 < robot.snapshot()["neck"]["yaw"] < 25
    clock[0] = 1
    flow.touch()
    assert flow.tick(now=1)["priority"] == "touch"
    assert flow.tick({"phase": "speaking", "emotion": "happy"}, now=1.25)["priority"] == "voice"
    assert flow.snapshot()["target"] is not None  # speaking still retains perception
    assert robot.snapshot()["emotion"] == "happy"
    assert flow.tick(now=1.5)["priority"] == "touch"
    assert flow.tick(now=4)["priority"] == "vision"
    for i in range(20):
        flow.tick(now=4+i*.25)
    sim = robot.snapshot()
    assert abs(sim["neck"]["yaw"]) <= 25
    assert abs(sim["neck"]["pitch"]) <= 12
    assert sim["emotion"] == "calm"
    flow.observe([], now=12)
    for i in range(20):
        flow.tick(now=12+i*.25)
    assert abs(robot.snapshot()["neck"]["yaw"]) < .7
    # All calls are to the simulator, never the serial supervisor.


def test_target_continuity_and_touch_edge():
    robot = RobotSimulator()
    flow = Orchestrator(robot, clock=lambda: 0)
    flow.observe([target(-.5)], now=0)
    flow.observe([target(-.45), target(.7, .4)], now=.7)
    assert flow.tick(now=.7)["target"]["x"] == -.45
    robot.command("set_sensors", touch=True)
    assert flow.tick(now=1)["priority"] == "touch"
    assert flow.tick(now=4)["priority"] == "vision"  # held touch does not retrigger


def test_vision_api_does_not_require_a_camera_in_unit_tests(tmp_path):
    from fastapi.testclient import TestClient
    from enmotion_robot.web import create_app
    from enmotion_robot.work import WorkSpace
    app = create_app(workspace=WorkSpace(tmp_path / "work.db"), manage_supervisor=False)
    client = TestClient(app)
    assert not client.get("/api/vision").json()["online"]
    assert client.get("/vision").status_code == 200
    assert client.post("/api/vision/touch").json()["ok"]
    assert app.state.orchestration.tick()["priority"] == "touch"
