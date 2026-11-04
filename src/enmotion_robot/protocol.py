"""Wire protocol shared by the Jetson runtime and ESP32 firmware."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


PROTOCOL_VERSION = 1
MAX_LINE_BYTES = 1024


class ProtocolError(ValueError):
    """Raised when a serial frame is invalid."""


@dataclass(frozen=True)
class Message:
    type: str
    id: int = 0
    payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "v": PROTOCOL_VERSION,
            "id": self.id,
            "type": self.type,
        }
        if self.payload:
            result.update(self.payload)
        return result

    def encode(self) -> bytes:
        return encode_message(self.to_dict())


def encode_message(message: dict[str, Any]) -> bytes:
    """Encode one compact newline-delimited JSON frame."""
    if not isinstance(message.get("type"), str) or not message["type"]:
        raise ProtocolError("message type must be a non-empty string")
    if message.get("v", PROTOCOL_VERSION) != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > MAX_LINE_BYTES:
        raise ProtocolError("message exceeds maximum frame size")
    return encoded + b"\n"


def decode_message(line: bytes | str) -> dict[str, Any]:
    """Validate and decode one protocol frame."""
    raw = line.encode() if isinstance(line, str) else line
    if len(raw) > MAX_LINE_BYTES + 1:
        raise ProtocolError("message exceeds maximum frame size")
    try:
        message = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid JSON frame") from exc
    if not isinstance(message, dict):
        raise ProtocolError("frame must be a JSON object")
    if message.get("v") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    if not isinstance(message.get("id"), int) or message["id"] < 0:
        raise ProtocolError("message id must be a non-negative integer")
    if not isinstance(message.get("type"), str) or not message["type"]:
        raise ProtocolError("message type must be a non-empty string")
    return message

