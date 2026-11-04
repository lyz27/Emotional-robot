import pytest

from enmotion_robot.protocol import Message, ProtocolError, decode_message, encode_message


def test_message_round_trip() -> None:
    encoded = Message("heartbeat", 7, {"host_ms": 123}).encode()
    assert decode_message(encoded) == {
        "v": 1,
        "id": 7,
        "type": "heartbeat",
        "host_ms": 123,
    }


def test_rejects_invalid_version() -> None:
    with pytest.raises(ProtocolError, match="version"):
        decode_message('{"v":2,"id":1,"type":"hello"}')


def test_rejects_missing_type() -> None:
    with pytest.raises(ProtocolError, match="type"):
        encode_message({"v": 1, "id": 1})

