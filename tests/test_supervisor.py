from enmotion_robot.supervisor import DeviceSupervisor, SAFE_COMMANDS


def test_initial_snapshot_is_safe() -> None:
    snapshot = DeviceSupervisor().snapshot()
    assert snapshot["connected"] is False
    assert snapshot["armed"] is False
    assert snapshot["state"] == "unknown"


def test_browser_command_allowlist_excludes_arm() -> None:
    assert "arm" not in SAFE_COMMANDS
    assert {"ping", "get_status", "disarm", "estop", "clear_faults"} == SAFE_COMMANDS

