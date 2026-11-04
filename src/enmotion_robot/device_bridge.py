"""Jetson-to-ESP32 serial bridge and diagnostic CLI."""

from __future__ import annotations

import argparse
import glob
import logging
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import serial
from serial import SerialException

from .protocol import Message, ProtocolError, decode_message


LOG = logging.getLogger("enmotion.device")
DEFAULT_BAUD = 115_200


def discover_ports() -> list[str]:
    """Return serial candidates in stable preference order."""
    preferred = ["/dev/enmotion-esp32"]
    discovered = sorted(glob.glob("/dev/ttyACM*")) + sorted(glob.glob("/dev/ttyUSB*"))
    return [port for port in preferred + discovered if Path(port).exists()]


class DeviceClient:
    def __init__(self, port: str, baud: int = DEFAULT_BAUD, timeout: float = 0.2):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._serial: serial.Serial | None = None
        self._next_id = 1

    def __enter__(self) -> "DeviceClient":
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def open(self) -> None:
        self._serial = serial.Serial(
            self.port,
            self.baud,
            timeout=self.timeout,
            write_timeout=1,
        )
        time.sleep(0.15)
        self._serial.reset_input_buffer()

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def send(self, message_type: str, **payload: Any) -> int:
        if self._serial is None:
            raise RuntimeError("serial port is not open")
        message_id = self._next_id
        self._next_id += 1
        frame = Message(message_type, message_id, payload).encode()
        self._serial.write(frame)
        self._serial.flush()
        return message_id

    def read_available(self, deadline: float) -> Iterator[dict[str, Any]]:
        if self._serial is None:
            raise RuntimeError("serial port is not open")
        while time.monotonic() < deadline:
            line = self._serial.readline()
            if not line:
                continue
            try:
                yield decode_message(line)
            except ProtocolError as exc:
                LOG.warning("ignored invalid device frame: %s (%r)", exc, line[:120])

    def request(self, message_type: str, timeout: float = 1.0, **payload: Any) -> dict[str, Any]:
        message_id = self.send(message_type, **payload)
        for response in self.read_available(time.monotonic() + timeout):
            if response.get("id") == message_id:
                return response
        raise TimeoutError(f"no response to {message_type!r} within {timeout:.1f}s")


def diagnostic_loop(port: str, duration: float) -> int:
    try:
        with DeviceClient(port) as client:
            hello = client.request("hello", client="jetson")
            print(f"connected: {hello}")
            end = time.monotonic() + duration
            while time.monotonic() < end:
                started = time.monotonic()
                heartbeat = client.request("heartbeat", host_ms=int(started * 1000))
                print(f"heartbeat: {heartbeat}")
                time.sleep(max(0.0, 0.5 - (time.monotonic() - started)))
            status = client.request("get_status")
            print(f"status: {status}")
        return 0
    except (SerialException, TimeoutError, OSError) as exc:
        LOG.error("device diagnostic failed: %s", exc)
        return 1


def safety_test(port: str) -> int:
    """Exercise heartbeat timeout and latched emergency-stop behavior."""
    try:
        with DeviceClient(port) as client:
            hello = client.request("hello", client="jetson-safety-test")
            print(f"hello: {hello}")
            client.request("heartbeat")

            armed = client.request("arm")
            if armed.get("state") != "armed":
                raise RuntimeError(f"device did not arm: {armed}")
            print(f"armed: {armed}")

            time.sleep(2.5)
            timed_out = client.request("get_status")
            if timed_out.get("state") != "fault" or timed_out.get("armed") is not False:
                raise RuntimeError(f"heartbeat fail-safe did not trigger: {timed_out}")
            print(f"heartbeat timeout safe-stop: {timed_out}")

            recovered = client.request("heartbeat")
            if recovered.get("state") != "safe":
                raise RuntimeError(f"heartbeat recovery failed: {recovered}")
            print(f"heartbeat recovered: {recovered}")

            client.request("arm")
            estopped = client.request("estop")
            if estopped.get("state") != "estop":
                raise RuntimeError(f"emergency stop did not latch: {estopped}")
            print(f"estop latched: {estopped}")

            rejected = client.request("arm")
            if rejected.get("type") != "error" or rejected.get("error") != "unsafe_to_arm":
                raise RuntimeError(f"arming was not rejected during estop: {rejected}")
            print(f"arm correctly rejected: {rejected}")

            cleared = client.request("clear_faults")
            if cleared.get("state") != "safe":
                raise RuntimeError(f"fault clear failed: {cleared}")
            print(f"faults cleared: {cleared}")
        return 0
    except (SerialException, TimeoutError, OSError, RuntimeError) as exc:
        LOG.error("device safety test failed: %s", exc)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Test the Enmotion ESP32 device link")
    parser.add_argument("--port", default="auto", help="serial port or 'auto'")
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--safety-test", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.port == "auto":
        candidates = discover_ports()
        if not candidates:
            LOG.error("no serial device found")
            return 2
        port = candidates[0]
    else:
        port = args.port
    if args.safety_test:
        return safety_test(port)
    return diagnostic_loop(port, args.duration)


if __name__ == "__main__":
    raise SystemExit(main())

