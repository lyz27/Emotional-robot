import pytest

from enmotion_robot.simulator import EMOTIONS, RobotSimulator


def test_default_state_contains_all_simulated_inputs() -> None:
    state = RobotSimulator().snapshot()
    assert state["emotion"] == "calm"
    assert state["gaze"] == {"x": 0.0, "y": 0.0}
    assert state["neck"] == {"yaw": 0.0, "pitch": 0.0}
    assert state["sensors"]["distance_cm"] == 100.0
    assert state["motor"] == "disabled"
    assert state["events"][0]["kind"] == "system"


def test_all_week_two_emotions_are_supported() -> None:
    simulator = RobotSimulator()
    for emotion in EMOTIONS:
        response = simulator.command("set_emotion", emotion=emotion)
        assert response["emotion"] == emotion


def test_pose_is_clamped_to_safe_virtual_range() -> None:
    simulator = RobotSimulator()
    gaze = simulator.command("set_gaze", x=5, y=-5)
    neck = simulator.command("set_neck", yaw=100, pitch=-100)
    assert gaze["gaze"] == {"x": 1.0, "y": -1.0}
    assert neck["neck"] == {"yaw": 60.0, "pitch": -30.0}
    assert neck["motor"] == "moving"


def test_sensor_update_and_reset() -> None:
    simulator = RobotSimulator()
    result = simulator.command(
        "set_sensors",
        touch=True,
        distance_cm=23,
        face_detected=True,
        face_x=0.4,
        face_y=-0.2,
        motor="idle",
    )
    assert result["sensors"]["touch"] is True
    assert result["sensors"]["face_x"] == 0.4
    assert result["motor"] == "idle"
    assert simulator.command("reset")["emotion"] == "calm"


def test_invalid_emotion_and_sensor_types_are_rejected() -> None:
    simulator = RobotSimulator()
    with pytest.raises(ValueError):
        simulator.command("set_emotion", emotion="angry")
    with pytest.raises(ValueError):
        simulator.command("set_sensors", touch="yes")
