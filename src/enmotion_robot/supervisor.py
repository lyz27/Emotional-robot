"""Long-running ESP32 discovery, heartbeat, command, and reconnect service."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from serial import SerialException

from .device_bridge import DeviceClient, discover_ports


LOG = logging.getLogger("enmotion.supervisor")
SAFE_COMMANDS = frozenset({"ping", "get_status", "disarm", "estop", "clear_faults"})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ClientProtocol(Protocol):
    port: str

    def open(self) -> None: ...

    def close(self) -> None: ...

    def request(self, message_type: str, timeout: float = 1.0, **payload: Any) -> dict[str, Any]: ...


@dataclass
class SupervisorStatus:
    service: str = "starting"
    connected: bool = False
    port: str | None = None
    state: str = "unknown"
    armed: bool = False
    estop: bool = False
    heartbeat_fault: bool = False
    firmware: str | None = None
    protocol: int | None = None
    chip: str | None = None
    free_heap: int | None = None
    device_ms: int | None = None
    last_seen: str | None = None
    last_error: str | None = None
    connection_count: int = 0
    reconnect_count: int = 0
    started_at: str = field(default_factory=utc_now)


@dataclass
class CommandRequest:
    command: str
    done: threading.Event = field(default_factory=threading.Event)
    response: dict[str, Any] | None = None
    error: Exception | None = None


class DeviceSupervisor:
    """Own the serial port and keep an ESP32 connection healthy."""

    def __init__(
        self,
        *,
        heartbeat_interval: float = 0.5,
        status_interval: float = 2.0,
        retry_interval: float = 1.0,
        port_discovery: Callable[[], list[str]] = discover_ports,
        client_factory: Callable[[str], ClientProtocol] = DeviceClient,
    ) -> None:
        self.heartbeat_interval = heartbeat_interval
        self.status_interval = status_interval
        self.retry_interval = retry_interval
        self.port_discovery = port_discovery
        self.client_factory = client_factory
        self._status = SupervisorStatus()
        self._status_lock = threading.Lock()
        self._commands: queue.Queue[CommandRequest] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="esp32-supervisor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def snapshot(self) -> dict[str, Any]:
        with self._status_lock:
            return asdict(self._status)

    def command(self, command: str, timeout: float = 2.0) -> dict[str, Any]:
        if command not in SAFE_COMMANDS:
            raise ValueError(f"unsupported or unsafe command: {command}")
        if not self.snapshot()["connected"]:
            raise ConnectionError("ESP32 is not connected")
        request = CommandRequest(command)
        self._commands.put(request)
        if not request.done.wait(timeout):
            raise TimeoutError(f"command {command!r} timed out")
        if request.error:
            raise request.error
        if request.response is None:
            raise RuntimeError("command completed without a response")
        return request.response

    def _update(self, **changes: Any) -> None:
        with self._status_lock:
            for key, value in changes.items():
                if hasattr(self._status, key):
                    setattr(self._status, key, value)

    def _apply_response(self, response: dict[str, Any]) -> None:
        allowed = {
            "state",
            "armed",
            "estop",
            "heartbeat_fault",
            "firmware",
            "protocol",
            "chip",
            "free_heap",
            "device_ms",
        }
        changes = {key: response[key] for key in allowed if key in response}
        changes.update(last_seen=utc_now(), last_error=None)
        self._update(**changes)

    def _disconnect(self, error: Exception | str) -> None:
        message = str(error)
        LOG.warning("ESP32 disconnected: %s", message)
        self._update(
            service="searching",
            connected=False,
            port=None,
            state="unknown",
            armed=False,
            last_error=message,
        )
        while True:
            try:
                pending = self._commands.get_nowait()
            except queue.Empty:
                break
            pending.error = ConnectionError(message)
            pending.done.set()

    def _process_commands(self, client: ClientProtocol) -> None:
        while True:
            try:
                pending = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                pending.response = client.request(pending.command)
                self._apply_response(pending.response)
            except Exception as exc:  # propagated to caller and then reconnect loop
                pending.error = exc
                raise
            finally:
                pending.done.set()

    def _run_connected(self, client: ClientProtocol) -> None:
        hello = client.request("hello", client="enmotion-runtime")
        count = self.snapshot()["connection_count"] + 1
        self._update(
            service="running",
            connected=True,
            port=client.port,
            connection_count=count,
            reconnect_count=max(0, count - 1),
            last_error=None,
        )
        self._apply_response(hello)
        LOG.info("ESP32 connected on %s with firmware %s", client.port, hello.get("firmware"))

        next_heartbeat = 0.0
        next_status = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            self._process_commands(client)
            if now >= next_heartbeat:
                response = client.request("heartbeat", host_ms=int(now * 1000))
                self._apply_response(response)
                next_heartbeat = now + self.heartbeat_interval
            if now >= next_status:
                response = client.request("get_status")
                self._apply_response(response)
                next_status = now + self.status_interval
            self._stop.wait(0.05)

    def _run(self) -> None:
        self._update(service="searching")
        while not self._stop.is_set():
            ports = self.port_discovery()
            if not ports:
                self._update(service="searching", connected=False, port=None, last_error="no serial device found")
                self._stop.wait(self.retry_interval)
                continue

            client = self.client_factory(ports[0])
            try:
                client.open()
                self._run_connected(client)
            except (SerialException, TimeoutError, OSError, RuntimeError) as exc:
                self._disconnect(exc)
            finally:
                client.close()
            if not self._stop.is_set():
                self._stop.wait(self.retry_interval)

        self._update(service="stopped", connected=False, port=None, armed=False)

