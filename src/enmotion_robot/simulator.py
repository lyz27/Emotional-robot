"""In-memory robot behavior simulator used before physical hardware exists."""

from __future__ import annotations

import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


EMOTIONS = frozenset({"calm", "happy", "curious", "sleepy", "worried", "thinking"})
MOTOR_STATES = frozenset({"disabled", "idle", "moving", "fault"})
SIMULATOR_COMMANDS = frozenset(
    {"set_emotion", "set_gaze", "set_neck", "set_sensors", "blink", "reset"}
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _number(payload: dict[str, Any], name: str, low: float, high: float) -> float:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    return round(max(low, min(high, float(value))), 3)


def _boolean(payload: dict[str, Any], name: str) -> bool:
    value = payload.get(name)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


class RobotSimulator:
    """Store simulated expression, pose and sensor state behind a command API."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sequence = 0
        self._events: list[dict[str, Any]] = []
        self._state = self._default_state()
        self._record("system", "模拟器已启动")

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "emotion": "calm",
            "gaze": {"x": 0.0, "y": 0.0},
            "neck": {"yaw": 0.0, "pitch": 0.0},
            "sensors": {
                "touch": False,
                "distance_cm": 100.0,
                "face_detected": False,
                "face_x": 0.0,
                "face_y": 0.0,
            },
            "motor": "disabled",
            "blink_count": 0,
            "updated_at": utc_now(),
        }

    def _record(self, kind: str, message: str, **data: Any) -> None:
        self._sequence += 1
        self._events.insert(
            0,
            {"seq": self._sequence, "at": utc_now(), "kind": kind, "message": message, **data},
        )
        del self._events[50:]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            result = deepcopy(self._state)
            result["events"] = deepcopy(self._events)
            result["sequence"] = self._sequence
            return result

    def command(self, command: str, **payload: Any) -> dict[str, Any]:
        if command not in SIMULATOR_COMMANDS:
            raise ValueError(f"unsupported simulator command: {command}")

        with self._lock:
            if command == "set_emotion":
                emotion = payload.get("emotion")
                if emotion not in EMOTIONS:
                    raise ValueError(f"unsupported emotion: {emotion}")
                self._state["emotion"] = emotion
                self._record("emotion", f"情绪切换为 {emotion}", emotion=emotion)

            elif command == "set_gaze":
                self._state["gaze"] = {
                    "x": _number(payload, "x", -1.0, 1.0),
                    "y": _number(payload, "y", -1.0, 1.0),
                }
                self._record("motion", "注视方向已更新", **self._state["gaze"])

            elif command == "set_neck":
                self._state["neck"] = {
                    "yaw": _number(payload, "yaw", -60.0, 60.0),
                    "pitch": _number(payload, "pitch", -30.0, 30.0),
                }
                self._state["motor"] = "moving" if any(self._state["neck"].values()) else "idle"
                self._record("motion", "虚拟颈部姿态已更新", **self._state["neck"])

            elif command == "set_sensors":
                sensors = self._state["sensors"]
                if "touch" in payload:
                    sensors["touch"] = _boolean(payload, "touch")
                if "distance_cm" in payload:
                    sensors["distance_cm"] = _number(payload, "distance_cm", 0.0, 400.0)
                if "face_detected" in payload:
                    sensors["face_detected"] = _boolean(payload, "face_detected")
                if "face_x" in payload:
                    sensors["face_x"] = _number(payload, "face_x", -1.0, 1.0)
                if "face_y" in payload:
                    sensors["face_y"] = _number(payload, "face_y", -1.0, 1.0)
                if "motor" in payload:
                    motor = payload["motor"]
                    if motor not in MOTOR_STATES:
                        raise ValueError(f"unsupported motor state: {motor}")
                    self._state["motor"] = motor
                self._record("sensor", "模拟传感器已更新", sensors=deepcopy(sensors))

            elif command == "blink":
                self._state["blink_count"] += 1
                self._record("expression", "眨眼", count=self._state["blink_count"])

            elif command == "reset":
                self._state = self._default_state()
                self._record("system", "模拟状态已重置")

            self._state["updated_at"] = utc_now()
            result = deepcopy(self._state)
            result.update(ok=True, command=command, sequence=self._sequence)
            return result
